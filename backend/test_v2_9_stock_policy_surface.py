from __future__ import annotations
import os, subprocess, sys
from pathlib import Path
BACKEND=Path(__file__).resolve().parent

def test_negative_stock_policy_covers_products_imports_bulk_and_transfers(tmp_path:Path)->None:
 database=tmp_path/'stock-policy-surface.db'; env=os.environ.copy();env['DATABASE_URL']=f'sqlite:///{database.as_posix()}';env['PYTHONPATH']=str(BACKEND)
 script=r'''
from io import BytesIO
from urllib.parse import quote
from fastapi.testclient import TestClient
from openpyxl import Workbook
from app.main import app
c=TestClient(app);login=c.post('/api/auth/login',json={'username':'admin','password':'admin123'});assert login.status_code==200,login.text
b=login.json();cid=b['companies'][0]['id'];h={'Authorization':'Bearer '+b['access_token'],'X-Company-ID':str(cid)}
_pw = c.post('/api/auth/change-password', headers=h, json={'current_password':'admin123','new_password':'AdminRot!2026x'})
assert _pw.status_code == 200, _pw.text
h['Authorization'] = 'Bearer ' + _pw.json()['access_token']
assert c.put('/api/company-settings',headers=h,json={'negative_stock_policy':'manager_override','credit_limit_policy':'block'}).status_code==200
reason='Operasyon müdürü stok istisnası';oh={**h,'X-Policy-Override':'true','X-Policy-Override-Reason':quote(reason)}
p={'name':'Negatif Açılış','purchase_price':1,'sale_price':2,'vat_rate':0,'stock':-1,'unit':'Adet'}
assert c.post('/api/products',headers=h,json=p).status_code==409
r=c.post('/api/products',headers=oh,json=p);assert r.status_code==201,r.text;assert r.json()['policy_overrides']==['negative_stock']
r=c.post('/api/products',headers=h,json={'name':'Yüzey Test Ürünü','purchase_price':1,'sale_price':2,'vat_rate':0,'stock':0,'unit':'Adet'});assert r.status_code==201,r.text;pid=r.json()['id']
source=c.get('/api/warehouses',headers=h).json()[0]['id'];target=c.post('/api/warehouses',headers=h,json={'name':'Hedef Politika Deposu','code':'HPD'}).json()['id']
adj={'mode':'set','warehouse_id':source,'quantity':-2,'movement_date':'2026-07-13','note':'Politika testi'}
assert c.post(f'/api/products/{pid}/stock',headers=h,json=adj).status_code==409
r=c.post(f'/api/products/{pid}/stock',headers=oh,json=adj);assert r.status_code==200,r.text;assert r.json()['policy_overrides']==['negative_stock']
bulk={'method':'set','warehouse_id':source,'value':-3,'movement_date':'2026-07-13','note':'Toplu politika','product_ids':[pid]}
assert c.post('/api/products/bulk-stock',headers=h,json=bulk).status_code==409
r=c.post('/api/products/bulk-stock',headers=oh,json=bulk);assert r.status_code==200,r.text;assert r.json()['policy_overrides']==['negative_stock']
tp=c.post('/api/products',headers=h,json={'name':'Transfer Politika Ürünü','purchase_price':1,'sale_price':2,'vat_rate':0,'stock':0,'unit':'Adet'}).json()['id']
t={'source_warehouse_id':source,'target_warehouse_id':target,'transfer_date':'2026-07-13','note':'Transfer politika','items':[{'product_id':tp,'quantity':1}]}
# A transfer is a PHYSICAL move: the negative-stock policy does NOT extend to it.
# Insufficient source stock is 409 with AND without a manager override, and the
# refusal must not invite one (the frontend opens its override form on the
# phrase "Yönetici onayıyla").
assert c.post('/api/warehouses/transfers',headers=h,json=t).status_code==409
blocked=c.post('/api/warehouses/transfers',headers=oh,json=t);assert blocked.status_code==409,blocked.text
assert 'Yönetici onayıyla' not in blocked.json()['detail'],blocked.text
wb=Workbook();ws=wb.active;ws.append(['Ürün Adı','Ürün Kodu','Stok','Alış Fiyatı','Satış Fiyatı','Birim','KDV Oranı']);ws.append(['Excel Negatif','EXNEG',-4,1,2,'Adet',0]);out=BytesIO();wb.save(out);data=out.getvalue();mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
assert c.post('/api/imports/products/excel',headers=h,files={'file':('urunler.xlsx',data,mime)}).status_code==409
r=c.post('/api/imports/products/excel',headers=oh,files={'file':('urunler.xlsx',data,mime)});assert r.status_code==200,r.text;assert r.json()['policy_overrides']==['negative_stock']
logs=c.get('/api/policy-overrides',headers=h).json();types={x['resource_type'] for x in logs if x['policy_name']=='negative_stock'}
# 'stock_transfers' deliberately absent: a transfer can no longer consume a
# negative-stock override, so it must never write one to the audit log either.
assert {'products','products:stock','products:bulk-stock','imports:products'}<=types,types
assert 'stock_transfers' not in types,types
assert all(x['reason']==reason for x in logs if x['policy_name']=='negative_stock'),logs
c.close()
'''
 result=subprocess.run([sys.executable,'-c',script],cwd=BACKEND,env=env,text=True,capture_output=True,timeout=180)
 assert result.returncode==0,result.stdout+'\n'+result.stderr
