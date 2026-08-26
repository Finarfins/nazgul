from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parent


def test_manual_total_uses_exact_discount_and_preserves_vat_split(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'manual-total.db').as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    smoke = r'''
from fastapi.testclient import TestClient
from decimal import Decimal
from sqlalchemy import text
from app.main import app
from app.db import SessionLocal
from app.money import distribute_amount

c=TestClient(app)
login=c.post('/api/auth/login',json={'username':'admin','password':'admin123'}).json()
cid=login['companies'][0]['id']
h={'Authorization':'Bearer '+login['access_token'],'X-Company-ID':str(cid)}
rot=c.post('/api/auth/change-password',headers=h,json={'current_password':'admin123','new_password':'ManualTotal123!'}).json()
h['Authorization']='Bearer '+rot['access_token']
customer=c.post('/api/customers',headers=h,json={'name':'Toplam Test'}).json()
warehouse=c.get('/api/warehouses',headers=h).json()[0]
product=c.post('/api/products',headers=h,json={'name':'Pazarlık Ürünü','sale_price':'10000.00','purchase_price':'1','stock':'3','unit':'Adet','vat_rate':20}).json()
product2=c.post('/api/products',headers=h,json={'name':'Pazarlık Ürünü 2','sale_price':'22261.71','purchase_price':'1','stock':'3','unit':'Adet','vat_rate':20}).json()
payload={
 'entity_id':customer['id'],'transaction_date':'2026-07-29','warehouse_id':warehouse['id'],
 'status':'completed','payment_method':'credit','paid_amount':'0',
 'discount_percent':'0.8112','discount_amount':'261.71',
 'items':[
  {'product_id':product['id'],'quantity':'1','unit_price':'10000.00','vat_rate':20,'discount_percent':'0'},
  {'product_id':product2['id'],'quantity':'1','unit_price':'22261.71','vat_rate':20,'discount_percent':'0'},
 ],
}
sale=c.post('/api/orders',headers=h,json=payload)
assert sale.status_code==201,sale.text
body=sale.json()
assert str(body['grand_total'])=='32261.71'
assert str(body['discount_amount'])=='261.71'
assert str(body['subtotal'])=='26666.66'
assert str(body['vat_total'])=='5333.34'
assert str(body['final_total'])=='32000.0'
with SessionLocal() as db:
 rows=db.execute(text('SELECT product_id,line_subtotal,line_vat,line_total,discount_amount FROM order_items WHERE order_id=:oid ORDER BY id'),{'oid':body['id']}).mappings().all()
 assert [str(r['line_total']) for r in rows]==['9918.88','22081.12'],rows
 assert [str(r['discount_amount']) for r in rows]==['81.12','180.59'],rows
 assert [str(r['line_vat']) for r in rows]==['1653.15','3680.19'],rows
 assert sum((Decimal(str(r['line_total'])) for r in rows),Decimal('0'))==Decimal(str(body['final_total']))
 assert sum((Decimal(str(r['line_vat'])) for r in rows),Decimal('0'))==Decimal(str(body['vat_total']))
# Mutasyon siperi: kalan kuruş largest-remainder satırına verilmezse bu sabit
# dağılım [0.01, 0.00] yerine [0.00, 0.01] olur ve test kırmızıya döner.
assert distribute_amount('0.01',['2','1'])==[Decimal('0.01'),Decimal('0.00')]
pos=c.post('/api/pos/sale',headers={**h,'Idempotency-Key':'manual-total-pos'},json={
 'items':[
  {'product_id':product['id'],'quantity':'1','unit_price':'10000.00','discount_percent':'0'},
  {'product_id':product2['id'],'quantity':'1','unit_price':'22261.71','discount_percent':'0'},
 ],
 'warehouse_id':warehouse['id'],'payment_type':'cash',
 'discount_percent':'0.8112','discount_amount':'261.71',
})
assert pos.status_code==201,pos.text
pos_body=pos.json()
assert pos_body['final_total']=='32000.00'
assert pos_body['discount_amount']=='261.71'
assert pos_body['subtotal']=='26666.66'
assert pos_body['vat_total']=='5333.34'
too_large=c.post('/api/pos/sale',headers={**h,'Idempotency-Key':'manual-total-too-large'},json={
 'items':[{'product_id':product['id'],'quantity':'1','unit_price':'10000.00','discount_percent':'0'}],
 'warehouse_id':warehouse['id'],'payment_type':'cash','discount_amount':'32261.72',
})
assert too_large.status_code==400,too_large.text
assert 'aşamaz' in too_large.json()['detail']
'''
    result = subprocess.run(
        [sys.executable, "-c", smoke],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
