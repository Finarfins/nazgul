from __future__ import annotations
import os
from pathlib import Path

def test_product_industry_fields_roundtrip(tmp_path: Path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path/'product_fields.db'}"
    os.environ["AUTO_MIGRATE"] = "true"
    from app.main import app
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        login=client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
        assert login.status_code==200
        csrf=client.cookies.get('yhp_csrf_token')
        client.post('/api/auth/change-password', json={'current_password':'admin123','new_password':'DemoPass123!'}, headers={'X-CSRF-Token':csrf})
        csrf=client.cookies.get('yhp_csrf_token')
        payload={'name':'CS640 Fan Kayışı','product_code':'CS640-FAN','barcode':'869000001','purchase_price':100,'sale_price':150,'vat_rate':20,'stock':2,'unit':'Adet','category':'Kayış','location':'A-03','oem_number':'84977489','alternative_oem':'84977490, 84977491','brand':'Rubena','manufacturer':'Rubena','compatible_models':'CS640, TC56','technical_notes':'Fan sistemi için.'}
        r=client.post('/api/products', json=payload, headers={'X-CSRF-Token':csrf})
        assert r.status_code==201, r.text
        pid=r.json()['id']
        d=client.get(f'/api/products/{pid}')
        assert d.status_code==200
        product=d.json()['product']
        assert product['oem_number']=='84977489'
        assert product['location']=='A-03'
        q=client.get('/api/products', params={'q':'CS640'})
        assert any(x['id']==pid for x in q.json())
        q=client.get('/api/products', params={'q':'84977491'})
        assert any(x['id']==pid for x in q.json())
