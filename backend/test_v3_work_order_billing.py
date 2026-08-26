from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parent


def test_invoice_summary_precision_warranty_and_validation(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'billing.db').as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    smoke = r'''
from fastapi.testclient import TestClient
from app.main import app

with TestClient(app) as client:
    login=client.post('/api/auth/login',json={'username':'admin','password':'admin123'}).json()
    cid=login['companies'][0]['id']; uid=login['user']['id']
    h={'Authorization':'Bearer '+login['access_token'],'X-Company-ID':str(cid)}
    changed=client.post('/api/auth/change-password',headers=h,json={'current_password':'admin123','new_password':'BillingFoundation123!'}).json()
    h['Authorization']='Bearer '+changed['access_token']
    customer=client.post('/api/customers',headers=h,json={'name':'Fatura Müşterisi'}).json()
    machine=client.post('/api/machines',headers=h,json={'customer_id':customer['id'],'brand':'Sungur','model':'Fatura','serial_number':'BILL-M'}).json()
    warehouse=client.get('/api/warehouses',headers=h).json()[0]
    product=client.post('/api/products',headers=h,json={'name':'Fatura Parçası','purchase_price':'5','sale_price':'19.99','vat_rate':'20','stock':'1000005','unit':'Adet'}).json()
    base={'machine_id':machine['id'],'customer_id':customer['id'],'technician_id':uid,'actual_hours':'2.5','labor_rate':'100'}
    partial=client.post('/api/work-orders',headers=h,json={**base,'warranty_type':'PARTIAL','warranty_percent':'40'}).json()
    not_ready=client.get(f"/api/work-orders/{partial['id']}/invoice",headers=h)
    assert not_ready.status_code==409,not_ready.text
    part=client.post(f"/api/work-orders/{partial['id']}/parts",headers=h,json={'product_id':product['id'],'warehouse_id':warehouse['id'],'quantity':'3','unit_price':'19.99','discount':'10','tax_rate':'20'})
    assert part.status_code==201,part.text
    for status in ('IN_PROGRESS','COMPLETED'):
        assert client.patch(f"/api/work-orders/{partial['id']}/status",headers=h,json={'status':status}).status_code==200
    invoice=client.get(f"/api/work-orders/{partial['id']}/invoice",headers=h)
    assert invoice.status_code==200,invoice.text
    data=invoice.json()
    assert data['customer']['name']=='Fatura Müşterisi'
    assert data['machine']['serial_number']=='BILL-M'
    # FAZ-2 additive fields: with no APPROVED labor line the v1 header remains
    # the billing source and the amounts are unchanged.
    assert data['labor']=={'total_hours':'2.5000','labor_rate':'100.00','labor_total':'250.00',
                           'source':'header','line_ids':[],
                           'entries':[{'hours':'2.5000','hourly_rate':'100.00','line_id':None}]}
    assert data['parts']=={'parts_total':'64.76','parts_before_tax':'53.97','parts_tax':'10.79','parts_discount':'6.00'}
    assert data['totals']=={'labor':'250.00','parts':'64.76','tax':'10.79','discount':'6.00','grand_total':'314.76',
                            'labor_source':'header','labor_line_ids':[]}
    assert data['warranty']=={'type':'PARTIAL','coverage_percent':'40.0000','customer_amount':'188.86','warranty_amount':'125.90','company_cost':'125.90'}
    assert data['taxes']=='10.79'
    assert client.patch(f"/api/work-orders/{partial['id']}/status",headers=h,json={'status':'DELIVERED'}).status_code==200
    assert client.get(f"/api/work-orders/{partial['id']}/invoice",headers=h).status_code==200

    for warranty_type,percent,customer_amount,warranty_amount in (
        ('NONE','0','100.00','0.00'),('FULL','100','0.00','100.00')):
        order=client.post('/api/work-orders',headers=h,json={**base,'actual_hours':'1','warranty_type':warranty_type,'warranty_percent':percent}).json()
        for status in ('IN_PROGRESS','COMPLETED'):
            assert client.patch(f"/api/work-orders/{order['id']}/status",headers=h,json={'status':status}).status_code==200
        warranty=client.get(f"/api/work-orders/{order['id']}/invoice",headers=h).json()['warranty']
        assert warranty['customer_amount']==customer_amount and warranty['warranty_amount']==warranty_amount

    large=client.post('/api/work-orders',headers=h,json={**base,'actual_hours':'999999','labor_rate':'99999999','warranty_type':'NONE'}).json()
    large_part=client.post(f"/api/work-orders/{large['id']}/parts",headers=h,json={'product_id':product['id'],'warehouse_id':warehouse['id'],'quantity':'1000000','unit_price':'99999999','discount':'0','tax_rate':'0'})
    assert large_part.status_code==201,large_part.text
    for status in ('IN_PROGRESS','COMPLETED'):
        assert client.patch(f"/api/work-orders/{large['id']}/status",headers=h,json={'status':status}).status_code==200
    large_invoice=client.get(f"/api/work-orders/{large['id']}/invoice",headers=h)
    assert large_invoice.status_code==200,large_invoice.text
    assert large_invoice.json()['totals']['grand_total']=='199999898000001.00'
'''
    result=subprocess.run([sys.executable,'-c',smoke],cwd=BACKEND,env=env,text=True,capture_output=True,timeout=180)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
