"""E1 uç sözleşmesi: ``POST /api/invoices/{id}/einvoice/sync``.

Akış ucu, ayrı bir SQLite veritabanına karşı ALT SÜREÇTE koşuyor:
``DATABASE_URL`` motor içe aktarılırken bağlanıyor, o yüzden izolasyon ancak
ayrı süreçle sağlanır (``test_v3_einvoice_seam.py`` ile aynı desen).

KİRACI MUTANTI HAKKINDA — ÖLÇÜLDÜ, VARSAYILMADI: "``sync`` yazması
``company_id`` olmadan" mutantı HTTP'den GÖRÜNMEZ. Sebep: ``_invoice()``
satırı zaten ``id + company_id`` ile okuyor ve başka firmanın kimliğiyle
gelen istek UPDATE'e HİÇ ULAŞMADAN 404 alıyor. Yani uçtan uca bir test o
mutantı YEŞİL bırakırdı ve "kiracı sızıntısını test ediyorum" demek YANLIŞ
olurdu. Mutant iki yerde ölüyor ve ikisi de bu dosyada:

* ``test_SYNC_YAZMASI_KIRACI_YUKLEMI_TASIYOR`` — UPDATE metnini DOĞRUDAN ölçer,
* deponun kiracı kapsam nöbetçisi (``tests/test_core_tenant_scoping_guard.py``)
  aynı ifadeyi statik olarak zaten tarıyor.

İki koruma AYNI şeyi ölçmüyor: ilki bu ucun sözleşmesi, ikincisi deponun
küresel kuralı.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parent
ROUTER = BACKEND / "app" / "routers" / "invoices.py"


# --- Statik: yazmanın kiracı yüklemi --------------------------------------
def test_SYNC_YAZMASI_KIRACI_YUKLEMI_TASIYOR() -> None:
    """``sync``in UPDATE'i ``company_id=:cid`` TAŞIR.

    MUTANT: yüklem düşürülür ⇒ bu test kırmızı. ``_invoice()`` kapsamı zaten
    denetliyor ama YAZMA KENDİ YÜKLEMİNİ TAŞIR: sızmış bir fatura kimliği
    başka firmanın satırını tazeleyemesin diye (0080'in ``_sahip_mi``
    kaydıyla aynı gerekçe).
    """
    kaynak = ROUTER.read_text(encoding="utf-8")
    govde = kaynak.split("def einvoice_sync", 1)
    assert len(govde) == 2, "einvoice_sync ucu bulunamadı"
    govde = govde[1]

    guncelleme = re.search(r"UPDATE invoices SET einvoice_status=.*?\"\"\"", govde, re.S)
    assert guncelleme, "sync UPDATE ifadesi bulunamadı"
    metin = guncelleme.group(0)
    assert "company_id=:cid" in metin, metin
    assert "WHERE id=:id" in metin, metin


def test_SYNC_POST_VE_GET_ENVANTERINE_GIRMIYOR() -> None:
    """Uç POST'tur ve bu KASITLIDIR.

    ``GET``in envanterdeki anlamı "read" (``test_route_get_permission_inventory``);
    orada YAZAN bir uç o sözleşmeyi bozardı. Bu çağrı hem yerel satırı yazıyor
    hem de sağlayıcıda oturum açıp kota tüketiyor.
    """
    kaynak = ROUTER.read_text(encoding="utf-8")
    assert '@router.post("/{invoice_id}/einvoice/sync")' in kaynak
    assert '@router.get("/{invoice_id}/einvoice/sync")' not in kaynak


# --- Uçtan uca ------------------------------------------------------------
_SENARYO = r"""
import json
from types import SimpleNamespace
from fastapi.testclient import TestClient

import app.routers.invoices as inv
from app.einvoice.provider import EInvoiceResult

WEB_KEY = "https://portaltest.izibiz.com.tr/x.xhtml?webValidationKey=web-anahtari&viewType=PDF"


class SahteSaglayici:
    # Aga cikmaz. Gonderimde WEB_KEY verir, sorguda durumu ilerletir.

    def check_taxpayer(self, vkn):
        return {"is_efatura_user": False}          # -> EARSIV

    def submit(self, payload):
        return EInvoiceResult(status="PENDING", channel="EARSIV", uuid="ETTN-1",
                              external_id="SNG2026210141633", web_key=WEB_KEY,
                              gib_status_code="105")

    def query_status(self, external_id, *, channel=None, uuid=None):
        return EInvoiceResult(status="ACCEPTED", external_id=external_id,
                              gib_status_code="130", web_key=WEB_KEY)


inv.get_einvoice_provider = lambda settings, company_id=None: SahteSaglayici()
inv.einvoice_configuration = lambda settings: SimpleNamespace(configured=True, reason=None)

from app.main import app

with TestClient(app) as c:
    login = c.post('/api/auth/login', json={'username':'admin','password':'admin123'}).json()
    cid = login['companies'][0]['id']; uid = login['user']['id']
    h = {'Authorization':'Bearer '+login['access_token'],'X-Company-ID':str(cid)}
    ch = c.post('/api/auth/change-password', headers=h,
                json={'current_password':'admin123','new_password':'E1Sertlestirme123!'}).json()
    h['Authorization'] = 'Bearer ' + ch['access_token']

    c.put('/api/company-settings', headers=h, json={
        'negative_stock_policy':'block','credit_limit_policy':'block','tax_number':'1111111111'})

    cust = c.post('/api/customers', headers=h, json={'name':'E1 Musteri','tax_number':'2222222222'}).json()
    mach = c.post('/api/machines', headers=h,
                  json={'customer_id':cust['id'],'brand':'B','model':'M','serial_number':'SN-E1'}).json()
    wo = c.post('/api/work-orders', headers=h,
                json={'machine_id':mach['id'],'customer_id':cust['id'],'technician_id':uid,
                      'actual_hours':'2','labor_rate':'50'}).json()
    for st in ('IN_PROGRESS','COMPLETED'):
        assert c.patch(f"/api/work-orders/{wo['id']}/status", headers=h, json={'status':st}).status_code == 200
    iid = c.post('/api/invoices/generate', headers=h, json={'work_order_id':wo['id']}).json()['id']

    # Gonderilmemis belgede sorgu KURULAMAZ: 409, sessiz 200 degil.
    erken = c.post(f'/api/invoices/{iid}/einvoice/sync', headers=h)
    assert erken.status_code == 409, erken.text

    sub = c.post(f'/api/invoices/{iid}/einvoice/submit', headers=h)
    assert sub.status_code == 200, sub.text
    durum = sub.json()
    assert durum['einvoice_status'] == 'PENDING', durum
    # WEB_KEY SAKLANDI. Mutant (`_submit_web_key` -> None) burada da olur.
    assert durum['einvoice_web_key'] == WEB_KEY, durum['einvoice_web_key']
    assert durum['einvoice_gib_status_code'] == '105', durum

    # GET yalniz yerel DB okur: sagalayici ACCEPTED dese de burada degismez.
    okuma = c.get(f'/api/invoices/{iid}/einvoice/status', headers=h).json()
    assert okuma['einvoice_status'] == 'PENDING', okuma

    # SYNC sagalayiciya sorar ve yereli ilerletir.
    snc = c.post(f'/api/invoices/{iid}/einvoice/sync', headers=h)
    assert snc.status_code == 200, snc.text
    yeni = snc.json()
    assert yeni['einvoice_status'] == 'ACCEPTED', yeni
    assert yeni['einvoice_gib_status_code'] == '130', yeni

    # ACCEPTED TERMINAL: ikinci bir sync geri yuruyemez.
    tekrar = c.post(f'/api/invoices/{iid}/einvoice/sync', headers=h).json()
    assert tekrar['einvoice_status'] == 'ACCEPTED', tekrar

    # Baska firma bu faturayi HIC goremez: 404, yazmaya hic ulasilmaz.
    other = c.post('/api/companies', headers=h, json={'name':'Firma B'}).json()['id']
    hb = dict(h); hb['X-Company-ID'] = str(other)
    assert c.post(f'/api/invoices/{iid}/einvoice/sync', headers=hb).status_code == 404

    print('E1_UC_OK status=' + yeni['einvoice_status'] + ' gib=' + yeni['einvoice_gib_status_code'])
"""


def test_SYNC_UCU_SAGLAYICIYA_SORAR_VE_YERELI_ILERLETIR(tmp_path: Path) -> None:
    veritabani = tmp_path / "e1.db"
    ortam = os.environ.copy()
    ortam["DATABASE_URL"] = f"sqlite:///{veritabani.as_posix()}"
    ortam["PYTHONPATH"] = str(BACKEND)
    sonuc = subprocess.run(
        [sys.executable, "-c", _SENARYO],
        cwd=str(BACKEND),
        env=ortam,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert sonuc.returncode == 0, sonuc.stdout + sonuc.stderr
    assert "E1_UC_OK status=ACCEPTED gib=130" in sonuc.stdout, sonuc.stdout
