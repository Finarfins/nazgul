from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def _run(script: str, database: Path, *, auto_migrate: bool = True, timeout: int = 180):
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["AUTO_MIGRATE"] = "true" if auto_migrate else "false"
    env["PYTHONPATH"] = str(BACKEND)
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def test_company_stock_credit_policies_and_override_audit(tmp_path: Path) -> None:
    database = tmp_path / "company-policies.db"
    script = r'''
from fastapi.testclient import TestClient
from urllib.parse import quote
from app.main import app

client = TestClient(app)
login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
assert login.status_code == 200, login.text
body = login.json(); cid = body['companies'][0]['id']
headers = {'Authorization':'Bearer ' + body['access_token'], 'X-Company-ID':str(cid)}
changed = client.post('/api/auth/change-password', headers=headers, json={'current_password':'admin123','new_password':'AdminRot!2026x'})
assert changed.status_code == 200, changed.text
headers['Authorization'] = 'Bearer ' + changed.json()['access_token']
warehouse = client.get('/api/warehouses', headers=headers).json()[0]

# Default is fail-closed: a normal user cannot create negative stock.
customer = client.post('/api/customers', headers=headers, json={'name':'Stok Politika Müşterisi','risk_limit':0})
product = client.post('/api/products', headers=headers, json={
    'name':'Tek Stoklu Ürün','purchase_price':10,'sale_price':20,
    'vat_rate':0,'stock':1,'unit':'Adet',
})
assert customer.status_code == 201 and product.status_code == 201
sale_payload = {
    'entity_id':customer.json()['id'],'transaction_date':'2026-07-13',
    'warehouse_id':warehouse['id'],'status':'completed','payment_method':'credit',
    'paid_amount':0,'discount_percent':0,
    'items':[{'product_id':product.json()['id'],'quantity':2,'unit_price':20,'vat_rate':0,'discount_percent':0}],
}
blocked = client.post('/api/orders', headers=headers, json=sale_payload)
assert blocked.status_code == 409, blocked.text
assert 'negatif stok' in blocked.text.lower(), blocked.text
assert client.get('/api/orders', headers=headers).json() == []

# Manager-override mode requires a reason and writes an audit record.
settings = client.put('/api/company-settings', headers=headers, json={
    'negative_stock_policy':'manager_override','credit_limit_policy':'block',
})
assert settings.status_code == 200, settings.text
needs_approval = client.post('/api/orders', headers=headers, json=sale_payload)
assert needs_approval.status_code == 409 and 'Yönetici onayıyla' in needs_approval.text, needs_approval.text
override_headers = {**headers, 'X-Policy-Override':'true', 'X-Policy-Override-Reason':quote('Müşteriye acil sevkiyat onayı')}
overridden_sale = client.post('/api/orders', headers=override_headers, json=sale_payload)
assert overridden_sale.status_code == 201, overridden_sale.text
assert overridden_sale.json()['policy_overrides'] == ['negative_stock'], overridden_sale.text
logs = client.get('/api/policy-overrides', headers=headers).json()
assert any(x['policy_name']=='negative_stock' and x['reason']=='Müşteriye acil sevkiyat onayı' for x in logs), logs

# Credit exposure is enforced only when the new document increases exposure.
credit_customer = client.post('/api/customers', headers=headers, json={'name':'Riskli Müşteri','risk_limit':50})
credit_product = client.post('/api/products', headers=headers, json={
    'name':'Risk Test Ürünü','purchase_price':10,'sale_price':100,
    'vat_rate':0,'stock':10,'unit':'Adet',
})
credit_payload = {
    'entity_id':credit_customer.json()['id'],'transaction_date':'2026-07-13',
    'warehouse_id':warehouse['id'],'status':'completed','payment_method':'credit',
    'paid_amount':0,'discount_percent':0,
    'items':[{'product_id':credit_product.json()['id'],'quantity':1,'unit_price':100,'vat_rate':0,'discount_percent':0}],
}
credit_blocked = client.post('/api/orders', headers=headers, json=credit_payload)
assert credit_blocked.status_code == 409 and 'risk limiti' in credit_blocked.text.lower(), credit_blocked.text
client.put('/api/company-settings', headers=headers, json={
    'negative_stock_policy':'manager_override','credit_limit_policy':'manager_override',
})
credit_needs_approval = client.post('/api/orders', headers=headers, json=credit_payload)
assert credit_needs_approval.status_code == 409 and 'Yönetici onayıyla' in credit_needs_approval.text, credit_needs_approval.text
credit_headers = {**headers, 'X-Policy-Override':'true', 'X-Policy-Override-Reason':quote('Genel müdür vadeli satış onayı')}
credit_sale = client.post('/api/orders', headers=credit_headers, json=credit_payload)
assert credit_sale.status_code == 201, credit_sale.text
assert credit_sale.json()['policy_overrides'] == ['credit_limit'], credit_sale.text
logs = client.get('/api/policy-overrides', headers=headers).json()
assert any(x['policy_name']=='credit_limit' and x['resource_id']==credit_sale.json()['id'] for x in logs), logs

# Deleting a purchase may also reduce stock and must respect the same policy.
cycle_product = client.post('/api/products', headers=headers, json={
    'name':'Alış Silme Testi','purchase_price':10,'sale_price':20,
    'vat_rate':0,'stock':0,'unit':'Adet',
}).json()
supplier = client.post('/api/suppliers', headers=headers, json={'name':'Politika Tedarikçisi'}).json()
purchase_payload = {
    'entity_id':supplier['id'],'transaction_date':'2026-07-13',
    'warehouse_id':warehouse['id'],'status':'completed','payment_method':'credit',
    'paid_amount':0,'discount_percent':0,
    'items':[{'product_id':cycle_product['id'],'quantity':5,'unit_price':10,'vat_rate':0,'discount_percent':0}],
}
purchase = client.post('/api/purchases', headers=headers, json=purchase_payload)
assert purchase.status_code == 201, purchase.text
cycle_sale_payload = {
    'entity_id':customer.json()['id'],'transaction_date':'2026-07-13',
    'warehouse_id':warehouse['id'],'status':'completed','payment_method':'credit',
    'paid_amount':0,'discount_percent':0,
    'items':[{'product_id':cycle_product['id'],'quantity':5,'unit_price':20,'vat_rate':0,'discount_percent':0}],
}
cycle_sale = client.post('/api/orders', headers=headers, json=cycle_sale_payload)
assert cycle_sale.status_code == 201, cycle_sale.text
purchase_delete_blocked = client.delete(f"/api/purchases/{purchase.json()['id']}", headers=headers)
assert purchase_delete_blocked.status_code == 409, purchase_delete_blocked.text
purchase_delete = client.delete(
    f"/api/purchases/{purchase.json()['id']}",
    headers={**headers, 'X-Policy-Override':'true', 'X-Policy-Override-Reason':quote('Hatalı alış belgesi iptal onayı')},
)
assert purchase_delete.status_code == 204, purchase_delete.text
logs = client.get('/api/policy-overrides', headers=headers).json()
assert any(x['resource_type']=='purchases:delete' and x['policy_name']=='negative_stock' for x in logs), logs

# Strict block cannot be bypassed even by admin.
client.put('/api/company-settings', headers=headers, json={
    'negative_stock_policy':'block','credit_limit_policy':'block',
})
strict_product = client.post('/api/products', headers=headers, json={
    'name':'Kesin Blok Ürünü','purchase_price':1,'sale_price':2,
    'vat_rate':0,'stock':0,'unit':'Adet',
}).json()
strict_payload = {**sale_payload, 'items':[{'product_id':strict_product['id'],'quantity':1,'unit_price':2,'vat_rate':0,'discount_percent':0}]}
strict = client.post('/api/orders', headers=override_headers, json=strict_payload)
assert strict.status_code == 409, strict.text
client.close()
'''
    result = _run(script, database)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


def test_auto_migrate_false_fails_fast_on_stale_schema_and_accepts_head(tmp_path: Path) -> None:
    database = tmp_path / "migration-gate.db"
    stale = _run("import app.main", database, auto_migrate=False)
    assert stale.returncode != 0
    assert "AUTO_MIGRATE=false" in stale.stderr

    migrated = _run("import app.main", database, auto_migrate=True)
    assert migrated.returncode == 0, migrated.stdout + "\n" + migrated.stderr
    verified = _run("import app.main", database, auto_migrate=False)
    assert verified.returncode == 0, verified.stdout + "\n" + verified.stderr
