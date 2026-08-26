from __future__ import annotations
import os, subprocess, sys
from pathlib import Path
BACKEND=Path(__file__).resolve().parent

def test_customer_before_after_delete_and_restore(tmp_path: Path)->None:
    dbfile=tmp_path/'history.db'; env=os.environ.copy(); env['DATABASE_URL']=f'sqlite:///{dbfile.as_posix()}'; env['PYTHONPATH']=str(BACKEND); env['COOKIE_SECURE']='false'
    smoke=r'''
from fastapi.testclient import TestClient
from app.main import app
c=TestClient(app)
r=c.post('/api/auth/login',json={'username':'admin','password':'admin123'}); assert r.status_code==200,r.text
cid=r.json()['companies'][0]['id']; csrf=c.cookies.get('yhp_csrf_token'); h={'X-Company-ID':str(cid),'X-CSRF-Token':csrf}
r=c.post('/api/auth/change-password',headers=h,json={'current_password':'admin123','new_password':'AdminRot!2026x'}); assert r.status_code==200,r.text
csrf=c.cookies.get('yhp_csrf_token'); h={'X-Company-ID':str(cid),'X-CSRF-Token':csrf}
payload={'name':'Deneme Müşteri','phone':'111','opening_balance':'0','risk_limit':'0','payment_term_days':0,'is_active':True}
r=c.post('/api/customers',headers=h,json=payload); assert r.status_code==201,r.text
entity_id=r.json()['id']; payload['name']='Güncel Müşteri'; payload['phone']='222'
r=c.put(f'/api/customers/{entity_id}',headers={**h,'X-Request-ID':'history-update-1'},json=payload); assert r.status_code==200,r.text
hist=c.get(f'/api/history/customer/{entity_id}',headers={'X-Company-ID':str(cid)}); assert hist.status_code==200,hist.text
row=hist.json()[0]; assert row['action']=='update'; assert row['before']['name']=='Deneme Müşteri'; assert row['after']['name']=='Güncel Müşteri'; assert 'name' in row['changed_fields']['fields']; assert row['request_id']=='history-update-1'
r=c.delete(f'/api/customers/{entity_id}',headers=h); assert r.status_code==204,r.text
hist=c.get(f'/api/history/customer/{entity_id}',headers={'X-Company-ID':str(cid)}); delete_log=next(x for x in hist.json() if x['action']=='delete'); assert delete_log['before']['name']=='Güncel Müşteri'; assert delete_log['after'] is None
r=c.post(f"/api/history/restore/{delete_log['id']}",headers=h); assert r.status_code==200,r.text; assert r.json()['name']=='Güncel Müşteri'
detail=c.get(f'/api/customers/{entity_id}',headers={'X-Company-ID':str(cid)}); assert detail.status_code==200,detail.text
hist=c.get(f'/api/history/customer/{entity_id}',headers={'X-Company-ID':str(cid)}); assert hist.json()[0]['action']=='restore'; assert hist.json()[0]['restored_from_log_id']==delete_log['id']
'''
    r=subprocess.run([sys.executable,'-c',smoke],cwd=BACKEND,env=env,text=True,capture_output=True,timeout=120)
    assert r.returncode==0,r.stdout+'\n'+r.stderr
