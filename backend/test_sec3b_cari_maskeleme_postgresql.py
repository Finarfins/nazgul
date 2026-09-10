"""PostgreSQL ikizi: SEC-3b rol matrisinin GERÇEK PostgreSQL üzerindeki eşi.

SQLite ikizi `tests/test_sec3b_cari_maskeleme.py` maskelemenin TAMAMINI
ölçüyor (birim testler + beş rollük matris + arama orakülü + kiracı
yalıtımı). Bu dosya onu TEKRARLAMIYOR; yalnız GELİŞTİRME DİYALEKTİNDE
GÖRÜNMEYEN kısmı ölçüyor.

--- BU İKİZ NEDEN VAR ------------------------------------------------------

1. **`SELECT *` SÜTUN KÜMESİ LEHÇEYE GÖRE DEĞİŞİR.** Cari kartı
   (`entity_detail._entity_row`) ve ekstre başlığı (`statement._entity_row`)
   cari satırını `SELECT *` ile çekiyor -- alan adları kaynakta HİÇ
   GEÇMİYOR. Maskeleme alan ADIYLA çalıştığı için, sütun adları iki lehçede
   ayrışsaydı maskeleme PostgreSQL'de SESSİZCE kapanırdı: hata yok, sadece
   ham VKN. SQLite ikizi bunu ölçemez çünkü orada sütun kümesi zaten
   uyuyor.

2. **`is_active` GERÇEKTEN BOOLEAN.** PostgreSQL'de `customers.is_active`
   boolean; SQLite'ta 1/0. Liste ucu bu sütunu `CASE WHEN ... THEN 1 ELSE 0`
   ile normalleştiriyor ve maskeleme AYNI sözlükten geçiyor. Satırın
   maskelemeden bozulmadan çıktığı gerçek tiplerle ölçülüyor.

3. **BOOLEAN `true` ile YAZILAN KİRACI SATIRI.** İkiz, cariyi doğrudan SQL
   ile açıyor (`is_active` -> `true`); SQLite'ta `1` yazan bir kopya burada
   tip hatası verirdi ve o hata ancak PostgreSQL'de görünür.

--- NE ÖLÇÜLMÜYOR ----------------------------------------------------------

Maskeleme fonksiyonlarının kendisi SAF PYTHON'dur ve lehçeden bağımsızdır;
birim testleri burada TEKRARLANMIYOR. Bu dosyanın tek sorusu: "gerçek
PostgreSQL satırı maskeleme dikişinden geçince rol matrisi aynı mı?"
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent

FIRMA_ADI = "SEC3B İKİZİ firması"

HAM_TELEFON = "05551234512"
HAM_EPOSTA = "ahmet@ornek.com"
HAM_VKN = "1234567890"
HAM_ADRES = "Atatürk Caddesi No 5 Daire 3\nKadıköy/İstanbul"

MASKE_TELEFON = "05** *** ** 12"
MASKE_EPOSTA = "a***@ornek.com"
MASKE_VKN = "*******890"
MASKE_ADRES = "Atatürk Caddesi No 5 Dai…"

HAM_DEGERLER = (HAM_TELEFON, HAM_EPOSTA, HAM_VKN, "Atatürk Caddesi No 5 Daire 3")

ROL_PAROLASI = "Sec3bIkiz!2026"
MASKELI_ROLLER = ("depo", "rapor")
MASKESIZ_ROLLER = ("yonetici", "muhasebe")


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("SEC-3b ikizi APP_TEST_DATABASE_URL ister")
    return url


@pytest.fixture(scope="module")
def ortam():
    """Şema + temiz kiracı + cari + beş rol; uygulama PostgreSQL'e bağlı."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    url = _url()
    os.environ["DATABASE_URL"] = url
    os.environ["AUTO_MIGRATE"] = "false"

    config = Config(str(BACKEND / "alembic.ini"))
    command.upgrade(config, "head")
    engine = create_engine(url)

    def temizle(baglanti):
        for deyim in (
            "DELETE FROM user_company_memberships WHERE company_id IN "
            "(SELECT id FROM companies WHERE name=:a)",
            "DELETE FROM app_users WHERE username LIKE 'sec3bpg_%'",
            "DELETE FROM customers WHERE company_id IN "
            "(SELECT id FROM companies WHERE name=:a)",
            "DELETE FROM suppliers WHERE company_id IN "
            "(SELECT id FROM companies WHERE name=:a)",
            "DELETE FROM companies WHERE name=:a",
        ):
            baglanti.execute(text(deyim), {"a": FIRMA_ADI})

    with engine.begin() as baglanti:
        temizle(baglanti)

    from app.auth import hash_password

    simdi = datetime.now(timezone.utc)
    with engine.begin() as baglanti:
        cid = baglanti.execute(
            text(
                "INSERT INTO companies(name,is_active,created_at)"
                " VALUES(:n,true,:t) RETURNING id"
            ),
            {"n": FIRMA_ADI, "t": simdi},
        ).scalar_one()
        kimlikler = {}
        for tablo in ("customers", "suppliers"):
            # ``is_active`` GERÇEKTEN boolean: ``true`` yazılıyor, ``1`` değil.
            kimlikler[tablo] = baglanti.execute(
                text(
                    f"INSERT INTO {tablo}(name,phone,email,address,tax_number,"
                    "opening_balance,risk_limit,payment_term_days,is_active,"
                    "company_id) VALUES(:n,:p,:e,:a,:v,0,0,0,true,:c)"
                    " RETURNING id"
                ),
                {
                    "n": f"SEC3B İKİZ {tablo}", "p": HAM_TELEFON,
                    "e": HAM_EPOSTA, "a": HAM_ADRES, "v": HAM_VKN, "c": cid,
                },
            ).scalar_one()
        for rol in MASKELI_ROLLER + MASKESIZ_ROLLER:
            uid = baglanti.execute(
                text(
                    "INSERT INTO app_users(username,email,display_name,"
                    "password_hash,role,is_active,must_change_password,"
                    "email_verified,created_at)"
                    " VALUES(:k,:e,:d,:p,:r,true,false,true,:t) RETURNING id"
                ),
                {
                    "k": f"sec3bpg_{rol}", "e": f"sec3bpg_{rol}@ornek.test",
                    "d": f"SEC3BPG {rol}", "p": hash_password(ROL_PAROLASI),
                    "r": rol, "t": simdi,
                },
            ).scalar_one()
            baglanti.execute(
                text(
                    "INSERT INTO user_company_memberships"
                    "(user_id,company_id,is_default,created_at)"
                    " VALUES(:u,:c,true,:t)"
                ),
                {"u": uid, "c": cid, "t": simdi},
            )

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as istemci:
        basliklar = {}
        for rol in MASKELI_ROLLER + MASKESIZ_ROLLER:
            giris = istemci.post(
                "/api/auth/login",
                json={"username": f"sec3bpg_{rol}", "password": ROL_PAROLASI},
            )
            assert giris.status_code == 200, (rol, giris.text)
            basliklar[rol] = {
                "Authorization": "Bearer " + giris.json()["access_token"],
                "X-Company-ID": str(cid),
            }
        yield {
            "istemci": istemci,
            "basliklar": basliklar,
            "musteri_id": kimlikler["customers"],
            "tedarikci_id": kimlikler["suppliers"],
        }

    with engine.begin() as baglanti:
        temizle(baglanti)
    engine.dispose()


@pytest.mark.parametrize("rol", MASKELI_ROLLER)
def test_maskeli_rol_musteri_listesi_pg(ortam, rol):
    """Gerçek PostgreSQL satırı, maskeli rol: ham değer YOK, maske YERİNDE."""
    yanit = ortam["istemci"].get("/api/customers", headers=ortam["basliklar"][rol])
    assert yanit.status_code == 200, yanit.text
    satir = next(s for s in yanit.json() if s["id"] == ortam["musteri_id"])
    assert satir["phone"] == MASKE_TELEFON
    assert satir["email"] == MASKE_EPOSTA
    assert satir["tax_number"] == MASKE_VKN
    assert satir["address"] == MASKE_ADRES
    for ham in HAM_DEGERLER:
        assert ham not in yanit.text


@pytest.mark.parametrize("rol", MASKELI_ROLLER)
def test_maskeli_rol_cari_karti_pg(ortam, rol):
    """`SELECT *` ile gelen sütun kümesi PostgreSQL'de de maskeden geçiyor.

    Bu ikizin ASIL sorusu bu: sütun adları lehçeye göre ayrışsaydı maskeleme
    burada sessizce kapanırdı.
    """
    yanit = ortam["istemci"].get(
        f"/api/customers/{ortam['musteri_id']}", headers=ortam["basliklar"][rol]
    )
    assert yanit.status_code == 200, yanit.text
    govde = yanit.json()
    for anahtar in ("customer", "entity"):
        assert govde[anahtar]["tax_number"] == MASKE_VKN, anahtar
        assert govde[anahtar]["phone"] == MASKE_TELEFON, anahtar
        assert govde[anahtar]["address"] == MASKE_ADRES, anahtar
    for ham in HAM_DEGERLER:
        assert ham not in yanit.text


def test_depo_tedarikci_ekstresi_pg(ortam):
    """`depo` `purchases` taşır: 200 alır ve MASKELİ görür (gerçek PostgreSQL)."""
    yanit = ortam["istemci"].get(
        f"/api/suppliers/{ortam['tedarikci_id']}/statement",
        headers=ortam["basliklar"]["depo"],
    )
    assert yanit.status_code == 200, yanit.text
    entity = yanit.json()["entity"]
    assert entity["tax_number"] == MASKE_VKN
    assert entity["phone"] == MASKE_TELEFON
    assert entity["address"] == MASKE_ADRES


@pytest.mark.parametrize("rol", MASKESIZ_ROLLER)
def test_maskesiz_rol_tam_veri_pg(ortam, rol):
    """Karşı hücre: maskesiz roller gerçek PostgreSQL'de HAM değeri görür."""
    yanit = ortam["istemci"].get("/api/customers", headers=ortam["basliklar"][rol])
    assert yanit.status_code == 200, yanit.text
    satir = next(s for s in yanit.json() if s["id"] == ortam["musteri_id"])
    assert satir["phone"] == HAM_TELEFON
    assert satir["tax_number"] == HAM_VKN
    assert satir["email"] == HAM_EPOSTA
