import os,shutil,sqlite3,tempfile
from pathlib import Path
tmp=Path(tempfile.mkdtemp())/'test.db';shutil.copy2(Path(__file__).with_name('veriler.db'),tmp);os.environ['DATABASE_URL']=f'sqlite:///{tmp.as_posix()}'
from fastapi.testclient import TestClient
from app.main import app
c=TestClient(app)
login=c.post('/api/auth/login',json={'username':'admin','password':'admin123'})
assert login.status_code==200,login.text
c.headers.update({'Authorization':'Bearer '+login.json()['access_token']})
def ok(r):
 assert r.status_code<300,(r.status_code,r.text)
 return r.json() if r.content else None
p=ok(c.get('/api/products?limit=1'))[0];pid=p['id']
new=ok(c.post('/api/products',json={'name':'TEST URUN','product_code':'T-1','barcode':'','purchase_price':10,'sale_price':20,'vat_rate':20,'stock':5,'unit':'Adet','category':'Test'}));assert new['id']
ok(c.post(f'/api/products/{pid}/stock',json={'mode':'add','quantity':2,'movement_date':'2026-07-11','note':'test'}))
ok(c.post('/api/products/bulk-price',json={'field':'sale_price','method':'percent','value':1,'product_ids':[pid]}))
ok(c.post('/api/products/bulk-stock',json={'method':'add','value':1,'movement_date':'2026-07-11','note':'bulk','product_ids':[pid]}))
assert ok(c.get('/api/products/stock/movements/all'))
r=ok(c.get('/api/reports/summary'));assert 'sales_total' in r and 'top_products' in r
con=sqlite3.connect(tmp);assert con.execute('pragma integrity_check').fetchone()[0]=='ok';con.close();print('ALL_NEXT_0_3_INVENTORY_REPORTS_TESTS_PASSED')
