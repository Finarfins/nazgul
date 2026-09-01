"""Kantar fişi — SÖZLEŞME. Türetim, üç değerli bayraklar, kapılar.

Konu: migration 0064 (kantar fişi PR1). Defter tarafı KARDEŞ dosyada
(`test_kantar_fisi_defter.py`); burada ölçülen şey UÇLARIN sözleşmesi.

--- SESSİZCE YANLIŞ OLABİLECEK BEŞ ŞEY --------------------------------------

1. **BİLEŞİM.** ``net = brüt − Σ(brüt × oran/100)`` TOPLAMSALDIR, SIRALI
   DEĞİL. %2 + %3 toplamsalda ``0.9500·brüt``, sıralıda ``0.9506·brüt`` verir.
   İkisi de "makul" görünür; test SAYIYI çiviliyor.
2. **`ticket_net_quantity` TÜRETİME GİRMEZ.** Kağıdın neti bir TANIKTIR.
   Türetim ona kayarsa sunucu, istemcinin gönderdiği sayıyı kendi hesabının
   kaynağı yapmış olur — ``ActivityInputWrite.total_cost``un yasakladığı şey.
3. **BAYRAKLAR ÜÇ DEĞERLİ.** ``None`` (ölçülmedi) ile ``False`` (ölçüldü,
   uyuşuyor) aynı şey değil. İkisini birleştirmek, hiç fişi olmayan bir hasadı
   "satış neti aşmıyor" diye okuturdu.
4. **MEVCUT OKUMA UÇLARI DEĞİŞMEDİ.** Fişin varlığı `/api/field-harvests`,
   `/api/field-dashboard` ve parsel zaman çizelgesinin gövdesini
   DEĞİŞTİRMEMELİ. Defter iddiasının okuma tarafındaki eşi.
5. **KAPI SESSİZ KALMASIN.** ``/api/field-harvest-tickets`` hiçbir mevcut
   tarla önekinin altına düşmüyor; listeye yazılmasaydı `/api/field` genel
   kuralına düşüp SESSİZCE `field_service` iznine bağlanırdı.

--- STATİK KAPILAR NİYE VAR -------------------------------------------------

Defter testi davranışı ölçüyor; ama "tüketici fişi okumuyor" iddiası, ileride
biri `_hasat_kalemleri`ye bir LEFT JOIN eklediğinde davranış testinden ÖNCE
kırılmalı ve SEBEBİYLE kırılmalı. İki statik kapı bunu yapıyor:
tüketici kaynağında fiş tablosunun ADI GEÇMEYECEK, ve ``create_harvest_ticket``
gövdesinde outbox yazıcısı ÇAĞRILMAYACAK.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

FIS_TABLOLARI = ("field_harvest_tickets", "field_harvest_ticket_deductions")


# ---------------------------------------------------------------------------
# STATİK KAPILAR — veritabanı yok.
# ---------------------------------------------------------------------------


def test_tuketici_fisin_adini_bile_gecirmiyor() -> None:
    """`field_stok_tuketici` fiş tablolarına HİÇBİR yerde değinmez.

    Bu kapı, defter testinden ÖNCE ve SEBEBİYLE kırılır: biri
    `_hasat_kalemleri`ye `LEFT JOIN field_harvest_tickets` eklediğinde burası
    kırmızı olur ve hata mesajı ne yapıldığını söyler.

    NE İDDİA ETMEZ: tüketicinin fişi DOLAYLI bir yoldan (başka bir modül
    üzerinden) okumadığını. Ölçtüğü şey, unutmanın/eklemenin pratikte aldığı
    biçim olan doğrudan referanstır.
    """
    kaynak = (BACKEND / "app" / "field_stok_tuketici.py").read_text(encoding="utf-8")
    gecenler = [ad for ad in FIS_TABLOLARI if ad in kaynak]
    assert gecenler == [], (
        "Tüketici kantar fişine değiniyor: "
        f"{gecenler}. PR1'in tek iddiası defterin DEĞİŞMEMESİYDİ; fişi deftere "
        "bağlamak AYRI bir iştir ve o iş hasat olayının ÜRETİM ANINI ya da "
        "düzeltici bir ikinci olayı gerektirir (bkz. migration 0064 başlığı)."
    )


def _fonksiyon(kaynak: str, ad: str) -> ast.FunctionDef:
    agac = ast.parse(kaynak)
    for dugum in ast.walk(agac):
        if isinstance(dugum, ast.FunctionDef) and dugum.name == ad:
            return dugum
    raise AssertionError(f"{ad} bulunamadı")


def test_fis_yazimi_outbox_olayi_uretmiyor() -> None:
    """`create_harvest_ticket` outbox yazıcısını ÇAĞIRMAZ.

    Çağırsaydı aynı hasat defterde İKİ KEZ üretilirdi: biri hasat yazılırken
    doğan olaydan, biri fişten.
    """
    kaynak = (BACKEND / "app" / "routers" / "farm.py").read_text(encoding="utf-8")
    govde = _fonksiyon(kaynak, "create_harvest_ticket")
    cagrilar = {
        dugum.func.id
        for dugum in ast.walk(govde)
        if isinstance(dugum, ast.Call) and isinstance(dugum.func, ast.Name)
    }
    assert "_entegrasyon_olayi_yaz" not in cagrilar, (
        "Fiş yazımı outbox olayı üretiyor: aynı hasat defterde İKİ KEZ "
        "üretilir (biri hasat olayından, biri fişten)."
    )


def test_fis_ucu_farm_iznine_bagli() -> None:
    """GET -> `farm.view`, POST -> `farm.manage`.

    `/api/field-harvest-tickets`, `/api/field-harvests` ile BAŞLAMIYOR
    (sondaki `s`); `_FARM_PATH_PREFIXES`e yazılmasaydı `/api/field` genel
    kuralına düşer ve sessizce `field_service` iznine bağlanırdı — tarla
    modülünde iki kez yaşanmış tuzak.
    """
    sys.path.insert(0, str(BACKEND))
    from app.auth import required_permission

    assert required_permission("GET", "/api/field-harvest-tickets") == "farm.view"
    assert required_permission("POST", "/api/field-harvest-tickets") == "farm.manage"
    # Kapının GERÇEKTEN bu satır sayesinde kapalı olduğunu göster: önek
    # listesinden çıkarılmış bir yol `field_service`e düşer.
    assert required_permission("GET", "/api/field-baska-bir-sey") == "field_service"


# ---------------------------------------------------------------------------
# DAVRANIŞ — taze veritabanı, gerçek uçlar.
# ---------------------------------------------------------------------------


def run_sozlesme_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    assert "KANTAR FISI SOZLESME TAMAM" in completed.stdout, completed.stdout


def test_kantar_fisi_sozlesme_sqlite(tmp_path: Path) -> None:
    run_sozlesme_smoke(f"sqlite:///{(tmp_path / 'kantar-sozlesme.db').as_posix()}")


_SMOKE = r'''
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import app

ADMIN_PW = 'KantarSozlesme!123'


def admin_headers(client):
    login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
    assert login.status_code == 200, login.text
    body = login.json()
    h = {'Authorization':'Bearer '+body['access_token'],
         'X-Company-ID':str(body['companies'][0]['id'])}
    ch = client.post('/api/auth/change-password', headers=h,
                     json={'current_password':'admin123','new_password':ADMIN_PW})
    assert ch.status_code == 200, ch.text
    h['Authorization'] = 'Bearer '+ch.json()['access_token']
    return h


def fis(client, h, **alanlar):
    return client.post('/api/field-harvest-tickets', headers=h, json=alanlar)


with TestClient(app) as client:
    h = admin_headers(client)

    ciftlik = client.post('/api/farms', headers=h,
                          json={'code':'ks','name':'Kantar Sozlesme'}).json()
    parsel = client.post('/api/farm-parcels', headers=h,
                         json={'farm_id':ciftlik['id'],'code':'ksp','name':'Parsel',
                               'area_decare':'100.0000'}).json()
    sezon = client.post('/api/crop-seasons', headers=h,
                        json={'parcel_id':parsel['id'],'season_year':2026,
                              'crop':'Bugday','started_on':'2026-03-01',
                              'planted_area_decare':'100.0000'}).json()

    def hasat(**alanlar):
        govde = {'season_id':sezon['id'],'harvested_on':'2026-07-10',
                 'quantity':'1000','unit':'KG'}
        govde.update(alanlar)
        cevap = client.post('/api/field-harvests', headers=h, json=govde)
        assert cevap.status_code == 201, cevap.text
        return cevap.json()

    # --- 1) BILESIM TOPLAMSAL, SIRALI DEGIL --------------------------------
    h1 = hasat()
    f1 = fis(client, h, harvest_id=h1['id'], gross_quantity='1000',
             deductions=[{'label':'Rutubet','rate_percent':'2'},
                         {'label':'Yabanci madde','rate_percent':'3'}])
    assert f1.status_code == 201, f1.text
    f1 = f1.json()
    # TOPLAMSAL: 1000 - (20 + 30) = 950.0000
    # SIRALI olsaydi: 1000 * 0.98 * 0.97 = 950.6000
    assert f1['derived_net_quantity'] == '950.0000', f1
    assert f1['deduction_rate_total'] == '5.0000', f1
    # Kagidin neti girilmemis -> ayrisacak bir sey YOK.
    assert f1['net_mismatch'] is None, f1

    # --- 2) KESINTISIZ FIS: net == brut ------------------------------------
    h2 = hasat(harvested_on='2026-07-11')
    f2 = fis(client, h, harvest_id=h2['id'], gross_quantity='1000',
             ticket_net_quantity='1000').json()
    assert f2['derived_net_quantity'] == '1000.0000', f2
    assert f2['deduction_rate_total'] == '0.0000', f2
    assert f2['net_mismatch'] is False, f2

    # --- 3) KAGIDIN NETI TURETIME GIRMEZ -----------------------------------
    # Ayni kesintiler, ama kagit 900 diyor. Sunucu YINE 950 turetiyor ve
    # ayrismayi GOSTERIYOR — kagida uymuyor.
    h3 = hasat(harvested_on='2026-07-12')
    f3 = fis(client, h, harvest_id=h3['id'], gross_quantity='1000',
             ticket_net_quantity='900',
             deductions=[{'label':'Rutubet','rate_percent':'2'},
                         {'label':'Yabanci madde','rate_percent':'3'}]).json()
    assert f3['derived_net_quantity'] == '950.0000', (
        'KAGIDIN NETI TURETIME SIZDI', f3)
    assert f3['ticket_net_quantity'] == '900.0000', f3
    assert f3['net_mismatch'] is True, f3

    # --- 4) `sold_exceeds_net` UC DEGERLI ----------------------------------
    def ozet(hasat_id):
        cevap = client.get('/api/field-harvest-tickets', headers=h,
                           params={'harvest_id':hasat_id})
        assert cevap.status_code == 200, cevap.text
        return cevap.json()['summary']

    # satilan GIRILMEMIS -> None
    assert ozet(h1['id'])['sold_exceeds_net'] is None, ozet(h1['id'])
    # fis YOK -> None (satilan girilmis olsa bile: net OLCULMEMIS)
    h4 = hasat(harvested_on='2026-07-13', sold_quantity='900',
               revenue_amount='45000.00')
    o4 = ozet(h4['id'])
    assert o4['sold_exceeds_net'] is None, o4
    assert o4['ticket_count'] == 0, o4
    # FIS YOKKEN TOPLAMLAR DA None — SIFIR DEGIL.
    assert o4['gross_quantity_total'] is None, o4
    assert o4['derived_net_total'] is None, o4

    # asmiyor -> False
    fis(client, h, harvest_id=h4['id'], gross_quantity='1000',
        deductions=[{'label':'Fire','rate_percent':'5'}])
    o4 = ozet(h4['id'])
    assert o4['derived_net_total'] == '950.0000', o4
    assert o4['sold_exceeds_net'] is False, o4

    # asiyor -> True
    h5 = hasat(harvested_on='2026-07-14', sold_quantity='1000',
               revenue_amount='50000.00')
    fis(client, h, harvest_id=h5['id'], gross_quantity='1000',
        deductions=[{'label':'Fire','rate_percent':'10'}])
    o5 = ozet(h5['id'])
    assert o5['derived_net_total'] == '900.0000', o5
    assert o5['sold_exceeds_net'] is True, o5

    # --- 5) BIR HASAT, BIRDEN COK FIS: TOPLAMLAR TOPLANIYOR ---------------
    h6 = hasat(harvested_on='2026-07-15')
    fis(client, h, harvest_id=h6['id'], ticket_no='C-1', gross_quantity='600',
        deductions=[{'label':'Fire','rate_percent':'10'}])
    fis(client, h, harvest_id=h6['id'], ticket_no='C-2', gross_quantity='400')
    o6 = ozet(h6['id'])
    assert o6['ticket_count'] == 2, o6
    assert o6['gross_quantity_total'] == '1000.0000', o6
    assert o6['derived_net_total'] == '940.0000', o6   # 540 + 400

    # --- 6) DOGRULAMA KAPILARI --------------------------------------------
    asiri = fis(client, h, harvest_id=h1['id'], gross_quantity='100',
                deductions=[{'label':'a','rate_percent':'60'},
                            {'label':'b','rate_percent':'50'}])
    assert asiri.status_code == 422, asiri.text

    tekrar_etiket = fis(client, h, harvest_id=h1['id'], gross_quantity='100',
                        deductions=[{'label':'Rutubet','rate_percent':'1'},
                                    {'label':'rutubet','rate_percent':'2'}])
    assert tekrar_etiket.status_code == 422, tekrar_etiket.text

    sifir = fis(client, h, harvest_id=h1['id'], gross_quantity='0')
    assert sifir.status_code == 422, sifir.text

    negatif_net = fis(client, h, harvest_id=h1['id'], gross_quantity='10',
                      ticket_net_quantity='-1')
    assert negatif_net.status_code == 422, negatif_net.text

    # `derived_net_quantity` ISTEMCIDEN ALINMAZ: sema `extra=forbid`.
    turev = fis(client, h, harvest_id=h1['id'], gross_quantity='10',
                derived_net_quantity='9999')
    assert turev.status_code == 422, turev.text

    # --- 7) KAGIDIN KIMLIGI: AYNI HASADA AYNI NUMARA IKI KEZ GIREMEZ ------
    h7 = hasat(harvested_on='2026-07-16')
    ilk = fis(client, h, harvest_id=h7['id'], ticket_no='D-9', gross_quantity='10')
    assert ilk.status_code == 201, ilk.text
    ikinci = fis(client, h, harvest_id=h7['id'], ticket_no='D-9', gross_quantity='10')
    assert ikinci.status_code == 409, ikinci.text
    # BEDEL ADI KONMUS: numarasiz fis IKI KEZ girilebilir. Bu bir kusur degil,
    # kabul edilmis bir sinir (bkz. migration 0064 basligi) ve BURADA DONDU.
    a = fis(client, h, harvest_id=h7['id'], gross_quantity='10')
    b = fis(client, h, harvest_id=h7['id'], gross_quantity='10')
    assert (a.status_code, b.status_code) == (201, 201), (a.text, b.text)
    assert ozet(h7['id'])['ticket_count'] == 3, ozet(h7['id'])

    # --- 8) KIRACI: OLMAYAN/BASKA FIRMANIN HASADI 404 ---------------------
    yok = fis(client, h, harvest_id=987654, gross_quantity='10')
    assert yok.status_code == 404, yok.text
    liste_yok = client.get('/api/field-harvest-tickets', headers=h,
                           params={'harvest_id':987654})
    assert liste_yok.status_code == 404, liste_yok.text

    # --- 9) MEVCUT OKUMA UCLARI DEGISMEDI ---------------------------------
    # Fisin varligi hasat listesini, panoyu ve zaman cizelgesini
    # DEGISTIRMEMELI. Defter iddiasinin okuma tarafindaki esi.
    h8 = hasat(harvested_on='2026-07-17', sold_quantity='500',
               revenue_amount='25000.00')

    def yuzeyler():
        return (
            client.get('/api/field-harvests', headers=h,
                       params={'season_id':sezon['id']}).json(),
            client.get('/api/field-dashboard', headers=h).json(),
            client.get(f"/api/farm-parcels/{parsel['id']}/timeline", headers=h).json(),
        )

    once = yuzeyler()
    fis(client, h, harvest_id=h8['id'], ticket_no='E-1', gross_quantity='1200',
        ticket_net_quantity='1100',
        deductions=[{'label':'Rutubet','rate_percent':'4'}])
    sonra = yuzeyler()
    for ad, a, b in zip(('field-harvests', 'field-dashboard', 'timeline'),
                        once, sonra):
        assert a == b, ('FIS MEVCUT OKUMA UCUNU DEGISTIRDI: ' + ad, a, b)

    # Yeni alanlar YALNIZ fis ucunda gorunur.
    (hasat_satiri,) = [x for x in sonra[0]['items'] if x['id'] == h8['id']]
    for alan in ('gross_quantity', 'derived_net_quantity', 'net_mismatch',
                 'sold_exceeds_net', 'ticket_net_quantity'):
        assert alan not in hasat_satiri, (alan, hasat_satiri)

    print('KANTAR FISI SOZLESME TAMAM')
'''
