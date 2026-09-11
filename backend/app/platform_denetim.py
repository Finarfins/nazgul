"""PLATFORM OLAYLARININ DENETİM KAYDI (PP1).

NEDEN BURADA, ``activity_logs``TA DEĞİL. ``activity_logs`` kiracının kendi
hareket defteridir ve ``company_id`` taşır. Platform yedeği, yedek indirme,
geri yükleme ve kiracı geri yükleme hiçbir kiracıya ait DEĞİLDİR. PP1 öncesi
ölçüldü (develop 6442794): ``POST /api/platform/backups`` ``X-Company-Id: 1``
ile çağrıldığında olay ``activity_logs(company_id=1, action_type=
'backup.created')`` olarak O KİRACININ defterine düşüyordu — yani platform
işlemi, operatörün üyesi olduğu rastgele bir firmanın yöneticisine
görünüyordu. Muafiyetten sonra ``request.state.company_id`` zaten ``None``dır;
olay ``security_audit_logs``a firmasız yazılır ve ``GET /api/platform/audit``
ile okunur.

CHECK KISITINA UYUM — ve bu bir TAVİZDİR, açıkça yazılıyor.
``ck_security_audit_logs_untenanted_only_preauth`` firmasız satırda
``user_id`` ve ``username``i NULL ister (göç 20260812_0059). Platform olayı
kimliği çözülmüş bir operatörün eylemidir ve kısıtın hayal ettiği "kimlik
öncesi" olay değildir; ama bu PR göç AÇMAZ. Bu yüzden:

* ``user_id``/``username`` NULL yazılır (kısıt tutar — yazım yedek çıkışa
  düşmez, ``/api/ready`` 503'e dönmez);
* aktörün kimliği ``failure_reason`` sütununa ``aktor=<id>`` olarak yazılır.
  Sütunun adı bu kullanım için yanlıştır; tablonun serbest metin taşıyan tek
  sütunu odur. Kısıtı ``path LIKE '/api/platform/%'`` ile gevşetmek AYRI bir
  göç kararıdır (PP0/PP2 adayı).

``action`` sütunu ``String(20)``dır: ``platform.`` öneki 9 karakter yer, geriye
11 kalır. Bu yüzden olay adları KISA bir kataloğa eşlenir; uzun ad (ör.
``backup.restore_rollback_completed``) ``failure_reason`` içinde ``olay=``
olarak durur. Katalog dışı olay ``ValueError`` ile reddedilir.

SİSTEM AKTÖRÜ (H32). Bakım kurtarması (``maintenance._record_recovery``) ve
CLI zorla temizleme (``backup_cli._force_clear_activity``) HTTP isteği ve
uygulama kullanıcısı OLMADAN koşar. Onlar için sahte bir kullanıcı eşlemesi
(``{"id": 0}``) uydurulmaz — ``aktor=0`` var olmayan bir kullanıcıyı işaret
ederdi. Bunun yerine AÇIK bir yol vardır: ``platform_olayi_yaz(None, ...,
sistem=True)`` ve ``failure_reason``da ``aktor=sistem`` yazılır. İstek ile
bayrak birbirini dışlar: isteksiz ama bayraksız çağrı ``ValueError`` alır,
yani aktörsüz bir platform satırı ancak BİLEREK yazılabilir. H32 öncesi bu iki
yazıcı olayı ``SELECT id FROM companies ORDER BY id LIMIT 1`` ile İLK
kiracının ``activity_logs``una yazıyordu (#122 mercegi, develop 50b0dba).
"""
from __future__ import annotations

from typing import Any, Mapping

from sqlalchemy import insert

from .auth import audit_logs, utcnow
from .db import SessionLocal

PLATFORM_EYLEM_ONEKI = "platform."
#: ``security_audit_logs.action`` genişliği (``app/auth.py``).
EYLEM_GENISLIGI = 20

#: Uzun olay adı -> ``action`` sütununa sığan kısa kod. KAPALI katalog.
PLATFORM_OLAYLARI: dict[str, str] = {
    "backup.created": "platform.bk_create",
    "backup.downloaded": "platform.bk_download",
    "backup.restore_started": "platform.rs_start",
    "backup.restore_completed": "platform.rs_done",
    "backup.restore_failed": "platform.rs_fail",
    "backup.restore_rollback_started": "platform.rb_start",
    "backup.restore_rollback_completed": "platform.rb_done",
    "backup.restore_rollback_failed": "platform.rb_fail",
    "company.restored": "platform.tn_restore",
    # H32: istek dışı yazıcılar (bakım kurtarması, CLI zorla temizleme).
    "backup.maintenance_recovered": "platform.mt_recover",
    "backup.maintenance_force_cleared": "platform.mt_force",
}

#: İstek dışı (sistem/CLI) yazıcının ``failure_reason``daki aktör notu.
SISTEM_AKTOR_NOTU = "aktor=sistem"


def aktor_notu(kullanici: Mapping[str, Any] | None) -> str | None:
    """Firmasız satırda kimliğin taşındığı biçim: ``aktor=<id>``."""
    if not kullanici or kullanici.get("id") is None:
        return None
    return f"aktor={int(kullanici['id'])}"


def platform_olayi_yaz(
    request: Any | None,
    olay: str,
    ozet: str,
    *,
    status_code: int | None = None,
    sistem: bool = False,
) -> None:
    """Platform olayını firmasız denetim satırı olarak yazar.

    ``sistem=True`` yalnız ``request=None`` ile geçerlidir (modül notu,
    "SİSTEM AKTÖRÜ").

    Yazım HATASI YUTULMAZ: çağıran karar verir (``platform_backups._safe_log``
    geri yükleme sonrası yutar; olayın asıl kalıcı izi orada dış günlüktür).
    """
    if sistem != (request is None):
        raise ValueError("platform olayı: sistem=True yalnız request=None ile verilir")
    eylem = PLATFORM_OLAYLARI.get(olay)
    # Başarısız olay "success" yazılmaz: ``outcome`` okuyanın ilk süzgecidir.
    # Durum kodu verilmezse olayın kendisinden türer: bu uçlarda başarısız
    # geri yükleme/dönüş 409 ile döner (`platform_backups.restore_backup`).
    basarisiz = olay.endswith("_failed")
    sonuc = "error" if basarisiz else "success"
    if status_code is None:
        status_code = 409 if basarisiz else 200
    if eylem is None:
        raise ValueError(f"Katalog dışı platform olayı: {olay}")
    state = getattr(request, "state", None)
    kullanici = getattr(state, "user", None)
    istemci = getattr(request, "client", None)
    basliklar = getattr(request, "headers", None) or {}
    aktor = SISTEM_AKTOR_NOTU if sistem else aktor_notu(kullanici)
    parcalar = [p for p in (aktor, f"olay={olay}") if p]
    not_metni = "; ".join(parcalar) + f" | {ozet}"
    # ``company_id`` AÇIKÇA ``None`` — kiracı kapsam kapısında
    # (`tests/test_core_tenant_scoping_guard.py::CEKIRDEK_KIRACI_ISTISNALARI`)
    # bu ifade gerekçesiyle lisanslıdır. Satır tanımı gereği firmasızdır.
    with SessionLocal.begin() as db:
        db.execute(
            insert(audit_logs).values(
                company_id=None,
                user_id=None,
                username=None,
                action=eylem,
                path=str(getattr(getattr(request, "url", None), "path", "") or "")[:500],
                status_code=int(status_code),
                ip_address=getattr(istemci, "host", None),
                created_at=utcnow(),
                request_id=getattr(state, "request_id", None),
                outcome=sonuc,
                duration_ms=None,
                auth_source=getattr(state, "auth_source", None),
                user_agent=(basliklar.get("user-agent") or "")[:500] or None,
                failure_reason=not_metni[:300],
            )
        )
