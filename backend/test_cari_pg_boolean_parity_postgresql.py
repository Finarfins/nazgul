from __future__ import annotations

import os

import pytest


@pytest.mark.postgresql
def test_cari_read_path_boolean_parity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression for the SQLite->PostgreSQL boolean parity bug.

    ``customers.is_active`` / ``suppliers.is_active`` / ``products.active`` are real
    ``boolean`` columns on PostgreSQL. The cari read path compared them as integers
    (``COALESCE(x.is_active, 1)=1``) and the supplier write path inserted an integer
    (``is_active=1``). Both raise a datatype mismatch on PostgreSQL while passing on
    SQLite, so this whole class was invisible to the SQLite test suite. This test runs
    the affected endpoints against a real PostgreSQL server and must get 200/201 with
    the rows actually returned.
    """
    database_url = os.environ.get("CARI_PARITY_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("CARI_PARITY_TEST_DATABASE_URL is not configured")
    monkeypatch.setenv("DATABASE_URL", database_url)

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        login = client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin123'})
        assert login.status_code == 200, login.text
        body = login.json()
        headers = {
            'Authorization': 'Bearer ' + body['access_token'],
            'X-Company-ID': str(body['companies'][0]['id']),
        }
        changed = client.post('/api/auth/change-password', headers=headers, json={
            'current_password': 'admin123', 'new_password': 'CariParityPg123!',
        })
        assert changed.status_code == 200, changed.text
        headers['Authorization'] = 'Bearer ' + changed.json()['access_token']

        # --- WRITE paths: supplier create previously 500'd (integer into boolean) ---
        customer = client.post('/api/customers', headers=headers, json={
            'name': 'Cari Parity Müşteri', 'opening_balance': 0,
            'risk_limit': 0, 'payment_term_days': 0, 'is_active': True,
        })
        assert customer.status_code == 201, customer.text
        customer_id = customer.json()['id']

        supplier = client.post('/api/suppliers', headers=headers, json={
            'name': 'Cari Parity Tedarikçi', 'opening_balance': 0,
            'risk_limit': 0, 'payment_term_days': 0, 'is_active': True,
        })
        assert supplier.status_code == 201, supplier.text
        supplier_id = supplier.json()['id']

        product = client.post('/api/products', headers=headers, json={
            'name': 'Cari Parity Ürün', 'purchase_price': 10, 'sale_price': 20,
            'vat_rate': 20, 'stock': 5, 'unit': 'Adet', 'category': 'Parity',
        })
        assert product.status_code == 201, product.text

        # --- READ paths: cari lists previously 500'd (COALESCE(bool, 1)=1) ---
        customers = client.get('/api/customers', headers=headers)
        assert customers.status_code == 200, customers.text
        created = [row for row in customers.json() if row['id'] == customer_id]
        assert created, f"created customer missing from list: {customers.text}"
        # projection must keep the integer 1/0 JSON contract, not switch to true/false
        assert created[0]['is_active'] == 1, created[0]
        # every active-filter branch (active / inactive / all) must be dialect-safe
        for active in ('active', 'inactive', 'all'):
            resp = client.get('/api/customers', headers=headers, params={'active': active})
            assert resp.status_code == 200, (active, resp.text)

        suppliers = client.get('/api/suppliers', headers=headers)
        assert suppliers.status_code == 200, suppliers.text
        assert any(row['id'] == supplier_id for row in suppliers.json()), suppliers.text
        for active in ('active', 'inactive', 'all'):
            resp = client.get('/api/suppliers', headers=headers, params={'active': active})
            assert resp.status_code == 200, (active, resp.text)

        # supplier active-toggle also wrote an integer into the boolean column
        toggle = client.patch('/api/suppliers/%d/active' % supplier_id, headers=headers,
                              params={'active': False})
        assert toggle.status_code == 200, toggle.text

        # --- analytics + global search scan the same boolean columns ---
        insights = client.get('/api/analytics/insights', headers=headers)
        assert insights.status_code == 200, insights.text

        search = client.get('/api/search', headers=headers, params={'q': 'Parity'})
        assert search.status_code == 200, search.text
