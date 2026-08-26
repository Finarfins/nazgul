from concurrent.futures import ThreadPoolExecutor
import os
import pytest

@pytest.mark.postgresql
def test_invoice_numbering_and_generation_concurrency(monkeypatch:pytest.MonkeyPatch):
    url=os.environ.get('ENTERPRISE_INVOICE_TEST_DATABASE_URL')
    if not url: pytest.skip('ENTERPRISE_INVOICE_TEST_DATABASE_URL is not configured')
    monkeypatch.setenv('DATABASE_URL',url)
    from fastapi.testclient import TestClient
    from sqlalchemy import text
    from app.db import SessionLocal
    from app.main import app
    with TestClient(app) as c:
        login=c.post('/api/auth/login',json={'username':'admin','password':'admin123'}).json(); cid=login['companies'][0]['id']; uid=login['user']['id']
        h={'Authorization':'Bearer '+login['access_token'],'X-Company-ID':str(cid)}
        changed=c.post('/api/auth/change-password',headers=h,json={'current_password':'admin123','new_password':'EnterpriseInvoicePg123!'}).json(); h['Authorization']='Bearer '+changed['access_token']
        customer=c.post('/api/customers',headers=h,json={'name':'PG Invoice'}).json(); machine=c.post('/api/machines',headers=h,json={'customer_id':customer['id'],'brand':'PG','model':'Invoice','serial_number':'PG-INV'}).json()
        orders=[]
        for _ in range(8):
            wo=c.post('/api/work-orders',headers=h,json={'machine_id':machine['id'],'customer_id':customer['id'],'technician_id':uid,'actual_hours':'1','labor_rate':'10'}).json()
            for status in ('IN_PROGRESS','COMPLETED'): assert c.patch(f"/api/work-orders/{wo['id']}/status",headers=h,json={'status':status}).status_code==200
            orders.append(wo['id'])
    def generate(work_order_id):
        with TestClient(app) as client:
            response=client.post('/api/invoices/generate',headers=h,json={'work_order_id':work_order_id,'branch_prefix':'PG'})
            return response.status_code,response.json()
    with ThreadPoolExecutor(max_workers=8) as pool: results=list(pool.map(generate,orders))
    assert all(status==201 for status,_ in results)
    numbers=sorted(row['invoice_number'] for _,row in results); assert len(set(numbers))==8
    suffixes=sorted(int(number.rsplit('-',1)[1]) for number in numbers); assert suffixes==list(range(suffixes[0],suffixes[0]+8))
    with SessionLocal() as db:
        assert db.execute(text('SELECT COUNT(*) FROM invoices WHERE company_id=:cid'),{'cid':cid}).scalar_one()==8
        assert db.execute(text('SELECT COUNT(*) FROM invoice_history WHERE company_id=:cid AND action=\'CREATED\''),{'cid':cid}).scalar_one()==8
