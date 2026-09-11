"""KİRACI GERİ YÜKLEME UCU — ``POST /api/platform/tenant-restore`` (5.1c).

NEDEN PLATFORM ÖNEKİ
--------------------
Bu uç HİÇBİR kiracının kendi ucu değildir: yazdığı firma istek anında
YOKTUR (``yeni`` kipi) ya da KAPALIDIR (``yerine`` kipi), yani çağıranın
üyeliği o firmaya çözülemez. Kapı ``/api/platform/backups`` ile AYNIDIR:
``require_platform_operator`` (admin rolü + ``SUNGUR_PLATFORM_OPERATORS``
listesi). Ara katman kimlik ve CSRF ister ama PP1'den beri KİRACI ÇÖZMEZ:
``/api/platform/`` öneki ``platform_access.platform_yolu`` ile muaftır,
``X-Company-Id`` okunmaz ve ``request.state.company_id`` ``None`` kalır.
Denetim satırı firmasız ``security_audit_logs``a yazılır
(``app/platform_denetim.py``); PP1 öncesi operatörün varsayılan firmasının
``activity_logs``una düşüyordu.

İZİN ``__admin_only__``, ``read`` DEĞİL — ve bu, yedek ucundan BİLİNÇLİ bir
sapmadır. Yedek uçları ``read`` alır ve gerçek kapıyı yönlendiriciye bırakır;
burada aynı şeyi yapmak ``EXPECTED_READ`` sayacını (80) oynatır ve bir POST'u
``read`` ailesine sokardı. Operatör tanım gereği ``admin``dir
(``is_platform_operator`` rolü de denetler), yani ``__admin_only__`` hiçbir
operatörü dışarıda bırakmaz — yalnız kapıyı ara katmanda da kapatır.

GÖVDE AKAR, BELLEKTE TUTULMAZ
-----------------------------
Zip ``settings.max_tenant_restore_upload_bytes`` (varsayılan 512 MiB) tavanına
kadar geçici dosyaya PARÇA PARÇA yazılır; tavanı aşan yükleme 413 alır ve
dosya silinir. ``main.py``deki yol tavanı bu sayının 1 MiB üstüdür (multipart
çerçevesi), yani etkin sınır BU dosyadadır.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from ..config import settings
from ..kiraci_geri_yukleme import KIPLER, geri_yukle
from ..platform_access import require_platform_operator
from ..platform_denetim import platform_olayi_yaz

router = APIRouter(prefix="/platform/tenant-restore", tags=["Kiracı Geri Yükleme"])

_PARCA = 1024 * 1024


def _gecici_dosyaya_yaz(dosya: UploadFile) -> Path:
    tavan = int(settings.max_tenant_restore_upload_bytes)
    alinan = 0
    gecici = tempfile.NamedTemporaryFile(prefix="kiraci-restore-", suffix=".zip", delete=False)
    yol = Path(gecici.name)
    try:
        with gecici:
            while True:
                parca = dosya.file.read(_PARCA)
                if not parca:
                    break
                alinan += len(parca)
                if alinan > tavan:
                    raise HTTPException(
                        413, f"Zip boyutu izin verilen sınırı aşıyor ({tavan} bayt)"
                    )
                gecici.write(parca)
        if alinan == 0:
            raise HTTPException(422, "Boş dosya yüklenemez")
    except BaseException:
        yol.unlink(missing_ok=True)
        raise
    return yol


def _gunlukle(request: Request, rapor: dict) -> None:
    """Platform denetim satırı: FİRMASIZ, işlem bittikten sonra (PP1).

    PP1 öncesi satır ``activity_logs``a operatörün ``request.state.company_id``
    ile çözülen KENDİ firmasına yazılıyordu. ``/api/platform/`` artık kiracı
    çözümünden muaf (``platform_access.platform_yolu``) ve o değer ``None``dır;
    eski çağrı ``int(None)`` ile geri yükleme BİTTİKTEN SONRA 500 verirdi.
    Olay ``security_audit_logs``a firmasız yazılır; kaynak/hedef firma ve satır
    toplamı özet metninde durur, raporun tamamı yanıttadır.
    """
    platform_olayi_yaz(
        request,
        "company.restored",
        (
            f"Kiracı geri yüklendi: kaynak {rapor['source_company_id']} -> "
            f"firma {rapor['company_id']} (kip {rapor['mode']}, "
            f"{rapor['row_total']} satır, {rapor['table_count']} tablo, "
            f"şema {rapor['schema_revision']})"
        ),
    )


@router.post("")
def kiraciyi_geri_yukle(
    request: Request,
    file: UploadFile = File(...),
    mode: str = Form("yeni"),
    dry_run: bool = Form(False),
) -> dict:
    """5.1a zip'ini yeni bir firma olarak (ya da kapalı kimliğin yerine) yükler.

    ``dry_run=true`` doğrulama + haritalama planını sonuna kadar yürütür,
    işlemi geri alır ve raporu döndürür — hiçbir satır, hiçbir dosya kalmaz.
    Kuru koşu denetim satırı YAZMAZ: kataloğun "geri yüklendi" etiketi
    yazılmamış bir firma için yalan olurdu.
    """
    require_platform_operator(request)
    kip = (mode or "").strip().lower()
    if kip not in KIPLER:
        raise HTTPException(422, "mode 'yeni' ya da 'yerine' olmalı")
    kullanici = getattr(request.state, "user", {}) or {}
    yol = _gecici_dosyaya_yaz(file)
    try:
        rapor = geri_yukle(
            yol,
            kip=kip,
            kuru_kosu=bool(dry_run),
            operator_user_id=int(kullanici["id"]) if kullanici.get("id") is not None else None,
        )
    finally:
        yol.unlink(missing_ok=True)
    if not dry_run:
        _gunlukle(request, rapor)
    return rapor
