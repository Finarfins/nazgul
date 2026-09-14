"""PostgreSQL ikizi: H51 — `GET /api/despatch-notes` süzgeçsiz ve offset tavanı.

KUSUR (#127 çalışma zamanı merceği ölçtü, taban `0868b0d`de YENİDEN
ÜRETİLDİ; E4a `1143025`ten beri var):

* `invoice_id` VERİLMEYEN her liste isteği PG'de 500:
  `(psycopg.errors.AmbiguousParameter) could not determine data type of
  parameter $2`. psycopg3 `None`ı tipsiz gönderiyor ve PG
  `(:invoice_id IS NULL OR invoice_id=:invoice_id)` içindeki parametrenin
  tipini çıkaramıyor.
* `offset >= 2^63` PG'de 500: `(psycopg.errors.NumericValueOutOfRange)
  bigint out of range`.

İKİSİ DE SQLite'ta GÖRÜNMEZ — SQLite tipsiz NULL'u sorunsuz karşılaştırır ve
kusur tam bu yüzden yaşadı. SQLite tarafındaki kapılar
(`tests/test_e4a_despatch_notes.py`) 422 sözleşmesini ölçer; ASIL kanıt bu
dosyadır.

Ölçülen dört şey:
1. Süzgeçsiz liste 200 döner ve YALNIZ kendi firmasının irsaliyelerini taşır.
2. COUNT ile sayfa sorgusu `total` üzerinde ANLAŞIR (iki sorgu da aynı tipli
   parametreyi taşıyor; biri tipli biri tipsiz kalsaydı biri 500 verirdi).
3. Kiracı kapsamı: B firmasının irsaliyeleri ne listelenir ne sayılır —
   süzgeçli ve süzgeçsiz iki yolda da.
4. Uçtaki sınırlar (`INT4_UST`): tipli `::INTEGER` bağı int4 dışındaki
   `invoice_id`yi `integer out of range` ile 500 yapıyordu (PR'ın ilk
   hâlinde ÖLÇÜLDÜ: 2147483648, -2147483649, 2^63). Artık `invoice_id`
   1..INT4_UST, `offset` 0..INT4_UST; dışı 422, sürücüye hiç ulaşmaz.

TEMİZLİK: KENDİ satırlarını önekle siler, tablo SÜPÜRMEZ (paylaşık şemada
arkada kalan satır komşu dosyayı kırar — ölçülmüş hata sınıfı).
`activity_logs` yalnız-eklemedir; API girişi bir iz bırakırsa firma ya da
kullanıcı silinemez ve pasife alınır (CS1/CS2 ikizlerinin gerekçesi).
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

BACKEND = Path(__file__).resolve().parent

pytestmark = pytest.mark.postgresql

KOSU = uuid4().hex[:8]
ONEK = f"H51-{KOSU}"
PAROLA = "H51Liste!2026xyz"
#: A firmasına yazılan irsaliye sayısı — `limit=1` ile sayfalanır.
A_ADEDI = 3
B_ADEDI = 2


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("H51 ikizi APP_TEST_DATABASE_URL ister")
    return url


def _config(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _acilisi_kostur() -> None:
    """Açılış verisi firmalar yazılmadan ÖNCE (CS1 ikizinin gerekçesi)."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app):
        pass


def _temizle(engine) -> None:
    """SIRA ÖNEMLİ: satır -> irsaliye -> fatura -> üyelik -> kullanıcı -> firma."""
    with engine.begin() as c:
        firmalar = [r[0] for r in c.execute(
            text("SELECT id FROM companies WHERE name LIKE :o"), {"o": ONEK + "%"})]
        kullanicilar = [r[0] for r in c.execute(
            text("SELECT id FROM app_users WHERE username LIKE :o"), {"o": ONEK.lower() + "%"})]
    for cid in firmalar:
        with engine.begin() as c:
            for tablo in ("despatch_lines", "despatch_notes", "invoices", "user_company_memberships"):
                c.execute(text(f"DELETE FROM {tablo} WHERE company_id=:c"), {"c": cid})
    for uid in kullanicilar:
        try:
            with engine.begin() as c:
                c.execute(text("DELETE FROM user_company_memberships WHERE user_id=:u"), {"u": uid})
                c.execute(text("DELETE FROM app_users WHERE id=:u"), {"u": uid})
        except IntegrityError:
            with engine.begin() as c:
                c.execute(text("UPDATE app_users SET is_active=false WHERE id=:u"), {"u": uid})
    for cid in firmalar:
        try:
            with engine.begin() as c:
                c.execute(text("DELETE FROM companies WHERE id=:c"), {"c": cid})
        except IntegrityError:
            with engine.begin() as c:
                c.execute(text("UPDATE companies SET is_active=false WHERE id=:c"), {"c": cid})


@pytest.fixture(scope="module")
def motor():
    url = _url()
    command.upgrade(_config(url), "head")
    _acilisi_kostur()
    engine = create_engine(url)
    _temizle(engine)
    try:
        yield engine
    finally:
        _temizle(engine)
        engine.dispose()


_OZET: dict[str, str] = {}


def _parola_ozeti() -> str:
    if "p" not in _OZET:
        from app.auth import hash_password

        _OZET["p"] = hash_password(PAROLA)
    return _OZET["p"]


def _firma(c, ek: str, adet: int) -> dict:
    """Bir firma + üye bir kullanıcı + tek fatura + ``adet`` irsaliye."""
    an = datetime.now(timezone.utc)
    kullanici = f"{ONEK.lower()}-{ek.lower()}"
    cid = c.execute(text(
        "INSERT INTO companies(name,is_active,created_at) VALUES(:n,true,:t) RETURNING id"),
        {"n": f"{ONEK}-{ek}", "t": an}).scalar_one()
    uid = c.execute(text(
        "INSERT INTO app_users(username,email,email_verified,display_name,password_hash,role,"
        "is_active,must_change_password,created_at) VALUES(:k,:e,true,'H51',:p,'admin',true,false,:t)"
        " RETURNING id"),
        {"k": kullanici, "e": f"{kullanici}@ornek.test", "p": _parola_ozeti(), "t": an}).scalar_one()
    c.execute(text("INSERT INTO user_company_memberships(user_id,company_id,is_default,created_at) "
                   "VALUES(:u,:c,true,:t)"), {"u": uid, "c": cid, "t": an})
    fatura = c.execute(text(
        "INSERT INTO invoices(company_id,invoice_number,invoice_type,status,currency,exchange_rate,"
        "customer_snapshot,machine_snapshot,work_order_snapshot,company_snapshot,technician_snapshot,"
        "warranty_snapshot,totals_snapshot,tax_snapshot,created_by,created_at,updated_at)"
        " VALUES(:c,:n,'INVOICE','ISSUED','TRY',1,'{}','{}','{}','{}','{}','{}','{}','{}',:u,:t,:t)"
        " RETURNING id"),
        {"c": cid, "n": f"{ONEK}-{ek}-FTR", "u": uid, "t": an}).scalar_one()
    irsaliyeler = []
    for sira in range(adet):
        irsaliyeler.append(c.execute(text(
            "INSERT INTO despatch_notes(company_id,invoice_id,despatch_uuid,despatch_number,issue_date,"
            "actual_shipment_at,driver_name,driver_national_id,vehicle_plate,delivery_address,"
            "delivery_postal_code,edespatch_status,created_at,updated_at)"
            " VALUES(:c,:f,:u,:n,:d,:t,'Ahmet Yilmaz','11111111110','34ABC123','Depo Yolu 7','34000',"
            "'NONE',:t,:t)"
            " RETURNING id"),
            {"c": cid, "f": fatura, "u": str(uuid4()), "n": f"H{KOSU}{ek}{sira:06d}",
             "d": date(2026, 9, 1 + sira), "t": an}).scalar_one())
    return {"cid": cid, "kullanici": kullanici, "fatura": fatura, "irsaliyeler": irsaliyeler}


@pytest.fixture(scope="module")
def iki_firma(motor):
    with motor.begin() as c:
        return _firma(c, "A", A_ADEDI), _firma(c, "B", B_ADEDI)


@pytest.fixture(scope="module")
def istemci(iki_firma):
    """A firmasının kullanıcısıyla GERÇEK giriş. 500'ler istisna değil yanıt olarak
    görünür — iddia durum koduna bakar."""
    from fastapi.testclient import TestClient

    from app.main import app

    a, _ = iki_firma
    with TestClient(app, raise_server_exceptions=False) as client:
        giris = client.post("/api/auth/login", json={"username": a["kullanici"], "password": PAROLA})
        assert giris.status_code == 200, giris.text
        client.cookies.clear()
        client.headers.update({
            "Authorization": "Bearer " + giris.json()["access_token"],
            "X-Company-ID": str(a["cid"]),
        })
        yield client


def test_SUZGECSIZ_liste_PGde_200_ve_yalniz_kendi_firmasi(iki_firma, istemci) -> None:
    """Tabanda 500 (`AmbiguousParameter`). MUTASYON: `.bindparams(tipli_fatura)`ı
    iki sorgudan birinden silmek bunu KIRMIZI yapar."""
    a, b = iki_firma
    yanit = istemci.get("/api/despatch-notes?limit=200")
    assert yanit.status_code == 200, yanit.text
    govde = yanit.json()
    kimlikler = {x["id"] for x in govde["items"]}
    assert not kimlikler & set(b["irsaliyeler"]), "B firmasının irsaliyesi listelendi"
    # A firması bu koşuya ait ve taze: süzgeçsiz liste TAM OLARAK onları taşır.
    assert kimlikler == set(a["irsaliyeler"])
    assert govde["total"] == A_ADEDI


def test_COUNT_ve_SAYFA_total_uzerinde_ANLASIR(iki_firma, istemci) -> None:
    """`total` COUNT'tan gelir; `limit=1` sayfalarının birleşimi ona eşittir."""
    a, _ = iki_firma
    gorulen: list[int] = []
    toplamlar = set()
    for offset in range(A_ADEDI + 1):
        yanit = istemci.get(f"/api/despatch-notes?limit=1&offset={offset}")
        assert yanit.status_code == 200, yanit.text
        govde = yanit.json()
        toplamlar.add(govde["total"])
        gorulen.extend(x["id"] for x in govde["items"])
    assert toplamlar == {A_ADEDI}
    assert len(gorulen) == A_ADEDI and set(gorulen) == set(a["irsaliyeler"])


def test_SUZGECLI_liste_de_calisir_ve_KIRACI_kapsamli(iki_firma, istemci) -> None:
    """Süzgeçli yol tabanda da 200'dü; tipli bağlama onu BOZMAMALI.

    B'nin faturası A'nın oturumuyla sorulunca boş ve sıfır döner — sayılmaz."""
    a, b = iki_firma
    kendi = istemci.get(f"/api/despatch-notes?invoice_id={a['fatura']}&limit=200")
    assert kendi.status_code == 200, kendi.text
    assert kendi.json()["total"] == A_ADEDI
    assert {x["id"] for x in kendi.json()["items"]} == set(a["irsaliyeler"])

    yabanci = istemci.get(f"/api/despatch-notes?invoice_id={b['fatura']}")
    assert yabanci.status_code == 200, yabanci.text
    assert yabanci.json() == {"items": [], "total": 0}


@pytest.mark.parametrize("sorgu", ["", "invoice_id={fatura}&"])
@pytest.mark.parametrize("tasan_offset", [2**31, 2**63])
def test_OFFSET_TAVANI_INT4_UST_422_sinirda_200(iki_firma, istemci, sorgu, tasan_offset) -> None:
    """Tabanda 2^63 -> 500 (`bigint out of range`). Tavan `INT4_UST`: 2^31 ve 2^63
    422, INT4_UST boş sayfa. MUTASYON: `le=INT4_UST`yi silmek 2^63 dalını 500'e
    döndürür; tavanı 2^63-1'e geri almak 2^31 dalını 200'e döndürür."""
    from app.routers.cek_senetler import INT4_UST

    a, _ = iki_firma
    on = sorgu.format(fatura=a["fatura"])
    tasan = istemci.get(f"/api/despatch-notes?{on}offset={tasan_offset}")
    assert tasan.status_code != 500, tasan.text
    assert tasan.status_code == 422, tasan.text
    assert tasan.json()["detail"][0]["loc"] == ["query", "offset"]

    sinir = istemci.get(f"/api/despatch-notes?{on}offset={INT4_UST}")
    assert sinir.status_code == 200, sinir.text
    assert sinir.json() == {"items": [], "total": A_ADEDI}


@pytest.mark.parametrize("fatura", [2147483648, -2147483649, 2**63, 0])
def test_INVOICE_ID_int4_DISI_422_500_DEGIL(istemci, fatura) -> None:
    """PR'ın ilk hâlinde ÖLÇÜLDÜ: tipli `::INTEGER` bağı 2147483648, -2147483649
    ve 2^63'ü `integer out of range` ile 500 yapıyordu (tabanın tipsiz bağı
    200 boş sayfa veriyordu). Beklenen artık 422 — ve 500 OLMADIĞI AYRICA
    iddia ediliyor: yalnız "422 değilse kırmızı" diyen bir iddia, sınır
    kaldırılınca 500'ü de 200'ü de aynı mesajla boğardı. `0` `ge=1`in dalı.
    MUTASYON: `invoice_id`nin `Query(ge=1, le=INT4_UST)`sını silmek İLK ÜÇ
    değeri 500'e, `0`ı 200'e döndürür."""
    yanit = istemci.get(f"/api/despatch-notes?invoice_id={fatura}")
    assert yanit.status_code != 500, f"sürücü taşması uca sızdı: {yanit.text}"
    assert yanit.status_code == 422, yanit.text
    assert yanit.json()["detail"][0]["loc"] == ["query", "invoice_id"]


def test_INVOICE_ID_sinirda_INT4_UST_200(istemci) -> None:
    """Sınırın kendisi geçerli: yabancı/olmayan fatura boş sayfa, 500 değil."""
    from app.routers.cek_senetler import INT4_UST

    yanit = istemci.get(f"/api/despatch-notes?invoice_id={INT4_UST}")
    assert yanit.status_code == 200, yanit.text
    assert yanit.json() == {"items": [], "total": 0}
