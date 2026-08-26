from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest


@pytest.mark.postgresql
def test_work_order_invoice_postgresql(monkeypatch: pytest.MonkeyPatch) -> None:
    url = os.environ.get("WORK_ORDER_BILLING_TEST_DATABASE_URL")
    if not url:
        pytest.skip("WORK_ORDER_BILLING_TEST_DATABASE_URL is not configured")
    monkeypatch.setenv("DATABASE_URL", url)
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        login=client.post('/api/auth/login',json={'username':'admin','password':'admin123'}).json()
        cid=login['companies'][0]['id']; uid=login['user']['id']
        h={'Authorization':'Bearer '+login['access_token'],'X-Company-ID':str(cid)}
        changed=client.post('/api/auth/change-password',headers=h,json={'current_password':'admin123','new_password':'BillingPgFoundation123!'}).json()
        h['Authorization']='Bearer '+changed['access_token']
        customer=client.post('/api/customers',headers=h,json={'name':'PG Billing'}).json()
        machine=client.post('/api/machines',headers=h,json={'customer_id':customer['id'],'brand':'PG','model':'Billing','serial_number':'PG-BILL-M'}).json()
        warehouse=client.get('/api/warehouses',headers=h).json()[0]
        product=client.post('/api/products',headers=h,json={'name':'PG Billing Part','purchase_price':'1','sale_price':'12.34','vat_rate':'18','stock':'10','unit':'Adet'}).json()
        order=client.post('/api/work-orders',headers=h,json={'machine_id':machine['id'],'customer_id':customer['id'],'technician_id':uid,'actual_hours':'1.25','labor_rate':'200','warranty_type':'PARTIAL','warranty_percent':'25'}).json()
        assert client.post(f"/api/work-orders/{order['id']}/parts",headers=h,json={'product_id':product['id'],'warehouse_id':warehouse['id'],'quantity':'2','unit_price':'12.34','discount':'5','tax_rate':'18'}).status_code==201
        for status in ('IN_PROGRESS','COMPLETED'):
            assert client.patch(f"/api/work-orders/{order['id']}/status",headers=h,json={'status':status}).status_code==200
        response=client.get(f"/api/work-orders/{order['id']}/invoice",headers=h)
        assert response.status_code==200,response.text
        data=response.json()
        assert data['labor']['labor_total']=='250.00'
        assert data['parts']['parts_before_tax']=='23.45'
        assert data['parts']['parts_tax']=='4.22'
        assert data['parts']['parts_discount']=='1.23'
        assert data['totals']['grand_total']=='277.67'
        assert data['warranty']['customer_amount']=='208.25'
        assert data['warranty']['warranty_amount']=='69.42'
        race_product=client.post('/api/products',headers=h,json={'name':'PG Invoice Race','purchase_price':'1','sale_price':'10','vat_rate':'0','stock':'5','unit':'Adet'}).json()

    barrier=Barrier(2)
    def read_invoice():
        with TestClient(app) as concurrent_client:
            barrier.wait()
            response=concurrent_client.get(f"/api/work-orders/{order['id']}/invoice",headers=h)
            return response.status_code,response.json()

    def add_part():
        with TestClient(app) as concurrent_client:
            barrier.wait()
            return concurrent_client.post(f"/api/work-orders/{order['id']}/parts",headers=h,json={
                'product_id':race_product['id'],'warehouse_id':warehouse['id'],'quantity':'1',
                'unit_price':'10','discount':'0','tax_rate':'0'}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        invoice_future=pool.submit(read_invoice)
        part_future=pool.submit(add_part)
        invoice_status,race_invoice=invoice_future.result()
        part_status=part_future.result()
    assert invoice_status==200 and part_status==201
    assert race_invoice['totals']['grand_total'] in {'277.67','287.67'}
