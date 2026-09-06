"""PostgreSQL ikizi: E2 ŞEMASININ gerçek kısıtlarla eşi.

Göç `20260908_0074`. SQLite ikizi `tests/test_e2_tedavi_arinma.py` kilidin
DAVRANIŞINI ölçüyor (üç politika, en uzun kazanır, tür yedeği, sınır günü,
sürü yolu, köken); bu dosya yalnız ŞEMANIN GERÇEKTEN ISIRAN kısımlarını ve
GELİŞTİRME DİYALEKTİNDE GÖRÜNMEYEN tuzakları ölçer.

--- BU İKİZ NEDEN VAR ------------------------------------------------------

1. **BEŞ BİLEŞİK YABANCI ANAHTARIN GERÇEKTEN ISIRMASI.** SQLite'ta yabancı
   anahtar uygulaması varsayılan olarak KAPALIDIR (`PRAGMA foreign_keys` temiz
   bir şemada **0** döner), yani `fk_vet_drugs_product_same_company`,
   `fk_animal_treatments_animal_same_company`,
   `fk_animal_treatments_group_same_company`, `fk_ati_treatment_same_company`
   ve `fk_ati_product_same_company` orada YEŞİL kalırdı: bir kiracının tedavisi
   BAŞKA kiracının hayvanını işaret edebilirdi ve o satır, komşunun sağımını
   keserdi. Bu göçün kiracı savunmasının TAMAMI bu beş kısıttır.

2. **`UNIQUE(company_id, product_id, species)`.** Aynı ürün+tür için İKİ satır,
   çözümü belirsiz bırakır (hangi arınma süresi?). Uygulama kontrolü İKİ
   EŞZAMANLI isteği ayırt EDEMEZ; ayıran yalnız şemadır.

3. **`ck_animal_treatments_hedef`.** "Hayvan ya da grup, ikisi birden değil"
   kuralı `milk_yields`te (0049) YALNIZ uygulama katmanındaydı ve uygulama
   katmanı elle yazılmış bir INSERT'ü ya da bir betiği durdurmaz. 0074 kuralı
   ŞEMAYA yazdı; burada GERÇEKTEN reddettiği ölçülüyor.

4. **`ck_vet_drugs_species` KAPALI KÜMESİ.** Çözüm TAM EŞİTLİKLE çalışıyor ve
   0063'ün Türkçe katlaması BİLEREK kullanılmadı; o tercihin dayanağı türün
   KAPALI bir küme olmasıdır. Kısıt gerçekten ısırmasaydı dayanak ÇÜRÜK olurdu
   — yanlış yazılmış bir tür kodu kataloğu SESSİZCE işlevsiz bırakırdı.

5. **ARALIK CHECK'LERİ ve `herd_withdrawal_policy` SEVİYE KÜMESİ.** Hepsi
   CHECK; şemaya yazılmamış bir seviye kümesi, uçtaki `Literal` atlandığı anda
   sessizce gevşer ve burada gevşeyen şey İNSAN GIDASIDIR.

6. **`NUMERIC(14,4)` DOZ ÖLÇEĞİ.** SQLite'ta ölçek DAYATILMAZ; yanlış ölçekli
   bir doz orada SESSİZCE geçerdi.

--- ÖLÇÜLEN KÖK SEBEP, İDDİA DEĞİL -----------------------------------------

Testler kısıtın VARLIĞINI değil GERÇEKTEN REDDETTİĞİNİ ölçüyor: her biri
kısıtı ihlal eden bir yazma deneyip `IntegrityError` bekliyor.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

BACKEND = Path(__file__).resolve().parent

FIRMA_ADI = "E2 İKİZİ firması"
KOMSU_ADI = "E2 İKİZİ komşu firması"


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("E2 ikizi APP_TEST_DATABASE_URL ister")
    return url


def _temizle(engine) -> None:
    with engine.begin() as baglanti:
        for deyim in (
            "DELETE FROM animal_treatment_items WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM animal_treatments WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM vet_drugs WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM milk_yields WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM animal_movements WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM animals WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM animal_groups WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM products WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM activity_logs WHERE company_id IN "
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


def _firma_kur(baglanti, firma_adi: str) -> tuple[int, int, int, int]:
    """Firma + ürün + sürü + hayvan; (company_id, product_id, group_id, animal_id)."""
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
            "VALUES (:cid, 'İkiz Antibiyotik', 'ML', 10, true) RETURNING id"
        ),
        {"cid": cid},
    ).scalar_one()
    suru = baglanti.execute(
        text(
            "INSERT INTO animal_groups (company_id, code, name, species, status, "
            "created_at, updated_at) VALUES (:cid, 'ikiz', 'İkiz Sürü', 'CATTLE', "
            "'ACTIVE', :simdi, :simdi) RETURNING id"
        ),
        {"cid": cid, "simdi": simdi},
    ).scalar_one()
    hayvan = baglanti.execute(
        text(
            "INSERT INTO animals (company_id, ear_tag, species, sex, acquisition, "
            "group_id, status, created_at, updated_at) VALUES (:cid, NULL, 'CATTLE', "
            "'FEMALE', 'BORN', :gid, 'ACTIVE', :simdi, :simdi) RETURNING id"
        ),
        {"cid": cid, "gid": suru, "simdi": simdi},
    ).scalar_one()
    return cid, urun, suru, hayvan


def _katalog_yaz(baglanti, cid: int, pid: int, **ustune) -> int:
    simdi = datetime.now(timezone.utc)
    degerler = {
        "cid": cid, "pid": pid, "species": "", "sut": 3, "et": 28, "simdi": simdi,
    }
    degerler.update(ustune)
    return baglanti.execute(
        text(
            "INSERT INTO vet_drugs (company_id, product_id, species, "
            "milk_withdrawal_days, meat_withdrawal_days, status, origin, "
            "created_at, updated_at) VALUES (:cid, :pid, :species, :sut, :et, "
            "'ACTIVE', 'MANUAL', :simdi, :simdi) RETURNING id"
        ),
        degerler,
    ).scalar_one()


def _tedavi_yaz(baglanti, cid: int, **ustune) -> int:
    simdi = datetime.now(timezone.utc)
    degerler = {
        "cid": cid, "aid": None, "gid": None, "gun": date(2026, 9, 1),
        "sut": 10, "et": 28, "simdi": simdi,
    }
    degerler.update(ustune)
    return baglanti.execute(
        text(
            "INSERT INTO animal_treatments (company_id, animal_id, group_id, "
            "treated_on, milk_withdrawal_days, meat_withdrawal_days, "
            "withdrawal_source, created_at, updated_at) VALUES (:cid, :aid, :gid, "
            ":gun, :sut, :et, 'CATALOGUE', :simdi, :simdi) RETURNING id"
        ),
        degerler,
    ).scalar_one()


# ---------------------------------------------------- şema GERÇEKTEN ısırır ---

def test_KATALOG_capraz_kiraci_urun_referansi_REDDEDILIYOR(motor) -> None:
    """A'nın katalog satırı B'nin ürününü GÖSTEREMEZ.

    SQLite'ta `PRAGMA foreign_keys` KAPALI doğar; bu satır orada SESSİZCE
    yazılır ve komşunun ürünü üzerinden A'nın sağımını keserdi.
    """
    with motor.begin() as baglanti:
        cid, _, _, _ = _firma_kur(baglanti, FIRMA_ADI)
        _, komsu_urun, _, _ = _firma_kur(baglanti, KOMSU_ADI)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _katalog_yaz(baglanti, cid, komsu_urun)


def test_TEDAVI_capraz_kiraci_hayvan_referansi_REDDEDILIYOR(motor) -> None:
    with motor.begin() as baglanti:
        cid, _, _, _ = _firma_kur(baglanti, FIRMA_ADI)
        _, _, _, komsu_hayvan = _firma_kur(baglanti, KOMSU_ADI)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _tedavi_yaz(baglanti, cid, aid=komsu_hayvan)


def test_TEDAVI_capraz_kiraci_suru_referansi_REDDEDILIYOR(motor) -> None:
    with motor.begin() as baglanti:
        cid, _, _, _ = _firma_kur(baglanti, FIRMA_ADI)
        _, _, komsu_suru, _ = _firma_kur(baglanti, KOMSU_ADI)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _tedavi_yaz(baglanti, cid, gid=komsu_suru)


def test_KALEM_capraz_kiraci_tedavi_referansi_REDDEDILIYOR(motor) -> None:
    with motor.begin() as baglanti:
        cid, _, _, _ = _firma_kur(baglanti, FIRMA_ADI)
        komsu_cid, _, _, komsu_hayvan = _firma_kur(baglanti, KOMSU_ADI)
        komsu_tedavi = _tedavi_yaz(baglanti, komsu_cid, aid=komsu_hayvan)

    simdi = datetime.now(timezone.utc)
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            baglanti.execute(
                text(
                    "INSERT INTO animal_treatment_items (company_id, treatment_id, "
                    "drug_name, created_at, updated_at) VALUES (:cid, :tid, 'X', "
                    ":simdi, :simdi)"
                ),
                {"cid": cid, "tid": komsu_tedavi, "simdi": simdi},
            )


def test_ayni_urun_ve_tur_IKI_KEZ_yazilamiyor(motor) -> None:
    """`UNIQUE(company_id, product_id, species)` GERÇEKTEN reddediyor."""
    with motor.begin() as baglanti:
        cid, pid, _, _ = _firma_kur(baglanti, FIRMA_ADI)
        _katalog_yaz(baglanti, cid, pid)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _katalog_yaz(baglanti, cid, pid, sut=1, et=1)


def test_TUR_FARKLIYSA_ikinci_satir_GECIYOR(motor) -> None:
    """Kataloğun VAR OLMA ŞEKLİ: aynı ilaç için TÜR BAŞINA ayrı satır.

    Aynı etken madde sığırda ve koyunda farklı arınma taşır; şema bunu ifade
    edemeseydi katalog alan gerçeğini YALAN söylerdi.
    """
    with motor.begin() as baglanti:
        cid, pid, _, _ = _firma_kur(baglanti, FIRMA_ADI)
        _katalog_yaz(baglanti, cid, pid, species="", sut=3, et=28)
        _katalog_yaz(baglanti, cid, pid, species="SHEEP", sut=7, et=10)
        _katalog_yaz(baglanti, cid, pid, species="GOAT", sut=5, et=12)
        sayi = baglanti.execute(
            text(
                "SELECT COUNT(*) FROM vet_drugs "
                "WHERE company_id=:cid AND product_id=:pid"
            ),
            {"cid": cid, "pid": pid},
        ).scalar_one()
    assert sayi == 3


def test_TUR_KUMESI_KAPALI_uydurma_kod_REDDEDILIYOR(motor) -> None:
    """`ck_vet_drugs_species`: türün KAPALI küme olması ŞEMADA yazılı.

    Çözümün TAM EŞİTLİKLE (Türkçe katlama olmadan) çalışmasının dayanağı bu
    kısıttır. Kısıt olmasaydı "Sığır" gibi bir satır yazılır ve HİÇBİR hayvana
    eşleşmezdi — katalog SESSİZCE işlevsiz kalırdı.
    """
    with motor.begin() as baglanti:
        cid, pid, _, _ = _firma_kur(baglanti, FIRMA_ADI)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _katalog_yaz(baglanti, cid, pid, species="DEVE")

    # Boş dize ("bütün türler") ve dört gerçek kod GEÇİYOR.
    with motor.begin() as baglanti:
        for tur in ("", "CATTLE", "BUFFALO", "SHEEP", "GOAT"):
            _katalog_yaz(baglanti, cid, pid, species=tur)


def test_TEDAVI_hedefi_TAM_OLARAK_BIRI(motor) -> None:
    """`ck_animal_treatments_hedef`: ikisi birden de, hiçbiri de OLMAZ.

    `milk_yields`te aynı kural YALNIZ uygulama katmanındadır (0049) ve o
    katman elle yazılmış bir INSERT'ü durdurmaz. 0074 kuralı şemaya yazdı.
    """
    with motor.begin() as baglanti:
        cid, _, suru, hayvan = _firma_kur(baglanti, FIRMA_ADI)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _tedavi_yaz(baglanti, cid, aid=hayvan, gid=suru)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _tedavi_yaz(baglanti, cid)

    with motor.begin() as baglanti:
        _tedavi_yaz(baglanti, cid, aid=hayvan)
        _tedavi_yaz(baglanti, cid, gid=suru)


@pytest.mark.parametrize("gun", [-1, 3651])
def test_KATALOG_arinma_ARALIGI_isiriyor(motor, gun: int) -> None:
    with motor.begin() as baglanti:
        cid, pid, _, _ = _firma_kur(baglanti, FIRMA_ADI)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _katalog_yaz(baglanti, cid, pid, sut=gun)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _katalog_yaz(baglanti, cid, pid, et=gun)


@pytest.mark.parametrize("gun", [-1, 3651])
def test_TEDAVI_arinma_ARALIGI_isiriyor(motor, gun: int) -> None:
    """Tedavide süre NULL kabul eder ama SAYIYSA aynı aralıktadır."""
    with motor.begin() as baglanti:
        cid, _, _, hayvan = _firma_kur(baglanti, FIRMA_ADI)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _tedavi_yaz(baglanti, cid, aid=hayvan, sut=gun)

    # NULL GEÇİYOR: ne katalog ne operatör konuştuysa süre BOŞ kalır.
    with motor.begin() as baglanti:
        _tedavi_yaz(baglanti, cid, aid=hayvan, sut=None, et=None)


def test_firma_politikasi_KAPALI_KUMEDE(motor) -> None:
    """`herd_withdrawal_policy` CHECK'i "allow"u ŞEMA seviyesinde reddediyor.

    Uçtaki `Literal` tek başına savunma DEĞİLDİR: bir betik ya da elle yazılmış
    bir `UPDATE` onu atlar. 0072'de ölçülmüş kusur (açılış DDL'i sütunu kurar,
    göç dalı atlar, CHECK HİÇ kurulmaz) tam olarak burada görünür — göç sütunu
    ve CHECK'i AYRI AYRI sormasaydı bu test KIRMIZI olurdu.
    """
    with motor.begin() as baglanti:
        cid, _, _, _ = _firma_kur(baglanti, FIRMA_ADI)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            baglanti.execute(
                text(
                    "UPDATE companies SET herd_withdrawal_policy='allow' "
                    "WHERE id=:cid"
                ),
                {"cid": cid},
            )

    with motor.begin() as baglanti:
        for seviye in ("block", "require_reason", "warn"):
            baglanti.execute(
                text("UPDATE companies SET herd_withdrawal_policy=:s WHERE id=:cid"),
                {"s": seviye, "cid": cid},
            )


def test_varsayilan_politika_require_reason(motor) -> None:
    with motor.begin() as baglanti:
        cid, _, _, _ = _firma_kur(baglanti, FIRMA_ADI)
        seviye = baglanti.execute(
            text("SELECT herd_withdrawal_policy FROM companies WHERE id=:cid"),
            {"cid": cid},
        ).scalar_one()
    assert seviye == "require_reason"


def test_DOZ_OLCEGI_NUMERIC_14_4(motor) -> None:
    """Doz `NUMERIC(14,4)`tür; SQLite ölçeği DAYATMAZ, PostgreSQL dayatır."""
    simdi = datetime.now(timezone.utc)
    with motor.begin() as baglanti:
        cid, _, _, hayvan = _firma_kur(baglanti, FIRMA_ADI)
        tid = _tedavi_yaz(baglanti, cid, aid=hayvan)
        baglanti.execute(
            text(
                "INSERT INTO animal_treatment_items (company_id, treatment_id, "
                "drug_name, dose, dose_unit, created_at, updated_at) VALUES "
                "(:cid, :tid, 'İkiz', 12.5, 'ML', :simdi, :simdi)"
            ),
            {"cid": cid, "tid": tid, "simdi": simdi},
        )
        doz = baglanti.execute(
            text(
                "SELECT dose FROM animal_treatment_items "
                "WHERE company_id=:cid AND treatment_id=:tid"
            ),
            {"cid": cid, "tid": tid},
        ).scalar_one()
    assert doz == Decimal("12.5000"), doz

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            baglanti.execute(
                text(
                    "INSERT INTO animal_treatment_items (company_id, treatment_id, "
                    "drug_name, dose, created_at, updated_at) VALUES "
                    "(:cid, :tid, 'Negatif', -1, :simdi, :simdi)"
                ),
                {"cid": cid, "tid": tid, "simdi": simdi},
            )


# ----------------------------------------------------------- uçtan uca ------

ADMIN_PW = "E2VetPG!123"


@pytest.fixture()
def acilis_sifresi():
    """Admin şifresini AÇILIŞ DURUMUNA çeker; testten sonra GERİ KOYAR.

    Kalıp `test_d2_avans_tescil_postgresql.py`den DEVRALINDI ve gerekçesi orada
    ölçülmüştür: PostgreSQL ikizleri AYNI veritabanını paylaşıyor ve her biri
    girişten sonra admin şifresini KENDİ sabitine çeviriyor; iki ikiz aynı
    shard'a düştüğünde giriş SIRAYA BAĞLI olarak kırılıyor.

    Fikstür İKİ UÇTAN da çalışıyor: testten ÖNCE açılış durumunu (`admin123` +
    `must_change_password`) yazıyor, sonra GERİ koyuyor — tek yönlü bir çare
    dosyayı iyi bir komşu yapar ama KENDİSİNİ korumaz. Şifre uçtan değil
    SATIRDAN yazılıyor (`change-password` mevcut şifreyi ister ve
    `must_change_password` uçtan yazılamaz); tablo `app_users`tır.
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
    return headers


def test_kilit_GERCEK_PostgreSQLde_isiriyor(motor, acilis_sifresi) -> None:
    """Uçtan uca: katalog -> tedavi -> sağım 422 -> gerekçeyle 201 -> sınır günü.

    Şema kapıları kısıtları ölçüyor; bu test kilidin ÜRETİM DİYALEKTİNDE
    çalıştığını ölçüyor — `DATE` aritmetiği (sürücü SQLite'ta metin,
    PostgreSQL'de `date` döndürüyor ve `_gun` farkı burada gerçekten
    düzeltiyor), sürü yolundaki `IN (SELECT ...)` alt sorgusu ve köken
    sütunlarının yazımı burada BİRLİKTE görünüyor.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        h = _admin_headers(client)
        urun = client.post("/api/products", headers=h, json={
            "name": "PG İkiz Antibiyotik", "unit": "ML",
            "sale_price": "10.00"}).json()
        pid = urun["id"]
        assert client.post("/api/vet-drugs", headers=h, json={
            "product_id": pid, "species": "", "milk_withdrawal_days": 10,
            "meat_withdrawal_days": 28}).status_code == 201

        suru = client.post("/api/animal-groups", headers=h, json={
            "code": "pge2", "name": "PG İkiz Sürü", "species": "CATTLE"}).json()
        inek = client.post("/api/animals", headers=h, json={
            "ear_tag": "TR9000000001", "species": "CATTLE", "sex": "FEMALE",
            "group_id": suru["id"]}).json()

        tedavi = client.post("/api/animal-treatments", headers=h, json={
            "animal_id": inek["id"], "treated_on": "2026-09-01",
            "items": [{"product_id": pid, "drug_name": "PG İkiz", "dose": "12.5",
                       "dose_unit": "ML"}]})
        assert tedavi.status_code == 201, tedavi.text
        tj = tedavi.json()
        # KÖKEN ÇİFTİ ÜRETİM DİYALEKTİNDE DE YAZILIYOR.
        assert tj["withdrawal_source"] == "CATALOGUE", tj
        assert tj["catalogue_milk_days"] == 10, tj
        assert tj["catalogue_meat_days"] == 28, tj

        ihlal = client.post("/api/milk-yields", headers=h, json={
            "animal_id": inek["id"], "milked_on": "2026-09-05",
            "quantity_liters": "20"})
        assert ihlal.status_code == 422, ihlal.text
        detay = ihlal.json()["detail"]
        assert detay["sebep"] == "ARINMA_SURESI_DOLMADI", detay
        assert detay["blocking"][0]["earliest_allowed"] == "2026-09-11", detay

        gecti = client.post("/api/milk-yields", headers=h, json={
            "animal_id": inek["id"], "milked_on": "2026-09-05",
            "quantity_liters": "20",
            "withdrawal_override_reason": "veteriner onayı"})
        assert gecti.status_code == 201, gecti.text
        assert gecti.json()["withdrawal_warning"], gecti.text

        # SINIR GÜNÜ İZİNLİ, üretim diyalektinde de.
        sinir = client.post("/api/milk-yields", headers=h, json={
            "animal_id": inek["id"], "milked_on": "2026-09-11",
            "quantity_liters": "20"})
        assert sinir.status_code == 201, sinir.text
        assert sinir.json()["withdrawal_warning"] is None, sinir.text

        # SÜRÜ YOLU: bireysel tedavi GRUP sağımını da kesiyor
        # (`IN (SELECT ...)` alt sorgusu üretim diyalektinde de çalışıyor).
        grup = client.post("/api/milk-yields", headers=h, json={
            "group_id": suru["id"], "milked_on": "2026-09-05",
            "quantity_liters": "100"})
        assert grup.status_code == 422, grup.text
        assert grup.json()["detail"]["blocking"][0]["scope"] == "ANIMAL", grup.text

        # ET KİLİDİ: SALE kesiyor, DEATH kesmiyor.
        sat = client.post("/api/animal-movements", headers=h, json={
            "animal_id": inek["id"], "kind": "SALE", "moved_on": "2026-09-28"})
        assert sat.status_code == 422, sat.text
        olum = client.post("/api/animal-movements", headers=h, json={
            "animal_id": inek["id"], "kind": "DEATH", "moved_on": "2026-09-28"})
        assert olum.status_code == 201, olum.text
        assert olum.json()["withdrawal_warning"] is None, olum.text

        # AKTİVİTE KAYDI: katalog yazımı ve gerekçeli geçiş kayıtta.
        loglar = client.get("/api/activity-logs", headers=h,
                            params={"limit": 50}).json()["items"]
        tipler = {x["action_type"] for x in loglar}
        assert "vet_drug.create" in tipler, tipler
        assert "herd_withdrawal.overridden" in tipler, tipler
