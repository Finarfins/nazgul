import shutil,tempfile,os
from pathlib import Path
src=Path(__file__).with_name('veriler.db')
tmp=Path(tempfile.mkdtemp())/'test.db';shutil.copy2(src,tmp);os.environ['DATABASE_URL']=f'sqlite:///{tmp}'
from fastapi.testclient import TestClient
from app.main import app
client=TestClient(app)
login=client.post('/api/auth/login',json={'username':'admin','password':'admin123'});assert login.status_code==200,login.text
h={'Authorization':'Bearer '+login.json()['access_token']}
accounts=client.get('/api/finance/accounts',headers=h).json();assert len(accounts)>=3,accounts
cash=next(a for a in accounts if a['account_type']=='cash');bank=next(a for a in accounts if a['account_type']=='bank')
# manual movement
r=client.post('/api/finance/transactions',headers=h,json={'account_id':cash['id'],'txn_date':'2026-07-11','direction':'in','amount':1000,'category':'capital','payment_method':'cash','description':'Test giriş'});assert r.status_code==201,r.text
summary=client.get('/api/finance/summary',headers=h).json();cash_after=next(a for a in summary['accounts'] if a['id']==cash['id']);assert cash_after['balance']>=1000,cash_after
# transfer
r=client.post('/api/finance/transfers',headers=h,json={'source_account_id':cash['id'],'target_account_id':bank['id'],'txn_date':'2026-07-11','amount':200,'description':'Test virman'});assert r.status_code==201,r.text
# payment sync into cash
customers=client.get('/api/customers',headers=h).json();assert customers
r=client.post('/api/payments',headers=h,json={'entity_type':'customer','entity_id':customers[0]['id'],'amount':350,'payment_date':'2026-07-11','note':'Test tahsilat','payment_method':'cash','account_id':cash['id']});assert r.status_code==201,r.text
pid=r.json()['id'];rows=client.get('/api/finance/transactions',headers=h,params={'account_id':cash['id']}).json();assert any(x['reference_type']=='payment' and x['reference_id']==pid for x in rows)
# payment edit keeps one linked transaction
r=client.put(f'/api/payments/{pid}',headers=h,json={'entity_type':'customer','entity_id':customers[0]['id'],'amount':400,'payment_date':'2026-07-12','note':'Test tahsilat güncel','payment_method':'bank_transfer','account_id':bank['id']});assert r.status_code==200,r.text
rows=client.get('/api/finance/transactions',headers=h).json();linked=[x for x in rows if x['reference_type']=='payment' and x['reference_id']==pid];assert len(linked)==1 and linked[0]['account_id']==bank['id'] and linked[0]['amount']==400,linked
# instrument lifecycle
r=client.post('/api/finance/instruments',headers=h,json={'instrument_type':'check','direction':'received','entity_type':'customer','entity_id':customers[0]['id'],'amount':750,'issue_date':'2026-07-11','due_date':'2026-08-01','serial_no':'CHK-TEST','bank_name':'Test Bank','account_no':'1','note':'test'});assert r.status_code==201,r.text
iid=r.json()['id'];r=client.put(f'/api/finance/instruments/{iid}/status',headers=h,json={'status':'collected','account_id':bank['id'],'transaction_date':'2026-08-01'});assert r.status_code==200,r.text
inst=client.get('/api/finance/instruments',headers=h).json();assert any(x['id']==iid and x['status']=='collected' and x['financial_transaction_id'] for x in inst)
# delete payment removes linked finance tx
r=client.delete(f'/api/payments/{pid}',headers=h);assert r.status_code==204,r.text
rows=client.get('/api/finance/transactions',headers=h).json();assert not any(x['reference_type']=='payment' and x['reference_id']==pid for x in rows)
print('ALL_NEXT_1_0_7_FINANCE_CORE_TESTS_PASSED')
