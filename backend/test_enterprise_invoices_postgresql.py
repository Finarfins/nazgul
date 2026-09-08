from concurrent.futures import ThreadPoolExecutor
import os
import pytest

def _acilisa_cek() -> None:
    """Admin şifresini AÇILIŞ DURUMUNA (`admin123` + `must_change_password`) yaz.

    D2/1B-A ikizlerinden DEVRALINDI. CI dosya başına `reset_schema` çalıştırdığı
    için (`ci.yml:609`) CI ortamında DB durumu paylaşılmaz; bu dikiş yerel/pglens
    paylaşılan veritabanı koşularını korur ve gelecekte reset_schema adımının
    kaldırılmasına karşı savunma sağlar. Tek yönlü bir çare (yalnız teardown)
    dosyayı iyi bir komşu yapar ama KENDİSİNİ korumaz, çünkü şifreyi bozan
    ÖNCEKİ dosya olabilir. Bu yüzden İKİ UÇTAN çağrılır.

    these two count rows in the company; on a shared DB the login company is polluted by neighbours, so they isolate into a fresh company — deliberate, part of the shared-DB property.
    """
    try:
        from tests.pg_ikiz_yardimci import acilisa_cek
        acilisa_cek()
    except ImportError:
        from sqlalchemy import text as _text
        from app.auth import hash_password
        from app.db import SessionLocal, engine as _eng

        if _eng.dialect.name != "postgresql":
            return
        with SessionLocal() as db:
            if db.execute(_text("SELECT to_regclass('public.app_users')")).scalar() is None:
                return
            db.execute(
                _text(
                    "UPDATE app_users SET password_hash=:h, "
                    "must_change_password=true WHERE username='admin'"
                ),
                {"h": hash_password("admin123")},
            )
            db.commit()


@pytest.fixture(autouse=True)
def _acilis_sifresi():
    _acilisa_cek()
    try:
        yield
    finally:
        _acilisa_cek()


@pytest.mark.postgresql
def test_invoice_numbering_and_generation_concurrency(monkeypatch:pytest.MonkeyPatch):
    """these two count rows in the company; on a shared DB the login company is polluted by neighbours, so they isolate into a fresh company — deliberate, part of the shared-DB property."""
    url=os.environ.get('ENTERPRISE_INVOICE_TEST_DATABASE_URL')
    if not url: pytest.skip('ENTERPRISE_INVOICE_TEST_DATABASE_URL is not configured')
    assert url.startswith('postgresql'),'Enterprise invoice PostgreSQL twin must use PostgreSQL'
    monkeypatch.setenv('DATABASE_URL',url)
    from fastapi.testclient import TestClient
    from sqlalchemy import text
    from app.db import SessionLocal
    from app.main import app
    with TestClient(app) as c:
        login=c.post('/api/auth/login',json={'username':'admin','password':'admin123'}).json(); uid=login['user']['id']
        h={'Authorization':'Bearer '+login['access_token'],'X-Company-ID':str(login['companies'][0]['id'])}
        changed=c.post('/api/auth/change-password',headers=h,json={'current_password':'admin123','new_password':'EnterpriseInvoicePg123!'}).json(); h['Authorization']='Bearer '+changed['access_token']
        company=c.post('/api/companies',headers=h,json={'name':'Enterprise Invoices Co'}).json(); cid=company['id']; h['X-Company-ID']=str(cid)
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
