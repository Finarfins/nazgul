"""Belge ŞUBESİ istekten gelir ve kiracı sınırında doğrulanmak ZORUNDA.

``orders``/``purchases`` ile ``branches`` arasında BİLEŞİK yabancı anahtar yok
(bkz. ``app/tenancy.py``: ``branches`` yalnız ``companies.id``'ye bağlı), yani
veritabanı çapraz kiracı bağı ENGELLEMİYOR. ``_save`` içinde ``warehouse_id``
``_warehouse`` ile doğrulanıyordu ama ``branch_id`` ham payload'dan yazılıyordu:
B firmasının satış/alış belgesi A firmasının şubesine referans verebiliyordu.

Bu testin iddiası iki yönlü ve ikisi de gerekli:

1. **Yabancı şube 404.** 403 değil — 403 "o şube VAR ama sana kapalı" bilgisini
   sızdırır ve şube kimlikleri ardışık tamsayı olduğu için sayılabilir hale
   gelir. Kodun geri kalanı (``_satir``, ``_makine_dogrula``) da 404 veriyor.
2. **Kendi şubesi hâlâ çalışır.** Doğrulama eklerken meşru akışı kırmamak,
   düzeltmenin kendisi kadar önemli: yalnız reddi test etmek, her şeyi
   reddeden bir yamayı da yeşil gösterirdi.

İki uç da (``/api/orders`` ve ``/api/purchases``) ayrı ayrı sınanıyor; ikisi
``_save``'i paylaşıyor ama farklı ``config`` ile çağırıyor.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def run_branch_scope_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_document_branch_must_belong_to_the_active_company(tmp_path: Path) -> None:
    run_branch_scope_smoke(f"sqlite:///{(tmp_path / 'branch-scope.db').as_posix()}")


_SMOKE = r'''
from fastapi.testclient import TestClient

from app.main import app

ADMIN_PW = 'BranchScope!12345'

with TestClient(app) as client:
    login = client.post('/api/auth/login',
                        json={'username':'admin','password':'admin123'})
    assert login.status_code == 200, login.text
    body = login.json()
    firma_a = body['companies'][0]['id']
    h = {'Authorization':'Bearer '+body['access_token'],
         'X-Company-ID':str(firma_a)}
    ch = client.post('/api/auth/change-password', headers=h,
                     json={'current_password':'admin123','new_password':ADMIN_PW})
    assert ch.status_code == 200, ch.text
    h['Authorization'] = 'Bearer '+ch.json()['access_token']

    # --- kurulum: iki firma, her birinin KENDİ şubesi ----------------------
    # Firma olusturma ucu her firmaya bir 'Merkez' subesi aciyor
    # (routers/companies.py), yani B'nin subesi A'ya ait DEGIL.
    b = client.post('/api/companies', headers=h, json={'name':'Sube B Firmasi'})
    assert b.status_code == 201, b.text
    firma_b = b.json()['id']
    assert firma_b != firma_a

    sube_a = client.get('/api/branches', headers=h).json()
    assert sube_a, 'A firmasinin subesi yok; kurulum varsayimi bozuk'
    sube_a_id = sube_a[0]['id']

    hb = dict(h, **{'X-Company-ID': str(firma_b)})
    sube_b = client.get('/api/branches', headers=hb).json()
    assert sube_b, 'B firmasinin subesi yok; kurulum varsayimi bozuk'
    sube_b_id = sube_b[0]['id']
    assert sube_b_id != sube_a_id

    # A firmasinda belge icin gereken asgari kayitlar.
    musteri = client.post('/api/customers', headers=h,
                          json={'name':'Sube Testi Musterisi'})
    assert musteri.status_code == 201, musteri.text
    musteri_id = musteri.json()['id']

    tedarikci = client.post('/api/suppliers', headers=h,
                            json={'name':'Sube Testi Tedarikcisi'})
    assert tedarikci.status_code == 201, tedarikci.text
    tedarikci_id = tedarikci.json()['id']

    urun = client.post('/api/products', headers=h,
                       json={'name':'Sube Testi Urunu','purchase_price':'10.00',
                             'sale_price':'20.00','vat_rate':20})
    assert urun.status_code == 201, urun.text
    urun_id = urun.json()['id']

    def belge(entity_id, branch_id):
        govde = {'entity_id':entity_id,'transaction_date':'2026-01-15',
                 'status':'draft','payment_method':'credit',
                 'items':[{'product_id':urun_id,'quantity':'1',
                           'unit_price':'20.00','vat_rate':20}]}
        if branch_id is not None:
            govde['branch_id'] = branch_id
        return govde

    # --- KAPI 1: YABANCI SUBE 404 -----------------------------------------
    # A firmasi olarak B'nin subesine belge baglamaya calis.
    r = client.post('/api/orders', headers=h, json=belge(musteri_id, sube_b_id))
    assert r.status_code == 404, (
        f'satis: yabanci sube {sube_b_id} -> {r.status_code} (404 bekleniyordu): {r.text}')
    assert 'Sube' in r.json()['detail'] or 'ube bulunamad' in r.json()['detail'], r.text

    r = client.post('/api/purchases', headers=h,
                    json=belge(tedarikci_id, sube_b_id))
    assert r.status_code == 404, (
        f'alis: yabanci sube {sube_b_id} -> {r.status_code} (404 bekleniyordu): {r.text}')

    # Var OLMAYAN sube de ayni cevabi vermeli: 404 vs 500 farki, FK ihlalinin
    # sizdirdigi "bu kimlik gercekte var" sinyalini kapatir.
    r = client.post('/api/orders', headers=h, json=belge(musteri_id, 999999))
    assert r.status_code == 404, (
        f'satis: olmayan sube -> {r.status_code} (404 bekleniyordu): {r.text}')

    # --- KAPI 2: KENDI SUBESI CALISIR --------------------------------------
    r = client.post('/api/orders', headers=h, json=belge(musteri_id, sube_a_id))
    assert r.status_code == 201, (
        f'satis: KENDI subesi {sube_a_id} -> {r.status_code} (201 bekleniyordu): {r.text}')
    satis_id = r.json()['id']

    r = client.post('/api/purchases', headers=h,
                    json=belge(tedarikci_id, sube_a_id))
    assert r.status_code == 201, (
        f'alis: KENDI subesi {sube_a_id} -> {r.status_code} (201 bekleniyordu): {r.text}')

    # Sube YOKSA (None) belge yine olusmali: alan istege bagli, dogrulama
    # zorunluluga donusmemeli.
    r = client.post('/api/orders', headers=h, json=belge(musteri_id, None))
    assert r.status_code == 201, (
        f'satis: subesiz belge -> {r.status_code} (201 bekleniyordu): {r.text}')

    # --- KAPI 3: GUNCELLEME DE KAPALI --------------------------------------
    # _save hem INSERT hem UPDATE yolunda kullaniliyor; ikinci yazma noktasi
    # dogrulanmazsa belge once temiz olusturulup sonra yabanci subeye
    # tasinabilirdi.
    r = client.put(f'/api/orders/{satis_id}', headers=h,
                   json=belge(musteri_id, sube_b_id))
    assert r.status_code == 404, (
        f'satis guncelleme: yabanci sube -> {r.status_code} (404 bekleniyordu): {r.text}')

    r = client.put(f'/api/orders/{satis_id}', headers=h,
                   json=belge(musteri_id, sube_a_id))
    assert r.status_code == 200, (
        f'satis guncelleme: KENDI subesi -> {r.status_code} (200 bekleniyordu): {r.text}')

    print('SUBE KIRACI KAPSAMI TAMAM')
'''
