"""PostgreSQL ikizi: 0061'in GERİ ALMA yolu — UPDATE kolu TAŞIYICIDIR.

--- BU İKİZİN SOMUT SEBEBİ ---------------------------------------------------

SQLite `VARCHAR` uzunluğunu YOK SAYAR. Yani SQLite kulvarında
`ALTER ... VARCHAR(64) -> VARCHAR(20)` daraltması, sütunda 26 karakterlik bir
değer DURURKEN bile sorunsuz koşar. Orada alınan yeşil, geri almanın
doğruluğunu değil ORTAMIN kayıtsızlığını ölçer.

Bu dosyanın ölçtüğü şey gerçek PG'de ŞUDUR: `downgrade()` içindeki
`UPDATE ... WHERE length(status) > 20` satırı bir SÜS DEĞİL, daraltmanın ÖN
KOŞULUDUR. O satır olmadan daraltmanın kendisi
`psycopg.errors.StringDataRightTruncation` verir ve geri alma yarıda kalır.

Bu, 0061'in yukarı yönünün var olma sebebinin AYNADAKİ görüntüsüdür: göç,
26 karakterlik `SKIPPED_SOURCE_NOT_VISIBLE` 20'ye sığmadığı için yazıldı;
geri alma da tam olarak o değerle karşılaşacağı için veri koluna muhtaçtır.

--- BURADA ÖLÇÜLEN İKİ ŞEY ---------------------------------------------------

1. **DÜZ DARALTMA PATLAR.** Sığmayan bir satır dururken çıplak
   `ALTER COLUMN ... TYPE VARCHAR(20)` reddedilir. Bu, dosyanın KENDİ NEGATİF
   KONTROLÜDÜR: veri kolunun taşıyıcı olduğu iddiası, o kol olmadan ne
   olduğunu ÖLÇEREK kanıtlanır — mutasyon beklemeden.
2. **GERÇEK GERİ ALMA GEÇER.** Aynı satır dururken `command.downgrade` başarılı
   olur, sütun gerçekten `VARCHAR(20)` olur ve satır `'DEAD'` olarak okunur.

Kulvar dışı (SQLite) tarafın gidiş-dönüşü ve SINIRIN SIĞAN TARAFI
`tests/test_field_stok_0061_gidis_donus.py` içinde ölçülür.

--- BU DOSYA VERİTABANINI NASIL BIRAKIR --------------------------------------

`command.downgrade` PAYLAŞILAN test veritabanını değiştirir (aynı desen:
`test_platform_backups_postgresql.py::test_0034_retry_...`). Her iddia kendi
`finally` kolunda zinciri `head`e geri çıkarır ve tohum satırlarını siler,
yani dosya veritabanını BULDUĞU seviyede bırakır.
"""
from __future__ import annotations

import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DatabaseError

from app.bootstrap_data import seed_bootstrap_data
from app.runtime_migrations import run_database_migrations

TABLO = "field_integration_events"
SUTUN = "status"

#: Tohum satırın id'si; önyükleme verisiyle çarpışmayacak kadar yüksek.
TOHUM_ID = 960100

#: Çıplak daraltma — `downgrade()`in veri kolu OLMADAN yapacağı iş.
#: SABİT metin; çalışma zamanında SQL kurulmuyor.
_CIPLAK_DARALTMA = (
    "ALTER TABLE field_integration_events "
    "ALTER COLUMN status TYPE VARCHAR(20)"
)


def _url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("PostgreSQL ikizi yalnız PostgreSQL kulvarında çalışır")
    return url


def _motor():
    motor = create_engine(_url())
    run_database_migrations(motor)
    seed_bootstrap_data(motor)
    return motor


def _genislik(motor) -> int | None:
    for sutun in inspect(motor).get_columns(TABLO):
        if sutun["name"] == SUTUN:
            return getattr(sutun["type"], "length", None)
    raise AssertionError(f"{TABLO}.{SUTUN} sütunu yok")


def _tasan_durum() -> str:
    """Bildirilen durumlar arasından ESKİ genişliği AŞAN en kısası."""
    from app import field_stok_tuketici as tuketici

    tasan = sorted(
        (d for ad, d in vars(tuketici).items()
         if ad.startswith("DURUM_") and isinstance(d, str) and len(d) > 20),
        key=len,
    )
    assert tasan, (
        "Bildirilen hiçbir durum 20 karakteri aşmıyor; bu ikizin ölçtüğü "
        "daraltma çatışması ORTADAN KALKMIŞ olur ve dosya boşa döner."
    )
    return tasan[0]


def _tohumla(motor, durum: str) -> None:
    with motor.begin() as baglanti:
        baglanti.execute(
            text(
                """DELETE FROM field_integration_events
                WHERE company_id = 1 AND id = :id"""
            ),
            {"id": TOHUM_ID},
        )
        baglanti.execute(
            text(
                """INSERT INTO field_integration_events(
                company_id,id,source_type,source_id,target,idempotency_key,
                status,attempts,created_at,updated_at)
                VALUES(1,:id,'field_activity',:id,'stock',:anahtar,:durum,0,
                       :z,:z)"""
            ),
            {"id": TOHUM_ID, "anahtar": "pg-0061:%d" % TOHUM_ID,
             "durum": durum, "z": "2026-08-24T00:00:00"},
        )


def _temizle(motor) -> None:
    with motor.begin() as baglanti:
        baglanti.execute(
            text(
                """DELETE FROM field_integration_events
                WHERE company_id = 1 AND id = :id"""
            ),
            {"id": TOHUM_ID},
        )


def test_CIPLAK_daraltma_SIGMAYAN_satir_varken_REDDEDILIYOR() -> None:
    """Veri kolu TAŞIYICI: onsuz daraltmanın kendisi PG'de patlar."""
    motor = _motor()
    durum = _tasan_durum()
    try:
        _tohumla(motor, durum)
        assert _genislik(motor) is not None and _genislik(motor) > 20, (
            "Sütun zaten dar; bu ikiz daraltma çatışmasını ölçemez"
        )
        # İŞLEM HER HÂLDE GERİ ALINIR: daraltma beklenmedik biçimde BAŞARIRSA
        # commit edilmiş bir şema değişikliği dosyanın geri kalanını bozardı.
        with motor.connect() as baglanti:
            islem = baglanti.begin()
            try:
                with pytest.raises(DatabaseError) as hata:
                    baglanti.execute(text(_CIPLAK_DARALTMA))
            finally:
                islem.rollback()
        assert "StringDataRightTruncation" in str(type(hata.value.orig)) or (
            "too long" in str(hata.value).lower()
        ), (
            "Daraltma reddedildi ama beklenen SINIF değil; bu ikizin ölçtüğü "
            f"şey taşma OLMAYABİLİR: {type(hata.value.orig)!r} {hata.value!s}"
        )
        assert _genislik(motor) > 20, "Geri alınmış olması gereken daraltma KALICI olmuş"
    finally:
        _temizle(motor)
        motor.dispose()


def test_GERI_ALMA_ayni_satir_dururken_GECIYOR_ve_DEAD_yaziyor() -> None:
    """Gerçek `downgrade()`: sütun 20'ye iner, sığmayan satır `DEAD` olur."""
    motor = _motor()
    durum = _tasan_durum()
    from app.field_stok_tuketici import DURUM_OLU

    try:
        _tohumla(motor, durum)
        yapilandirma = Config("alembic.ini")
        command.downgrade(yapilandirma, "20260821_0060")

        assert _genislik(motor) == 20, (
            f"Geri alma sonrası sütun 20 olmalıydı: {_genislik(motor)!r}"
        )
        with motor.connect() as baglanti:
            sonra = baglanti.execute(
                text(
                    """SELECT status FROM field_integration_events
                    WHERE company_id = 1 AND id = :id"""
                ),
                {"id": TOHUM_ID},
            ).scalar_one()
        assert sonra == DURUM_OLU, (
            f"Sığmayan satır {DURUM_OLU!r} olmalıydı, {sonra!r} okundu"
        )
    finally:
        command.upgrade(Config("alembic.ini"), "head")
        _temizle(motor)
        motor.dispose()
