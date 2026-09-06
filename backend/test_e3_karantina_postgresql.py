"""PostgreSQL ikizi: E3 ŞEMASININ gerçek kısıtlarla eşi.

Göç `20260909_0075`. SQLite ikizi `tests/test_e3_karantina.py` kilidin
DAVRANIŞINI ölçüyor (üç politika, sürü yolu, sınır günü, açma/kapatma
döngüsü); bu dosya yalnız ŞEMANIN GERÇEKTEN ISIRAN kısımlarını ve GELİŞTİRME
DİYALEKTİNDE GÖRÜNMEYEN tuzakları ölçer.

--- BU İKİZ NEDEN VAR ------------------------------------------------------

1. **İKİ BİLEŞİK YABANCI ANAHTARIN GERÇEKTEN ISIRMASI.** SQLite'ta yabancı
   anahtar uygulaması varsayılan olarak KAPALIDIR (`PRAGMA foreign_keys` temiz
   bir şemada **0** döner), yani
   `fk_animal_quarantines_animal_same_company` ve
   `fk_animal_quarantines_group_same_company` orada YEŞİL kalırdı: bir
   kiracının karantinası BAŞKA kiracının hayvanını işaret edebilirdi ve o
   satır, komşunun sağımını ve satışını keserdi.

2. **KISMİ TEKİL İNDEKSLERİN GERÇEKTEN KISMİ OLMASI.** Bu göçün EN İNCE
   parçası budur ve İKİ YÖNDE DE yanlış olabilir:

   * Koşul HİÇ uygulanmazsa (`postgresql_where` yazılmasaydı) indeks
     KAPANMIŞ karantinaları da tekilleştirirdi — aynı hayvana ikinci bir
     karantina HİÇ açılamazdı ve hata "zaten açık karantina var" derdi,
     yani YANLIŞ BİR CÜMLEYLE.
   * `animal_id IS NOT NULL` süzgeci olmasaydı, sürü karantinalarının
     tamamı (`animal_id` NULL) tek bir NULL grubuna düşmezdi — SQL'de UNIQUE
     NULL'ları BİRBİRİNDEN FARKLI sayar, yani kısıt HİÇBİR ŞEYİ engellemezdi
     ve yokluğu SESSİZ olurdu.

   İkisi de ancak GERÇEK bir eşzamanlılık kısıtında ölçülebilir; SQLite ikizi
   indeksin METNİNİ okuyor, burası DAVRANIŞINI.

3. **`ck_animal_quarantines_hedef`.** "Hayvan ya da grup, ikisi birden değil"
   kuralı `milk_yields`te (0049) YALNIZ uygulama katmanındaydı ve uygulama
   katmanı elle yazılmış bir INSERT'ü ya da bir betiği durdurmaz.

4. **`ck_animal_quarantines_aralik`.** `ended_on >= started_on`. Uygulama
   katmanı bunu 422'ye çeviriyor; kısıt olmasaydı bir betik geriye akan bir
   aralık yazar ve o karantina HİÇBİR günü kapsamazdı — yani sessizce
   ETKİSİZ olurdu.

5. **`ck_animal_quarantines_sebep_dolu`.** `NOT NULL` tek başına `' '`
   dizesini geçirirdi ve "sebepsiz karantina" tam olarak o satırdır.

6. **`herd_quarantine_policy` SEVİYE KÜMESİ ve VARSAYILANI.** CHECK
   geliştirme diyalektinde ÖLÇÜLEMEZ (SQLite onu yansıtmıyor) ve 0072'de
   ölçülen kusur — açılış DDL'i sütunu kurar, göç dalı atlar, CHECK HİÇ
   kurulmaz — tam olarak burada görünür. Varsayılanın `block` olması da
   burada, GERÇEK `server_default` üzerinden ölçülüyor.

--- ÖLÇÜLEN KÖK SEBEP, İDDİA DEĞİL -----------------------------------------

Testler kısıtın VARLIĞINI değil GERÇEKTEN REDDETTİĞİNİ ölçüyor: her biri
kısıtı ihlal eden bir yazma deneyip `IntegrityError` bekliyor.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

BACKEND = Path(__file__).resolve().parent

#: DOSYA SEVİYESİNDE İŞARET: bu ikizin TAMAMI gerçek bir PostgreSQL sunucusu
#: ister (`pytest.ini`in `postgresql` işareti). `_url()` zaten atlıyor; işaret
#: seçimi de mümkün kılıyor (`-m postgresql`).
pytestmark = pytest.mark.postgresql

FIRMA_ADI = "E3 İKİZİ firması"
KOMSU_ADI = "E3 İKİZİ komşu firması"


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("E3 ikizi APP_TEST_DATABASE_URL ister")
    return url


def _temizle(engine) -> None:
    with engine.begin() as baglanti:
        for deyim in (
            "DELETE FROM animal_quarantines WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM milk_yields WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM animal_movements WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM animals WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM animal_groups WHERE company_id IN "
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


def _firma_kur(baglanti, firma_adi: str) -> tuple[int, int, int]:
    """Firma + sürü + hayvan; (company_id, group_id, animal_id)."""
    simdi = datetime.now(timezone.utc)
    cid = baglanti.execute(
        text(
            "INSERT INTO companies (name, is_active, created_at) "
            "VALUES (:ad, true, :simdi) RETURNING id"
        ),
        {"ad": firma_adi, "simdi": simdi},
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
    return cid, suru, hayvan


def _karantina_yaz(baglanti, cid: int, **ustune) -> int:
    simdi = datetime.now(timezone.utc)
    degerler = {
        "cid": cid, "aid": None, "gid": None,
        "bas": date(2026, 9, 1), "bit": None, "sebep": "İkiz gözlem",
        "simdi": simdi,
    }
    degerler.update(ustune)
    return baglanti.execute(
        text(
            "INSERT INTO animal_quarantines (company_id, animal_id, group_id, "
            "started_on, ended_on, reason, created_at, updated_at) VALUES "
            "(:cid, :aid, :gid, :bas, :bit, :sebep, :simdi, :simdi) RETURNING id"
        ),
        degerler,
    ).scalar_one()


# ---------------------------------------------------- şema GERÇEKTEN ısırır ---

def test_capraz_kiraci_HAYVAN_referansi_REDDEDILIYOR(motor) -> None:
    """A'nın karantinası B'nin hayvanını GÖSTEREMEZ.

    SQLite'ta `PRAGMA foreign_keys` KAPALI doğar; bu satır orada SESSİZCE
    yazılır ve komşunun hayvanı üzerinden A'nın sağımını keserdi.
    """
    with motor.begin() as baglanti:
        cid, _, _ = _firma_kur(baglanti, FIRMA_ADI)
        _, _, komsu_hayvan = _firma_kur(baglanti, KOMSU_ADI)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _karantina_yaz(baglanti, cid, aid=komsu_hayvan)


def test_capraz_kiraci_SURU_referansi_REDDEDILIYOR(motor) -> None:
    with motor.begin() as baglanti:
        cid, _, _ = _firma_kur(baglanti, FIRMA_ADI)
        _, komsu_suru, _ = _firma_kur(baglanti, KOMSU_ADI)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _karantina_yaz(baglanti, cid, gid=komsu_suru)


def test_hedef_TAM_OLARAK_BIRI(motor) -> None:
    """`ck_animal_quarantines_hedef`: ikisi birden de, hiçbiri de OLMAZ."""
    with motor.begin() as baglanti:
        cid, suru, hayvan = _firma_kur(baglanti, FIRMA_ADI)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _karantina_yaz(baglanti, cid, aid=hayvan, gid=suru)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _karantina_yaz(baglanti, cid)

    with motor.begin() as baglanti:
        _karantina_yaz(baglanti, cid, aid=hayvan)
        _karantina_yaz(baglanti, cid, gid=suru)


def test_IKINCI_ACIK_KARANTINA_hayvan_basina_REDDEDILIYOR(motor) -> None:
    """`uq_animal_quarantines_acik_hayvan` GERÇEKTEN reddediyor.

    İki AÇIK karantina, "bu hayvan ne zaman çıkacak" sorusunu CEVAPSIZ
    bırakırdı. Uygulama katmanındaki bir "önce bak, sonra yaz" kontrolü İKİ
    EŞZAMANLI isteği ayırt EDEMEZDİ; ayıran yalnız indekstir.
    """
    with motor.begin() as baglanti:
        cid, _, hayvan = _firma_kur(baglanti, FIRMA_ADI)
        _karantina_yaz(baglanti, cid, aid=hayvan)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _karantina_yaz(baglanti, cid, aid=hayvan, bas=date(2026, 9, 3))


def test_IKINCI_ACIK_KARANTINA_suru_basina_REDDEDILIYOR(motor) -> None:
    """İKİNCİ İNDEKSİN VAR OLMA SEBEBİ BURADA ÖLÇÜLÜYOR.

    Tek bir `(company_id, animal_id, group_id)` indeksi olsaydı bu satır
    GEÇERDİ: sürü karantinalarında `animal_id` NULL'dur ve SQL UNIQUE
    NULL'ları BİRBİRİNDEN FARKLI sayar.
    """
    with motor.begin() as baglanti:
        cid, suru, _ = _firma_kur(baglanti, FIRMA_ADI)
        _karantina_yaz(baglanti, cid, gid=suru)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _karantina_yaz(baglanti, cid, gid=suru, bas=date(2026, 9, 3))


def test_KAPANMIS_KARANTINALAR_SINIRSIZ(motor) -> None:
    """İNDEKSİN KISMİ OLMASININ ÖLÇÜSÜ.

    `postgresql_where` yazılmasaydı indeks KOŞULSUZ kurulur ve bu test
    kırmızı olurdu: aynı hayvana ikinci bir karantina HİÇ açılamazdı ve hata
    "zaten AÇIK karantina var" derdi — yani YANLIŞ bir cümleyle.
    """
    with motor.begin() as baglanti:
        cid, suru, hayvan = _firma_kur(baglanti, FIRMA_ADI)
        for ay in (1, 2, 3):
            _karantina_yaz(
                baglanti, cid, aid=hayvan,
                bas=date(2026, ay, 1), bit=date(2026, ay, 20),
            )
            _karantina_yaz(
                baglanti, cid, gid=suru,
                bas=date(2026, ay, 1), bit=date(2026, ay, 20),
            )
        # KAPANMIŞLARIN YANINDA BİRER AÇIK DA DURABİLİR.
        _karantina_yaz(baglanti, cid, aid=hayvan, bas=date(2026, 6, 1))
        _karantina_yaz(baglanti, cid, gid=suru, bas=date(2026, 6, 1))
        sayi = baglanti.execute(
            text(
                "SELECT COUNT(*) FROM animal_quarantines WHERE company_id=:cid"
            ),
            {"cid": cid},
        ).scalar_one()
    assert sayi == 8, sayi


def test_ARALIK_GERIYE_AKAMAZ_ama_AYNI_GUN_SERBEST(motor) -> None:
    """`ck_animal_quarantines_aralik`.

    Geriye akan bir aralık HİÇBİR günü kapsamazdı, yani karantina sessizce
    ETKİSİZ olurdu. Aynı gün kapanış ise GEÇERLİ bir kayıttır: yanlış açılıp
    aynı gün kapatılan karantinayı reddetmek düzeltmeyi imkânsız kılardı.
    """
    with motor.begin() as baglanti:
        cid, _, hayvan = _firma_kur(baglanti, FIRMA_ADI)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _karantina_yaz(
                baglanti, cid, aid=hayvan,
                bas=date(2026, 9, 10), bit=date(2026, 9, 1),
            )

    with motor.begin() as baglanti:
        _karantina_yaz(
            baglanti, cid, aid=hayvan,
            bas=date(2026, 9, 10), bit=date(2026, 9, 10),
        )


def test_SEBEP_BOSLUKTAN_IBARET_OLAMAZ(motor) -> None:
    """`ck_animal_quarantines_sebep_dolu`: `NOT NULL` tek başına YETMEZDİ."""
    with motor.begin() as baglanti:
        cid, _, hayvan = _firma_kur(baglanti, FIRMA_ADI)

    for bos in ("", "   ", "\t"):
        with pytest.raises(IntegrityError):
            with motor.begin() as baglanti:
                _karantina_yaz(baglanti, cid, aid=hayvan, sebep=bos)


def test_firma_politikasi_KAPALI_KUMEDE_ve_VARSAYILANI_block(motor) -> None:
    """`ck_companies_herd_quarantine_policy` "allow"u ŞEMA seviyesinde reddeder.

    Bu kısıt geliştirme diyalektinde ÖLÇÜLEMEZ: SQLite onu YANSITMIYOR.
    0072'de ölçülen kusur (açılış DDL'i sütunu kurar, göç dalı atlar, CHECK
    HİÇ kurulmaz) tam olarak burada görünür — o kusurdayken bu test
    `UPDATE ... = 'allow'`u KABUL ederdi.

    VARSAYILAN da burada ölçülüyor ve kardeşlerinden (`require_reason`) FARKLI
    olması BİLİNÇLİDİR: karantinayı bir insan ELLE açtı ve AÇIK bıraktı.
    """
    with motor.begin() as baglanti:
        cid, _, _ = _firma_kur(baglanti, FIRMA_ADI)
        # `_firma_kur` politika sütununu HİÇ YAZMIYOR; okunan şey
        # `server_default`ın kendisidir.
        varsayilan = baglanti.execute(
            text("SELECT herd_quarantine_policy FROM companies WHERE id=:cid"),
            {"cid": cid},
        ).scalar_one()
    assert varsayilan == "block", varsayilan

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            baglanti.execute(
                text(
                    "UPDATE companies SET herd_quarantine_policy='allow' "
                    "WHERE id=:cid"
                ),
                {"cid": cid},
            )

    for seviye in ("block", "require_reason", "warn"):
        with motor.begin() as baglanti:
            baglanti.execute(
                text(
                    "UPDATE companies SET herd_quarantine_policy=:s WHERE id=:cid"
                ),
                {"s": seviye, "cid": cid},
            )
            okunan = baglanti.execute(
                text("SELECT herd_quarantine_policy FROM companies WHERE id=:cid"),
                {"cid": cid},
            ).scalar_one()
        assert okunan == seviye, okunan


def test_ARINMA_CIFTI_YERINDE_ve_KARANTINA_CIFTI_AYRI(motor) -> None:
    """0074'ün sütunlarına DOKUNULMADI; iki kilit AYRI çiftlerde yaşıyor.

    Bir sağım HEM arınma HEM karantina ihlal edebilir. Tek çifte bindirmek,
    ikinci uyarının birinciyi EZMESİ demekti — ve bu, üretim diyalektinde
    sütunların GERÇEKTEN var olmasıyla ölçülüyor.
    """
    with motor.begin() as baglanti:
        cid, suru, hayvan = _firma_kur(baglanti, FIRMA_ADI)
        simdi = datetime.now(timezone.utc)
        baglanti.execute(
            text(
                "INSERT INTO milk_yields (company_id, animal_id, milked_on, "
                "quantity_liters, withdrawal_warning, withdrawal_override_reason, "
                "quarantine_warning, quarantine_override_reason, created_at, "
                "updated_at) VALUES (:cid, :aid, :gun, 20, 'arınma uyarısı', "
                "'arınma gerekçesi', 'karantina uyarısı', 'karantina gerekçesi', "
                ":simdi, :simdi)"
            ),
            {"cid": cid, "aid": hayvan, "gun": date(2026, 9, 5), "simdi": simdi},
        )
        satir = baglanti.execute(
            text(
                "SELECT withdrawal_warning, withdrawal_override_reason, "
                "quarantine_warning, quarantine_override_reason "
                "FROM milk_yields WHERE company_id=:cid"
            ),
            {"cid": cid},
        ).mappings().one()
    assert satir["withdrawal_warning"] == "arınma uyarısı", satir
    assert satir["quarantine_warning"] == "karantina uyarısı", satir
    assert satir["withdrawal_override_reason"] != satir["quarantine_override_reason"]


# ----------------------------------------------------------- uçtan uca ------

ADMIN_PW = "E3KarPG!123"


@pytest.fixture()
def acilis_sifresi():
    """Admin şifresini AÇILIŞ DURUMUNA çeker; testten sonra GERİ KOYAR.

    Kalıp `test_e2_tedavi_arinma_postgresql.py`den DEVRALINDI ve gerekçesi
    orada ölçülmüştür: PostgreSQL ikizleri AYNI veritabanını paylaşıyor ve her
    biri girişten sonra admin şifresini KENDİ sabitine çeviriyor; iki ikiz aynı
    shard'a düştüğünde giriş SIRAYA BAĞLI olarak kırılıyor.

    Fikstür İKİ UÇTAN da çalışıyor: testten ÖNCE açılış durumunu (`admin123` +
    `must_change_password`) yazıyor, sonra GERİ koyuyor — tek yönlü bir çare
    dosyayı iyi bir komşu yapar ama KENDİSİNİ korumaz.
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
    """Uçtan uca: aç -> sağım 422 -> nakil 422 -> kapat -> sınır günü serbest.

    Şema kapıları kısıtları ölçüyor; bu test kilidin ÜRETİM DİYALEKTİNDE
    çalıştığını ölçüyor. Burada BİRLİKTE görünen şeyler:

    * `DATE` karşılaştırması — sürücü SQLite'ta metin, PostgreSQL'de `date`
      döndürüyor ve `_gun` farkı burada GERÇEKTEN düzeltiyor. Yarı açık
      aralığın (`ended_on` günü SERBEST) iki diyalektte aynı sonucu vermesi
      ancak burada ölçülür.
    * Sürü yolundaki `IN (SELECT ...)` alt sorgusu.
    * `close_quarantine`ın CAS'i: ikinci kapatma 409.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        h = _admin_headers(client)
        suru = client.post("/api/animal-groups", headers=h, json={
            "code": "pge3", "name": "PG İkiz Karantina Sürüsü",
            "species": "CATTLE"}).json()
        inek = client.post("/api/animals", headers=h, json={
            "ear_tag": "TR9100000001", "species": "CATTLE", "sex": "FEMALE",
            "group_id": suru["id"]}).json()

        # VARSAYILAN `block` ÜRETİM DİYALEKTİNDE DE geçerli.
        ayar = client.get("/api/company-settings", headers=h).json()
        assert ayar["herd_quarantine_policy"] == "block", ayar

        acildi = client.post("/api/animal-quarantines", headers=h, json={
            "animal_id": inek["id"], "started_on": "2026-09-01",
            "reason": "PG ikiz şüphe"})
        assert acildi.status_code == 201, acildi.text
        kid = acildi.json()["id"]
        assert acildi.json()["ended_on"] is None, acildi.text

        # İKİNCİ AÇIK KARANTİNA -> 409 (kısmi tekil indeks üretimde ısırıyor).
        ikinci = client.post("/api/animal-quarantines", headers=h, json={
            "animal_id": inek["id"], "started_on": "2026-09-03",
            "reason": "ikinci"})
        assert ikinci.status_code == 409, ikinci.text

        ihlal = client.post("/api/milk-yields", headers=h, json={
            "animal_id": inek["id"], "milked_on": "2026-09-05",
            "quantity_liters": "20"})
        assert ihlal.status_code == 422, ihlal.text
        detay = ihlal.json()["detail"]
        assert detay["sebep"] == "KARANTINA_ACIK", detay
        assert detay["blocking"][0]["ended_on"] is None, detay

        # SÜRÜ YOLU: bireysel karantina GRUP sağımını da kesiyor.
        grup = client.post("/api/milk-yields", headers=h, json={
            "group_id": suru["id"], "milked_on": "2026-09-05",
            "quantity_liters": "100"})
        assert grup.status_code == 422, grup.text
        assert grup.json()["detail"]["blocking"][0]["scope"] == "ANIMAL", grup.text

        # NAKİL KİLİTLİ (arınmadan FARKLI), ÖLÜM DEĞİL.
        cikis = client.post("/api/animal-movements", headers=h, json={
            "animal_id": inek["id"], "kind": "TRANSFER_OUT",
            "moved_on": "2026-09-05"})
        assert cikis.status_code == 422, cikis.text

        kapandi = client.post(
            "/api/animal-quarantines/%d/close" % kid, headers=h,
            json={"ended_on": "2026-09-10"})
        assert kapandi.status_code == 200, kapandi.text
        assert kapandi.json()["ended_on"] == "2026-09-10", kapandi.text

        # CAS: ikinci kapatma 409.
        tekrar = client.post(
            "/api/animal-quarantines/%d/close" % kid, headers=h,
            json={"ended_on": "2026-09-11"})
        assert tekrar.status_code == 409, tekrar.text

        # SINIR GÜNÜ SERBEST, üretim diyalektinde de: `ended_on` KAPSANMAZ.
        sinir = client.post("/api/animal-movements", headers=h, json={
            "animal_id": inek["id"], "kind": "TRANSFER_OUT",
            "moved_on": "2026-09-10"})
        assert sinir.status_code == 201, sinir.text
        assert sinir.json()["quarantine_warning"] is None, sinir.text
        # BİR GÜN ÖNCESİ HALA KESİYOR.
        assert client.post("/api/milk-yields", headers=h, json={
            "animal_id": inek["id"], "milked_on": "2026-09-09",
            "quantity_liters": "20"}).status_code == 422

        # AKTİVİTE KAYDI: açma ve kapatma İKİSİ DE kayıtta.
        loglar = client.get("/api/activity-logs", headers=h,
                            params={"limit": 50}).json()["items"]
        tipler = {x["action_type"] for x in loglar}
        assert "animal_quarantine.opened" in tipler, tipler
        assert "animal_quarantine.closed" in tipler, tipler
