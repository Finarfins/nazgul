"""İş günü İstanbul'a sabitlendi mi — birim + uçtan uca regresyon.

Prod EC2 UTC koşar, kullanıcılar Europe/Istanbul'dadır. İstanbul'da 00:00-03:00
arasında UTC hâlâ önceki günü gösterir; o aralıkta oluşan kayıtlar UTC tabanlı
"bugün" ile bir gün geride kalır. Bu dosya o üç saatlik pencereyi sabitleyerek
(gerçek saate HİÇ bakmadan) davranışı pinler.

Zaman ``app.business_time.business_now`` yamalanarak donduruluyor:
``business_today`` onu modül global'inden çağırdığı için tek yama tüm çağrı
noktalarını kapsar. Depoda freezegun yok; tek zaman kaynağı olması bu yüzden
tasarımın parçası.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from app import business_time
from app.business_time import ISTANBUL, business_today


BACKEND = Path(__file__).resolve().parent

# İstanbul 01:30 == UTC 22:30 (önceki gün). Testlerin tamamı bu ana kilitli.
# Tarih bilerek "gerçek bugün"den uzak: bozuk bir business_today() tesadüfen
# doğru sonuç veremesin.
FROZEN_LOCAL = datetime(2026, 3, 15, 1, 30, tzinfo=ISTANBUL)
ISTANBUL_DAY = date(2026, 3, 15)
UTC_DAY = date(2026, 3, 14)


# --- Birim: takvim günü semantiği -------------------------------------------


def test_frozen_moment_really_straddles_midnight() -> None:
    """Senaryonun kendisi doğru mu: UTC günü İstanbul gününden geri olmalı."""
    assert FROZEN_LOCAL.astimezone(timezone.utc).date() == UTC_DAY
    assert FROZEN_LOCAL.date() == ISTANBUL_DAY
    assert UTC_DAY != ISTANBUL_DAY


def test_business_today_follows_business_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(business_time, "business_now", lambda: FROZEN_LOCAL)
    assert business_today() == ISTANBUL_DAY


def test_business_today_is_istanbul_not_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    """Asıl hata: UTC 22:30 anında iş günü ertesi gündür."""
    utc_moment = datetime(2026, 3, 14, 22, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(
        business_time, "business_now", lambda: utc_moment.astimezone(ISTANBUL)
    )
    assert business_today() == ISTANBUL_DAY
    assert utc_moment.date() == UTC_DAY


def test_business_now_is_timezone_aware() -> None:
    moment = business_time.business_now()
    assert moment.tzinfo is not None
    assert moment.utcoffset() is not None


def test_single_istanbul_source() -> None:
    """İkinci bir ISTANBUL kopyası doğmasın (service_receivable_engine taşındı)."""
    from app import service_receivable_engine

    assert service_receivable_engine.ISTANBUL is ISTANBUL


# --- Kaynak taraması: iş günü modüllerinde çıplak date.today() kalmasın ------

# Kapsam dışı bırakılan backlog maddesi (bilerek olduğu gibi kaldı).
_TODAY_ALLOWLIST = {
    # TCMB kur bülteninin tarihi: doğrusu ne UTC ne İstanbul, bültenin kendi
    # tarihi olmalı. Ayrı iş olarak backlog'da.
    "app/routers/supplier_prices.py",
}

_BUSINESS_DATE_MODULES = [
    "app/entity_detail.py",
    "app/payment_allocation_engine.py",
    "app/statement.py",
    "app/schemas.py",
    "app/labels.py",
    "app/routers/absorption.py",
    "app/routers/analytics.py",
    "app/routers/companies.py",
    "app/routers/customers.py",
    "app/routers/dashboard.py",
    "app/routers/finance.py",
    "app/routers/imports.py",
    "app/routers/pos.py",
    "app/routers/products.py",
    "app/routers/quick_pick.py",
    "app/routers/reports.py",
    "app/routers/seasonal_plan.py",
    "app/routers/supplier_prices.py",
    "app/routers/transactions.py",
]


@pytest.mark.parametrize("relative_path", _BUSINESS_DATE_MODULES)
def test_no_bare_today_in_business_date_modules(relative_path: str) -> None:
    source = (BACKEND / relative_path).read_text(encoding="utf-8")
    # ``_date.today()`` (aliased import) da sayılmalı; \b onu kaçırır.
    hits = re.findall(r"(?<![A-Za-z0-9])_?date\.today\(\)", source)
    expected = 1 if relative_path in _TODAY_ALLOWLIST else 0
    assert len(hits) == expected, (
        f"{relative_path}: {len(hits)} adet date.today() bulundu, {expected} bekleniyordu. "
        "İş günü okuyan yerler business_today() kullanmalı."
    )


@pytest.mark.parametrize(
    "relative_path", ["app/routers/products.py", "app/routers/imports.py"]
)
def test_stock_movement_date_is_bound_not_current_date(relative_path: str) -> None:
    """CURRENT_DATE ham SQL'de kalmamalı: PostgreSQL'de sunucu (UTC) günüdür."""
    source = (BACKEND / relative_path).read_text(encoding="utf-8")
    assert "CURRENT_DATE" not in source
    assert "business_today()" in source


def test_business_time_has_no_app_imports() -> None:
    """Döngü riski: bu modül hiçbir app modülünü import etmemeli."""
    source = (BACKEND / "app" / "business_time.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*from\s+\.", source, re.MULTILINE)
    assert not re.search(r"^\s*import\s+app\b", source, re.MULTILINE)


# --- Uçtan uca: SQLite ve PostgreSQL ortak gövdesi --------------------------


def run_business_date_smoke(database_url: str) -> str:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "ISTANBUL_BUSINESS_DATE_OK" in completed.stdout
    return completed.stdout


def test_business_date_endpoints_sqlite(tmp_path: Path) -> None:
    database = tmp_path / "istanbul-business-date.db"
    run_business_date_smoke(f"sqlite:///{database.as_posix()}")


_SMOKE = r'''
from datetime import date, datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import text

import app.business_time as business_time
from app.main import app
from app.db import SessionLocal

# --- zamanı dondur: İstanbul 01:30, UTC hâlâ 14 Mart ------------------------
FROZEN_LOCAL = datetime(2026, 3, 15, 1, 30, tzinfo=business_time.ISTANBUL)
ISTANBUL_DAY = "2026-03-15"
UTC_DAY = "2026-03-14"
assert FROZEN_LOCAL.astimezone(timezone.utc).date().isoformat() == UTC_DAY

# business_today() bunu modül global'inden çağırır; tek yama her yeri kapsar.
business_time.business_now = lambda: FROZEN_LOCAL
assert business_time.business_today().isoformat() == ISTANBUL_DAY

with TestClient(app) as client:
    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin123"},
    ).json()
    headers = {
        "Authorization": "Bearer " + login["access_token"],
        "X-Company-ID": str(login["companies"][0]["id"]),
    }
    changed = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={
            "current_password": "admin123",
            "new_password": "IstanbulBusiness123!",
        },
    )
    assert changed.status_code == 200, changed.text
    headers["Authorization"] = "Bearer " + changed.json()["access_token"]

    cid = client.post(
        "/api/companies", headers=headers, json={"name": "İstanbul İş Günü"}
    ).json()["id"]
    headers["X-Company-ID"] = str(cid)
    customer = client.post(
        "/api/customers", headers=headers, json={"name": "Gece Yarısı Müşterisi"}
    ).json()["id"]
    product = client.post(
        "/api/products",
        headers=headers,
        json={"name": "İş Günü Ürünü", "sale_price": "100", "stock": "1000"},
    ).json()["id"]

    # ---------------------------------------------------------------- 1) aging
    # İstanbul'da bugün (01:30) kesilen harman-vadeli tahakkuk. UTC "bugün"ü
    # 2026-03-14 olduğu için eski davranışta order_date<=as_of elemesine takılıp
    # varsayılan raporda GÖRÜNMÜYORDU.
    sale = client.post(
        "/api/orders",
        headers=headers,
        json={
            "entity_id": customer,
            "transaction_date": ISTANBUL_DAY,
            "due_date": "2026-04-10",
            "payment_term": "HARMAN_VADELI",
            "status": "completed",
            "payment_method": "cash",
            "paid_amount": "0",
            "items": [
                {
                    "product_id": product,
                    "quantity": "1",
                    "unit_price": "100",
                    "vat_rate": 20,
                }
            ],
        },
    )
    assert sale.status_code == 201, sale.text
    todays_accrual = sale.json()["id"]

    default_report = client.get("/api/reports/receivables-aging", headers=headers)
    assert default_report.status_code == 200, default_report.text
    default_data = default_report.json()
    assert default_data["as_of"] == ISTANBUL_DAY, default_data["as_of"]
    default_ids = {
        document["id"]
        for row in default_data["customers"]
        for document in row["documents"]
    }
    assert todays_accrual in default_ids, (
        "Bugünün tahakkuku varsayılan aging raporunda görünmüyor: " + str(default_ids)
    )

    # ---------------------------------------- 2) regresyon: açık as_of aynen
    # Kullanıcı as_of verdiğinde davranış DEĞİŞMEZ; UTC gününü isterse dünü alır.
    explicit = client.get(
        "/api/reports/receivables-aging",
        headers=headers,
        params={"as_of": UTC_DAY},
    )
    assert explicit.status_code == 200, explicit.text
    explicit_data = explicit.json()
    assert explicit_data["as_of"] == UTC_DAY, explicit_data["as_of"]
    explicit_ids = {
        document["id"]
        for row in explicit_data["customers"]
        for document in row["documents"]
    }
    assert todays_accrual not in explicit_ids, (
        "Açık as_of=" + UTC_DAY + " verildiğinde bugünün belgesi elenmeliydi"
    )

    # İleri bir as_of da aynen saygı görmeli (varsayılana düşmemeli).
    future = client.get(
        "/api/reports/receivables-aging",
        headers=headers,
        params={"as_of": "2026-04-15"},
    )
    assert future.status_code == 200, future.text
    assert future.json()["as_of"] == "2026-04-15"

    # ------------------------------------------------------------------ 3) POS
    # POS satışı İstanbul gününe yazılmalı; eski davranışta 2026-03-14 olurdu.
    pos = client.post(
        "/api/pos/sale",
        headers={**headers, "Idempotency-Key": "istanbul-business-date-pos-1"},
        json={
            "items": [
                {"product_id": product, "quantity": "1", "unit_price": "100"}
            ],
            "payment_type": "cash",
        },
    )
    assert pos.status_code == 201, pos.text
    pos_sale_id = pos.json()["sale_id"]
    with SessionLocal() as db:
        pos_date = db.execute(
            text("SELECT order_date FROM orders WHERE id=:id AND company_id=:cid"),
            {"id": pos_sale_id, "cid": cid},
        ).scalar_one()
    assert str(pos_date)[:10] == ISTANBUL_DAY, (
        "POS satışı " + str(pos_date) + " tarihine yazıldı, " + ISTANBUL_DAY + " olmalıydı"
    )

    # ------------------------------------------------- 4) tahsis tarih guard'ı
    # "Tahsis işlem tarihi gelecekte olamaz" guard'ı, kullanıcının BUGÜNÜNÜ
    # reddetmemeli. UTC tabanlıyken 2026-03-15 > 2026-03-14 olduğu için 422 idi.
    from app.payment_allocation_engine import _validate_mutation_date

    _validate_mutation_date(date(2026, 3, 15), "2026-03-01", None)

    # Gerçekten gelecek olan tarih hâlâ reddedilmeli (guard delinmedi).
    from fastapi import HTTPException

    try:
        _validate_mutation_date(date(2026, 3, 16), "2026-03-01", None)
    except HTTPException as exc:
        assert exc.status_code == 422, exc.status_code
    else:
        raise AssertionError("Gelecek tarihli tahsis reddedilmedi")

    # ------------------------------------------- 5) stok hareketi iş gününde
    # products.py artık CURRENT_DATE yerine business_today() bind ediyor.
    stock_product = client.post(
        "/api/products",
        headers=headers,
        json={"name": "Açılış Stoklu", "sale_price": "50", "stock": "7"},
    )
    assert stock_product.status_code == 201, stock_product.text
    with SessionLocal() as db:
        movement_date = db.execute(
            text(
                """SELECT movement_date FROM stock_movements
                WHERE company_id=:cid AND product_id=:pid AND movement_type='opening'"""
            ),
            {"cid": cid, "pid": stock_product.json()["id"]},
        ).scalar_one()
    assert str(movement_date)[:10] == ISTANBUL_DAY, (
        "Açılış stok hareketi " + str(movement_date) + " tarihine yazıldı"
    )

print("ISTANBUL_BUSINESS_DATE_OK")
'''
