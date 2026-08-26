from __future__ import annotations
import os,subprocess,sys
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
from app.invoice_engines import DiscountEngine,TaxEngine

BACKEND=Path(__file__).resolve().parent

def test_financial_engines_precision_and_boundaries():
    tax=TaxEngine.calculate("100.005","20"); assert tax.tax==Decimal("20.00") and tax.total==Decimal("120.01")
    assert TaxEngine.calculate("99.99","20",exempt=True).tax==Decimal("0.00")
    discount=DiscountEngine.calculate("100","12.5"); assert discount.discount==Decimal("12.50") and discount.net==Decimal("87.50")
    assert DiscountEngine.calculate("100","10",kind="FIXED").net==Decimal("90.00")

def test_enterprise_invoice_snapshot_pdf_audit_tenant_and_rbac(tmp_path:Path):
    env=os.environ.copy(); env["DATABASE_URL"]=f"sqlite:///{(tmp_path/'invoice.db').as_posix()}"; env["PYTHONPATH"]=str(BACKEND)
    smoke=r'''
from datetime import datetime,timezone
from fastapi.testclient import TestClient
from sqlalchemy import inspect,text
from app.db import engine,SessionLocal
from app.invoice_numbering import next_invoice_number
from app.main import app

with TestClient(app) as c:
 login=c.post('/api/auth/login',json={'username':'admin','password':'admin123'}).json(); cid=login['companies'][0]['id']; uid=login['user']['id']
 h={'Authorization':'Bearer '+login['access_token'],'X-Company-ID':str(cid)}
 changed=c.post('/api/auth/change-password',headers=h,json={'current_password':'admin123','new_password':'EnterpriseInvoice123!'}).json(); h['Authorization']='Bearer '+changed['access_token']
 assert {'invoices','invoice_items','invoice_history','invoice_audit','invoice_counters'} <= set(inspect(engine).get_table_names())
 customer=c.post('/api/customers',headers=h,json={'name':'Snapshot Müşteri'}).json()
 machine=c.post('/api/machines',headers=h,json={'customer_id':customer['id'],'brand':'Sungur','model':'Enterprise','serial_number':'INV-M'}).json()
 warehouse=c.get('/api/warehouses',headers=h).json()[0]
 product=c.post('/api/products',headers=h,json={'name':'Karma KDV Parçası','purchase_price':'1','sale_price':'100','vat_rate':'20','stock':'20','unit':'Adet'}).json()
 def completed(name,warranty='PARTIAL'):
  wo=c.post('/api/work-orders',headers=h,json={'machine_id':machine['id'],'customer_id':customer['id'],'technician_id':uid,'actual_hours':'2','labor_rate':'150','warranty_type':warranty,'warranty_percent':'30'}).json()
  c.post(f"/api/work-orders/{wo['id']}/parts",headers=h,json={'product_id':product['id'],'warehouse_id':warehouse['id'],'quantity':'2','unit_price':'100','discount':'10','tax_rate':'20'})
  for status in ('IN_PROGRESS','COMPLETED'): assert c.patch(f"/api/work-orders/{wo['id']}/status",headers=h,json={'status':status}).status_code==200
  return wo
 wo=completed('one')
 generated=c.post('/api/invoices/generate',headers=h,json={'work_order_id':wo['id'],'branch_prefix':'MRK','currency':'EUR','exchange_rate':'35.123456','global_discount_type':'FIXED','global_discount_value':'10','payment_terms':'30 gün','notes':'Snapshot testi'})
 assert generated.status_code==201,generated.text; invoice=generated.json(); iid=invoice['id']
 assert invoice['invoice_number'].startswith('MRK-2026-') and invoice['currency']=='EUR'
 assert len(invoice['items'])==2 and invoice['customer']['name']=='Snapshot Müşteri'
 assert invoice['totals']['grand_total']=='506.00'
 duplicate=c.post('/api/invoices/generate',headers=h,json={'work_order_id':wo['id']}); assert duplicate.status_code==409,duplicate.text
 with engine.begin() as conn:
  conn.execute(text("UPDATE customers SET name='Değişen Müşteri' WHERE id=:id"),{'id':customer['id']})
  conn.execute(text("UPDATE products SET name='Değişen Parça' WHERE id=:id"),{'id':product['id']})
 detail=c.get(f'/api/invoices/{iid}',headers=h); assert detail.status_code==200
 assert detail.json()['customer']['name']=='Snapshot Müşteri' and detail.json()['items'][1]['description']=='Karma KDV Parçası'
 pdf=c.get(f'/api/invoices/{iid}/pdf',headers=h); assert pdf.status_code==200 and pdf.content.startswith(b'%PDF') and len(pdf.content)>3000
 history=c.get(f'/api/invoices/{iid}/history',headers=h).json(); assert [x['action'] for x in history]==['CREATED']
 cancelled=c.post(f'/api/invoices/{iid}/cancel',headers=h,json={'reason':'Müşteri talebi'}); assert cancelled.status_code==200 and cancelled.json()['status']=='CANCELLED'
 history=c.get(f'/api/invoices/{iid}/history',headers=h).json(); assert [x['action'] for x in history]==['CREATED','CANCELLED']
 with SessionLocal() as db:
  actions=[x[0] for x in db.execute(text('SELECT action FROM invoice_audit WHERE invoice_id=:id ORDER BY id'),{'id':iid}).all()]
  assert actions==['CREATED','VIEWED','DOWNLOADED','CANCELLED']
  y2027=next_invoice_number(db,cid,'MRK',now=datetime(2027,1,1,tzinfo=timezone.utc)); db.rollback(); assert y2027=='MRK-2027-000001'
 listed=c.get('/api/invoices',headers=h,params={'q':'Snapshot','status':'CANCELLED','page':1,'page_size':10,'sort':'invoice_number','direction':'asc'}).json(); assert listed['total']==1
 import time
 started=time.perf_counter()
 for _ in range(50): assert c.get('/api/invoices',headers=h,params={'page_size':200}).status_code==200
 elapsed=time.perf_counter()-started; print(f'INVOICE_LIST_BENCHMARK_50={elapsed:.4f}s'); assert elapsed<10
 other=c.post('/api/companies',headers=h,json={'name':'Invoice Tenant B'}).json(); hb={**h,'X-Company-ID':str(other['id'])}
 assert c.get(f'/api/invoices/{iid}',headers=hb).status_code==404
 report=c.post('/api/users',headers=h,json={'username':'invoice_report','display_name':'Invoice Report','password':'InvoiceReport123!','role':'rapor'}).json()
 rl=c.post('/api/auth/login',json={'username':'invoice_report','password':'InvoiceReport123!'}).json(); rh={'Authorization':'Bearer '+rl['access_token'],'X-Company-ID':str(cid)}
 rc=c.post('/api/auth/change-password',headers=rh,json={'current_password':'InvoiceReport123!','new_password':'InvoiceReport456!'}).json(); rh['Authorization']='Bearer '+rc['access_token']
 assert c.get('/api/invoices',headers=rh).status_code==200
 assert c.post('/api/invoices/generate',headers=rh,json={'work_order_id':wo['id']}).status_code==403
'''
    result=subprocess.run([sys.executable,'-c',smoke],cwd=BACKEND,env=env,text=True,capture_output=True,timeout=180)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
