"""PUSH CİHAZ DEFTERİ: bir kullanıcıya hangi cihazdan ulaşılabileceği (5.4c).

Şema `alembic/versions/20260909_0077_push_devices.py`de; uçlar
`app/routers/push.py`de; gönderim kanalı `app/notifications/`dedir. Bu modül
HİÇBİR uç tanımlamaz ve HTTP'yi BİLMEZ — bildiği tek şey defterin kendisidir.

--- SQL'İN TAMAMI SABİT METİN VE KİRACI YÜKLEMLİ -------------------------

Aşağıdaki altı metnin altısı da MODÜL SABİTİDİR (f-string DEĞİL) ve KÖK
YÜKLEMİ `company_id=:cid` taşır. İkisi de zorunlu: kiracı nöbetçisi
(`tests/test_tenant_scoping_guard.py`) sabit metni AST'de GÖREBİLİR ve
`push_devices` `TENANT_TABLES`ta olduğu için yüklemsiz bir sorgu KIRMIZI
olur. Değerlerin HİÇBİRİ metne girmiyor; hepsi bağlı parametredir.

--- UPSERT NEDEN `ON CONFLICT` DEĞİL — ÖLÇÜLDÜ, VARSAYILMADI -------------

`ON CONFLICT` SQLite ve PostgreSQL'de AYNI yazılabilir ama davranışı aynı
DEĞİL: SQLite'ta hedef kısıtı ADIYLA veremezsiniz (sütun listesi gerekir) ve
`RETURNING` desteği sürüme bağlıdır. Bu modül bunun yerine İKİ ADIM
kullanıyor ve sıra ÖNEMLİ:

  1. ÖNCE `UPDATE` (rowcount).
  2. Hiç satır dokunmadıysa `INSERT`.
  3. `INSERT` `IntegrityError` verirse (araya başka bir yazan girdi) YENİDEN
     `UPDATE`.

Adım 3 olmasaydı iki eşzamanlı kayıt isteğinden biri 500 alırdı; ve tekili
düşürmek — kapıların `mutasyon: upsert kopya üretir` adımı — İKİ satır
üretirdi, yani aynı cihaza aynı bildirim İKİ KEZ giderdi.

--- `logout-all` NEDEN FİRMA FİRMA DOLAŞIYOR ----------------------------

`revoke_user_refresh_tokens` bir kullanıcının TÜM oturumlarını düşürür ve
`app_users` KİRACI TABLOSU DEĞİLDİR (`company_id` sütunu YOKTUR), yani o
süpürgenin bir firma yüklemi yoktur. `push_devices` ise KİRACI TABLOSUDUR:
tek bir `WHERE user_id=:uid` yazımı, kiracı yüklemi TAŞIMAYAN bir yazma
olurdu ve nöbetçi onu KIRMIZI yapar — haklı olarak, çünkü o desen bir gün
başka bir firmanın satırına da dokunabilir.

Çare ölçüldü ve DAR: `user_companies` kullanıcının ÜYE OLDUĞU firmaları
verir ve süpürge O LİSTE ÜZERİNDE, firma başına BİR yazımla dönüyor.
Semantik AYNI (kullanıcının her cihazı düşer), kiracı yüklemi ise her
yazımda AÇIK duruyor.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

#: Kapalı küme — göçün `ck_push_devices_platform` CHECK'i ile AYNI üç değer.
#: Burada TEKRAR yazılmasının sebebi: uç, veritabanına gitmeden ÖNCE 422
#: verebilmeli; CHECK'e bırakmak istemciye 500 döndürürdü.
PLATFORMLAR: frozenset[str] = frozenset({"android", "ios", "web"})

#: Göçün `token` sütunu ile AYNI genişlik. Uzun jeton SESSİZCE KIRPILMAZ —
#: kırpılsaydı iki farklı cihaz aynı satıra düşerdi.
AZAMI_JETON = 512


def _simdi() -> datetime:
    return datetime.now(timezone.utc)


#: Zaman parametreleri TİPLİ bağlanıyor (0076'nın aynı gerekçesi): tipsiz
#: bırakılsaydı iki diyalekt `datetime`i FARKLI biçimde yazardı.
_GUNCELLE = text(
    "UPDATE push_devices "
    "SET user_id=:uid, platform=:platform, session_family_id=:aile, "
    "last_seen_at=:now, is_active=:etkin "
    "WHERE company_id=:cid AND token=:token"
).bindparams(sa.bindparam("now", type_=sa.DateTime(timezone=True)))

_EKLE = text(
    "INSERT INTO push_devices("
    "company_id,user_id,platform,token,session_family_id,"
    "last_seen_at,is_active,created_at"
    ") VALUES(:cid,:uid,:platform,:token,:aile,:now,:etkin,:now)"
).bindparams(sa.bindparam("now", type_=sa.DateTime(timezone=True)))

_JETONDAN_OKU = text(
    "SELECT id,user_id,platform,token,session_family_id,is_active,"
    "last_seen_at,created_at "
    "FROM push_devices WHERE company_id=:cid AND token=:token"
)

_KIMLIKTEN_OKU = text(
    "SELECT id,user_id,platform,token,session_family_id,is_active,"
    "last_seen_at,created_at "
    "FROM push_devices WHERE company_id=:cid AND id=:id"
)

_KULLANICININ_CIHAZLARI = text(
    "SELECT id,user_id,platform,token,session_family_id,is_active,"
    "last_seen_at,created_at "
    "FROM push_devices WHERE company_id=:cid AND user_id=:uid "
    "ORDER BY id"
)

#: GÖNDERİM HEDEFİ. `is_active` süzgeci BURADA ve yalnız burada: pasif cihaz
#: kuyruğa HİÇ GİRMEZ — girseydi outbox satırı doğar, sağlayıcı onu
#: gönderemez ve satır yeniden denemeye takılırdı.
_ETKIN_HEDEFLER = text(
    "SELECT id,platform,token FROM push_devices "
    "WHERE company_id=:cid AND user_id=:uid AND is_active=:etkin "
    "ORDER BY id"
)

_PASIFLESTIR = text(
    "UPDATE push_devices SET is_active=:etkin "
    "WHERE company_id=:cid AND id=:id"
)

#: `logout-all` süpürgesi — FİRMA BAŞINA bir yazım (gerekçe başlıkta).
_KULLANICIYI_PASIFLESTIR = text(
    "UPDATE push_devices SET is_active=:etkin "
    "WHERE company_id=:cid AND user_id=:uid"
)


def _satir(ham: Any) -> dict[str, Any]:
    kayit = dict(ham)
    # SQLite `is_active`i 1/0 olarak döndürür, PostgreSQL `True`/`False`.
    # Sözleşme İKİ DİYALEKTTE DE bool olmalı; aksi hâlde JSON cevabı bir
    # veritabanında `1`, ötekinde `true` olurdu.
    kayit["is_active"] = bool(kayit["is_active"])
    return kayit


def kaydet(
    db: Session,
    *,
    company_id: int,
    user_id: int,
    platform: str,
    token: str,
    session_family_id: str | None = None,
) -> dict[str, Any]:
    """Jetonu kaydeder ya da VAR OLAN satırı günceller (upsert).

    İkinci kayıt YENİ SATIR ÜRETMEZ: `uq_push_devices_company_token` tekildir
    ve bu fonksiyon önce `UPDATE` dener. Yeniden kayıt satırı CANLANDIRIR
    (`is_active` yeniden true) — bir kullanıcı çıkış yaptıktan sonra tekrar
    girdiğinde cihazı sessizce sağır kalmamalı.

    `db` çağıranındır ve BU FONKSİYON COMMIT ETMEZ: çağıran uç, kaydı kendi
    işlem sınırında kapatır.
    """
    simdi = _simdi()
    parametre = {
        "cid": company_id,
        "uid": user_id,
        "platform": platform,
        "token": token,
        "aile": session_family_id,
        "now": simdi,
        "etkin": True,
    }
    dokunulan = db.execute(_GUNCELLE, parametre).rowcount
    if not dokunulan:
        try:
            with db.begin_nested():
                db.execute(_EKLE, parametre)
        except IntegrityError:
            # ARAYA BAŞKA BİR YAZAN GİRDİ. Tekil kısıt onu reddetti; satır
            # ARTIK VAR, yani doğru hamle GÜNCELLEMEKTİR. Hata yukarı
            # taşınsaydı eşzamanlı iki kayıt isteğinden biri 500 alırdı.
            db.execute(_GUNCELLE, parametre)
    ham = db.execute(
        _JETONDAN_OKU, {"cid": company_id, "token": token}
    ).mappings().first()
    assert ham is not None, "upsert sonrası satır okunamadı"
    return _satir(ham)


def listele(db: Session, *, company_id: int, user_id: int) -> list[dict[str, Any]]:
    """Çağıranın KENDİ cihazları. Başka kullanıcının defteri okunamaz."""
    return [
        _satir(s)
        for s in db.execute(
            _KULLANICININ_CIHAZLARI, {"cid": company_id, "uid": user_id}
        ).mappings()
    ]


def oku(db: Session, *, company_id: int, device_id: int) -> dict[str, Any] | None:
    """Tek satır — sahiplik denetimi için. Firma dışı kimlik None döner."""
    ham = db.execute(
        _KIMLIKTEN_OKU, {"cid": company_id, "id": device_id}
    ).mappings().first()
    return None if ham is None else _satir(ham)


def etkin_hedefler(
    db: Session, *, company_id: int, user_id: int
) -> list[dict[str, Any]]:
    """Kuyruğa girecek cihazlar — YALNIZ etkin olanlar (gerekçe SQL'in üstünde)."""
    return [
        dict(s)
        for s in db.execute(
            _ETKIN_HEDEFLER, {"cid": company_id, "uid": user_id, "etkin": True}
        ).mappings()
    ]


def pasiflestir(db: Session, *, company_id: int, device_id: int) -> int:
    """Cihazı düşürür. SATIR SİLİNMEZ ve bu bilinçli.

    Silseydik aynı jeton yeniden kaydedildiğinde YENİ bir kimlik alırdı ve
    "bu cihaz ne zaman ilk görüldü" (`created_at`) bilgisi kaybolurdu.
    Pasifleştirme, jetonun geçmişini koruyarak gönderim hedefliğini bitirir.
    """
    return db.execute(
        _PASIFLESTIR, {"cid": company_id, "id": device_id, "etkin": False}
    ).rowcount


def kullanicinin_cihazlarini_dusur(
    db: Session, *, company_ids: list[int], user_id: int
) -> int:
    """`logout-all` süpürgesi: her firmada AYRI yazım (gerekçe başlıkta).

    Dönen sayı DOKUNULAN SATIRDIR, düşürülen cihaz sayısı değil: zaten pasif
    olan bir satır da yeniden yazılır. Ayrım kapıda ölçülüyor.
    """
    toplam = 0
    for company_id in company_ids:
        toplam += db.execute(
            _KULLANICIYI_PASIFLESTIR,
            {"cid": company_id, "uid": user_id, "etkin": False},
        ).rowcount
    return toplam
