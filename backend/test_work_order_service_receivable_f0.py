from __future__ import annotations

import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from app.service_receivable_engine import ISTANBUL, _aware_utc


BACKEND = Path(__file__).resolve().parent


def test_completed_timestamp_uses_istanbul_business_date() -> None:
    completed_utc = datetime(2026, 1, 1, 21, 30, tzinfo=timezone.utc)
    assert _aware_utc(completed_utc).astimezone(ISTANBUL).date() == date(2026, 1, 2)


def _run_smoke(tmp_path: Path, body: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'service-receivable.db').as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    result = subprocess.run(
        [sys.executable, "-c", body],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


def test_completed_service_receivable_reversal_and_returned_parts(tmp_path: Path) -> None:
    _run_smoke(
        tmp_path,
        r'''
from datetime import date
from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy import text
from app.db import SessionLocal
from app.main import app
from app.receivables_engine import calculate_net_receivables
from app.routers.transactions import _credit_exposure

with TestClient(app) as c:
 login=c.post('/api/auth/login',json={'username':'admin','password':'admin123'}).json()
 cid=login['companies'][0]['id']; uid=login['user']['id']
 h={'Authorization':'Bearer '+login['access_token'],'X-Company-ID':str(cid)}
 changed=c.post('/api/auth/change-password',headers=h,json={'current_password':'admin123','new_password':'ServiceRecPass123!'})
 h['Authorization']='Bearer '+changed.json()['access_token']
 customer=c.post('/api/customers',headers=h,json={'name':'Servis Cari','payment_term_days':5}).json()
 machine=c.post('/api/machines',headers=h,json={'customer_id':customer['id'],'brand':'S','model':'R','serial_number':'SR-1'}).json()
 warehouse=c.get('/api/warehouses',headers=h).json()[0]
 product=c.post('/api/products',headers=h,json={'name':'İade Parça','purchase_price':'10','sale_price':'120','vat_rate':'20','stock':'20','unit':'Adet'}).json()
 wo=c.post('/api/work-orders',headers=h,json={'machine_id':machine['id'],'customer_id':customer['id'],'technician_id':uid,'actual_hours':'2','labor_rate':'100','warranty_type':'PARTIAL','warranty_percent':'25'}).json()
 wid=wo['id']
 part=c.post(f'/api/work-orders/{wid}/parts',headers=h,json={'product_id':product['id'],'warehouse_id':warehouse['id'],'quantity':'1','unit_price':'120','discount':'0','tax_rate':'20'}).json()
 returned=c.post(f"/api/work-orders/{wid}/parts/{part['id']}/return",headers=h)
 assert returned.status_code==200,returned.text
 listed=c.get(f'/api/work-orders/{wid}',headers=h).json()
 assert Decimal(str(listed['parts_total']))==Decimal('0')
 assert c.patch(f'/api/work-orders/{wid}/status',headers=h,json={'status':'IN_PROGRESS'}).status_code==200
 completed=c.patch(f'/api/work-orders/{wid}/status',headers=h,json={'status':'COMPLETED'})
 assert completed.status_code==200,completed.text
 with SessionLocal() as db:
  docs=db.execute(text("""SELECT * FROM receivable_charge_documents
    WHERE company_id=:cid AND work_order_id=:wid ORDER BY revision_no"""),{'cid':cid,'wid':wid}).mappings().all()
  assert len(docs)==1
  doc=docs[0]
  assert doc['charge_type']=='service_fee' and doc['status']=='posted'
  assert Decimal(str(doc['gross_amount']))==Decimal('150.00')
  assert str(doc['currency'])=='TRY' and Decimal(str(doc['exchange_rate']))==Decimal('1')
  due=date.fromisoformat(str(doc['due_date_snapshot']))
  document_date=date.fromisoformat(str(doc['period_end']))
  assert (due-document_date).days==5
  # as_of, belgenin İSTANBUL iş gününe (period_end/document_date) göre verilir;
  # runner'ın yerel date.today()'i (CI'da UTC) İstanbul gece yarısını geçtiğinde
  # bir gün geride kalıp period_end<=as_of filtresini kaçırıyordu (zaman-bağımlı
  # flake). Belgenin kendi iş günü deterministik ve doğru kesişim tarihidir.
  net=[x for x in calculate_net_receivables(db,cid,document_date) if x.document_type=='service_fee']
  assert len(net)==1 and net[0].total==Decimal('150.00')
  _limit,current,projected=_credit_exposure(
    db,cid=cid,customer_id=customer['id'],final_total=Decimal('20'),
    paid_amount=Decimal('0'),status='completed',transaction_id=None)
  assert current==Decimal('150.00') and projected==Decimal('170.00')
 other=c.post('/api/companies',headers=h,json={'name':'Başka Tenant'}).json()
 with SessionLocal() as db:
  assert not [x for x in calculate_net_receivables(db,other['id'],document_date) if x.document_type=='service_fee']
 rh={**h,'Idempotency-Key':'wo-reverse-1'}
 first=c.post(f'/api/work-orders/{wid}/receivable/reverse',headers=rh,json={'reason':'Müşteri mutabakatı'})
 assert first.status_code==200,first.text
 replay=c.post(f'/api/work-orders/{wid}/receivable/reverse',headers=rh,json={'reason':'Müşteri mutabakatı'})
 assert replay.status_code==200 and replay.json()['id']==first.json()['id']
 fresh=c.post(f'/api/work-orders/{wid}/receivable/reverse',headers={**h,'Idempotency-Key':'wo-reverse-2'},json={'reason':'Müşteri mutabakatı'})
 assert fresh.status_code==409,fresh.text
 with SessionLocal() as db:
  docs=db.execute(text("""SELECT status,gross_amount,reversal_of_document_id
    FROM receivable_charge_documents WHERE company_id=:cid AND work_order_id=:wid
    ORDER BY revision_no"""),{'cid':cid,'wid':wid}).mappings().all()
  assert len(docs)==2 and docs[0]['status']=='reversed'
  assert sum(Decimal(str(row['gross_amount'])) for row in docs)==Decimal('0.00')
  log_count=db.execute(text("""SELECT COUNT(*) FROM entity_change_logs
    WHERE company_id=:cid AND entity_type='receivable_charge_document'"""),{'cid':cid}).scalar_one()
  assert log_count==1
 inv=c.post('/api/invoices/generate',headers=h,json={'work_order_id':wid,'branch_prefix':'SR'})
 assert inv.status_code==201,inv.text
 assert [item['item_type'] for item in inv.json()['items']]==['LABOR']
 assert Decimal(str(inv.json()['totals']['grand_total']))==Decimal('200.00')
 iid=inv.json()['id']
 with SessionLocal() as db:
  count=db.execute(text("""SELECT COUNT(*) FROM receivable_charge_documents
    WHERE company_id=:cid AND work_order_id=:wid"""),{'cid':cid,'wid':wid}).scalar_one()
  assert count==2
 cancelled=c.post(f'/api/invoices/{iid}/cancel',headers=h,json={'reason':'İptal'})
 assert cancelled.status_code==200,cancelled.text
 reopened=c.patch(f'/api/work-orders/{wid}/status',headers=h,json={'status':'IN_PROGRESS'})
 assert reopened.status_code==200,reopened.text
print('SERVICE_RECEIVABLE_LIFECYCLE_OK')
''',
    )


def test_invoice_reconciliation_and_live_allocation_guard(tmp_path: Path) -> None:
    _run_smoke(
        tmp_path,
        r'''
from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy import text
from app.db import SessionLocal
from app.main import app

with TestClient(app) as c:
 login=c.post('/api/auth/login',json={'username':'admin','password':'admin123'}).json()
 cid=login['companies'][0]['id']; uid=login['user']['id']
 h={'Authorization':'Bearer '+login['access_token'],'X-Company-ID':str(cid)}
 changed=c.post('/api/auth/change-password',headers=h,json={'current_password':'admin123','new_password':'ReconPass123!'})
 h['Authorization']='Bearer '+changed.json()['access_token']
 customer=c.post('/api/customers',headers=h,json={'name':'Mutabakat Cari'}).json()
 machine=c.post('/api/machines',headers=h,json={'customer_id':customer['id'],'brand':'S','model':'R','serial_number':'REC-1'}).json()
 wo=c.post('/api/work-orders',headers=h,json={'machine_id':machine['id'],'customer_id':customer['id'],'technician_id':uid,'actual_hours':'2','labor_rate':'100','warranty_type':'NONE','warranty_percent':'0'}).json()
 wid=wo['id']
 for status in ('IN_PROGRESS','COMPLETED'):
  response=c.patch(f'/api/work-orders/{wid}/status',headers=h,json={'status':status})
  assert response.status_code==200,response.text
 invoice=c.post('/api/invoices/generate',headers=h,json={'work_order_id':wid,'branch_prefix':'RC','global_discount_type':'FIXED','global_discount_value':'20','currency':'EUR','exchange_rate':'2'})
 assert invoice.status_code==201,invoice.text
 iid=invoice.json()['id']
 with SessionLocal() as db:
  docs=db.execute(text("""SELECT revision_no,status,gross_amount,reversal_of_document_id,currency,exchange_rate
    FROM receivable_charge_documents WHERE company_id=:cid AND work_order_id=:wid
    ORDER BY revision_no"""),{'cid':cid,'wid':wid}).mappings().all()
  assert len(docs)==3
  assert [row['revision_no'] for row in docs]==[1,2,3]
  assert docs[0]['status']=='reversed' and docs[1]['reversal_of_document_id'] is not None
  assert Decimal(str(docs[2]['gross_amount']))==Decimal('360.00')
  assert docs[2]['currency']=='EUR' and Decimal(str(docs[2]['exchange_rate']))==Decimal('2')
  active_id=int(docs[2]['reversal_of_document_id'] or 0) if False else int(db.execute(text("""SELECT id FROM receivable_charge_documents
    WHERE company_id=:cid AND work_order_id=:wid AND status='posted' AND reversal_of_document_id IS NULL"""),{'cid':cid,'wid':wid}).scalar_one())
  payment_id=int(db.execute(text("""INSERT INTO payments(entity_type,entity_id,amount,payment_date,note,company_id,payment_method)
    VALUES('customer',:customer_id,10,'2026-01-01','test',:cid,'cash') RETURNING id"""),{'customer_id':customer['id'],'cid':cid}).scalar_one())
  db.execute(text("""INSERT INTO payment_allocations(
    company_id,payment_id,order_id,receivable_charge_id,amount,allocation_type,effective_date,created_by
  ) VALUES(:cid,:payment_id,NULL,:charge_id,10,'manual','2026-01-01',:uid)"""),
    {'cid':cid,'payment_id':payment_id,'charge_id':active_id,'uid':uid})
  db.commit()
 blocked=c.post(f'/api/invoices/{iid}/cancel',headers=h,json={'reason':'Tahsis varken iptal'})
 assert blocked.status_code==409,blocked.text
 detail=c.get(f'/api/invoices/{iid}',headers=h).json()
 assert detail['status']=='ISSUED'
 reverse=c.post(f'/api/work-orders/{wid}/receivable/reverse',headers={**h,'Idempotency-Key':'allocated-reverse'},json={'reason':'Tahsisli'})
 assert reverse.status_code==409,reverse.text
 with SessionLocal() as db:
  allocation_id=int(db.execute(text("""SELECT id FROM payment_allocations
    WHERE company_id=:cid AND receivable_charge_id=:charge_id"""),{'cid':cid,'charge_id':active_id}).scalar_one())
  db.execute(text("""INSERT INTO payment_allocations(
    company_id,payment_id,order_id,receivable_charge_id,amount,allocation_type,effective_date,
    created_by,reversal_of_allocation_id
  ) VALUES(:cid,:payment_id,NULL,:charge_id,10,'manual','2026-01-02',:uid,:source_id)"""),
    {'cid':cid,'payment_id':payment_id,'charge_id':active_id,'uid':uid,'source_id':allocation_id})
  db.commit()
 cancelled=c.post(f'/api/invoices/{iid}/cancel',headers=h,json={'reason':'Tahsis terslendi'})
 assert cancelled.status_code==200,cancelled.text
 with SessionLocal() as db:
  docs=db.execute(text("""SELECT gross_amount FROM receivable_charge_documents
    WHERE company_id=:cid AND work_order_id=:wid ORDER BY revision_no"""),{'cid':cid,'wid':wid}).all()
  assert len(docs)==5
  assert sum(Decimal(str(row[0])) for row in docs)==Decimal('200.00')
print('SERVICE_RECEIVABLE_RECONCILIATION_OK')
''',
    )
