from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.auth import ROLE_PERMISSIONS, required_permission

BACKEND = Path(__file__).resolve().parent


def test_payments_and_finance_permission_map() -> None:
    """Rol→yetki haritasının TAMAMINI sabitler.

    Eşitlik bilerek birebir: yeni bir yetki eklendiğinde bu test düşer ve
    ekleyen kişi hangi rollerin genişlediğini AÇIKÇA yazmak zorunda kalır.
    Alt küme kontrolü olsaydı bir rolün sessizce genişlemesi görülmezdi.

    Tarla yetkileri (mobil-erp#2, FAZ 2) üç parçaya bölündü — tek `farm`
    yetkisi olsaydı depo rolü girdi girebilmek için parsel/sezon yazma hakkı
    da alırdı:
      * ``farm.view``    — okuma; rapor ve satış dâhil herkes.
      * ``farm.manage``  — çiftlik/parsel/sezon/faaliyet yazma; yalnız yönetici.
      * ``farm.inputs``  — faaliyete girdi (gübre/ilaç) bağlama; depo da yapar,
                           çünkü stoktan düşen malzemeyi fiilen depo işliyor.
    """
    assert ROLE_PERMISSIONS == {
        "admin": {"*"},
        # FAZ-1 bildirim yetkileri (tasarım §6): görüntüleme, onay, tetikleme
        # ve yönetim kasıtlı olarak dört ayrı yetkidir.
        "yonetici": {
            "farm.view",
            "farm.manage",
            "farm.inputs",
            "herd.view",
            "herd.manage",
            "herd.health",
            "read",
            "sales",
            "field_service",
            "purchases",
            "payments",
            "finance",
            "stock",
            "reports",
            "users",
            "machines",
            "notifications",
            "notifications_approve",
                "notifications_dispatch",
                "notifications_admin",
                "supplier_prices.view",
                "supplier_prices.import",
                "supplier_prices.apply",
                "supplier_prices.override_block",
                "farm.view",
                "farm.manage",
                "farm.inputs",
        },
        "muhasebe": {
            "farm.view",
            "herd.view",
            "read",
            "sales",
            "purchases",
            "payments",
            "finance",
            "reports",
            "notifications",
            "notifications_approve",
                "notifications_dispatch",
                "supplier_prices.view",
                "supplier_prices.import",
                "supplier_prices.apply",
                "farm.view",
        },
        "satis": {
            "read", "sales", "field_service", "payments", "notifications",
            "farm.view", "herd.view",
        },
        # Depo girdi BAĞLAYABİLİR ama parsel/sezon YAZAMAZ (farm.manage yok).
        "depo": {
            "read", "stock", "purchases", "supplier_prices.view",
            "farm.view", "farm.inputs",
            # Depo hayvan sağlık kaydı GİREMEZ (herd.health yok).
            "herd.view",
        },
        "rapor": {"read", "reports", "farm.view", "herd.view"},
    }
    # Yalnız yönetici tarla kaydı yazabilir. Bu satır düşerse bir rol sessizce
    # yazma hakkı kazanmış demektir.
    assert {rol for rol, izin in ROLE_PERMISSIONS.items()
            if "farm.manage" in izin} == {"yonetici"}
    # Hayvancılık: yazma ve SAĞLIK kaydı da yalnız yöneticide. `herd.health`
    # ayrı bir izin çünkü veteriner aşı girip hayvan satamamalı.
    assert {rol for rol, izin in ROLE_PERMISSIONS.items()
            if "herd.manage" in izin} == {"yonetici"}
    assert {rol for rol, izin in ROLE_PERMISSIONS.items()
            if "herd.health" in izin} == {"yonetici"}
    assert required_permission("GET", "/api/payments") == "payments"
    assert required_permission("POST", "/api/payments") == "payments"
    assert required_permission("GET", "/api/payments/accounts") == "payments"
    assert required_permission("GET", "/api/finance/accounts") == "finance"
    assert required_permission("POST", "/api/finance/transfers") == "finance"


def test_sales_can_collect_without_treasury_access(tmp_path: Path) -> None:
    database = tmp_path / "payments-permission.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    smoke = r'''
from fastapi.testclient import TestClient
from app.main import app

INITIAL_PASSWORD = 'PermissionInit123!'
ROTATED_PASSWORD = 'PermissionRotated123!'

def create_user(client, admin_headers, username, role):
    created = client.post('/api/users', headers=admin_headers, json={
        'username':username,
        'display_name':username,
        'password':INITIAL_PASSWORD,
        'role':role,
    })
    assert created.status_code in (200, 201), created.text
    login = client.post('/api/auth/login', json={
        'username':username,
        'password':INITIAL_PASSWORD,
    })
    assert login.status_code == 200, login.text
    body = login.json()
    headers = {
        'Authorization':'Bearer ' + body['access_token'],
        'X-Company-ID':str(body['companies'][0]['id']),
    }
    changed = client.post('/api/auth/change-password', headers=headers, json={
        'current_password':INITIAL_PASSWORD,
        'new_password':ROTATED_PASSWORD,
    })
    assert changed.status_code == 200, changed.text
    headers['Authorization'] = 'Bearer ' + changed.json()['access_token']
    return headers

with TestClient(app) as client:
    login = client.post('/api/auth/login', json={
        'username':'admin',
        'password':'admin123',
    })
    assert login.status_code == 200, login.text
    body = login.json()
    admin_headers = {
        'Authorization':'Bearer ' + body['access_token'],
        'X-Company-ID':str(body['companies'][0]['id']),
    }
    changed = client.post('/api/auth/change-password', headers=admin_headers, json={
        'current_password':'admin123',
        'new_password':'PaymentsAdmin123!',
    })
    assert changed.status_code == 200, changed.text
    admin_headers['Authorization'] = 'Bearer ' + changed.json()['access_token']

    customer = client.post('/api/customers', headers=admin_headers, json={
        'name':'Yetki Test Müşterisi',
    })
    assert customer.status_code in (200, 201), customer.text
    customer_id = customer.json()['id']

    account_response = client.get('/api/finance/accounts', headers=admin_headers)
    assert account_response.status_code == 200, account_response.text
    accounts = account_response.json()
    cash = next(item for item in accounts if item['account_type'] == 'cash')
    bank = next(item for item in accounts if item['account_type'] == 'bank')

    sales_headers = create_user(client, admin_headers, 'payments_sales', 'satis')
    accounting_headers = create_user(client, admin_headers, 'payments_accounting', 'muhasebe')
    reporter_headers = create_user(client, admin_headers, 'payments_reporter', 'rapor')
    warehouse_headers = create_user(client, admin_headers, 'payments_warehouse', 'depo')

    assert client.get('/api/receivables', headers=sales_headers).status_code == 200
    assert client.get('/api/receivables', headers=accounting_headers).status_code == 200
    assert client.get('/api/receivables', headers=reporter_headers).status_code == 403
    assert client.get('/api/receivables', headers=warehouse_headers).status_code == 403

    sales_payment = client.post('/api/payments', headers=sales_headers, json={
        'entity_type':'customer',
        'entity_id':customer_id,
        'amount':'25.50',
        'payment_date':'2026-07-25',
        'note':'Satış rolü tahsilatı',
        'payment_method':'cash',
        'account_id':cash['id'],
    })
    assert sales_payment.status_code == 201, sales_payment.text

    sales_options = client.get(
        '/api/payments/accounts',
        headers=sales_headers,
        params={'active_only':True},
    )
    assert sales_options.status_code == 200, sales_options.text
    assert sales_options.json()
    allowed_fields = {'id', 'name', 'account_type', 'currency', 'is_active'}
    forbidden_fields = {
        'balance',
        'iban',
        'opening_balance',
        'transaction_count',
        'bank_name',
    }
    for option in sales_options.json():
        assert set(option) == allowed_fields, option
        assert forbidden_fields.isdisjoint(option), option

    assert client.get(
        '/api/finance/accounts',
        headers=sales_headers,
    ).status_code == 403
    denied_transfer = client.post('/api/finance/transfers', headers=sales_headers, json={
        'source_account_id':cash['id'],
        'target_account_id':bank['id'],
        'txn_date':'2026-07-25',
        'amount':'1.00',
        'description':'Yetkisiz virman',
    })
    assert denied_transfer.status_code == 403, denied_transfer.text

    accounting_payment = client.post('/api/payments', headers=accounting_headers, json={
        'entity_type':'customer',
        'entity_id':customer_id,
        'amount':'10.00',
        'payment_date':'2026-07-25',
        'note':'Muhasebe tahsilatı',
        'payment_method':'cash',
        'account_id':cash['id'],
    })
    assert accounting_payment.status_code == 201, accounting_payment.text
    assert client.get(
        '/api/payments/accounts',
        headers=accounting_headers,
    ).status_code == 200
    assert client.get(
        '/api/finance/accounts',
        headers=accounting_headers,
    ).status_code == 200
    accounting_transfer = client.post('/api/finance/transfers', headers=accounting_headers, json={
        'source_account_id':cash['id'],
        'target_account_id':bank['id'],
        'txn_date':'2026-07-25',
        'amount':'1.00',
        'description':'Muhasebe virmanı',
    })
    assert accounting_transfer.status_code == 201, accounting_transfer.text

    accounting_dashboard = client.get('/api/dashboard', headers=accounting_headers)
    assert accounting_dashboard.status_code == 200, accounting_dashboard.text
    accounting_dashboard_body = accounting_dashboard.json()
    assert accounting_dashboard_body['finance_accounts']
    assert accounting_dashboard_body['cash_bank_total'] != 0
    assert any(
        item['type'] == 'finance'
        for item in accounting_dashboard_body['recent_activity']
    )

    assert client.get(
        '/api/finance/accounts',
        headers=reporter_headers,
    ).status_code == 403

    for restricted_headers in (sales_headers, warehouse_headers, reporter_headers):
        restricted_dashboard = client.get('/api/dashboard', headers=restricted_headers)
        assert restricted_dashboard.status_code == 200, restricted_dashboard.text
        restricted_dashboard_body = restricted_dashboard.json()
        assert restricted_dashboard_body['finance_accounts'] == []
        assert restricted_dashboard_body['cash_bank_total'] == 0
        assert not any(
            item['type'] == 'finance'
            for item in restricted_dashboard_body['recent_activity']
        )
'''
    result = subprocess.run(
        [sys.executable, "-c", smoke],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
