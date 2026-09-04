"""Sahadan gelen durum değişikliğinin sözleşmesi.

Bu ucun panelin `PATCH /work-orders/{id}/status` ucundan ayrıldığı ÜÇ nokta var
ve üçü de çevrimdışı çalışmanın doğrudan sonucu:

1. **Kimlik kapsamı** — teknisyen yalnız KENDİ iş emrini değiştirebilir.
2. **Dar geçiş kümesi** — iptal ve teslim sahadan yapılamaz (ticari sonuç
   doğururlar: stok iadesi, işçilik iptali, hizmet alacağının kesinleşmesi).
3. **İyimser kilit + tekrar koruması** — cihaz saatlerce eski veriyle gezmiş
   olabilir ve kuyruk aynı işlemi iki kez gönderebilir.

Aşağıdaki testler bu üçünü DAVRANIŞLA sınar; kaynak metni okuyan kırılgan
kontroller değil.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_field_transitions_are_a_strict_subset_of_the_domain() -> None:
    """Saha, alan modelinin izin vermediği bir geçişe ASLA izin veremez.

    Bu saf bir küme kontrolü ve veritabanı istemiyor; ama değeri yüksek: biri
    FIELD_ALLOWED_TRANSITIONS'a elle bir hedef eklerse ve bu hedef domain
    tablosunda yoksa, uç 409 yerine sessizce geçersiz bir duruma yazmaya
    çalışırdı.
    """
    sys.path.insert(0, str(BACKEND))
    from app.routers.field import FIELD_ALLOWED_TRANSITIONS
    from app.routers.work_orders import ALLOWED_TRANSITIONS

    assert set(FIELD_ALLOWED_TRANSITIONS) == set(ALLOWED_TRANSITIONS)
    for state, hedefler in FIELD_ALLOWED_TRANSITIONS.items():
        assert hedefler <= ALLOWED_TRANSITIONS[state], (
            f"{state} için saha, domain'in izin vermediği bir hedefe izin veriyor: "
            f"{hedefler - ALLOWED_TRANSITIONS[state]}"
        )


def test_field_can_never_cancel_or_deliver() -> None:
    """İptal ve teslim sahaya KAPALI — bilerek.

    İptal rezerve parça stoğunu geri verir ve işçiliği iptal eder; teslim
    hizmet alacağını kesinleştirir. İkisi de ofisin kararı. Bu test, birinin
    "kolaylık olsun" diye listeye eklemesini gürültüsüzce engellemek için var.
    """
    sys.path.insert(0, str(BACKEND))
    from app.routers.field import FIELD_ALLOWED_TRANSITIONS

    for state, hedefler in FIELD_ALLOWED_TRANSITIONS.items():
        assert "CANCELLED" not in hedefler, f"{state} → CANCELLED sahaya açılmış"
        assert "DELIVERED" not in hedefler, f"{state} → DELIVERED sahaya açılmış"
    # Tamamlanmış işi yeniden açmak da sahaya kapalı.
    assert FIELD_ALLOWED_TRANSITIONS["COMPLETED"] == set()


def run_field_status_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_field_status_write_sqlite(tmp_path: Path) -> None:
    run_field_status_smoke(f"sqlite:///{(tmp_path / 'field-status.db').as_posix()}")


def run_field_parts_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _PARTS_SMOKE],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_field_parts_write_sqlite(tmp_path: Path) -> None:
    run_field_parts_smoke(f"sqlite:///{(tmp_path / 'field-parts.db').as_posix()}")


def test_field_attachment_upload_sqlite(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'field-ek.db').as_posix()}"
    # Yüklenen dosyalar depoya değil, teste özel geçici klasöre yazılsın.
    env["SUNGUR_DATA_DIR"] = str(tmp_path / "veri")
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _ATTACHMENT_SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


_ATTACHMENT_SMOKE = r'''
from fastapi.testclient import TestClient

from app.main import app

ADMIN_PW = 'FieldEk!123'
# Küçük ama geçerli bir PNG.
PNG = bytes.fromhex(
    '89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4'
    '890000000a49444154789c6360000002000100ffff03000006000557bfabd400'
    '00000049454e44ae426082')


def admin_headers(client):
    # Paylasilan PG veritabaninda bu dosyanin dort smoke'u AYNI bootstrap
    # kullanicisini kullaniyor ve her biri sifreyi kendi ADMIN_PW'sine
    # donduruyor. 'admin123' sabit yazilinca smoke ILK kosmaya bagimli
    # oluyordu; ters sirada giris
    # {"detail":"Kullanici adi veya sifre hatali"} donuyordu. (Olculdu.)
    bilinen = ('admin123', ADMIN_PW, 'FieldEk!123', 'FieldParts!123',
               'FieldStatus!123', 'FieldIscilik!123')
    for candidate in bilinen:
        login = client.post('/api/auth/login',
                            json={'username':'admin','password':candidate})
        if login.status_code == 200:
            break
    assert login.status_code == 200, login.text
    body = login.json()
    headers = {'Authorization':'Bearer '+body['access_token'],
               'X-Company-ID':str(body['companies'][0]['id'])}
    if candidate != ADMIN_PW:
        changed = client.post('/api/auth/change-password', headers=headers,
                              json={'current_password':candidate,'new_password':ADMIN_PW})
        assert changed.status_code == 200, changed.text
        headers['Authorization'] = 'Bearer '+changed.json()['access_token']
    return headers, body


with TestClient(app) as client:
    headers, body = admin_headers(client)
    admin_id = int(body['user']['id'])

    musteri = client.post('/api/customers', headers=headers, json={'name':'Ek Test Müşterisi'})
    makine = client.post('/api/machines', headers=headers,
                         json={'customer_id':musteri.json()['id'],'brand':'CASE','model':'CX 8'})
    emir = client.post('/api/work-orders', headers=headers,
                       json={'machine_id':makine.json()['id'],'customer_id':musteri.json()['id'],
                             'technician_id':admin_id})
    assert emir.status_code == 201, emir.text
    emir_id = emir.json()['id']

    def yukle(op_id, tur='photo', emir_no=None, icerik=PNG):
        return client.post(f'/api/field/work-orders/{emir_no or emir_id}/attachments',
                           headers=headers,
                           data={'operation_id':op_id,'kind':tur},
                           files={'file':('saha.png', icerik, 'image/png')})

    def ek_sayisi():
        r = client.get(f'/api/work-order-attachments/{emir_id}', headers=headers)
        assert r.status_code == 200, r.text
        govde = r.json()
        return len(govde['items'] if isinstance(govde, dict) else govde)

    # --- 1) Fotoğraf yüklenir ------------------------------------------------
    ilk = yukle('op-foto-0001')
    assert ilk.status_code == 201, ilk.text
    assert ek_sayisi() == 1

    # --- 2) TEKRAR GÖNDERİM ikinci bir dosya BİRİKTİRMEMELİ -----------------
    # Fotoğraf yüklemesi uzun sürer; zayıf şebekede cevabın kaybolması olağan.
    # Koruma olmasaydı her yeniden deneme aynı resmi bir kez daha yazardı.
    tekrar = yukle('op-foto-0001')
    assert tekrar.status_code in (200, 201), tekrar.text
    assert tekrar.json().get('status') == 'already_applied', tekrar.text
    assert ek_sayisi() == 1, 'tekrar gönderim ikinci bir ek oluşturmuş'

    # --- 3) İmza da kabul edilir --------------------------------------------
    imza = yukle('op-imza-0001', 'signature')
    assert imza.status_code == 201, imza.text
    assert ek_sayisi() == 2

    # --- 4) Serbest "other" türü sahaya KAPALI ------------------------------
    # Panelde var ama saha yalnız kamera ve imza üretir; serbest kanal
    # cihazdan sunucuya rastgele dosya taşımanın önünü açardı.
    digeri = yukle('op-diger-0001', 'other')
    assert digeri.status_code == 422, digeri.text
    assert ek_sayisi() == 2

    # --- 5) BOŞ dosya reddedilir ve İZ BIRAKMAZ -----------------------------
    bos = yukle('op-bos-0001', 'photo', icerik=b'')
    assert bos.status_code == 422, bos.text
    from sqlalchemy import text as _sql
    from app.db import engine
    with engine.connect() as conn:
        iz = conn.execute(_sql(
            "SELECT COUNT(*) FROM field_operations WHERE operation_id='op-bos-0001'")).scalar()
    assert iz == 0, 'reddedilen yükleme field_operations izi bırakmış'

    # --- 6) BAŞKA teknisyenin iş emrine yüklenemez --------------------------
    baska = client.post('/api/users', headers=headers,
                        json={'username':'saha-ek-tek','password':'SahaEk!123456',
                              'display_name':'Diğer Teknisyen','role':'satis'})
    baska_emir = client.post('/api/work-orders', headers=headers,
                             json={'machine_id':makine.json()['id'],'customer_id':musteri.json()['id'],
                                   'technician_id':baska.json()['id']})
    yabanci = yukle('op-yabanci-ek-01', 'photo', emir_no=baska_emir.json()['id'])
    assert yabanci.status_code == 404, yabanci.text

    print('SAHA EK YÜKLEME SÖZLEŞMESİ TAMAM')
'''


_PARTS_SMOKE = r'''
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import app

ADMIN_PW = 'FieldParts!123'


def admin_headers(client):
    # Paylasilan PG veritabaninda bu dosyanin dort smoke'u AYNI bootstrap
    # kullanicisini kullaniyor ve her biri sifreyi kendi ADMIN_PW'sine
    # donduruyor. 'admin123' sabit yazilinca smoke ILK kosmaya bagimli
    # oluyordu; ters sirada giris
    # {"detail":"Kullanici adi veya sifre hatali"} donuyordu. (Olculdu.)
    bilinen = ('admin123', ADMIN_PW, 'FieldEk!123', 'FieldParts!123',
               'FieldStatus!123', 'FieldIscilik!123')
    for candidate in bilinen:
        login = client.post('/api/auth/login',
                            json={'username':'admin','password':candidate})
        if login.status_code == 200:
            break
    assert login.status_code == 200, login.text
    body = login.json()
    headers = {'Authorization':'Bearer '+body['access_token'],
               'X-Company-ID':str(body['companies'][0]['id'])}
    if candidate != ADMIN_PW:
        changed = client.post('/api/auth/change-password', headers=headers,
                              json={'current_password':candidate,'new_password':ADMIN_PW})
        assert changed.status_code == 200, changed.text
        headers['Authorization'] = 'Bearer '+changed.json()['access_token']
    return headers, body


with TestClient(app) as client:
    headers, body = admin_headers(client)
    admin_id = int(body['user']['id'])

    musteri = client.post('/api/customers', headers=headers, json={'name':'Parça Test Müşterisi'})
    assert musteri.status_code == 201, musteri.text
    makine = client.post('/api/machines', headers=headers,
                         json={'customer_id':musteri.json()['id'],'brand':'CASE','model':'CX 8'})
    assert makine.status_code == 201, makine.text
    urun = client.post('/api/products', headers=headers,
                       json={'name':'SAHA FİLTRE','product_code':'SF-1',
                             'sale_price':'250.00','purchase_price':'150.00','vat_rate':20,'unit':'ADET'})
    assert urun.status_code == 201, urun.text
    urun_id = urun.json()['id']

    emir = client.post('/api/work-orders', headers=headers,
                       json={'machine_id':makine.json()['id'],'customer_id':musteri.json()['id'],
                             'technician_id':admin_id})
    assert emir.status_code == 201, emir.text
    emir_id = emir.json()['id']
    # Parça yalnız açılmış iş emrine eklenebilir.
    ilerlet = client.patch(f'/api/work-orders/{emir_id}/status', headers=headers,
                           json={'status':'IN_PROGRESS'})
    assert ilerlet.status_code == 200, ilerlet.text

    def parca_ekle(op_id, adet='1'):
        return client.post(f'/api/field/work-orders/{emir_id}/parts', headers=headers,
                           json={'operation_id':op_id,'product_id':urun_id,'quantity':adet})

    # --- 1) Servis aracı deposu YOKKEN anlaşılır hata; merkez depoya DÜŞMEZ ---
    depo_yok = parca_ekle('op-depoyok-0001')
    assert depo_yok.status_code == 409, depo_yok.text
    assert 'servis aracı deposu' in depo_yok.json()['detail'], depo_yok.text

    # Reddedilen istek "uygulandı" izi bırakmamalı, yoksa depo tanımlandıktan
    # sonra aynı işlem sessizce atlanırdı.
    from sqlalchemy import text as _sql
    from app.db import engine
    with engine.connect() as conn:
        iz = conn.execute(_sql(
            "SELECT COUNT(*) FROM field_operations WHERE operation_id='op-depoyok-0001'")).scalar()
    assert iz == 0, 'başarısız istek field_operations izi bırakmış'

    # --- 2) Servis aracı deposu tanımlanınca çalışır -------------------------
    depo = client.post('/api/warehouses', headers=headers,
                       json={'name':'Servis Aracı 34','code':'SRV-34'})
    assert depo.status_code == 201, depo.text
    depo_id = depo.json()['id']
    with engine.begin() as conn:
        conn.execute(_sql("UPDATE warehouses SET warehouse_type='SERVICE_VEHICLE',"
                          "technician_user_id=:uid WHERE id=:id"),
                     {'uid':admin_id,'id':depo_id})
        # Araca stok koy ki çıkış yapılabilsin.
        # Depo oluşturulurken ürünler için satır zaten açılmış olabiliyor;
        # önce güncelle, yoksa ekle.
        guncellendi = conn.execute(_sql(
            "UPDATE warehouse_stocks SET quantity=10,reserved_quantity=0"
            " WHERE warehouse_id=:wid AND product_id=:pid"),
            {'wid':depo_id,'pid':urun_id}).rowcount
        if not guncellendi:
            conn.execute(_sql(
                "INSERT INTO warehouse_stocks(company_id,warehouse_id,product_id,quantity,"
                "reserved_quantity,critical_stock) VALUES(:cid,:wid,:pid,10,0,0)"),
                {'cid':int(headers['X-Company-ID']),'wid':depo_id,'pid':urun_id})

    eklendi = parca_ekle('op-parca-0001', '2')
    assert eklendi.status_code == 201, eklendi.text
    satir = eklendi.json()
    assert int(satir['warehouse_id']) == depo_id, 'yanlış depodan düşülmüş'
    # Fiyat ÜRÜNDEN gelmeli; istemci fiyat göndermiyor.
    assert Decimal(str(satir['unit_price'])) == Decimal('250.00'), satir

    # --- 3) TEKRAR GÖNDERİM stoğu ikinci kez düşürmemeli --------------------
    def arac_stogu():
        with engine.connect() as conn:
            return conn.execute(_sql(
                "SELECT quantity FROM warehouse_stocks WHERE warehouse_id=:wid AND product_id=:pid"),
                {'wid':depo_id,'pid':urun_id}).scalar()

    stok_once = arac_stogu()
    tekrar = parca_ekle('op-parca-0001', '2')
    assert tekrar.status_code in (200, 201), tekrar.text
    assert tekrar.json().get('status') == 'already_applied', tekrar.text
    assert arac_stogu() == stok_once, 'tekrar gönderim stoğu ikinci kez düşürmüş'

    # --- 4) BAŞKA teknisyenin iş emrine parça eklenemez ----------------------
    baska = client.post('/api/users', headers=headers,
                        json={'username':'saha-parca-tek','password':'SahaParca!123',
                              'display_name':'Diğer Teknisyen','role':'satis'})
    assert baska.status_code in (200, 201), baska.text
    baska_emir = client.post('/api/work-orders', headers=headers,
                             json={'machine_id':makine.json()['id'],'customer_id':musteri.json()['id'],
                                   'technician_id':baska.json()['id']})
    assert baska_emir.status_code == 201, baska_emir.text
    yabanci = client.post(f"/api/field/work-orders/{baska_emir.json()['id']}/parts", headers=headers,
                          json={'operation_id':'op-yabanci-parca-01','product_id':urun_id,'quantity':'1'})
    assert yabanci.status_code == 404, yabanci.text

    print('SAHA PARÇA SÖZLEŞMESİ TAMAM')
'''


_SMOKE = r'''
from fastapi.testclient import TestClient

from app.main import app

ADMIN_PW = 'FieldStatus!123'


def admin_headers(client):
    # Paylasilan PG veritabaninda bu dosyanin dort smoke'u AYNI bootstrap
    # kullanicisini kullaniyor ve her biri sifreyi kendi ADMIN_PW'sine
    # donduruyor. 'admin123' sabit yazilinca smoke ILK kosmaya bagimli
    # oluyordu; ters sirada giris
    # {"detail":"Kullanici adi veya sifre hatali"} donuyordu. (Olculdu.)
    bilinen = ('admin123', ADMIN_PW, 'FieldEk!123', 'FieldParts!123',
               'FieldStatus!123', 'FieldIscilik!123')
    for candidate in bilinen:
        login = client.post('/api/auth/login',
                            json={'username':'admin','password':candidate})
        if login.status_code == 200:
            break
    assert login.status_code == 200, login.text
    body = login.json()
    headers = {'Authorization':'Bearer '+body['access_token'],
               'X-Company-ID':str(body['companies'][0]['id'])}
    if candidate != ADMIN_PW:
        changed = client.post('/api/auth/change-password', headers=headers,
                              json={'current_password':candidate,'new_password':ADMIN_PW})
        assert changed.status_code == 200, changed.text
        headers['Authorization'] = 'Bearer '+changed.json()['access_token']
    return headers, body


with TestClient(app) as client:
    headers, body = admin_headers(client)
    admin_id = int(body['user']['id'])

    musteri = client.post('/api/customers', headers=headers,
                          json={'name':'Saha Test Müşterisi','address':'Tarla yolu 3'})
    assert musteri.status_code == 201, musteri.text
    musteri_id = musteri.json()['id']

    makine = client.post('/api/machines', headers=headers,
                         json={'customer_id':musteri_id,'brand':'CASE','model':'CX 8'})
    assert makine.status_code == 201, makine.text
    makine_id = makine.json()['id']

    # Teknisyen olarak admin'in kendisi atanıyor: saha ucu isteği yapan
    # kullanıcıyı teknisyen kabul ediyor.
    emir = client.post('/api/work-orders', headers=headers,
                       json={'machine_id':makine_id,'customer_id':musteri_id,
                             'technician_id':admin_id,'complaint':'Çalışmıyor'})
    assert emir.status_code == 201, emir.text
    emir_id = emir.json()['id']

    def snapshot_emri():
        s = client.get('/api/field/snapshot', headers=headers)
        assert s.status_code == 200, s.text
        for item in s.json()['work_orders']:
            if item['id'] == emir_id:
                return item
        raise AssertionError('iş emri snapshot içinde yok')

    def durum_degistir(op_id, version, status):
        return client.post(f'/api/field/work-orders/{emir_id}/status', headers=headers,
                           json={'operation_id':op_id,'expected_version':version,
                                 'status':status})

    ilk = snapshot_emri()
    assert ilk['status'] == 'OPEN', ilk['status']

    # --- 1) Eski sürümle yazma reddedilmeli ve GÜNCEL kaydı döndürmeli -------
    bayat = durum_degistir('op-bayat-0001', '1999-01-01T00:00:00+00:00', 'IN_PROGRESS')
    assert bayat.status_code == 409, bayat.text
    govde = bayat.json()
    assert 'current' in govde, govde
    assert govde['current']['id'] == emir_id
    assert govde['current']['status'] == 'OPEN'
    assert snapshot_emri()['status'] == 'OPEN', 'reddedilen yazma durumu değiştirmiş'

    # --- 2) Sahadan iptal edilemez (domain izin verse bile) ------------------
    iptal = durum_degistir('op-iptal-0001', ilk['version'], 'CANCELLED')
    assert iptal.status_code == 409, iptal.text
    assert snapshot_emri()['status'] == 'OPEN'

    # --- 3) Geçerli geçiş -----------------------------------------------------
    tamam = durum_degistir('op-ilerlet-0001', ilk['version'], 'IN_PROGRESS')
    assert tamam.status_code == 200, tamam.text
    sonra = tamam.json()
    assert sonra['status'] == 'IN_PROGRESS', sonra
    assert sonra['version'] != ilk['version'], 'sürüm ilerlemedi; iyimser kilit işlemez'
    assert snapshot_emri()['status'] == 'IN_PROGRESS'

    # --- 4) TEKRAR GÖNDERİM: aynı operation_id ikinci kez -------------------
    # Kuyruk cevabı kaybederse aynı işlemi yeniden yollar. İkinci gönderim
    # durumu tekrar DEĞİŞTİRMEMELİ; üstelik gönderdiği sürüm artık bayat
    # olduğu hâlde 409 da vermemeli — işlem zaten uygulanmış durumda.
    tekrar = durum_degistir('op-ilerlet-0001', ilk['version'], 'IN_PROGRESS')
    assert tekrar.status_code == 200, tekrar.text
    assert tekrar.json()['status'] == 'IN_PROGRESS'

    # Tekrar gönderim yeni bir field_operations satırı da açmamalı.
    from sqlalchemy import text as _sql
    from app.db import engine
    with engine.connect() as conn:
        adet = conn.execute(_sql(
            "SELECT COUNT(*) FROM field_operations WHERE operation_id='op-ilerlet-0001'"
        )).scalar()
    assert adet == 1, f'tekrar gönderim {adet} kayıt bırakmış'

    # --- 5) BAŞKA teknisyenin iş emri görünmemeli ---------------------------
    baska = client.post('/api/users', headers=headers,
                        json={'username':'saha-teknisyen','password':'SahaTeknisyen!123',
                              'display_name':'Saha Teknisyeni','role':'satis'})
    assert baska.status_code in (200, 201), baska.text
    baska_id = baska.json()['id']

    baska_emir = client.post('/api/work-orders', headers=headers,
                             json={'machine_id':makine_id,'customer_id':musteri_id,
                                   'technician_id':baska_id,'complaint':'Başkasının işi'})
    assert baska_emir.status_code == 201, baska_emir.text
    baska_emir_id = baska_emir.json()['id']

    yabanci = client.post(f'/api/field/work-orders/{baska_emir_id}/status', headers=headers,
                          json={'operation_id':'op-yabanci-0001',
                                'expected_version':'1999-01-01T00:00:00+00:00',
                                'status':'IN_PROGRESS'})
    # 404: "var ama sana kapalı" bilgisini sızdırmamak için 403 DEĞİL.
    assert yabanci.status_code == 404, yabanci.text

    print('SAHA DURUM YAZMA SÖZLEŞMESİ TAMAM')
'''


def test_field_labor_write_sqlite(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'field-iscilik.db').as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _LABOR_SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


_LABOR_SMOKE = r'''
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import app

ADMIN_PW = 'FieldIscilik!123'


def admin_headers(client):
    # Paylasilan PG veritabaninda bu dosyanin dort smoke'u AYNI bootstrap
    # kullanicisini kullaniyor ve her biri sifreyi kendi ADMIN_PW'sine
    # donduruyor. 'admin123' sabit yazilinca smoke ILK kosmaya bagimli
    # oluyordu; ters sirada giris
    # {"detail":"Kullanici adi veya sifre hatali"} donuyordu. (Olculdu.)
    bilinen = ('admin123', ADMIN_PW, 'FieldEk!123', 'FieldParts!123',
               'FieldStatus!123', 'FieldIscilik!123')
    for candidate in bilinen:
        login = client.post('/api/auth/login',
                            json={'username':'admin','password':candidate})
        if login.status_code == 200:
            break
    assert login.status_code == 200, login.text
    body = login.json()
    headers = {'Authorization':'Bearer '+body['access_token'],
               'X-Company-ID':str(body['companies'][0]['id'])}
    if candidate != ADMIN_PW:
        changed = client.post('/api/auth/change-password', headers=headers,
                              json={'current_password':candidate,'new_password':ADMIN_PW})
        assert changed.status_code == 200, changed.text
        headers['Authorization'] = 'Bearer '+changed.json()['access_token']
    return headers, body


with TestClient(app) as client:
    headers, body = admin_headers(client)
    admin_id = int(body['user']['id'])

    musteri = client.post('/api/customers', headers=headers, json={'name':'İşçilik Müşterisi'})
    makine = client.post('/api/machines', headers=headers,
                         json={'customer_id':musteri.json()['id'],'brand':'CASE','model':'CX 8'})
    # Saatlik ücret İŞ EMRİ BAŞLIĞINDA belirleniyor; saha onu göndermiyor.
    emir = client.post('/api/work-orders', headers=headers,
                       json={'machine_id':makine.json()['id'],'customer_id':musteri.json()['id'],
                             'technician_id':admin_id,'labor_rate':'450.00'})
    assert emir.status_code == 201, emir.text
    emir_id = emir.json()['id']
    client.patch(f'/api/work-orders/{emir_id}/status', headers=headers, json={'status':'IN_PROGRESS'})

    def iscilik(op_id, saat='2.5', emir_no=None):
        return client.post(f'/api/field/work-orders/{emir_no or emir_id}/labor', headers=headers,
                           json={'operation_id':op_id,'hours':saat,'note':'Saha müdahalesi'})

    def satirlar():
        r = client.get(f'/api/work-orders/{emir_id}/labor-lines', headers=headers)
        assert r.status_code == 200, r.text
        govde = r.json()
        return govde['items'] if isinstance(govde, dict) else govde

    # --- 1) İşçilik yazılır; ücret BAŞLIKTAN donar, teknisyen İSTEĞİ YAPAN olur
    ilk = iscilik('op-iscilik-0001')
    assert ilk.status_code == 201, ilk.text
    satir = ilk.json()
    assert Decimal(str(satir['hourly_rate'])) == Decimal('450.00'), satir
    assert int(satir['technician_user_id']) == admin_id, satir
    # DRAFT: ofis onaylamadan faturaya girmemeli.
    assert str(satir['line_status']) == 'DRAFT', satir
    assert len(satirlar()) == 1

    # --- 2) TEKRAR GÖNDERİM ikinci satır AÇMAMALI ---------------------------
    # Açsaydı aynı işçilik iki kez faturalanırdı.
    tekrar = iscilik('op-iscilik-0001')
    assert tekrar.status_code in (200, 201), tekrar.text
    assert tekrar.json().get('status') == 'already_applied', tekrar.text
    assert len(satirlar()) == 1, 'tekrar gönderim ikinci satır açmış'

    # --- 3) Sıfır/negatif süre reddedilir ve iz bırakmaz --------------------
    sifir = iscilik('op-iscilik-sifir', '0')
    assert sifir.status_code == 422, sifir.text
    from sqlalchemy import text as _sql
    from app.db import engine
    with engine.connect() as conn:
        iz = conn.execute(_sql(
            "SELECT COUNT(*) FROM field_operations WHERE operation_id='op-iscilik-sifir'")).scalar()
    assert iz == 0, 'reddedilen istek iz bırakmış'

    # --- 4) BAŞKA teknisyenin iş emrine yazılamaz ---------------------------
    baska = client.post('/api/users', headers=headers,
                        json={'username':'saha-iscilik-tek','password':'SahaIscilik!123',
                              'display_name':'Diğer Teknisyen','role':'satis'})
    baska_emir = client.post('/api/work-orders', headers=headers,
                             json={'machine_id':makine.json()['id'],'customer_id':musteri.json()['id'],
                                   'technician_id':baska.json()['id']})
    yabanci = iscilik('op-yabanci-iscilik', '1', baska_emir.json()['id'])
    assert yabanci.status_code == 404, yabanci.text

    print('SAHA İŞÇİLİK SÖZLEŞMESİ TAMAM')
'''
