"""PostgreSQL ikizi: göç `20260915_0087` (E4b-1 kısmi sevk) GERÇEK PG 16'da.

SQLite ikizi (`tests/test_e4b1_kismi_sevk.py`) kuralları ve uçları ölçüyor.
Bu dosya DÖRT şeyi ölçüyor ve dördü de YALNIZ burada görünür:

1. **İKİ BİLEŞİK FK çapraz firmayı REDDEDİYOR.** `(company_id, despatch_id)`
   ve `(company_id, invoice_item_id)`: bir satır başka firmanın irsaliyesini
   de, başka firmanın fatura kalemini de gösteremez. SQLite FK'yi varsayılan olarak ZORLAMAZ.
2. **CHECK `quantity > 0` ve Numeric(18,4) gerçek.**
3. **Geri doldurma + up/down/up.** `downgrade` birden çok irsaliyeli fatura
   varken ADIYLA durur; tek irsaliyeye inince 1:1 kısıtını GERİ kurar;
   yeniden `upgrade` eski irsaliyeyi yine doldurur.
4. **Faturanın satır kilidi GERÇEKTEN kilitliyor.** `_faturayi_kilitle`in
   boş UPDATE'i açık bir işlemde tutulurken ikinci bir bağlantının aynı
   UPDATE'i `lock_timeout` ile DÜŞER — kalan hesabının hakemi budur ve
   SQLite'ın veritabanı kilidi bunu satır düzeyinde ölçemez.

TEMİZLİK: yalnız KENDİ satırlarını (firma adı öneki) siler, tablo SÜPÜRMEZ.
Bu dosya bir faturaya İKİ irsaliye yazar; arkada kalırsa KOMŞU bir ikizin
`downgrade`ı "birden fazla e-Irsaliyesi olan fatura var" ile düşerdi.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

BACKEND = Path(__file__).resolve().parent

pytestmark = pytest.mark.postgresql

KOSU = uuid4().hex[:8]
GOC = "20260915_0087"
ONCEKI = "20260914_0086"
#: ZİNCİRİN BAŞI — `GOC`tan AYRI (gerekçe `test_e1_efatura_sertlestirme_postgresql.py`).
BAS = "20260915_0087"

SATIR = "despatch_lines"
IRSALIYE = "despatch_notes"
SOFOR_TCKN = "11111111110"


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("E4b-1 ikizi APP_TEST_DATABASE_URL ister")
    return url


def _temizle(engine) -> None:
    """SIRA: satır -> irsaliye -> kalem -> fatura -> sayaç -> firma -> kullanıcı."""
    onek = {"onek": f"{KOSU}%"}
    firmalar = "(SELECT id FROM companies WHERE name LIKE :onek)"
    with engine.begin() as b:
        d = inspect(b)
        if d.has_table(SATIR):
            b.execute(text(f"DELETE FROM {SATIR} WHERE company_id IN {firmalar}"), onek)
        if d.has_table(IRSALIYE):
            b.execute(text(f"DELETE FROM {IRSALIYE} WHERE company_id IN {firmalar}"), onek)
        b.execute(text(f"DELETE FROM invoice_items WHERE company_id IN {firmalar}"), onek)
        b.execute(text(f"DELETE FROM invoices WHERE company_id IN {firmalar}"), onek)
        b.execute(
            text(f"DELETE FROM document_sequences WHERE company_id IN {firmalar}"), onek
        )
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


def _firma_fatura(motor, kalemler=(("LABOR", "Iscilik", "2"), ("PART", "Bugday", "3.5"))) -> dict:
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
        kimlikler = []
        for tur, ad, miktar in kalemler:
            kimlikler.append(
                b.execute(
                    text(
                        "INSERT INTO invoice_items(company_id,invoice_id,item_type,description,"
                        "quantity,unit_price,original_price,discount_amount,tax_rate,tax_amount,"
                        "total,warranty_percent,customer_payable,company_payable,source_snapshot) "
                        "VALUES(:c,:f,:tur,:ad,:m,1,1,0,0,0,1,0,1,0,:k) RETURNING id"
                    ),
                    {
                        "c": firma, "f": fatura, "tur": tur, "ad": ad, "m": Decimal(miktar),
                        "k": json.dumps({"product_id": 55} if tur == "PART" else {}),
                    },
                ).scalar_one()
            )
    return {"firma": int(firma), "fatura": int(fatura), "kalemler": kimlikler}


def _irsaliye(motor, firma: int, fatura: int) -> int:
    _SAYAC["n"] += 1
    an = datetime.now(timezone.utc)
    with motor.begin() as b:
        return int(
            b.execute(
                text(
                    f"INSERT INTO {IRSALIYE}(company_id,invoice_id,despatch_uuid,despatch_number,"
                    "issue_date,actual_shipment_at,driver_name,driver_national_id,vehicle_plate,"
                    "delivery_address,delivery_postal_code,edespatch_status,created_at,updated_at) "
                    "VALUES(:c,:f,:u,:no,:d,:t,'A',:tc,'34ABC123','Adres','34000','NONE',:t,:t) "
                    "RETURNING id"
                ),
                {
                    "c": firma, "f": fatura, "u": str(uuid4()),
                    "no": f"IRS2026{_SAYAC['n'] + 500000000:09d}", "d": date(2026, 9, 15),
                    "t": an, "tc": SOFOR_TCKN,
                },
            ).scalar_one()
        )


def _satir_ekle(b, firma: int, irsaliye: int, kalem: int, miktar: str, no: int = 1) -> None:
    an = datetime.now(timezone.utc)
    b.execute(
        text(
            f"INSERT INTO {SATIR}(company_id,despatch_id,invoice_item_id,line_no,item_name,"
            "quantity,created_at,updated_at) VALUES(:c,:d,:k,:no,'Bugday',:m,:t,:t)"
        ),
        {"c": firma, "d": irsaliye, "k": kalem, "no": no, "m": Decimal(miktar), "t": an},
    )


# ==========================================================================
# 1. ŞEMA
# ==========================================================================

def test_SEMA_BASI_0087_ve_KISITLAR(motor) -> None:
    with motor.connect() as b:
        assert b.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == BAS
    d = inspect(motor)
    assert {u["name"] for u in d.get_unique_constraints(SATIR)} == {
        "uq_despatch_lines_company_id", "uq_despatch_lines_despatch_line",
    }
    assert {c["name"] for c in d.get_check_constraints(SATIR)} == {
        "ck_despatch_lines_quantity_positive",
    }
    bilesik = [
        f for f in d.get_foreign_keys(SATIR)
        if f["name"] == "fk_despatch_lines_despatch_same_company"
    ]
    assert len(bilesik) == 1
    assert bilesik[0]["constrained_columns"] == ["company_id", "despatch_id"]
    assert bilesik[0]["referred_columns"] == ["company_id", "id"]
    # `invoice_item_id` de BİLEŞİK (Şef, 2026-09-11; 0044 kuralı) ve hedefi
    # `uq_invoice_items_company_id` bu göçte kuruldu.
    kalem_fk = [
        f for f in d.get_foreign_keys(SATIR)
        if f["name"] == "fk_despatch_lines_invoice_item_same_company"
    ]
    assert len(kalem_fk) == 1
    assert kalem_fk[0]["constrained_columns"] == ["company_id", "invoice_item_id"]
    assert kalem_fk[0]["referred_table"] == "invoice_items"
    assert kalem_fk[0]["referred_columns"] == ["company_id", "id"]
    assert "uq_invoice_items_company_id" in {
        u["name"] for u in d.get_unique_constraints("invoice_items")
    }
    assert "ix_despatch_lines_company_despatch" in {i["name"] for i in d.get_indexes(SATIR)}
    miktar = next(c for c in d.get_columns(SATIR) if c["name"] == "quantity")["type"]
    assert (miktar.precision, miktar.scale) == (18, 4)
    assert "uq_despatch_notes_company_invoice" not in {
        u["name"] for u in d.get_unique_constraints(IRSALIYE)
    }


# ==========================================================================
# 2. KISITLAR REDDEDİYOR
# ==========================================================================

def test_BILESIK_FK_capraz_firma_RED_ayni_firma_KABUL(motor) -> None:
    """MUTASYON: göçteki bileşik FK'yi çıplak `despatch_id -> despatch_notes.id`
    FK'sine çevirmek ilk dalı KIRMIZI yapar."""
    a = _firma_fatura(motor)
    b_firma = _firma_fatura(motor)
    irs_a = _irsaliye(motor, a["firma"], a["fatura"])
    with pytest.raises(IntegrityError):
        with motor.begin() as b:
            _satir_ekle(b, b_firma["firma"], irs_a, a["kalemler"][1], "1")
    with motor.begin() as b:
        _satir_ekle(b, a["firma"], irs_a, a["kalemler"][1], "1")


def test_BILESIK_FK_capraz_firma_FATURA_KALEMI_RED(motor) -> None:
    """A'nın irsaliyesine B'nin fatura kalemi bağlanamaz — satırın
    `company_id`si irsaliyeyle (A) aynı olsa bile. Şef kararı (2026-09-11):
    çıplak `invoice_item_id` FK'si bunu GEÇİRİRDİ.
    MUTASYON: `fk_despatch_lines_invoice_item_same_company`i çıplak
    `invoice_item_id -> invoice_items.id`e geri çevirmek bunu KIRMIZI yapar."""
    a = _firma_fatura(motor)
    b_firma = _firma_fatura(motor)
    irs_a = _irsaliye(motor, a["firma"], a["fatura"])
    with pytest.raises(IntegrityError):
        with motor.begin() as b:
            _satir_ekle(b, a["firma"], irs_a, b_firma["kalemler"][1], "1")
    with motor.begin() as b:
        _satir_ekle(b, a["firma"], irs_a, a["kalemler"][1], "1")


def test_CHECK_miktar_pozitif_ve_satir_no_tekil(motor) -> None:
    a = _firma_fatura(motor)
    irs = _irsaliye(motor, a["firma"], a["fatura"])
    for kotu in ("0", "-1"):
        with pytest.raises(IntegrityError):
            with motor.begin() as b:
                _satir_ekle(b, a["firma"], irs, a["kalemler"][1], kotu)
    with motor.begin() as b:
        _satir_ekle(b, a["firma"], irs, a["kalemler"][1], "1.2345", no=1)
    with pytest.raises(IntegrityError):
        with motor.begin() as b:
            _satir_ekle(b, a["firma"], irs, a["kalemler"][1], "1", no=1)
    with motor.connect() as b:
        assert b.execute(
            text(f"SELECT quantity FROM {SATIR} WHERE despatch_id=:d"), {"d": irs}
        ).scalar_one() == Decimal("1.2345")


# ==========================================================================
# 3. GERİ DOLDURMA + up -> down -> up
# ==========================================================================

def test_GERI_DOLDURMA_ve_GOC_TURU(motor) -> None:
    yapilandirma = Config(str(BACKEND / "alembic.ini"))
    command.downgrade(yapilandirma, ONCEKI)
    try:
        assert not inspect(motor).has_table(SATIR)
        a = _firma_fatura(
            motor,
            (("LABOR", "Iscilik", "2"), ("PART", "Bugday", "3.5"), ("PART", "Sifir", "0")),
        )
        irs = _irsaliye(motor, a["firma"], a["fatura"])

        command.upgrade(yapilandirma, "head")
        with motor.connect() as b:
            satirlar = b.execute(
                text(
                    f"SELECT line_no, invoice_item_id, product_id, quantity, unit_code FROM {SATIR} "
                    "WHERE despatch_id=:d AND company_id=:c ORDER BY line_no"
                ),
                {"d": irs, "c": a["firma"]},
            ).all()
        assert [tuple(s) for s in satirlar] == [
            (1, a["kalemler"][1], 55, Decimal("3.5000"), "C62")
        ]

        # İkinci irsaliye aynı faturaya: downgrade ADIYLA durur.
        ikinci = _irsaliye(motor, a["firma"], a["fatura"])
        with pytest.raises(RuntimeError, match="birden fazla e-Irsaliyesi olan fatura var"):
            command.downgrade(yapilandirma, ONCEKI)
        with motor.begin() as b:
            b.execute(text(f"DELETE FROM {IRSALIYE} WHERE id=:i"), {"i": ikinci})

        command.downgrade(yapilandirma, ONCEKI)
        d = inspect(motor)
        assert not d.has_table(SATIR)
        # Hedef UNIQUE KOŞULLU düştü (0085 kalıbı).
        assert "uq_invoice_items_company_id" not in {
            u["name"] for u in d.get_unique_constraints("invoice_items")
        }
        assert "uq_despatch_notes_company_invoice" in {
            u["name"] for u in d.get_unique_constraints(IRSALIYE)
        }
        # Geri kurulan kısıt GERÇEKTEN reddediyor.
        with pytest.raises(IntegrityError):
            _irsaliye(motor, a["firma"], a["fatura"])
    finally:
        command.upgrade(yapilandirma, "head")
    with motor.connect() as b:
        assert b.execute(
            text(f"SELECT COUNT(*) FROM {SATIR} WHERE despatch_id=:d"), {"d": irs}
        ).scalar_one() == 1


# ==========================================================================
# 4. SATIR KİLİDİ — kalan hesabının hakemi
# ==========================================================================

def test_FATURA_KILIDI_ikinci_islemi_BEKLETIR(motor) -> None:
    """`_faturayi_kilitle` açık bir işlemde tutulurken ikinci bir işlemin
    AYNI kilidi `lock_timeout` içinde ALAMADIĞI ölçülür; birincisi bitince
    alır. MUTASYON: `_faturayi_kilitle`i bir `SELECT`e çevirmek (kilit
    almayan okuma) bunu KIRMIZI yapar."""
    from app.routers.despatch_notes import _faturayi_kilitle

    a = _firma_fatura(motor)
    birinci = Session(bind=motor)
    ikinci = Session(bind=motor)
    try:
        _faturayi_kilitle(birinci, a["firma"], a["fatura"])  # kilit TUTULUYOR
        ikinci.execute(text("SET LOCAL lock_timeout = '300ms'"))
        with pytest.raises(OperationalError, match="lock timeout"):
            _faturayi_kilitle(ikinci, a["firma"], a["fatura"])
        ikinci.rollback()
        birinci.commit()
        ikinci.execute(text("SET LOCAL lock_timeout = '300ms'"))
        _faturayi_kilitle(ikinci, a["firma"], a["fatura"])  # artık alır
        ikinci.commit()
    finally:
        birinci.close()
        ikinci.close()
