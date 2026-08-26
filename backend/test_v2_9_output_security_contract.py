from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.routers import outputs


def request(role: str | None) -> Request:
    value = Request({"type": "http", "method": "GET", "path": "/api/test", "headers": []})
    if role is not None:
        value.state.user = {"id": 1, "role": role}
    return value


def test_output_permissions_follow_business_roles() -> None:
    outputs._require_permission(request("satis"), "sales")
    outputs._require_permission(request("depo"), "stock")
    outputs._require_permission(request("muhasebe"), "purchases")
    outputs._require_permission(request("admin"), "stock")

    with pytest.raises(HTTPException) as denied:
        outputs._require_permission(request("rapor"), "stock")
    assert denied.value.status_code == 403

    # Satış rolünün stok çıktısına erişimi YOKTUR: ürün etiketi/QR uçları
    # "stock" ister, satis izinleri ise {read, sales, payments} ile sınırlıdır.
    # Etiket uçları eklenirken bu sınırın gevşetilmemesi gerekir.
    with pytest.raises(HTTPException) as sales_denied:
        outputs._require_permission(request("satis"), "stock")
    assert sales_denied.value.status_code == 403

    with pytest.raises(HTTPException) as anonymous:
        outputs._require_permission(request(None), "sales")
    assert anonymous.value.status_code == 403


def test_document_kind_carries_explicit_permission() -> None:
    assert outputs._kind("orders")["permission"] == "sales"
    assert outputs._kind("purchases")["permission"] == "purchases"
    with pytest.raises(HTTPException) as missing:
        outputs._kind("unknown")
    assert missing.value.status_code == 404


def test_excel_formula_and_filename_sanitization() -> None:
    assert outputs._safe_excel_cell("=SUM(A1:A2)") == "'=SUM(A1:A2)"
    assert outputs._safe_excel_cell(" \t@cmd") == "' \t@cmd"
    assert outputs._safe_excel_cell("\r\n-CMD") == "'\r\n-CMD"
    assert outputs._safe_excel_cell(-12.5) == -12.5
    assert outputs._safe_filename('INV\r\nX-Test: 1/../../', "document") == "INV-X-Test-1"
    assert outputs._safe_filename("...", "document") == "document"
    assert len(outputs._safe_filename("x" * 500, "document")) == 100


def test_document_queries_keep_tenant_join_guards() -> None:
    source = inspect.getsource(outputs._document)
    assert "e.company_id=h.company_id" in source
    assert "w.company_id=h.company_id" in source
    assert "h.company_id=:cid" in source


def test_pdf_note_is_escaped_before_reportlab_paragraph() -> None:
    source = inspect.getsource(outputs.document_pdf)
    assert "escape(str(head" in source
    assert "safe_note" in source


def test_all_sensitive_output_routes_require_explicit_permission() -> None:
    pdf = inspect.getsource(outputs.document_pdf)
    xlsx = inspect.getsource(outputs.document_xlsx)
    qr = inspect.getsource(outputs.product_qr)
    label = inspect.getsource(outputs.product_label)
    barcode_label = inspect.getsource(outputs.product_barcode_label)
    labels = inspect.getsource(outputs.product_labels)
    products = inspect.getsource(outputs.products_xlsx)
    assert '_require_permission(request, config["permission"])' in pdf
    assert '_require_permission(request, config["permission"])' in xlsx
    assert '_require_permission(request, "stock")' in qr
    assert '_require_permission(request, "stock")' in label
    assert '_require_permission(request, "stock")' in barcode_label
    assert '_require_permission(request, "stock")' in labels
    assert '_require_permission(request, "stock")' in products


def test_label_queries_keep_tenant_scope() -> None:
    """Etiket sorguları aktif firmayla sınırlıdır; başka firmanın ürünü sızmaz."""
    single = inspect.getsource(outputs._product)
    bulk = inspect.getsource(outputs._label_products)
    assert "company_id=:cid" in single
    assert "company_id=:cid" in bulk
    # Toplu uçta kimlikler bağlı parametre olarak geçer, SQL'e gömülmez.
    assert "placeholders" in bulk
    # İstenen bir kimlik firmaya ait değilse istek tümüyle 404'e düşer.
    assert "missing" in bulk and "HTTPException(404" in bulk
