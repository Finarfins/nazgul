"""API kontrat kapısı — POS yüzeyi.

Bu dosya bugünkü prod POS çökmesinin ("d.replace is not a function") sınıfını
CI'da kalıcı olarak yakalar: OpenAPI şeması ile CANLI yanıt gövdesi birbirinden
ayrışamaz. Üç garanti verir:

1. Kapsanan her uç nokta OpenAPI'de gerçek bir yanıt şeması BİLDİRMEK zorunda
   (şemasız ham dict uçları frontend tip üretimini kör bırakıyordu).
2. Canlı yanıt, bildirilen şemaya karşı jsonschema ile doğrulanır — şema string
   derken çalışma anında sayı dönen uç burada kırmızı yanar.
3. Kanonik serileştirme: para alanları tam 2 ondalıklı, miktar alanları tam
   4 ondalıklı STRING'dir (bkz. app/pos_contracts.py). SQLite ve PostgreSQL
   aynı JSON şeklini üretmek zorundadır; PG ikizi test_api_contract_postgresql
   aynı kontrolleri gerçek PostgreSQL üzerinde çalıştırır.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

import pytest

MONEY_RE = re.compile(r"^-?\d+\.\d{2}$")
QUANTITY_RE = re.compile(r"^-?\d+\.\d{4}$")

# Kanonik kontratın kapsadığı alan adları. Yeni bir para/miktar alanı eklenen
# uç bu listeye de eklenmeli; kontrat yüzeyi bilinçli olarak POS ile başlıyor.
MONEY_FIELDS = {
    "unit_price",
    "subtotal",
    "vat_total",
    "grand_total",
    "final_total",
    "paid_amount",
    "remaining_amount",
}
QUANTITY_FIELDS = {"stock", "current_stock", "total_quantity"}

# (method, path, needs_body) — kontrat kapsamındaki uçlar.
COVERED_ENDPOINTS = [
    ("get", "/api/quick-pick", None),
    ("get", "/api/pos/lookup", None),
    ("post", "/api/pos/sale", None),
]


def _login_and_seed(client) -> dict[str, str]:
    """Bootstrap admin ile oturum aç, POS yüzeyi için deterministik veri ek."""
    login = client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin123"}
    )
    assert login.status_code == 200, login.text
    body = login.json()
    headers = {
        "Authorization": "Bearer " + body["access_token"],
        "X-Company-ID": str(body["companies"][0]["id"]),
    }
    changed = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={"current_password": "admin123", "new_password": "Contract123!Gate"},
    )
    assert changed.status_code == 200, changed.text
    headers["Authorization"] = "Bearer " + changed.json()["access_token"]

    product = client.post(
        "/api/products",
        headers=headers,
        json={
            "name": "Kontrat Filtresi",
            "product_code": "CT-1",
            "barcode": "8690000000017",
            "purchase_price": "5.00",
            "sale_price": "19.95",
            "vat_rate": 20,
            "stock": "10",
            "unit": "Adet",
        },
    )
    assert product.status_code == 201, product.text
    return headers


def _schema_for(openapi: dict, method: str, path: str) -> dict:
    operation = openapi["paths"].get(path, {}).get(method)
    assert operation is not None, f"{method.upper()} {path} OpenAPI'de yok"
    responses = operation.get("responses", {})
    success = responses.get("200") or responses.get("201")
    assert success, f"{method.upper()} {path} başarılı yanıt tanımlamıyor"
    schema = (
        success.get("content", {}).get("application/json", {}).get("schema")
    )
    assert schema, (
        f"{method.upper()} {path} yanıt şeması bildirmiyor — ham dict dönen uç "
        "frontend tip üretimini kör bırakır (bugünkü POS çökmesinin kökü)"
    )
    return schema


def _validate(instance: Any, schema: dict, openapi: dict, label: str) -> None:
    import jsonschema

    # "#/components/..." referansları kök şema belgesine karşı çözülür; kökü
    # yanıt şeması + components olarak kurmak inline çözümlemeye yetiyor.
    document = dict(schema)
    document["components"] = openapi["components"]
    try:
        jsonschema.validate(instance=instance, schema=document)
    except jsonschema.ValidationError as exc:  # pragma: no cover - hata yolu
        pytest.fail(f"{label}: canlı yanıt bildirilen şemaya uymuyor: {exc.message}")


def _assert_canonical(value: Any, label: str) -> None:
    """Para/miktar alanları kanonik sabit ölçekli string olmalı."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in MONEY_FIELDS:
                assert isinstance(item, str), (
                    f"{label}.{key} JSON'da string olmalı, {type(item).__name__} geldi "
                    "(sayı sızması = bugünkü POS çökmesi sınıfı)"
                )
                assert MONEY_RE.match(item), f"{label}.{key}={item!r} kanonik 2 ondalık değil"
            elif key in QUANTITY_FIELDS:
                assert isinstance(item, str), (
                    f"{label}.{key} JSON'da string olmalı, {type(item).__name__} geldi"
                )
                assert QUANTITY_RE.match(item), (
                    f"{label}.{key}={item!r} kanonik 4 ondalık değil"
                )
            else:
                _assert_canonical(item, f"{label}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_canonical(item, f"{label}[{index}]")


def run_contract_checks(client) -> None:
    from app.main import app

    headers = _login_and_seed(client)
    openapi = app.openapi()

    # Kapsanan her ucun şema bildirmesi zorunlu (canlı çağrıdan bağımsız).
    for method, path, _ in COVERED_ENDPOINTS:
        _schema_for(openapi, method, path)

    lookup = client.get(
        "/api/pos/lookup", headers=headers, params={"barcode": "8690000000017"}
    )
    assert lookup.status_code == 200, lookup.text
    lookup_body = lookup.json()
    _validate(lookup_body, _schema_for(openapi, "get", "/api/pos/lookup"), openapi, "pos/lookup")
    _assert_canonical(lookup_body, "pos/lookup")

    sale = client.post(
        "/api/pos/sale",
        headers={**headers, "Idempotency-Key": "contract-pos-sale"},
        json={
            "items": [
                {
                    "product_id": lookup_body["id"],
                    "quantity": "2",
                    "unit_price": lookup_body["unit_price"],
                }
            ],
            "payment_type": "cash",
        },
    )
    assert sale.status_code == 201, sale.text
    sale_body = sale.json()
    _validate(sale_body, _schema_for(openapi, "post", "/api/pos/sale"), openapi, "pos/sale")
    _assert_canonical(sale_body, "pos/sale")
    # Satış matematiği kanonik string üzerinden de doğru: 2 x 19.95 = 39.90.
    assert sale_body["final_total"] == "39.90", sale_body

    quick = client.get("/api/quick-pick", headers=headers)
    assert quick.status_code == 200, quick.text
    quick_body = quick.json()
    assert quick_body, "quick-pick satış geçmişine rağmen boş döndü"
    _validate(quick_body, _schema_for(openapi, "get", "/api/quick-pick"), openapi, "quick-pick")
    _assert_canonical(quick_body, "quick-pick")
    # Bugünkü bug'ın birebir tetiği: quick-pick unit_price sayı SIZAMAZ.
    assert isinstance(quick_body[0]["unit_price"], str)


def test_api_contract_sqlite(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    if not (os.environ.get("CONTRACT_TEST_DATABASE_URL") or os.environ.get("APP_TEST_DATABASE_URL")):
        db_path = tmp_path / "contract.db"
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    else:
        # PG ortamı dışarıdan verildiyse (ör. lokal tam parite koşusu) onu kullan.
        monkeypatch.setenv(
            "DATABASE_URL",
            os.environ.get("CONTRACT_TEST_DATABASE_URL")
            or os.environ["APP_TEST_DATABASE_URL"],
        )

    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        run_contract_checks(client)
