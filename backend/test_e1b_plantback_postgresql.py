"""PostgreSQL ikizi: E1b ŞEMASININ gerçek kısıtlarla eşi.

Göç `20260907_0072`. SQLite ikizi `tests/test_e1b_plantback.py` kilidin
davranışını (üç politika, en uzun kazanır, sınır günü, köken) ölçüyor; bu
dosya yalnız ŞEMANIN GERÇEKTEN ISIRAN kısımlarını ve GELİŞTİRME DİYALEKTİNDE
GÖRÜNMEYEN tuzakları ölçer.

--- BU İKİZ NEDEN VAR ------------------------------------------------------

1. **BİLEŞİK YABANCI ANAHTARIN GERÇEKTEN ISIRMASI.** SQLite'ta yabancı anahtar
   uygulaması varsayılan olarak KAPALIDIR, yani `fk_ppb_product_same_company`
   orada YEŞİL kalırdı: bir kiracının plant-back satırı BAŞKA kiracının
   ürününü işaret edebilirdi ve o satır, komşunun tarlasında ekim keserdi.

2. **`UNIQUE(company_id, product_id, crop, next_crop)`.** Aynı üçlü için İKİ
   satır, çözümü belirsiz bırakır (hangi süre?). Uygulama kontrolü İKİ
   EŞZAMANLI isteği ayırt EDEMEZ; ayıran yalnız şemadır.

3. **GÖÇÜN TASARIM GEREKÇESİNİN KENDİSİ.** 0072 "sütun değil ayrı tablo"
   tercihini `plant_protection_products`ın `UNIQUE(company_id, product_id,
   crop)`una dayandırıyor. O kısıt GERÇEKTEN ısırmasaydı gerekçe çürük olurdu;
   burada ısırdığı ÖLÇÜLÜYOR — yani ayrı tablo bir zevk değil, ZORUNLULUK.

4. **`interval_days` ARALIĞI ve `farm_plantback_policy` SEVİYE KÜMESİ.**
   İkisi de CHECK; şemaya yazılmamış bir seviye kümesi, uçtaki `Literal`
   atlandığı anda sessizce gevşer.

5. **`:haric IS NULL` PARAMETRE TİPİ.** `_PLANTBACK_SORGU`, `POST
   /api/crop-seasons` yolunda `haric=None` ile çağrılıyor. PostgreSQL tipsiz
   bir parametreyi bu karşılaştırmada ÇÖZEMEZ (`42P08 AmbiguousParameter`) ve
   uç 500 döner; SQLite NULL'u yutar. `_GIRIS_SORGU`nun `pid`inde ölçülmüş
   AYNI tuzak — kilidin İLK çağrısı bu yoldan geçtiği için kusur üretimde
   HER sezon yazımını düşürürdü.

--- ÖLÇÜLEN KÖK SEBEP, İDDİA DEĞİL -----------------------------------------

Testler kısıtın VARLIĞINI değil GERÇEKTEN REDDETTİĞİNİ ölçüyor: her biri
kısıtı ihlal eden bir yazma deneyip `IntegrityError` bekliyor.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

BACKEND = Path(__file__).resolve().parent

FIRMA_ADI = "E1b İKİZİ firması"
KOMSU_ADI = "E1b İKİZİ komşu firması"


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("E1b ikizi APP_TEST_DATABASE_URL ister")
    return url


def _temizle(engine) -> None:
    with engine.begin() as baglanti:
        for deyim in (
            "DELETE FROM plant_protection_plantbacks WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM plant_protection_products WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM field_activity_inputs WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM field_activities WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM crop_seasons WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM farm_parcels WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM farms WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM products WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM companies WHERE name IN (:a, :b)",
        ):
            baglanti.execute(text(deyim), {"a": FIRMA_ADI, "b": KOMSU_ADI})


@pytest.fixture()
def motor():
    config = Config(str(BACKEND / "alembic.ini"))
    engine = create_engine(_url())
    command.upgrade(config, "head")
    _temizle(engine)
    try:
        yield engine
    finally:
        _temizle(engine)
        engine.dispose()


def _firma_kur(baglanti, firma_adi: str) -> tuple[int, int]:
    """Bir firma ve ona ait BİR ürün kurar; (company_id, product_id)."""
    simdi = datetime.now(timezone.utc)
    cid = baglanti.execute(
        text(
            "INSERT INTO companies (name, is_active, created_at) "
            "VALUES (:ad, true, :simdi) RETURNING id"
        ),
        {"ad": firma_adi, "simdi": simdi},
    ).scalar_one()
    urun = baglanti.execute(
        text(
            "INSERT INTO products (company_id, name, unit, sale_price, active) "
            "VALUES (:cid, 'İkiz Herbisit', 'LT', 10, true) RETURNING id"
        ),
        {"cid": cid},
    ).scalar_one()
    return cid, urun


def _plantback_yaz(baglanti, cid: int, pid: int, **ustune) -> int:
    simdi = datetime.now(timezone.utc)
    degerler = {
        "cid": cid, "pid": pid, "crop": "Buğday", "next_crop": "Ayçiçeği",
        "gun": 365, "simdi": simdi,
    }
    degerler.update(ustune)
    return baglanti.execute(
        text(
            "INSERT INTO plant_protection_plantbacks (company_id, product_id, "
            "crop, next_crop, interval_days, status, created_at, updated_at) "
            "VALUES (:cid, :pid, :crop, :next_crop, :gun, 'ACTIVE', :simdi, "
            ":simdi) RETURNING id"
        ),
        degerler,
    ).scalar_one()


# ---------------------------------------------------- şema GERÇEKTEN ısırır ---

def test_capraz_kiraci_urun_referansi_REDDEDILIYOR(motor) -> None:
    """Bileşik yabancı anahtar: A'nın kuralı B'nin ürününü GÖSTEREMEZ.

    SQLite'ta `PRAGMA foreign_keys` KAPALI doğar; bu satır orada SESSİZCE
    yazılır ve kural komşunun ürünü üzerinden A'nın tarlasında ekim keserdi.
    """
    with motor.begin() as baglanti:
        cid, _ = _firma_kur(baglanti, FIRMA_ADI)
        _, komsu_urun = _firma_kur(baglanti, KOMSU_ADI)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _plantback_yaz(baglanti, cid, komsu_urun)


def test_ayni_uclu_IKI_KEZ_yazilamiyor(motor) -> None:
    """`UNIQUE(company_id, product_id, crop, next_crop)` GERÇEKTEN reddediyor."""
    with motor.begin() as baglanti:
        cid, pid = _firma_kur(baglanti, FIRMA_ADI)
        _plantback_yaz(baglanti, cid, pid)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _plantback_yaz(baglanti, cid, pid, gun=120)


def test_ARDIL_BITKI_FARKLIYSA_ikinci_satir_GECIYOR(motor) -> None:
    """Göçün VAR OLMA SEBEBİ: aynı ürün+bitki için ARDIL BAŞINA ayrı satır."""
    with motor.begin() as baglanti:
        cid, pid = _firma_kur(baglanti, FIRMA_ADI)
        _plantback_yaz(baglanti, cid, pid, next_crop="Ayçiçeği", gun=365)
        _plantback_yaz(baglanti, cid, pid, next_crop="Mercimek", gun=120)
        _plantback_yaz(baglanti, cid, pid, next_crop="", gun=60)
        sayi = baglanti.execute(
            text(
                "SELECT COUNT(*) FROM plant_protection_plantbacks "
                "WHERE company_id=:cid AND product_id=:pid"
            ),
            {"cid": cid, "pid": pid},
        ).scalar_one()
    assert sayi == 3


def test_KATALOG_TABLOSU_ayni_seyi_YAPAMIYOR_gocun_gerekcesi(motor) -> None:
    """0072'nin "sütun değil tablo" gerekçesi BURADA ÖLÇÜLÜYOR.

    `plant_protection_products`ın tekilliği `(company_id, product_id, crop)`;
    plant-back sütun olarak oraya konsaydı aynı ilaç+bitki için İKİNCİ bir
    ardıl bitki satırı YAZILAMAZDI. Bu kapı yeşil kaldığı sürece ayrı tablo
    bir tercih değil ZORUNLULUKTUR.
    """
    simdi = datetime.now(timezone.utc)
    with motor.begin() as baglanti:
        cid, pid = _firma_kur(baglanti, FIRMA_ADI)
        baglanti.execute(
            text(
                "INSERT INTO plant_protection_products (company_id, product_id, "
                "crop, preharvest_interval_days, status, origin, created_at, "
                "updated_at) VALUES (:cid, :pid, 'Buğday', 21, 'ACTIVE', "
                "'MANUAL', :simdi, :simdi)"
            ),
            {"cid": cid, "pid": pid, "simdi": simdi},
        )

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            baglanti.execute(
                text(
                    "INSERT INTO plant_protection_products (company_id, "
                    "product_id, crop, preharvest_interval_days, status, "
                    "origin, created_at, updated_at) VALUES (:cid, :pid, "
                    "'Buğday', 21, 'ACTIVE', 'MANUAL', :simdi, :simdi)"
                ),
                {"cid": cid, "pid": pid, "simdi": simdi},
            )


@pytest.mark.parametrize("gun", [-1, 3651])
def test_interval_days_ARALIGI_isiriyor(motor, gun: int) -> None:
    with motor.begin() as baglanti:
        cid, pid = _firma_kur(baglanti, FIRMA_ADI)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _plantback_yaz(baglanti, cid, pid, gun=gun)


def test_firma_politikasi_KAPALI_KUMEDE(motor) -> None:
    """`farm_plantback_policy` CHECK'i "allow"u ŞEMA seviyesinde reddediyor.

    Uçtaki `Literal` tek başına savunma DEĞİLDİR: bir betik ya da elle
    yazılmış bir `UPDATE` onu atlar. 0048/0064'ün sütunlarında bu boşluk
    ÖLÇÜLDÜ (orada CHECK yok); yeni sütun onunla doğmuyor.
    """
    with motor.begin() as baglanti:
        cid, _ = _firma_kur(baglanti, FIRMA_ADI)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            baglanti.execute(
                text(
                    "UPDATE companies SET farm_plantback_policy='allow' "
                    "WHERE id=:cid"
                ),
                {"cid": cid},
            )

    with motor.begin() as baglanti:
        for seviye in ("block", "require_reason", "warn"):
            baglanti.execute(
                text(
                    "UPDATE companies SET farm_plantback_policy=:s WHERE id=:cid"
                ),
                {"s": seviye, "cid": cid},
            )


def test_varsayilan_politika_require_reason(motor) -> None:
    with motor.begin() as baglanti:
        cid, _ = _firma_kur(baglanti, FIRMA_ADI)
        seviye = baglanti.execute(
            text("SELECT farm_plantback_policy FROM companies WHERE id=:cid"),
            {"cid": cid},
        ).scalar_one()
    assert seviye == "require_reason"


# ------------------------------------------- SQLite'ın GÖREMEDİĞİ tip tuzağı ---

def test_haric_parametresi_TIPLI_yoksa_sorgu_COZULEMEZ(motor) -> None:
    """`:haric IS NULL` tipsiz parametreyle PostgreSQL'de ÇÖZÜLEMEZ.

    `POST /api/crop-seasons` kilidi sorguyu HER ZAMAN `haric=None` ile
    çağırıyor; tip düşerse o uç 500 döner ve SQLite süiti YEŞİL KALIR.
    Ölçüm iki yönlü: gerçek sabit GEÇİYOR, tipi düşürülmüş kopyası
    `AmbiguousParameter` alıyor.
    """
    import sys

    sys.path.insert(0, str(BACKEND))
    from app.routers.farm import _PLANTBACK_SORGU

    with motor.begin() as baglanti:
        cid, _ = _firma_kur(baglanti, FIRMA_ADI)
        # GERÇEK SABİT: geçiyor.
        baglanti.execute(
            _PLANTBACK_SORGU, {"cid": cid, "pid": 1, "haric": None}
        ).mappings().all()

    tipsiz = text(str(_PLANTBACK_SORGU))
    with pytest.raises((ProgrammingError, IntegrityError)):
        with motor.begin() as baglanti:
            baglanti.execute(
                tipsiz, {"cid": cid, "pid": 1, "haric": None}
            ).mappings().all()


# ----------------------------------------------------------- uçtan uca ------

ADMIN_PW = "E1bPG!123"


@pytest.fixture()
def acilis_sifresi():
    """Admin şifresini AÇILIŞ DURUMUNA çeker; testten sonra GERİ KOYAR.

    Kalıp `test_d2_avans_tescil_postgresql.py`den DEVRALINDI ve gerekçesi
    orada ölçülmüştür: PostgreSQL ikizleri AYNI veritabanını paylaşıyor ve
    her biri girişten sonra admin şifresini KENDİ sabitine çeviriyor; iki
    ikiz aynı shard'a düştüğünde giriş SIRAYA BAĞLI olarak kırılıyor.

    Fikstür İKİ UÇTAN da çalışıyor: testten ÖNCE açılış durumunu
    (`admin123` + `must_change_password`) yazıyor, sonra GERİ koyuyor —
    tek yönlü bir çare dosyayı iyi bir komşu yapar ama KENDİSİNİ korumaz.
    Şifre uçtan değil SATIRDAN yazılıyor (`change-password` mevcut şifreyi
    ister ve `must_change_password` uçtan yazılamaz); tablo `app_users`tır.
    """
    def _acilisa_cek() -> None:
        from app.auth import hash_password
        from app.db import SessionLocal

        with SessionLocal() as db:
            varmi = db.execute(
                text("SELECT to_regclass('public.app_users')")
            ).scalar()
            if varmi is None:
                return
            db.execute(
                text(
                    "UPDATE app_users SET password_hash=:h, "
                    "must_change_password=true WHERE username='admin'"
                ),
                {"h": hash_password("admin123")},
            )
            db.commit()

    _acilisa_cek()
    yield
    _acilisa_cek()


def _admin_headers(client):
    for candidate in ("admin123", ADMIN_PW):
        login = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": candidate},
        )
        if login.status_code == 200:
            break
    assert login.status_code == 200, login.text
    body = login.json()
    headers = {
        "Authorization": "Bearer " + body["access_token"],
        "X-Company-ID": str(body["companies"][0]["id"]),
    }
    if candidate != ADMIN_PW:
        changed = client.post(
            "/api/auth/change-password",
            headers=headers,
            json={"current_password": candidate, "new_password": ADMIN_PW},
        )
        assert changed.status_code == 200, changed.text
        headers["Authorization"] = "Bearer " + changed.json()["access_token"]
    return headers, int(body["companies"][0]["id"])


def test_kilit_GERCEK_PostgreSQLde_isiriyor(motor, acilis_sifresi) -> None:
    """Uçtan uca: kural yaz -> ilaçla -> ek -> 422 -> gerekçeyle 201.

    Şema kapıları kısıtları ölçüyor; bu test kilidin ÜRETİM DİYALEKTİNDE
    çalıştığını ölçüyor — `:haric` tipi, tarih aritmetiği (`timestamptz` ->
    İstanbul günü) ve köken sütunlarının yazımı burada BİRLİKTE görünüyor.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        h, _ = _admin_headers(client)
        urun = client.post("/api/products", headers=h, json={
            "name": "PG İkiz Herbisit", "unit": "LT", "sale_price": "10.00"}).json()
        pid = urun["id"]
        assert client.post("/api/plant-protection-products", headers=h, json={
            "product_id": pid, "crop": "", "preharvest_interval_days": 21,
            "reentry_interval_days": 4}).status_code == 201
        assert client.post("/api/plant-protection-plantbacks", headers=h, json={
            "product_id": pid, "crop": "Buğday", "next_crop": "Ayçiçeği",
            "interval_days": 365}).status_code == 201

        ciftlik = client.post("/api/farms", headers=h, json={
            "code": "pge1b", "name": "PG İkiz Çiftlik"}).json()
        parsel = client.post("/api/farm-parcels", headers=h, json={
            "farm_id": ciftlik["id"], "code": "pge1bp", "name": "PG Parsel",
            "area_decare": "20.0000"}).json()
        onceki = client.post("/api/crop-seasons", headers=h, json={
            "parcel_id": parsel["id"], "season_year": 2025, "crop": "Buğday",
            "started_on": "2025-01-01"}).json()

        # AKŞAM 22:00 YEREL: UTC günü ERTESİ gündür. Süre İstanbul gününden
        # sayılmazsa en erken ekim BİR GÜN erken çıkar.
        ilac = client.post("/api/field-activities", headers=h, json={
            "season_id": onceki["id"], "activity_type": "SPRAYING",
            "performed_at": "2025-06-01T22:00:00+03:00",
            "applied_area_decare": "20.0000",
            "inputs": [{"product_id": pid, "input_name": "PG İkiz Herbisit",
                        "quantity": "5", "unit": "LT", "dose": "1",
                        "dose_unit": "LT/da"}]})
        assert ilac.status_code == 201, ilac.text
        # KÖKEN ÇİFTİ ÜRETİM DİYALEKTİNDE DE YAZILIYOR.
        assert ilac.json()["reentry_source"] == "CATALOGUE", ilac.text
        assert ilac.json()["catalogue_reentry_days"] == 4, ilac.text

        ihlal = client.post("/api/crop-seasons", headers=h, json={
            "parcel_id": parsel["id"], "season_year": 2026, "crop": "Ayçiçeği",
            "started_on": "2026-05-01"})
        assert ihlal.status_code == 422, ihlal.text
        detay = ihlal.json()["detail"]
        assert detay["sebep"] == "PLANTBACK_SURESI_DOLMADI", detay
        assert detay["blocking"][0]["earliest_allowed"] == "2026-06-01", detay

        gecti = client.post("/api/crop-seasons", headers=h, json={
            "parcel_id": parsel["id"], "season_year": 2026, "crop": "Ayçiçeği",
            "started_on": "2026-05-01",
            "plantback_override_reason": "toprak analizi temiz"})
        assert gecti.status_code == 201, gecti.text
        assert gecti.json()["plantback_warning"], gecti.text

        # SINIR GÜNÜ İZİNLİ, üretim diyalektinde de.
        sinir = client.post("/api/crop-seasons", headers=h, json={
            "parcel_id": parsel["id"], "season_year": 2026, "crop": "Ayçiçeği",
            "started_on": "2026-06-01"})
        assert sinir.status_code == 201, sinir.text
        assert sinir.json()["plantback_warning"] is None, sinir.text

        guvenlik = client.get("/api/field-safety", headers=h).json()
        assert "plantback_blocks" in guvenlik, guvenlik
