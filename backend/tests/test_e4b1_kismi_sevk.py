"""E4b-1 — e-İRSALİYE KISMI SEVK: göç 0087, `despatch_lines`, kalan kuralı, UBL.

Konu: göç `20260915_0087`, `app/routers/despatch_notes.py` (kısmi sevk,
`GET /api/invoices/{id}/despatchable-items`), `app/despatch_schema.py`,
`app/document_engine.py::next_sequence_value`, `app/einvoice/edespatch.py`
(`OrderLineReference/LineID`).

Keşif: `docs/e4b-kismi-sevk-kesif-2026-09-10.md` §1, §2, §6.

--- MUTASYON TABLOSU -------------------------------------------------------

  * `_tahsis`ten LABOR süzgecini kaldırmak
                          -> `test_TAM_SEVK_hizmet_kalemini_ATLAR` KIRMIZI
  * `_tahsis`in "kalan sıfır" 409 dalını silmek
                          -> `test_KISMI_60_40_sonra_TAMAMLANDI` KIRMIZI
  * `miktar > kalem["kalan"]` kontrolünü gevşetmek
                          -> `test_KALANI_ASAN_miktar_422` KIRMIZI
  * `_faturayi_kilitle` çağrısını kalan okumasının ARDINA taşımak
                          -> PG ikizinin kilit testi KIRMIZI (SQLite ölçemez)
  * `_belge_numarasi`nda sırayı yine fatura kimliğinden türetmek
                          -> `test_IKINCI_irsaliye_FARKLI_numara_...` KIRMIZI
  * Göçün geri doldurmasını silmek
                          -> `test_GOC_eski_irsaliyeyi_GERI_DOLDURUR_...` KIRMIZI
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree

import pytest

BACKEND = Path(__file__).resolve().parents[1]
GOC = BACKEND / "alembic" / "versions" / "20260915_0087_despatch_lines.py"

_CALISMA_ALANI = tempfile.mkdtemp(prefix="e4b1-kismi-")
os.environ["DATABASE_URL"] = "sqlite:///" + (
    os.path.join(_CALISMA_ALANI, "e4b1.db").replace(os.sep, "/")
)
os.environ["SUNGUR_DATA_DIR"] = _CALISMA_ALANI
os.environ["AUTO_MIGRATE"] = "true"

ACILIS_PAROLASI = "admin123"
ADMIN_PAROLASI = "E4b1Yonetim!2026"
ROL_PAROLASI = "E4b1Rolleri!2026"
ROLLER = ("yonetici", "muhasebe", "satis", "depo", "rapor")

#: SENTETİK kimlik/plaka — yalnız biçim.
SOFOR_TCKN = "11111111110"
PLAKA = "34ABC123"

_CAC = "{urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2}"
_CBC = "{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}"


def _goc_modulu():
    import importlib.util

    spec = importlib.util.spec_from_file_location("goc0087", GOC)
    goc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(goc)
    return goc


# ==========================================================================
# 1. STATİK
# ==========================================================================

def test_goc_ZINCIRE_dogru_yerden_bagli() -> None:
    """0087, CS2'nin 0086'sının ardından gelir (Şef, 2026-09-11)."""
    kaynak = GOC.read_text(encoding="utf-8")
    assert 'revision = "20260915_0087"' in kaynak
    assert 'down_revision = "20260914_0086"' in kaynak
    assert "branch_labels = None" in kaynak


def test_HIZMET_TURU_goc_ile_uc_AYNI() -> None:
    """Geri doldurmanın süzgeci ile ucun süzgeci AYNI değer olmalı — yoksa
    eski irsaliyeler yeni kuralla farklı satır alırdı."""
    from app.routers import despatch_notes as uc

    assert _goc_modulu().HIZMET_TURU == uc.HIZMET_TURU == "LABOR"


def test_ACILIS_DDLi_GOCUN_ONUNE_GECMIYOR() -> None:
    """`despatch_lines` açılışın `create_all` ettiği hiçbir modülde yok
    (0072 kusuru; E4a'nın aynı kapısıyla aynı tarama)."""
    import re

    acilis = "".join(
        (BACKEND / "app" / m).read_text(encoding="utf-8")
        for m in (
            "tenancy.py", "core_schema.py", "auth.py", "inventory.py",
            "finance_engine.py", "workflow.py",
        )
    )
    bildirilen = set(re.findall(r"""Table\(\s*['"]([a-z_]+)['"]""", acilis))
    assert "companies" in bildirilen, "tarayıcı hiçbir Table() görmüyor"
    assert "despatch_lines" not in bildirilen


def test_SEMA_MODULU_goc_ile_AYNI_SUTUNLAR() -> None:
    """Core tanımı göçün sütunlarını BİREBİR taşır (ad + Numeric ölçeği)."""
    import ast

    from app.despatch_schema import despatch_lines

    agac = ast.parse(GOC.read_text(encoding="utf-8"))
    goc_sutunlari = set()
    for dugum in ast.walk(agac):
        if (
            isinstance(dugum, ast.Call)
            and getattr(dugum.func, "attr", None) == "create_table"
        ):
            for arg in dugum.args[1:]:
                if (
                    isinstance(arg, ast.Call)
                    and getattr(arg.func, "attr", None) == "Column"
                ):
                    goc_sutunlari.add(arg.args[0].value)
    assert goc_sutunlari == set(despatch_lines.c.keys())
    tip = despatch_lines.c.quantity.type
    assert (tip.precision, tip.scale) == (18, 4)


# ==========================================================================
# 2. GÖÇ TURU — ayrı bir SQLite dosyasında, alt süreçte
# ==========================================================================

def _alembic(url: str, *argumanlar: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *argumanlar],
        cwd=str(BACKEND),
        env={**os.environ, "DATABASE_URL": url, "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )


def _eski_irsaliye_tohumu(url: str) -> dict:
    """0087'den ÖNCEKİ şemada bir E4a irsaliyesi: 1 LABOR + 2 PART (biri 0)."""
    from sqlalchemy import create_engine, text

    motor = create_engine(url)
    an = datetime(2026, 9, 12, 9, 0, tzinfo=timezone.utc)
    try:
        with motor.begin() as b:
            firma = b.execute(
                text("INSERT INTO companies(name,is_active,created_at) VALUES('E4b1 Goc',1,:t) RETURNING id"),
                {"t": an},
            ).scalar_one()
            kullanici = b.execute(
                text(
                    "INSERT INTO app_users(username,email,email_verified,display_name,password_hash,"
                    "role,is_active,must_change_password,created_at) "
                    "VALUES('e4b1_goc','e4b1_goc@ornek.test',1,'g','x','admin',1,0,:t) RETURNING id"
                ),
                {"t": an},
            ).scalar_one()
            fatura = b.execute(
                text(
                    "INSERT INTO invoices(company_id,invoice_number,invoice_type,status,currency,"
                    "exchange_rate,customer_snapshot,machine_snapshot,work_order_snapshot,"
                    "company_snapshot,technician_snapshot,warranty_snapshot,totals_snapshot,"
                    "tax_snapshot,created_by,created_at,updated_at) VALUES(:c,'E4B1-GOC-1',"
                    "'INVOICE','ISSUED','TRY',1,'{}','{}','{}','{}','{}','{}','{}','{}',:u,:t,:t) RETURNING id"
                ),
                {"c": firma, "u": kullanici, "t": an},
            ).scalar_one()
            kalemler = []
            for tur, ad, miktar, kaynak in (
                ("LABOR", "Servis İşçiliği", "2", "{}"),
                ("PART", "Bugday", "3.5", json.dumps({"product_id": 77})),
                ("PART", "Sifir", "0", json.dumps({"product_id": 78})),
            ):
                kalemler.append(
                    b.execute(
                        text(
                            "INSERT INTO invoice_items(company_id,invoice_id,item_type,description,"
                            "quantity,unit_price,original_price,discount_amount,tax_rate,tax_amount,"
                            "total,warranty_percent,customer_payable,company_payable,source_snapshot) "
                            "VALUES(:c,:f,:tur,:ad,:m,1,1,0,0,0,1,0,1,0,:k) RETURNING id"
                        ),
                        {"c": firma, "f": fatura, "tur": tur, "ad": ad, "m": miktar, "k": kaynak},
                    ).scalar_one()
                )
            irsaliye = b.execute(
                text(
                    "INSERT INTO despatch_notes(company_id,invoice_id,despatch_uuid,despatch_number,"
                    "issue_date,actual_shipment_at,driver_name,driver_national_id,vehicle_plate,"
                    "delivery_address,delivery_postal_code,edespatch_status,created_at,updated_at) "
                    "VALUES(:c,:f,'00000000-0000-4000-8000-000000000001','IRS2026000000001',"
                    "'2026-09-12',:t,'A',:tc,:p,'Adres','34000','NONE',:t,:t) RETURNING id"
                ),
                {"c": firma, "f": fatura, "t": an, "tc": SOFOR_TCKN, "p": PLAKA},
            ).scalar_one()
        return {"firma": firma, "fatura": fatura, "kalemler": kalemler, "irsaliye": irsaliye}
    finally:
        motor.dispose()


def test_GOC_eski_irsaliyeyi_GERI_DOLDURUR_ve_up_down_up() -> None:
    """0087 öncesi bir E4a irsaliyesi yükseltmede satır alır; tur geri alınabilir.

    Geri doldurma ÖLÇÜSÜ: LABOR atlanır (hizmet), sıfır miktarlı PART atlanır
    (CHECK), tam miktarlı PART bir satır olur — `product_id` kalemin donmuş
    kaynağından. Sonra: ikinci irsaliye varken downgrade ADIYLA DURUR, ikinci
    silinince 1:1 kısıtı GERİ KURULUR, yeniden upgrade satırı yine doldurur.
    """
    from sqlalchemy import create_engine, inspect, text

    dosya = os.path.join(_CALISMA_ALANI, "goc_turu.db").replace(os.sep, "/")
    url = f"sqlite:///{dosya}"
    sonuc = _alembic(url, "upgrade", "20260914_0086")
    assert sonuc.returncode == 0, sonuc.stderr[-2000:]
    tohum = _eski_irsaliye_tohumu(url)

    sonuc = _alembic(url, "upgrade", "head")
    assert sonuc.returncode == 0, sonuc.stderr[-2000:]
    motor = create_engine(url)
    try:
        with motor.connect() as b:
            satirlar = b.execute(
                text(
                    "SELECT line_no, invoice_item_id, product_id, item_name, quantity, unit_code "
                    "FROM despatch_lines WHERE despatch_id=:d ORDER BY line_no"
                ),
                {"d": tohum["irsaliye"]},
            ).all()
        assert len(satirlar) == 1, satirlar
        satir = satirlar[0]
        assert satir.line_no == 1
        assert satir.invoice_item_id == tohum["kalemler"][1]
        assert satir.product_id == 77
        assert satir.item_name == "Bugday"
        assert Decimal(str(satir.quantity)) == Decimal("3.5")
        assert satir.unit_code == "C62"
        tekiller = {u["name"] for u in inspect(motor).get_unique_constraints("despatch_notes")}
        assert "uq_despatch_notes_company_invoice" not in tekiller
        # Bileşik FK hedefi `invoice_items`e eklendi ve SQLite yeniden inşası
        # tablonun indeksini ve `ON DELETE CASCADE`ini KAYBETMEDİ.
        d = inspect(motor)
        assert "uq_invoice_items_company_id" in {
            u["name"] for u in d.get_unique_constraints("invoice_items")
        }
        assert "ix_invoice_items_invoice" in {i["name"] for i in d.get_indexes("invoice_items")}
        assert [
            (f.get("options") or {}).get("ondelete")
            for f in d.get_foreign_keys("invoice_items") if f["referred_table"] == "invoices"
        ] == ["CASCADE"]
        assert [
            (f["constrained_columns"], f["referred_columns"])
            for f in d.get_foreign_keys("despatch_lines") if f["referred_table"] == "invoice_items"
        ] == [(["company_id", "invoice_item_id"], ["company_id", "id"])]

        # İKİNCİ irsaliye aynı faturaya — başta YAZILABİLİR (kısıt düştü).
        with motor.begin() as b:
            ikinci = b.execute(
                text(
                    "INSERT INTO despatch_notes(company_id,invoice_id,despatch_uuid,despatch_number,"
                    "issue_date,actual_shipment_at,driver_name,driver_national_id,vehicle_plate,"
                    "delivery_address,delivery_postal_code,edespatch_status,created_at,updated_at) "
                    "SELECT company_id,invoice_id,'00000000-0000-4000-8000-000000000002',"
                    "'IRS2026000000002',issue_date,actual_shipment_at,driver_name,"
                    "driver_national_id,vehicle_plate,delivery_address,delivery_postal_code,"
                    "edespatch_status,created_at,updated_at FROM despatch_notes WHERE id=:i "
                    "RETURNING id"
                ),
                {"i": tohum["irsaliye"]},
            ).scalar_one()
    finally:
        motor.dispose()

    # Birden çok irsaliyeli fatura varken geri alma ADIYLA durur.
    sonuc = _alembic(url, "downgrade", "20260914_0086")
    assert sonuc.returncode != 0
    assert "birden fazla e-Irsaliyesi olan fatura var" in sonuc.stderr, sonuc.stderr[-2000:]

    motor = create_engine(url)
    try:
        with motor.begin() as b:
            b.execute(text("DELETE FROM despatch_notes WHERE id=:i"), {"i": ikinci})
    finally:
        motor.dispose()
    sonuc = _alembic(url, "downgrade", "20260914_0086")
    assert sonuc.returncode == 0, sonuc.stderr[-2000:]
    motor = create_engine(url)
    try:
        d = inspect(motor)
        assert not d.has_table("despatch_lines")
        assert "uq_invoice_items_company_id" not in {
            u["name"] for u in d.get_unique_constraints("invoice_items")
        }
        assert "uq_despatch_notes_company_invoice" in {
            u["name"] for u in d.get_unique_constraints("despatch_notes")
        }
        # 0083'ün KALAN kısıtları batch yeniden inşasında KAYBOLMADI.
        assert {c["name"] for c in d.get_check_constraints("despatch_notes")} == {
            "ck_despatch_notes_tasima", "ck_despatch_notes_durum",
        }
        assert "ix_despatch_notes_company_issue" in {
            i["name"] for i in d.get_indexes("despatch_notes")
        }
    finally:
        motor.dispose()

    sonuc = _alembic(url, "upgrade", "head")
    assert sonuc.returncode == 0, sonuc.stderr[-2000:]
    motor = create_engine(url)
    try:
        with motor.connect() as b:
            adet = b.execute(
                text("SELECT COUNT(*) FROM despatch_lines WHERE despatch_id=:d"),
                {"d": tohum["irsaliye"]},
            ).scalar_one()
        assert adet == 1
    finally:
        motor.dispose()


# ==========================================================================
# 3. UÇLAR — GERÇEK uygulama, GERÇEK HTTP
# ==========================================================================

@pytest.fixture(scope="module")
def istemci():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="module")
def admin_basliklari(istemci):
    giris = istemci.post(
        "/api/auth/login", json={"username": "admin", "password": ACILIS_PAROLASI}
    )
    assert giris.status_code == 200, giris.text
    govde = giris.json()
    basliklar = {
        "Authorization": "Bearer " + govde["access_token"],
        "X-Company-ID": str(govde["companies"][0]["id"]),
    }
    degis = istemci.post(
        "/api/auth/change-password",
        headers=basliklar,
        json={"current_password": ACILIS_PAROLASI, "new_password": ADMIN_PAROLASI},
    )
    assert degis.status_code == 200, degis.text
    basliklar["Authorization"] = "Bearer " + degis.json()["access_token"]
    return basliklar


_SAYAC = {"n": 0}


def _sql_fatura(cid: int, kalemler: list[tuple[str, str, str]]) -> dict:
    """Doğrudan SQL ile bir fatura + kalemleri. Kural testleri için: miktar
    ve kalem türü TAM kontrolde olsun (iş emrinden üretilen fatura parça
    miktarını stokla, işçiliği saatle bağlar).

    Anlık görüntüler UBL'in istediği kimlikleri taşır — indirme de çalışsın.
    """
    from sqlalchemy import text

    from app.db import SessionLocal

    _SAYAC["n"] += 1
    an = datetime.now(timezone.utc)
    with SessionLocal() as db:
        fatura = db.execute(
            text(
                "INSERT INTO invoices(company_id,invoice_number,invoice_type,status,currency,"
                "exchange_rate,customer_snapshot,machine_snapshot,work_order_snapshot,"
                "company_snapshot,technician_snapshot,warranty_snapshot,totals_snapshot,"
                "tax_snapshot,created_by,created_at,updated_at) VALUES(:c,:n,'INVOICE',"
                "'ISSUED','TRY',1,:musteri,'{}','{}',:firma,'{}','{}','{}','{}',1,:t,:t) "
                "RETURNING id"
            ),
            {
                "c": cid, "n": f"E4B1-{_SAYAC['n']}", "t": an,
                "musteri": json.dumps({"name": "E4b1 Alici", "tax_number": "9876543210", "address": "B"}),
                "firma": json.dumps({"name": "E4b1 Firma", "tax_number": "1234567890", "address": "A"}),
            },
        ).scalar_one()
        kimlikler = []
        for tur, ad, miktar in kalemler:
            kimlikler.append(
                db.execute(
                    text(
                        "INSERT INTO invoice_items(company_id,invoice_id,item_type,description,"
                        "quantity,unit_price,original_price,discount_amount,tax_rate,tax_amount,"
                        "total,warranty_percent,customer_payable,company_payable,source_snapshot) "
                        "VALUES(:c,:f,:tur,:ad,:m,1,1,0,0,0,1,0,1,0,:k) RETURNING id"
                    ),
                    {
                        "c": cid, "f": fatura, "tur": tur, "ad": ad, "m": miktar,
                        "k": json.dumps({"product_id": 900 + len(kimlikler)} if tur == "PART" else {}),
                    },
                ).scalar_one()
            )
        db.commit()
    return {"id": int(fatura), "kalemler": [int(k) for k in kimlikler]}


def _govde(fatura_id: int, **degisiklikler) -> dict:
    govde = {
        "invoice_id": fatura_id,
        "actual_shipment_at": "2026-09-15T08:30:00+00:00",
        "driver_name": "Ahmet Yilmaz",
        "driver_national_id": SOFOR_TCKN,
        "vehicle_plate": PLAKA,
        "delivery_address": "Depo Yolu 7",
        "delivery_postal_code": "34710",
    }
    govde.update(degisiklikler)
    return govde


def _kod(yanit) -> str | None:
    detay = yanit.json().get("detail")
    return detay.get("code") if isinstance(detay, dict) else None


@pytest.fixture()
def fatura(admin_basliklari):
    """LABOR + iki PART: 10 ve 5 birim. Her test KENDİ faturasını alır."""
    return _sql_fatura(
        int(admin_basliklari["X-Company-ID"]),
        [("LABOR", "Servis İşçiliği", "3"), ("PART", "Bugday", "10"), ("PART", "Arpa", "5")],
    )


def test_TAM_SEVK_hizmet_kalemini_ATLAR(istemci, admin_basliklari, fatura) -> None:
    """Gövdesiz istek = LABOR OLMAYAN her kalemin kalanı. Satır sayısı 2, 3 DEĞİL."""
    yanit = istemci.post("/api/despatch-notes", headers=admin_basliklari, json=_govde(fatura["id"]))
    assert yanit.status_code == 201, yanit.text
    satirlar = yanit.json()["lines"]
    assert [s["invoice_item_id"] for s in satirlar] == fatura["kalemler"][1:]
    assert [s["quantity"] for s in satirlar] == ["10.0000", "5.0000"]
    assert [s["line_no"] for s in satirlar] == [1, 2]
    assert {s["unit_code"] for s in satirlar} == {"C62"}
    # Ürün kimliği kalemin donmuş kaynağından geldi.
    assert satirlar[0]["product_id"] == 901
    # Detay ucu aynı satırları taşır.
    detay = istemci.get(f"/api/despatch-notes/{yanit.json()['id']}", headers=admin_basliklari)
    assert detay.json()["lines"] == satirlar


def test_KISMI_60_40_sonra_TAMAMLANDI(istemci, admin_basliklari, fatura) -> None:
    """%60 + %40 iki irsaliyede; kalan sıfırlanınca üçüncü 409."""
    _, bugday, arpa = fatura["kalemler"]
    h = admin_basliklari
    ilk = istemci.post(
        "/api/despatch-notes", headers=h,
        json=_govde(fatura["id"], lines=[{"invoice_item_id": bugday, "quantity": "6"},
                                         {"invoice_item_id": arpa, "quantity": "3"}]),
    )
    assert ilk.status_code == 201, ilk.text

    ara = istemci.get(f"/api/invoices/{fatura['id']}/despatchable-items", headers=h).json()
    assert [(k["invoice_item_id"], k["despatched"], k["remaining"]) for k in ara["items"]] == [
        (bugday, "6.0000", "4.0000"), (arpa, "3.0000", "2.0000"),
    ]
    assert ara["complete"] is False

    # İkinci: gövdesiz = KALANLAR (4 ve 2), baştaki toplam DEĞİL.
    ikinci = istemci.post("/api/despatch-notes", headers=h, json=_govde(fatura["id"]))
    assert ikinci.status_code == 201, ikinci.text
    assert [s["quantity"] for s in ikinci.json()["lines"]] == ["4.0000", "2.0000"]
    assert ikinci.json()["despatch_number"] != ilk.json()["despatch_number"]

    son = istemci.get(f"/api/invoices/{fatura['id']}/despatchable-items", headers=h).json()
    assert [k["remaining"] for k in son["items"]] == ["0.0000", "0.0000"]
    assert son["complete"] is True

    ucuncu = istemci.post(
        "/api/despatch-notes", headers=h,
        json=_govde(fatura["id"], lines=[{"invoice_item_id": bugday, "quantity": "1"}]),
    )
    assert ucuncu.status_code == 409, ucuncu.text
    assert _kod(ucuncu) == "IRSALIYE_TAMAMLANDI"
    assert istemci.post(
        "/api/despatch-notes", headers=h, json=_govde(fatura["id"])
    ).status_code == 409


def test_KALANI_ASAN_miktar_422(istemci, admin_basliklari, fatura) -> None:
    """Kalan 10 iken 10.0001 düşer; 10 geçer (sınır kapsayıcı)."""
    bugday = fatura["kalemler"][1]
    h = admin_basliklari
    asan = istemci.post(
        "/api/despatch-notes", headers=h,
        json=_govde(fatura["id"], lines=[{"invoice_item_id": bugday, "quantity": "10.0001"}]),
    )
    assert asan.status_code == 422, asan.text
    assert _kod(asan) == "SEVK_MIKTAR_ASIMI"
    assert asan.json()["detail"]["remaining"] == "10.0000"
    # Reddedilen istek HİÇBİR irsaliye bırakmadı.
    liste = istemci.get(f"/api/despatch-notes?invoice_id={fatura['id']}", headers=h).json()
    assert liste["total"] == 0

    tam = istemci.post(
        "/api/despatch-notes", headers=h,
        json=_govde(fatura["id"], lines=[{"invoice_item_id": bugday, "quantity": "10"}]),
    )
    assert tam.status_code == 201, tam.text


def test_HIZMET_satiri_govdede_422(istemci, admin_basliklari, fatura) -> None:
    iscilik = fatura["kalemler"][0]
    yanit = istemci.post(
        "/api/despatch-notes", headers=admin_basliklari,
        json=_govde(fatura["id"], lines=[{"invoice_item_id": iscilik, "quantity": "1"}]),
    )
    assert yanit.status_code == 422, yanit.text
    assert _kod(yanit) == "HIZMET_SATIRI_SEVK_EDILMEZ"


@pytest.mark.parametrize("miktar", ["0", "-1", "0.00001", "abc"])
def test_SIFIR_NEGATIF_ve_OLCEK_DISI_miktar_422(istemci, admin_basliklari, fatura, miktar) -> None:
    """`quantity > 0`, en çok 4 kesir hanesi (Numeric(18, 4))."""
    yanit = istemci.post(
        "/api/despatch-notes", headers=admin_basliklari,
        json=_govde(fatura["id"], lines=[{"invoice_item_id": fatura["kalemler"][1], "quantity": miktar}]),
    )
    assert yanit.status_code == 422, yanit.text


def test_BOS_liste_TEKRAR_ve_YABANCI_kalem_422(istemci, admin_basliklari, fatura) -> None:
    h = admin_basliklari
    bugday = fatura["kalemler"][1]
    bos = istemci.post("/api/despatch-notes", headers=h, json=_govde(fatura["id"], lines=[]))
    assert bos.status_code == 422, bos.text

    tekrar = istemci.post(
        "/api/despatch-notes", headers=h,
        json=_govde(fatura["id"], lines=[{"invoice_item_id": bugday, "quantity": "1"},
                                         {"invoice_item_id": bugday, "quantity": "1"}]),
    )
    assert tekrar.status_code == 422, tekrar.text
    assert _kod(tekrar) == "SEVK_SATIRI_TEKRAR"

    baska = _sql_fatura(int(h["X-Company-ID"]), [("PART", "Baska", "1")])
    yabanci = istemci.post(
        "/api/despatch-notes", headers=h,
        json=_govde(fatura["id"], lines=[{"invoice_item_id": baska["kalemler"][0], "quantity": "1"}]),
    )
    assert yabanci.status_code == 422, yabanci.text
    assert _kod(yabanci) == "FATURA_KALEMI_YOK"


def test_YALNIZ_HIZMET_faturasi_422(istemci, admin_basliklari) -> None:
    """Keşif §2: yalnız hizmet içeren faturaya irsaliye açılmaz."""
    f = _sql_fatura(int(admin_basliklari["X-Company-ID"]), [("LABOR", "Servis", "2")])
    yanit = istemci.post("/api/despatch-notes", headers=admin_basliklari, json=_govde(f["id"]))
    assert yanit.status_code == 422, yanit.text
    assert _kod(yanit) == "SEVK_KALEMI_YOK"


def test_DESPATCHABLE_ITEMS_sayilari_ve_LABOR_yok(istemci, admin_basliklari, fatura) -> None:
    yanit = istemci.get(f"/api/invoices/{fatura['id']}/despatchable-items", headers=admin_basliklari)
    assert yanit.status_code == 200, yanit.text
    govde = yanit.json()
    assert govde["invoice_id"] == fatura["id"]
    assert [k["invoice_item_id"] for k in govde["items"]] == fatura["kalemler"][1:]
    # Fatura satır sırası hizmet kalemini DE sayar: Bugday faturanın 2. satırı.
    assert [k["invoice_line_no"] for k in govde["items"]] == [2, 3]
    assert [(k["invoiced"], k["despatched"], k["remaining"]) for k in govde["items"]] == [
        ("10.0000", "0.0000", "10.0000"), ("5.0000", "0.0000", "5.0000"),
    ]
    assert govde["complete"] is False
    assert istemci.get(
        "/api/invoices/999999/despatchable-items", headers=admin_basliklari
    ).status_code == 404


def test_CAPRAZ_KIRACI_fatura_404_kalem_422(istemci, admin_basliklari, fatura) -> None:
    """Başka firmanın faturası 404 (varlık sızmaz); başka firmanın KALEMİ,
    kendi faturamızın gövdesinde bile 422 — kalem kiracı VE fatura
    kapsamında çözülüyor."""
    from sqlalchemy import text

    from app.db import SessionLocal

    with SessionLocal() as db:
        yabanci_firma = db.execute(
            text("INSERT INTO companies(name,is_active,created_at) VALUES('E4b1 Yabanci',1,:t) RETURNING id"),
            {"t": datetime.now(timezone.utc)},
        ).scalar_one()
        db.commit()
    yabanci = _sql_fatura(int(yabanci_firma), [("PART", "Yabanci Mal", "4")])
    h = admin_basliklari
    assert istemci.get(
        f"/api/invoices/{yabanci['id']}/despatchable-items", headers=h
    ).status_code == 404
    assert istemci.post(
        "/api/despatch-notes", headers=h, json=_govde(yabanci["id"])
    ).status_code == 404
    karisik = istemci.post(
        "/api/despatch-notes", headers=h,
        json=_govde(fatura["id"], lines=[{"invoice_item_id": yabanci["kalemler"][0], "quantity": "1"}]),
    )
    assert karisik.status_code == 422, karisik.text
    assert _kod(karisik) == "FATURA_KALEMI_YOK"


def test_IKINCI_irsaliye_FARKLI_numara_ve_TOHUM_verilmis_en_buyukten(
    istemci, admin_basliklari
) -> None:
    """E4a sırayı FATURA KİMLİĞİNDEN türetiyordu — bir faturanın iki
    irsaliyesi AYNI numarayı alırdı. Sayaç ilk doğuşunda o yılın verilmiş en
    büyük numarasından başlar (elle verilen de sayılır)."""
    h = admin_basliklari
    f = _sql_fatura(int(h["X-Company-ID"]), [("PART", "Yulaf", "4")])
    kalem = f["kalemler"][0]
    elle = istemci.post(
        "/api/despatch-notes", headers=h,
        json=_govde(f["id"], issue_date="2031-01-05", despatch_number="IRS2031000000777",
                    lines=[{"invoice_item_id": kalem, "quantity": "1"}]),
    )
    assert elle.status_code == 201, elle.text
    oto = istemci.post(
        "/api/despatch-notes", headers=h,
        json=_govde(f["id"], issue_date="2031-01-06",
                    lines=[{"invoice_item_id": kalem, "quantity": "1"}]),
    )
    assert oto.status_code == 201, oto.text
    assert oto.json()["despatch_number"] == "IRS2031000000778"
    oto2 = istemci.post(
        "/api/despatch-notes", headers=h,
        json=_govde(f["id"], issue_date="2031-02-01",
                    lines=[{"invoice_item_id": kalem, "quantity": "1"}]),
    )
    assert oto2.status_code == 201, oto2.text
    assert oto2.json()["despatch_number"] == "IRS2031000000779"


def test_NUMARA_SAYACI_FIRMA_KAPSAMLI(istemci, admin_basliklari) -> None:
    """GİB numarası mükellef başınadır, sayaç da FİRMA başına olmalı.

    ÖLÇÜLDÜ: `document_sequences`in birincil anahtarı `(company_id,
    sequence_key)` (`app/core_schema.py`), yani sayaç zaten firma
    kapsamlı — tasarım kusuru YOK. Bu test iki yarısını da çiviler: BAŞKA
    firmanın aynı yıldaki çok büyük numarası (a) tohumu ETKİLEMEZ, (b)
    sayaç satırı yalnız bizim firmamız için doğar.
    MUTASYON: `_belge_numarasi`nın MAX sorgusundan `company_id == cid`i
    düşürmek bunu KIRMIZI yapar (tohum 900000000'dan başlardı).
    """
    from sqlalchemy import text

    from app.db import SessionLocal

    h = admin_basliklari
    cid = int(h["X-Company-ID"])
    an = datetime.now(timezone.utc)
    with SessionLocal() as db:
        yabanci = db.execute(
            text("INSERT INTO companies(name,is_active,created_at) VALUES('E4b1 Sayac Yabanci',1,:t) RETURNING id"),
            {"t": an},
        ).scalar_one()
        db.commit()
    y = _sql_fatura(int(yabanci), [("PART", "Yabanci", "1")])
    with SessionLocal() as db:
        db.execute(
            text(
                "INSERT INTO despatch_notes(company_id,invoice_id,despatch_uuid,despatch_number,"
                "issue_date,actual_shipment_at,driver_name,driver_national_id,vehicle_plate,"
                "delivery_address,delivery_postal_code,edespatch_status,created_at,updated_at) "
                "VALUES(:c,:f,'00000000-0000-4000-8000-0000000e4b1a','IRS2032900000000',"
                "'2032-01-01',:t,'A',:tc,:p,'Adres','34000','NONE',:t,:t)"
            ),
            {"c": yabanci, "f": y["id"], "t": an, "tc": SOFOR_TCKN, "p": PLAKA},
        )
        db.commit()
    f = _sql_fatura(cid, [("PART", "Bizim", "2")])
    yanit = istemci.post(
        "/api/despatch-notes", headers=h,
        json=_govde(f["id"], issue_date="2032-03-01",
                    lines=[{"invoice_item_id": f["kalemler"][0], "quantity": "1"}]),
    )
    assert yanit.status_code == 201, yanit.text
    assert yanit.json()["despatch_number"] == "IRS2032000000001"
    with SessionLocal() as db:
        sahipler = db.execute(
            text("SELECT company_id FROM document_sequences WHERE sequence_key='despatch_notes:IRS2032'")
        ).scalars().all()
    assert sahipler == [cid], sahipler


def test_UBL_N_DespatchLine_dogru_miktar_ve_fatura_satiri(istemci, admin_basliklari, fatura) -> None:
    """XML satırları FATURA kalemlerinden değil İRSALİYE satırlarından.

    `DeliveredQuantity` sevk miktarı, `unitCode` satırın birimi,
    `OrderLineReference/LineID` kalemin FATURA içindeki sırası (hizmet
    kalemi 1. satır olduğu için mallar 2 ve 3).
    """
    _, bugday, arpa = fatura["kalemler"]
    h = admin_basliklari
    yanit = istemci.post(
        "/api/despatch-notes", headers=h,
        json=_govde(fatura["id"], lines=[{"invoice_item_id": arpa, "quantity": "1.25"},
                                         {"invoice_item_id": bugday, "quantity": "7"}]),
    )
    assert yanit.status_code == 201, yanit.text
    xml = istemci.get(
        f"/api/despatch-notes/{yanit.json()['id']}/edespatch/download", headers=h
    )
    assert xml.status_code == 200, xml.text
    kok = ElementTree.fromstring(xml.content.decode("utf-8"))
    satirlar = kok.findall(f"{_CAC}DespatchLine")
    assert len(satirlar) == 2
    assert kok.findtext(f"{_CBC}LineCountNumeric") == "2"
    # Satırlar FATURA SIRASIYLA (istek sırası değil).
    assert [s.findtext(f"{_CBC}ID") for s in satirlar] == ["1", "2"]
    miktarlar = [s.find(f"{_CBC}DeliveredQuantity") for s in satirlar]
    assert [Decimal(m.text) for m in miktarlar] == [Decimal("7"), Decimal("1.25")]
    assert {m.get("unitCode") for m in miktarlar} == {"C62"}
    assert [
        s.findtext(f"{_CAC}OrderLineReference/{_CBC}LineID") for s in satirlar
    ] == ["2", "3"]
    assert [s.findtext(f"{_CAC}Item/{_CBC}Name") for s in satirlar] == ["Bugday", "Arpa"]


# ==========================================================================
# 4. ROL MATRİSİ — yeni GET ucu
# ==========================================================================

@pytest.fixture(scope="module")
def rol_basliklari(istemci, admin_basliklari):
    from sqlalchemy import text

    from app.auth import hash_password
    from app.db import SessionLocal

    cid = int(admin_basliklari["X-Company-ID"])
    an = datetime.now(timezone.utc)
    with SessionLocal() as db:
        for rol in ROLLER:
            uid = db.execute(
                text(
                    "INSERT INTO app_users(username,email,display_name,password_hash,"
                    "role,is_active,must_change_password,email_verified,created_at)"
                    " VALUES(:k,:e,:d,:p,:r,:a,:m,:v,:t) RETURNING id"
                ),
                {
                    "k": f"e4b1_{rol}", "e": f"e4b1_{rol}@ornek.test", "d": f"E4b1 {rol}",
                    "p": hash_password(ROL_PAROLASI), "r": rol, "a": True, "m": False,
                    "v": True, "t": an,
                },
            ).scalar_one()
            db.execute(
                text(
                    "INSERT INTO user_company_memberships(user_id,company_id,"
                    "is_default,created_at) VALUES(:u,:c,1,:t)"
                ),
                {"u": uid, "c": cid, "t": an},
            )
        db.commit()
    basliklar = {}
    for rol in ROLLER:
        giris = istemci.post(
            "/api/auth/login", json={"username": f"e4b1_{rol}", "password": ROL_PAROLASI}
        )
        assert giris.status_code == 200, (rol, giris.text)
        basliklar[rol] = {
            "Authorization": "Bearer " + giris.json()["access_token"],
            "X-Company-ID": str(cid),
        }
    return basliklar


def test_ROL_MATRISI_despatchable_items(istemci, rol_basliklari, fatura) -> None:
    """ÖLÇÜLDÜ: `sales` taşıyanlar (yonetici, muhasebe, satis) 200; `depo`
    ve `rapor` 403 — irsaliye uçlarıyla AYNI matris
    (`test_e4a_despatch_notes.py::test_ROL_MATRISI_...`)."""
    from app.auth import ROLE_PERMISSIONS

    matris = {
        rol: istemci.get(
            f"/api/invoices/{fatura['id']}/despatchable-items", headers=basliklar
        ).status_code
        for rol, basliklar in rol_basliklari.items()
    }
    assert matris == {
        "yonetici": 200, "muhasebe": 200, "satis": 200, "depo": 403, "rapor": 403,
    }, matris
    assert {r for r in ROLLER if "sales" in ROLE_PERMISSIONS[r]} == {
        "yonetici", "muhasebe", "satis",
    }
