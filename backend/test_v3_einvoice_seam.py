"""V3 e-Fatura seam (Increment 3) — default NoOp, non-breaking.

Pure-function checks (provider factory + UBL payload) run in-process; the
end-to-end invoice flow runs in a subprocess against a throwaway SQLite database
so DATABASE_URL is isolated (the engine binds at import).
"""

from __future__ import annotations

import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.einvoice import (
    IzibizEInvoiceProvider,
    NesEInvoiceProvider,
    NoOpEInvoiceProvider,
    build_einvoice_payload,
    get_einvoice_provider,
)


BACKEND = Path(__file__).resolve().parent


# --- Provider factory (no DB) ---------------------------------------------
def test_factory_defaults_to_noop_and_wires_stubs() -> None:
    assert isinstance(get_einvoice_provider(SimpleNamespace()), NoOpEInvoiceProvider)  # unset
    assert isinstance(get_einvoice_provider(SimpleNamespace(einvoice_provider="noop")), NoOpEInvoiceProvider)
    # A real provider resolves ONLY when it is both named AND credentialed.
    assert isinstance(
        get_einvoice_provider(
            SimpleNamespace(einvoice_provider="IZIBIZ", einvoice_username="u", einvoice_password="p")
        ),
        IzibizEInvoiceProvider,
    )
    assert isinstance(
        get_einvoice_provider(
            SimpleNamespace(einvoice_provider="nes", einvoice_username="u", einvoice_password="p")
        ),
        NesEInvoiceProvider,
    )
    # An unknown provider name falls back to the safe default, never an error.
    assert isinstance(get_einvoice_provider(SimpleNamespace(einvoice_provider="bogus")), NoOpEInvoiceProvider)
    # Half-configured (named but missing credentials) stays inert NoOp, not a raising stub.
    assert isinstance(get_einvoice_provider(SimpleNamespace(einvoice_provider="izibiz")), NoOpEInvoiceProvider)
    assert isinstance(
        get_einvoice_provider(SimpleNamespace(einvoice_provider="izibiz", einvoice_username="u")),
        NoOpEInvoiceProvider,
    )  # password missing


def test_noop_submit_is_inert() -> None:
    result = get_einvoice_provider(SimpleNamespace()).submit({"any": "payload"})
    assert result.status == "NONE"
    assert result.channel is None and result.external_id is None


def test_izibiz_adapter_fails_loudly_instead_of_faking_a_send() -> None:
    # With credentials the real adapter is wired. Its provider endpoint is still
    # ‹doğrulanacak› (empty), so it must FAIL loudly — never silently succeed and
    # never fabricate an external_id. Full adapter coverage: test_efatura_adapter.py.
    provider = get_einvoice_provider(
        SimpleNamespace(einvoice_provider="izibiz", einvoice_username="u", einvoice_password="p")
    )
    assert isinstance(provider, IzibizEInvoiceProvider)
    result = provider.submit({})  # proves the wiring, not a live call
    assert result.status == "FAILED"
    assert result.external_id is None
    assert result.error


def test_payload_totals_equal_snapshot_to_the_kurus() -> None:
    snapshot = {
        "invoice_number": "A-1",
        "currency": "try",
        "company": {"name": "Satıcı A.Ş.", "tax_number": "1111111111"},
        "customer": {"name": "Alıcı Ltd."},
        "totals": {"tax": Decimal("18.00"), "grand_total": Decimal("118.00")},
        "items": [
            {"description": "Servis", "qty": Decimal("1"), "unit_price": Decimal("100"),
             "tax_rate": Decimal("18"), "tax_amount": Decimal("18.00"), "total": Decimal("118.00")},
        ],
    }
    payload = build_einvoice_payload(snapshot)
    # Contract: payload monetary totals == invoice totals to the kuruş.
    assert Decimal(payload["monetary_total"]["payable_amount"]) == Decimal("118.00")
    assert Decimal(payload["monetary_total"]["tax_total"]) == Decimal("18.00")
    assert payload["currency"] == "TRY"
    assert payload["supplier"]["vkn"] == "1111111111"
    assert len(payload["lines"]) == 1
    # Must persist as a TEXT JSON string (no non-serialisable types, no float).
    import json

    assert isinstance(json.dumps(payload), str)


# --- End-to-end invoice flow (subprocess, isolated SQLite) ----------------
def test_generate_invoice_sets_none_and_persists_payload(tmp_path: Path) -> None:
    database = tmp_path / "einvoice.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    smoke = r"""
from decimal import Decimal
import json
from fastapi.testclient import TestClient
from app.main import app

with TestClient(app) as c:
    login = c.post('/api/auth/login', json={'username':'admin','password':'admin123'}).json()
    cid = login['companies'][0]['id']; uid = login['user']['id']
    h = {'Authorization':'Bearer '+login['access_token'],'X-Company-ID':str(cid)}
    ch = c.post('/api/auth/change-password', headers=h,
                json={'current_password':'admin123','new_password':'EinvoiceSeam123!'}).json()
    h['Authorization'] = 'Bearer ' + ch['access_token']

    cust = c.post('/api/customers', headers=h, json={'name':'Seam Musteri'}).json()
    mach = c.post('/api/machines', headers=h,
                  json={'customer_id':cust['id'],'brand':'B','model':'M','serial_number':'SN-1'}).json()
    wo = c.post('/api/work-orders', headers=h,
                json={'machine_id':mach['id'],'customer_id':cust['id'],'technician_id':uid,
                      'actual_hours':'2','labor_rate':'50'}).json()
    for st in ('IN_PROGRESS','COMPLETED'):
        assert c.patch(f"/api/work-orders/{wo['id']}/status", headers=h, json={'status':st}).status_code == 200

    inv = c.post('/api/invoices/generate', headers=h, json={'work_order_id':wo['id']})
    assert inv.status_code == 201, inv.text
    detail = inv.json(); iid = detail['id']
    # Default NoOp: identical observable behaviour, status NONE.
    assert detail.get('einvoice_status') == 'NONE', detail.get('einvoice_status')
    grand = Decimal(str(detail['totals']['grand_total'])); tax = Decimal(str(detail['totals']['tax']))

    # status endpoint reflects columns + the persisted (ready) payload
    stt = c.get(f'/api/invoices/{iid}/einvoice/status', headers=h).json()
    assert stt['einvoice_status'] == 'NONE'
    assert stt['einvoice_channel'] is None
    assert stt['einvoice_uuid'] is None
    payload = json.loads(stt['einvoice_payload'])
    assert Decimal(payload['monetary_total']['payable_amount']) == grand   # totals to the kurus
    assert Decimal(payload['monetary_total']['tax_total']) == tax

    # Missing provider configuration is the outer fail-closed gate, even when
    # the company tax number is also absent; the invoice state stays unchanged.
    sub = c.post(f'/api/invoices/{iid}/einvoice/submit', headers=h)
    assert sub.status_code == 503, sub.text
    assert sub.json()['detail'] == 'E-fatura entegrasyonu yapılandırılmamış'
    unchanged = c.get(f'/api/invoices/{iid}/einvoice/status', headers=h).json()
    assert unchanged['einvoice_status'] == 'NONE'
    assert unchanged['einvoice_last_error'] is None
    print(f"VKN_MISSING status={sub.status_code} body={sub.json()} einvoice_status={unchanged['einvoice_status']}")

    updated = c.put('/api/company-settings', headers=h, json={
        'negative_stock_policy':'block',
        'credit_limit_policy':'block',
        'tax_number':'1111111111',
    })
    assert updated.status_code == 200, updated.text
    # Yapılandırma yok: sessiz 200 yerine 503. Belge durumu DEĞİŞMEZ — gönderim
    # hiç denenmediği için einvoice_last_error'a da bir şey yazılmaz.
    sub = c.post(f'/api/invoices/{iid}/einvoice/submit', headers=h)
    assert sub.status_code == 503, sub.text
    assert sub.json()['detail'] == 'E-fatura entegrasyonu yapılandırılmamış'
    after = c.get(f'/api/invoices/{iid}/einvoice/status', headers=h).json()
    assert after['einvoice_status'] == 'NONE'
    assert after['einvoice_last_error'] is None
    assert after['einvoice_configured'] is False

    # tenant isolation: another company cannot see this invoice's e-invoice state
    other = c.post('/api/companies', headers=h, json={'name':'Firma B'}).json()['id']
    hb = {**h, 'X-Company-ID':str(other)}
    assert c.get(f'/api/invoices/{iid}/einvoice/status', headers=hb).status_code == 404

    print('EINVOICE_SEAM_OK')
"""
    completed = subprocess.run(
        [sys.executable, "-c", smoke], env=env, capture_output=True, text=True, cwd=str(BACKEND)
    )
    assert "EINVOICE_SEAM_OK" in completed.stdout, completed.stdout + completed.stderr
    print(completed.stdout.strip())
