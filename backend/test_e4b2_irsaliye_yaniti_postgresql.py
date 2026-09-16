"""PostgreSQL ikizi: göç `20260915_0089` (E4b-2 e-İrsaliye yanıtı) GERÇEK PG 16'da.

SQLite ikizi (`tests/test_e4b2_irsaliye_yaniti.py`) ayrıştırıcıyı, durum
makinesini, sync'i ve uçları ölçüyor. Bu dosya DÖRT şeyi ölçüyor ve dördü de
YALNIZ burada tam görünür:

1. **ÜÇ BİLEŞİK FK çapraz firmayı REDDEDİYOR**: yanıt -> irsaliye, yanıt satırı
   -> yanıt, yanıt satırı -> sevk satırı.
2. **CHECK'ler ve Numeric(18,4) gerçek**; genişleyen `ck_despatch_notes_durum`
   yanıt durumlarını kabul ediyor, özet CHECK'i uydurma türü reddediyor.
3. **İdempotens yarışı iki oturumla**: birinci oturum yanıtı yazıp işlemi AÇIK
   tutarken ikinci oturumun `yaniti_kaydet`i UNIQUE indeksinde BEKLER, birinci
   bitince `uq_despatch_responses_uuid` ihlaline çarpar ve bunu `False` olarak
   ELE ALIR — istisna yok, kopya yok, ikinci oturumun işlemi ayakta.
   MUTASYON: göçten `uq_despatch_responses_uuid`i kaldırmak bunu KIRMIZI yapar
   (ikinci oturum beklemeden yazar, iki satır olur). SQLite bunu ÖLÇEMEZ:
   veritabanı kilidi ikinci yazıcıyı UNIQUE'e hiç ulaştırmaz.
4. **up -> down -> up ve geri alma reddi**: yanıt durumunda bir irsaliye
   varken downgrade ADIYLA durur; daraltılmış CHECK geri kurulunca yanıt
   durumu GERÇEKTEN reddedilir.

TEMİZLİK: yalnız KENDİ satırlarını (firma adı öneki) siler, tablo SÜPÜRMEZ.
Bu dosya yanıt durumunda irsaliye yazar; arkada kalırsa KOMŞU bir ikizin
downgrade'i "ticari yanit durumunda e-Irsaliye var" ile düşerdi.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

BACKEND = Path(__file__).resolve().parent
FIXTURES = BACKEND / "tests" / "fixtures" / "e4b2"

pytestmark = pytest.mark.postgresql

KOSU = uuid4().hex[:8]
GOC = "20260915_0089"
ONCEKI = "20260915_0088"
#: ZİNCİRİN BAŞI — `GOC`tan AYRI (gerekçe `test_e1_efatura_sertlestirme_postgresql.py`).
BAS = "20260915_0089"

IRSALIYE = "despatch_notes"
SATIR = "despatch_lines"
YANIT = "despatch_responses"
YANIT_SATIR = "despatch_response_lines"
SOFOR_TCKN = "11111111110"


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("E4b-2 ikizi APP_TEST_DATABASE_URL ister")
    return url


def _temizle(engine) -> None:
    """SIRA: yanıt satırı -> yanıt -> sevk satırı -> irsaliye -> kalem -> fatura
    -> sayaç -> firma -> kullanıcı."""
    onek = {"onek": f"{KOSU}%"}
    firmalar = "(SELECT id FROM companies WHERE name LIKE :onek)"
    with engine.begin() as b:
        d = inspect(b)
        for tablo in (YANIT_SATIR, YANIT, SATIR, IRSALIYE):
            if d.has_table(tablo):
                b.execute(text(f"DELETE FROM {tablo} WHERE company_id IN {firmalar}"), onek)
        b.execute(text(f"DELETE FROM invoice_items WHERE company_id IN {firmalar}"), onek)
        b.execute(text(f"DELETE FROM invoices WHERE company_id IN {firmalar}"), onek)
        b.execute(text(f"DELETE FROM document_sequences WHERE company_id IN {firmalar}"), onek)
        b.execute(text("DELETE FROM companies WHERE name LIKE :onek"), onek)
        b.execute(text("DELETE FROM app_users WHERE username LIKE :onek"), onek)


@pytest.fixture()
def motor():
    yapilandirma = Config(str(BACKEND / "alembic.ini"))
    engine = create_engine(_url())
    command.upgrade(yapilandirma, "head")
    _temizle(engine)
    try:
        yield engine
    finally:
        _temizle(engine)
        command.upgrade(yapilandirma, "head")
        engine.dispose()


_SAYAC = {"n": 0}


def _irsaliyeli_firma(motor, durum: str = "DELIVERED") -> dict:
    """Firma + fatura (iki PART kalemi) + iki satırlı irsaliye."""
    _SAYAC["n"] += 1
    n = _SAYAC["n"]
    an = datetime.now(timezone.utc)
    with motor.begin() as b:
        firma = b.execute(
            text("INSERT INTO companies(name,is_active,created_at) VALUES(:a,true,:t) RETURNING id"),
            {"a": f"{KOSU} firma {n}", "t": an},
        ).scalar_one()
        kullanici = b.execute(
            text(
                "INSERT INTO app_users(username,email,email_verified,display_name,password_hash,"
                "role,is_active,must_change_password,created_at)"
                " VALUES(:k,:e,true,'d','x','admin',true,false,:t) RETURNING id"
            ),
            {"k": f"{KOSU}_k_{n}", "e": f"{KOSU}_{n}@ornek.test", "t": an},
        ).scalar_one()
        fatura = b.execute(
            text(
                "INSERT INTO invoices(company_id,invoice_number,invoice_type,status,currency,"
                "exchange_rate,customer_snapshot,machine_snapshot,work_order_snapshot,"
                "company_snapshot,technician_snapshot,warranty_snapshot,totals_snapshot,"
                "tax_snapshot,created_by,created_at,updated_at) VALUES(:c,:no,'INVOICE',"
                "'ISSUED','TRY',1,'{}','{}','{}','{}','{}','{}','{}','{}',:u,:t,:t) RETURNING id"
            ),
            {"c": firma, "no": f"{KOSU}-{n}", "u": kullanici, "t": an},
        ).scalar_one()
        kalemler = [
            b.execute(
                text(
                    "INSERT INTO invoice_items(company_id,invoice_id,item_type,description,"
                    "quantity,unit_price,original_price,discount_amount,tax_rate,tax_amount,"
                    "total,warranty_percent,customer_payable,company_payable,source_snapshot) "
                    "VALUES(:c,:f,'PART',:ad,:m,1,1,0,0,0,1,0,1,0,:k) RETURNING id"
                ),
                {"c": firma, "f": fatura, "ad": ad, "m": Decimal(m), "k": json.dumps({"product_id": 55})},
            ).scalar_one()
            for ad, m in (("Bugday", "10"), ("Arpa", "5"))
        ]
        ettn = str(uuid4())
        numara = f"IRS2026{n + 700000000:09d}"
        irsaliye = b.execute(
            text(
                f"INSERT INTO {IRSALIYE}(company_id,invoice_id,despatch_uuid,despatch_number,"
                "issue_date,actual_shipment_at,driver_name,driver_national_id,vehicle_plate,"
                "delivery_address,delivery_postal_code,edespatch_status,created_at,updated_at) "
                "VALUES(:c,:f,:u,:no,:d,:t,'A',:tc,'34ABC123','Adres','34000',:s,:t,:t) "
                "RETURNING id"
            ),
            {"c": firma, "f": fatura, "u": ettn, "no": numara, "d": date(2026, 9, 15),
             "t": an, "tc": SOFOR_TCKN, "s": durum},
        ).scalar_one()
        satirlar = [
            b.execute(
                text(
                    f"INSERT INTO {SATIR}(company_id,despatch_id,invoice_item_id,line_no,item_name,"
                    "quantity,created_at,updated_at) VALUES(:c,:d,:k,:no,:ad,:m,:t,:t) RETURNING id"
                ),
                {"c": firma, "d": irsaliye, "k": kalem, "no": no, "ad": ad, "m": Decimal(m), "t": an},
            ).scalar_one()
            for no, (kalem, ad, m) in enumerate(
                ((kalemler[0], "Bugday", "10"), (kalemler[1], "Arpa", "5")), start=1
            )
        ]
    return {
        "firma": int(firma), "irsaliye": int(irsaliye), "ettn": ettn, "numara": numara,
        "satirlar": [int(s) for s in satirlar],
    }


def _yanit_ekle(b, firma: int, irsaliye: int, tur: str = "KABUL") -> int:
    return int(
        b.execute(
            text(
                f"INSERT INTO {YANIT}(company_id,despatch_id,response_uuid,response_number,"
                "response_type,issue_date,raw_xml,created_at) VALUES(:c,:d,:u,'ALC2026000000001',"
                ":tur,:g,'<ReceiptAdvice/>',:t) RETURNING id"
            ),
            {"c": firma, "d": irsaliye, "u": str(uuid4()), "tur": tur, "g": date(2026, 9, 16),
             "t": datetime.now(timezone.utc)},
        ).scalar_one()
    )


def _yanit_satiri_ekle(b, firma: int, yanit: int, sevk_satiri: int, alinan="1", reddedilen="0") -> None:
    b.execute(
        text(
            f"INSERT INTO {YANIT_SATIR}(company_id,response_id,despatch_line_id,"
            "received_quantity,rejected_quantity) VALUES(:c,:r,:s,:a,:rd)"
        ),
        {"c": firma, "r": yanit, "s": sevk_satiri, "a": Decimal(alinan), "rd": Decimal(reddedilen)},
    )


# ==========================================================================
# 1. ŞEMA
# ==========================================================================

def test_SEMA_BASI_0089_ve_KISITLAR(motor) -> None:
    with motor.connect() as b:
        assert b.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == BAS
    d = inspect(motor)
    assert {u["name"] for u in d.get_unique_constraints(YANIT)} == {
        "uq_despatch_responses_uuid", "uq_despatch_responses_company_id",
    }
    assert {u["name"] for u in d.get_unique_constraints(YANIT_SATIR)} == {
        "uq_despatch_response_lines_response_line",
    }
    assert {c["name"] for c in d.get_check_constraints(YANIT_SATIR)} == {
        "ck_despatch_response_lines_received_nonneg",
        "ck_despatch_response_lines_rejected_nonneg",
        "ck_despatch_response_lines_total_positive",
    }
    fkler = {
        f["name"]: (f["constrained_columns"], f["referred_table"], f["referred_columns"])
        for tablo in (YANIT, YANIT_SATIR)
        for f in d.get_foreign_keys(tablo)
        if f["name"] and "same_company" in f["name"]
    }
    assert fkler == {
        "fk_despatch_responses_despatch_same_company":
            (["company_id", "despatch_id"], IRSALIYE, ["company_id", "id"]),
        "fk_despatch_response_lines_response_same_company":
            (["company_id", "response_id"], YANIT, ["company_id", "id"]),
        "fk_despatch_response_lines_despatch_line_same_company":
            (["company_id", "despatch_line_id"], SATIR, ["company_id", "id"]),
    }
    for sutun in ("received_quantity", "rejected_quantity"):
        tip = next(c for c in d.get_columns(YANIT_SATIR) if c["name"] == sutun)["type"]
        assert (tip.precision, tip.scale) == (18, 4)
    # 0083'ün kısıtları yerinde; durum CHECK'i genişledi.
    assert {c["name"] for c in d.get_check_constraints(IRSALIYE)} == {
        "ck_despatch_notes_tasima", "ck_despatch_notes_durum", "ck_despatch_notes_response_status",
    }
    assert {u["name"] for u in d.get_unique_constraints(IRSALIYE)} == {
        "uq_despatch_notes_uuid", "uq_despatch_notes_company_id",
    }
    assert "ix_despatch_notes_company_issue" in {i["name"] for i in d.get_indexes(IRSALIYE)}


def test_DURUM_ve_OZET_CHECKleri(motor) -> None:
    a = _irsaliyeli_firma(motor)
    for durum in ("ACCEPTED", "PARTIALLY_ACCEPTED", "REJECTED"):
        with motor.begin() as b:
            b.execute(
                text(f"UPDATE {IRSALIYE} SET edespatch_status=:s, response_status='KABUL' WHERE id=:i"),
                {"s": durum, "i": a["irsaliye"]},
            )
    with pytest.raises(IntegrityError):
        with motor.begin() as b:
            b.execute(text(f"UPDATE {IRSALIYE} SET edespatch_status='KABUL' WHERE id=:i"), {"i": a["irsaliye"]})
    with pytest.raises(IntegrityError):
        with motor.begin() as b:
            b.execute(text(f"UPDATE {IRSALIYE} SET response_status='ACCEPTED' WHERE id=:i"), {"i": a["irsaliye"]})
    with pytest.raises(IntegrityError):
        with motor.begin() as b:
            _yanit_ekle(b, a["firma"], a["irsaliye"], tur="PARTIAL")


# ==========================================================================
# 2. BİLEŞİK FK'LER ve CHECK'ler REDDEDİYOR
# ==========================================================================

def test_BILESIK_FK_capraz_firma_RED_ayni_firma_KABUL(motor) -> None:
    """MUTASYON: üç bileşik FK'den birini çıplak `*_id -> tablo.id` FK'sine
    çevirmek ilgili dalı KIRMIZI yapar."""
    a = _irsaliyeli_firma(motor)
    b_ = _irsaliyeli_firma(motor)
    # Yanıt: B firması + A'nın irsaliyesi.
    with pytest.raises(IntegrityError):
        with motor.begin() as b:
            _yanit_ekle(b, b_["firma"], a["irsaliye"])
    with motor.begin() as b:
        yanit_a = _yanit_ekle(b, a["firma"], a["irsaliye"])
        yanit_b = _yanit_ekle(b, b_["firma"], b_["irsaliye"])
    # Yanıt satırı: A firması + B'nin yanıtı.
    with pytest.raises(IntegrityError):
        with motor.begin() as b:
            _yanit_satiri_ekle(b, a["firma"], yanit_b, a["satirlar"][0])
    # Yanıt satırı: A firması + A'nın yanıtı + B'nin sevk satırı.
    with pytest.raises(IntegrityError):
        with motor.begin() as b:
            _yanit_satiri_ekle(b, a["firma"], yanit_a, b_["satirlar"][0])
    with motor.begin() as b:
        _yanit_satiri_ekle(b, a["firma"], yanit_a, a["satirlar"][0])


def test_CHECK_miktarlar_ve_TEKIL_satir(motor) -> None:
    a = _irsaliyeli_firma(motor)
    with motor.begin() as b:
        yanit = _yanit_ekle(b, a["firma"], a["irsaliye"])
    for alinan, reddedilen in (("0", "0"), ("-1", "2"), ("2", "-1")):
        with pytest.raises(IntegrityError):
            with motor.begin() as b:
                _yanit_satiri_ekle(b, a["firma"], yanit, a["satirlar"][0], alinan, reddedilen)
    with motor.begin() as b:
        _yanit_satiri_ekle(b, a["firma"], yanit, a["satirlar"][0], "3.7512", "1.2488")
    with pytest.raises(IntegrityError):
        with motor.begin() as b:
            _yanit_satiri_ekle(b, a["firma"], yanit, a["satirlar"][0], "1", "0")
    with motor.connect() as b:
        assert tuple(
            b.execute(
                text(f"SELECT received_quantity, rejected_quantity FROM {YANIT_SATIR} WHERE response_id=:r"),
                {"r": yanit},
            ).one()
        ) == (Decimal("3.7512"), Decimal("1.2488"))


# ==========================================================================
# 3. İDEMPOTENS YARIŞI — iki oturum
# ==========================================================================

def _belge(a: dict):
    from app.einvoice import edespatch

    ham = (
        (FIXTURES / "kismi_kabul.xml").read_text(encoding="utf-8")
        .replace("{{YANIT_ETTN}}", str(uuid4()))
        .replace("{{IRSALIYE_NO}}", a["numara"])
        .replace("{{IRSALIYE_ETTN}}", a["ettn"])
        .encode("utf-8")
    )
    belge = edespatch.receipt_advice_coz(ham)
    eslesen = [(s, a["satirlar"][s.satir_no - 1]) for s in belge.satirlar]
    return belge, ham, eslesen


def test_YARIS_ikinci_oturum_UNIQUE_ihlalini_ELE_ALIR(motor) -> None:
    """Birinci oturum yazıp işlemi açık tutuyor; ikinci oturum `yaniti_kaydet`te
    BEKLİYOR (UNIQUE indeksi), birinci bitince ihlale çarpıp `False` dönüyor.

    MUTASYON: göçten `uq_despatch_responses_uuid`i kaldırmak bunu KIRMIZI yapar
    (ikinci oturum beklemez, `True` döner, iki yanıt satırı olur)."""
    from app.routers.despatch_notes import yaniti_kaydet

    a = _irsaliyeli_firma(motor)
    belge, ham, eslesen = _belge(a)
    simdi = datetime.now(timezone.utc)
    birinci = Session(bind=motor)
    ikinci = Session(bind=motor)
    sonuc: dict = {}

    def _ikinci_yazar() -> None:
        try:
            sonuc["deger"] = yaniti_kaydet(ikinci, a["firma"], a["irsaliye"], belge, ham, eslesen, simdi)
            # İşlem ayakta: çağıranın sonraki yazımı hâlâ yapılabilir.
            ikinci.execute(
                text(f"UPDATE {IRSALIYE} SET edespatch_synced_at=:t WHERE id=:i AND company_id=:c"),
                {"t": simdi, "i": a["irsaliye"], "c": a["firma"]},
            )
            ikinci.commit()
        except BaseException as exc:  # noqa: BLE001 — iş parçacığından taşı
            sonuc["hata"] = exc

    try:
        assert yaniti_kaydet(birinci, a["firma"], a["irsaliye"], belge, ham, eslesen, simdi) is True
        is_parcacigi = threading.Thread(target=_ikinci_yazar)
        is_parcacigi.start()
        time.sleep(1.0)
        assert is_parcacigi.is_alive(), (
            "ikinci oturum BEKLEMEDİ — UNIQUE indeksi yarışı serileştirmiyor", sonuc
        )
        birinci.commit()
        is_parcacigi.join(timeout=30)
        assert not is_parcacigi.is_alive()
        assert "hata" not in sonuc, sonuc.get("hata")
        assert sonuc["deger"] is False
    finally:
        birinci.close()
        ikinci.close()
    with motor.connect() as b:
        assert b.execute(
            text(f"SELECT COUNT(*) FROM {YANIT} WHERE despatch_id=:d"), {"d": a["irsaliye"]}
        ).scalar_one() == 1
        assert b.execute(
            text(
                f"SELECT COUNT(*) FROM {YANIT_SATIR} WHERE response_id IN "
                f"(SELECT id FROM {YANIT} WHERE despatch_id=:d)"
            ),
            {"d": a["irsaliye"]},
        ).scalar_one() == 2
        assert b.execute(
            text(f"SELECT edespatch_synced_at IS NOT NULL FROM {IRSALIYE} WHERE id=:i"), {"i": a["irsaliye"]}
        ).scalar_one() is True


def test_IKINCI_cagri_ayni_belgeyle_NO_OP(motor) -> None:
    from app.routers.despatch_notes import yaniti_kaydet

    a = _irsaliyeli_firma(motor)
    belge, ham, eslesen = _belge(a)
    simdi = datetime.now(timezone.utc)
    with Session(bind=motor) as s:
        assert yaniti_kaydet(s, a["firma"], a["irsaliye"], belge, ham, eslesen, simdi) is True
        s.commit()
    with Session(bind=motor) as s:
        assert yaniti_kaydet(s, a["firma"], a["irsaliye"], belge, ham, eslesen, simdi) is False
        s.commit()
    with motor.connect() as b:
        assert b.execute(
            text(f"SELECT COUNT(*) FROM {YANIT} WHERE despatch_id=:d"), {"d": a["irsaliye"]}
        ).scalar_one() == 1


# ==========================================================================
# 4. up -> down -> up ve GERİ ALMA REDDİ
# ==========================================================================

def test_GOC_TURU_ve_GERI_ALMA_reddi(motor) -> None:
    yapilandirma = Config(str(BACKEND / "alembic.ini"))
    a = _irsaliyeli_firma(motor, durum="PARTIALLY_ACCEPTED")
    try:
        with pytest.raises(RuntimeError, match="ticari yanit durumunda e-Irsaliye var"):
            command.downgrade(yapilandirma, ONCEKI)
        # Reddedilen downgrade hiçbir şey düşürmedi.
        assert inspect(motor).has_table(YANIT)
        with motor.begin() as b:
            b.execute(text(f"UPDATE {IRSALIYE} SET edespatch_status='DELIVERED' WHERE id=:i"), {"i": a["irsaliye"]})

        command.downgrade(yapilandirma, ONCEKI)
        d = inspect(motor)
        assert not d.has_table(YANIT) and not d.has_table(YANIT_SATIR)
        assert "response_status" not in {c["name"] for c in d.get_columns(IRSALIYE)}
        # Daraltılmış CHECK GERÇEKTEN reddediyor.
        with pytest.raises(IntegrityError):
            with motor.begin() as b:
                b.execute(
                    text(f"UPDATE {IRSALIYE} SET edespatch_status='ACCEPTED' WHERE id=:i"),
                    {"i": a["irsaliye"]},
                )

        command.upgrade(yapilandirma, GOC)
        d = inspect(motor)
        assert d.has_table(YANIT) and d.has_table(YANIT_SATIR)
        with motor.begin() as b:
            b.execute(
                text(f"UPDATE {IRSALIYE} SET edespatch_status='REJECTED', response_status='RED' WHERE id=:i"),
                {"i": a["irsaliye"]},
            )
            b.execute(
                text(f"UPDATE {IRSALIYE} SET edespatch_status='DELIVERED', response_status=NULL WHERE id=:i"),
                {"i": a["irsaliye"]},
            )
    finally:
        command.upgrade(yapilandirma, "head")
