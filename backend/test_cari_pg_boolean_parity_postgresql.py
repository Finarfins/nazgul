from __future__ import annotations

import os

import pytest


def _acilisa_cek() -> None:
    """Admin şifresini AÇILIŞ DURUMUNA (`admin123` + `must_change_password`) yaz.

    D2/1B-A ikizlerinden DEVRALINDI. CI dosya başına `reset_schema` çalıştırdığı
    için (`ci.yml:609`) CI ortamında DB durumu paylaşılmaz; bu dikiş yerel/pglens
    paylaşılan veritabanı koşularını korur ve gelecekte reset_schema adımının
    kaldırılmasına karşı savunma sağlar. Tek yönlü bir çare (yalnız teardown)
    dosyayı iyi bir komşu yapar ama KENDİSİNİ korumaz, çünkü şifreyi bozan
    ÖNCEKİ dosya olabilir. Bu yüzden İKİ UÇTAN çağrılır.
    """
    try:
        from tests.pg_ikiz_yardimci import acilisa_cek
        acilisa_cek()
    except ImportError:
        from sqlalchemy import text as _text
        from app.auth import hash_password
        from app.db import SessionLocal, engine as _eng

        if _eng.dialect.name != "postgresql":
            return
        with SessionLocal() as db:
            if db.execute(_text("SELECT to_regclass('public.app_users')")).scalar() is None:
                return
            db.execute(
                _text(
                    "UPDATE app_users SET password_hash=:h, "
                    "must_change_password=true WHERE username='admin'"
                ),
                {"h": hash_password("admin123")},
            )
            db.commit()


@pytest.fixture(autouse=True)
def _acilis_sifresi():
    _acilisa_cek()
    try:
        yield
    finally:
        _acilisa_cek()


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
