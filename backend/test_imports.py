import os, shutil, sqlite3, tempfile
from io import BytesIO
from pathlib import Path
from openpyxl import Workbook

tmp=Path(tempfile.mkdtemp())/'test.db';shutil.copy2(Path(__file__).with_name('veriler.db'),tmp)
os.environ['DATABASE_URL']=f'sqlite:///{tmp.as_posix()}'
from fastapi.testclient import TestClient
from app.main import app
client=TestClient(app)
login=client.post('/api/auth/login',json={'username':'admin','password':'admin123'});assert login.status_code==200,login.text
headers={'Authorization':'Bearer '+login.json()['access_token'],'X-Company-ID':str(login.json()['companies'][0]['id'])}

def book(headers_, row):
    wb=Workbook();ws=wb.active;ws.append(headers_);ws.append(row);out=BytesIO();wb.save(out);return out.getvalue()

r=client.post('/api/imports/customers/excel',headers=headers,files={'file':('musteri.xlsx',book(['İsim/Ünvan','Telefon','Açılış Bakiyesi'],['Excel Müşteri','5551112233',125]),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
assert r.status_code==200,r.text;assert r.json()['inserted']==1
r=client.post('/api/imports/suppliers/excel',headers=headers,files={'file':('tedarikci.xlsx',book(['İsim/Ünvan','Telefon'],['Excel Tedarikçi','5559998877']),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
assert r.status_code==200,r.text;assert r.json()['inserted']==1
r=client.post('/api/imports/products/excel',headers=headers,files={'file':('urun.xlsx',book(['Ürün Adı','Ürün Kodu','Satış Fiyatı','Stok','Birim','KDV Oranı'],['Excel Ürün','EX-001',250,7,'Adet',20]),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
assert r.status_code==200,r.text;assert r.json()['inserted']==1
products=client.get('/api/products?q=EX-001',headers=headers).json();assert len(products)==1 and products[0]['stock']==7
for path in ('/api/imports/customers/template.xlsx','/api/imports/suppliers/template.xlsx','/api/imports/products/template.xlsx'):
    x=client.get(path,headers=headers);assert x.status_code==200 and len(x.content)>1000
con=sqlite3.connect(tmp);assert con.execute('pragma integrity_check').fetchone()[0]=='ok';con.close()
print('ALL_NEXT_1_0_4_IMPORT_WORKFLOW_TESTS_PASSED')
