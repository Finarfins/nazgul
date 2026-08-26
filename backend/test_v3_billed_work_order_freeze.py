from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parent


def test_issued_invoice_freezes_work_order_until_cancelled(tmp_path: Path) -> None:
    """F3: a non-cancelled invoice freezes its work order.

    While an invoice is ISSUED, reopening the work order and any monetary/part
    mutation are rejected with 409; delivery (the normal terminal flow) is still
    allowed; after invoice cancellation the independently posted receivable must
    also be explicitly reversed before the work order can be reopened.
    """
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'freeze.db').as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    smoke = r'''
from fastapi.testclient import TestClient
from app.main import app

with TestClient(app) as client:
    login=client.post('/api/auth/login',json={'username':'admin','password':'admin123'}).json()
    cid=login['companies'][0]['id']; uid=login['user']['id']
    h={'Authorization':'Bearer '+login['access_token'],'X-Company-ID':str(cid)}
    changed=client.post('/api/auth/change-password',headers=h,json={'current_password':'admin123','new_password':'FreezeGuard123!'}).json()
    h['Authorization']='Bearer '+changed['access_token']
    customer=client.post('/api/customers',headers=h,json={'name':'Freeze Müşteri'}).json()
    machine=client.post('/api/machines',headers=h,json={'customer_id':customer['id'],'brand':'Sungur','model':'Freeze','serial_number':'FREEZE-M'}).json()
    warehouse=client.get('/api/warehouses',headers=h).json()[0]
    product=client.post('/api/products',headers=h,json={'name':'Freeze Parça','purchase_price':'5','sale_price':'10','vat_rate':'20','stock':'100','unit':'Adet'}).json()
    product2=client.post('/api/products',headers=h,json={'name':'Freeze Parça 2','purchase_price':'5','sale_price':'10','vat_rate':'20','stock':'100','unit':'Adet'}).json()
    base={'machine_id':machine['id'],'customer_id':customer['id'],'technician_id':uid,'actual_hours':'2','labor_rate':'100','warranty_type':'NONE','warranty_percent':'0'}

    def complete(order_id):
        for s in ('IN_PROGRESS','COMPLETED'):
            assert client.patch(f"/api/work-orders/{order_id}/status",headers=h,json={'status':s}).status_code==200

    # --- primary work order: reserve a part, complete, issue invoice ---
    wo=client.post('/api/work-orders',headers=h,json=base).json()
    part=client.post(f"/api/work-orders/{wo['id']}/parts",headers=h,json={'product_id':product['id'],'warehouse_id':warehouse['id'],'quantity':'3','unit_price':'19.99','discount':'10','tax_rate':'20'})
    assert part.status_code==201,part.text
    part_id=part.json()['id']
    complete(wo['id'])
    inv=client.post('/api/invoices/generate',headers=h,json={'work_order_id':wo['id']})
    assert inv.status_code==201,inv.text
    invoice_id=inv.json()['id']
    assert inv.json()['status']=='ISSUED'

    # --- while ISSUED: every money/part mutation and reopen is frozen (409) ---
    add=client.post(f"/api/work-orders/{wo['id']}/parts",headers=h,json={'product_id':product2['id'],'warehouse_id':warehouse['id'],'quantity':'1','unit_price':'5','tax_rate':'20'})
    assert add.status_code==409,('part add not frozen',add.status_code,add.text)
    upd=client.put(f"/api/work-orders/{wo['id']}/parts/{part_id}",headers=h,json={'product_id':product['id'],'warehouse_id':warehouse['id'],'quantity':'5','unit_price':'19.99','discount':'10','tax_rate':'20'})
    assert upd.status_code==409,('part update not frozen',upd.status_code,upd.text)
    dele=client.delete(f"/api/work-orders/{wo['id']}/parts/{part_id}",headers=h)
    assert dele.status_code==409,('part delete not frozen',dele.status_code,dele.text)
    reopen=client.patch(f"/api/work-orders/{wo['id']}/status",headers=h,json={'status':'IN_PROGRESS'})
    assert reopen.status_code==409,('reopen not frozen',reopen.status_code,reopen.text)
    labor=client.put(f"/api/work-orders/{wo['id']}",headers=h,json={**base,'actual_hours':'9','labor_rate':'250'})
    assert labor.status_code==409,('labor edit not frozen',labor.status_code,labor.text)

    # --- delivery (normal terminal flow) is still allowed while billed ---
    deliver_wo=client.post('/api/work-orders',headers=h,json=base).json()
    complete(deliver_wo['id'])
    assert client.post('/api/invoices/generate',headers=h,json={'work_order_id':deliver_wo['id']}).status_code==201
    assert client.patch(f"/api/work-orders/{deliver_wo['id']}/status",headers=h,json={'status':'DELIVERED'}).status_code==200

    # --- invoice cancellation alone does not silently remove the receivable ---
    cancel=client.post(f"/api/invoices/{invoice_id}/cancel",headers=h,json={'reason':'iade / hatali fatura'})
    assert cancel.status_code==200,cancel.text
    assert client.patch(f"/api/work-orders/{wo['id']}/status",headers=h,json={'status':'IN_PROGRESS'}).status_code==409
    reverse=client.post(
        f"/api/work-orders/{wo['id']}/receivable/reverse",
        headers={**h,'Idempotency-Key':'freeze-receivable-reverse'},
        json={'reason':'İş emri yeniden açılacak'},
    )
    assert reverse.status_code==200,reverse.text
    assert client.patch(f"/api/work-orders/{wo['id']}/status",headers=h,json={'status':'IN_PROGRESS'}).status_code==200
    add_again=client.post(f"/api/work-orders/{wo['id']}/parts",headers=h,json={'product_id':product2['id'],'warehouse_id':warehouse['id'],'quantity':'1','unit_price':'5','tax_rate':'20'})
    assert add_again.status_code==201,('unfreeze failed',add_again.status_code,add_again.text)
'''
    result = subprocess.run(
        [sys.executable, "-c", smoke], cwd=BACKEND, env=env, text=True, capture_output=True, timeout=180
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
