"""E2 uç sözleşmesi: ``.../einvoice/download`` ve e-belge duyarlı ``.../cancel``.

Akış ucu, ayrı bir SQLite veritabanına karşı ALT SÜREÇTE koşuyor: ``DATABASE_URL``
motor içe aktarılırken bağlanıyor, o yüzden izolasyon ancak ayrı süreçle
sağlanır (``test_e1_efatura_sertlestirme_uc.py`` ile aynı desen).

MUTASYONLAR AYNI SENARYOYU KULLANIYOR ve bu KASITLI. Bir mutasyon testinin tek
işi, yanındaki testin YÜK TAŞIDIĞINI kanıtlamaktır; ayrı bir senaryo yazmak o
kanıtı zayıflatırdı, çünkü o zaman "mutant başka bir testte ölüyor" demiş
olurduk. Burada mutant, TAM DA sağlam koşumun geçtiği senaryoda öldürülüyor:
``_senaryo_kos`` önce mutasyonsuz koşup YEŞİL olduğunu, sonra mutantla koşup
KIRMIZI olduğunu ölçüyor.

ÜÇ MUTANT, ÜÇÜ DE ADIYLA:

* ``test_MUTANT_YEREL_IPTAL_ENTEGRATORU_GECERSE`` — e-belge kapısı devre dışı;
  entegratör iptali REDDETMİŞKEN fatura yerelde iptal olur.
* ``test_MUTANT_INDIRME_KIRACI_YUKLEMI_OLMADAN`` — ``_invoice`` firma yüklemini
  düşürür; başka firmanın e-belgesi indirilebilir hâle gelir.
* ``test_MUTANT_CANCELLED_FAILEDDEN_KABUL_EDILIRSE`` — ``CANCELLABLE`` kümesine
  ``FAILED`` girer; hiç inmemiş bir gönderim "iptal edildi" diye yazılır.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


BACKEND = Path(__file__).resolve().parent

# --------------------------------------------------------------------------
# Senaryo. `MUTANT` ortam değişkeni ile mutasyonlar aynı gövdeye enjekte edilir.
# --------------------------------------------------------------------------
_SENARYO = r'''
import json, os
from types import SimpleNamespace
from fastapi.testclient import TestClient

import app.routers.invoices as inv
from app.einvoice.provider import EInvoiceResult

MUTANT = os.environ.get("MUTANT", "")
WEB_KEY = "https://portaltest.izibiz.com.tr/x.xhtml?webValidationKey=web-anahtari&viewType=PDF"
PDF = b"%PDF-1.4 sahte-saglayici-pdf"

# Uc iptal senaryosu tek bayrakla secilir: saglayici iptali kabul mu ediyor?
IPTAL_KABUL = {"deger": False}
KANAL = {"deger": "EARSIV"}
GONDERIM = {"deger": "PENDING"}


class SahteSaglayici:
    # Aga cikmaz.

    def check_taxpayer(self, vkn):
        return {"is_efatura_user": KANAL["deger"] == "EFATURA"}

    def submit(self, payload):
        if GONDERIM["deger"] == "FAILED":
            # Gonderim HIC INMEDI: ETTN de belge kimligi de YOK.
            return EInvoiceResult(status="FAILED", channel=KANAL["deger"],
                                  error="Gonderim inmedi")
        return EInvoiceResult(status="PENDING", channel=KANAL["deger"], uuid="ETTN-1",
                              external_id="SNG2026210141633", web_key=WEB_KEY,
                              gib_status_code="105")

    def query_status(self, external_id, *, channel=None, uuid=None):
        return EInvoiceResult(status="ACCEPTED", external_id=external_id,
                              gib_status_code="130", web_key=WEB_KEY)

    def fetch_pdf(self, external_id, *, channel=None, web_key=None):
        # Anahtar secimi KANALA gore ve bu OLCULUYOR: e-Arsiv WEB_KEY ister,
        # e-Fatura saglayici belge kimligi.
        if channel == "EARSIV":
            assert web_key == WEB_KEY, "e-Arsiv PDF'i WEB_KEY olmadan istendi"
        else:
            assert external_id == "SNG2026210141633", external_id
        return PDF

    def cancel(self, external_id, *, channel=None, uuid=None):
        if IPTAL_KABUL["deger"]:
            return EInvoiceResult(status="CANCELLED", channel=channel, uuid=uuid,
                                  external_id=external_id)
        return EInvoiceResult(status="FAILED", channel=channel, uuid=uuid,
                              external_id=external_id,
                              error="Entegrator iptali reddetti (10013)")


inv.get_einvoice_provider = lambda settings, company_id=None: SahteSaglayici()
inv.einvoice_configuration = lambda settings: SimpleNamespace(configured=True, reason=None)

# ---- MUTANTLAR -----------------------------------------------------------
if MUTANT == "YEREL_IPTAL_ONCE":
    # Kapi tamamen devre disi: yerel iptal entegratoru "gecer".
    inv._einvoice_cancel_gate = lambda db, cid, invoice_id, invoice, now: False
if MUTANT == "INDIRME_KIRACISIZ":
    # `_invoice` firma yuklemini DUSURUR.
    from sqlalchemy import text as _text
    def _kiracisiz(db, cid, invoice_id):
        row = db.execute(_text("SELECT * FROM invoices WHERE id=:id"), {"id": invoice_id}).mappings().first()
        if not row:
            raise inv.HTTPException(404, "Fatura bulunamadi")
        return dict(row)
    inv._invoice = _kiracisiz
if MUTANT == "CANCELLED_FAILEDDEN":
    # Kume IKI YERDE okunuyor ve mutant IKISINI DE degistirmek zorunda; bu
    # OLCULDU, varsayilmadi: yalniz `status` modulunu degistiren ilk deneme
    # MUTANTI HAYATTA BIRAKTI, cunku router `from ..einvoice import CANCELLABLE`
    # ile KENDI adini bagliyor ve modul ozniteligini yeniden baglamak o ada
    # ULASMIYOR. Tek bir yeri degistiren bir mutant, kapinin kapali oldugunu
    # degil yalnizca yanlis yere bakildigini kanitlardi.
    import app.einvoice.status as st
    BOZUK = frozenset({"PENDING", "SENT", "ACCEPTED", "FAILED"})
    st.CANCELLABLE = BOZUK          # `advance_status`in okudugu
    inv.CANCELLABLE = BOZUK         # `_einvoice_cancel_gate`in okudugu

from app.main import app


def kur(c, h, uid, ad, seri):
    cust = c.post('/api/customers', headers=h, json={'name':ad,'tax_number':'2222222222'}).json()
    mach = c.post('/api/machines', headers=h,
                  json={'customer_id':cust['id'],'brand':'B','model':'M','serial_number':seri}).json()
    wo = c.post('/api/work-orders', headers=h,
                json={'machine_id':mach['id'],'customer_id':cust['id'],'technician_id':uid,
                      'actual_hours':'2','labor_rate':'50'}).json()
    for st_ in ('IN_PROGRESS','COMPLETED'):
        assert c.patch(f"/api/work-orders/{wo['id']}/status", headers=h, json={'status':st_}).status_code == 200
    return c.post('/api/invoices/generate', headers=h, json={'work_order_id':wo['id']}).json()['id']


with TestClient(app) as c:
    login = c.post('/api/auth/login', json={'username':'admin','password':'admin123'}).json()
    cid = login['companies'][0]['id']; uid = login['user']['id']
    h = {'Authorization':'Bearer '+login['access_token'],'X-Company-ID':str(cid)}
    ch = c.post('/api/auth/change-password', headers=h,
                json={'current_password':'admin123','new_password':'E2YasamDongusu123!'}).json()
    h['Authorization'] = 'Bearer ' + ch['access_token']

    c.put('/api/company-settings', headers=h, json={
        'negative_stock_policy':'block','credit_limit_policy':'block','tax_number':'1111111111'})

    # ================= 1) INDIRME =================
    iid = kur(c, h, uid, 'E2 Musteri', 'SN-E2-A')

    # GONDERILMEMIS belgenin sureti YOK -> 404 (409 degil: kaynak hic olmadi).
    for bicim in ('pdf','xml'):
        erken = c.get(f'/api/invoices/{iid}/einvoice/download?format={bicim}', headers=h)
        assert erken.status_code == 404, (bicim, erken.status_code, erken.text)

    assert c.post(f'/api/invoices/{iid}/einvoice/submit', headers=h).status_code == 200

    xml = c.get(f'/api/invoices/{iid}/einvoice/download?format=xml', headers=h)
    assert xml.status_code == 200, xml.text
    assert xml.headers['content-type'].startswith('application/xml'), xml.headers
    assert b'<Invoice' in xml.content or b':Invoice' in xml.content, xml.content[:200]
    assert len(xml.content) > 200, len(xml.content)

    pdf = c.get(f'/api/invoices/{iid}/einvoice/download?format=pdf', headers=h)
    assert pdf.status_code == 200, pdf.text
    assert pdf.content == PDF, pdf.content[:40]
    assert pdf.headers['content-type'].startswith('application/pdf'), pdf.headers

    # Bicim KAPALI kume.
    assert c.get(f'/api/invoices/{iid}/einvoice/download?format=docx', headers=h).status_code == 400

    # BASKA FIRMA bu belgeyi HIC goremez.
    other = c.post('/api/companies', headers=h, json={'name':'Firma B'}).json()['id']
    hb = dict(h); hb['X-Company-ID'] = str(other)
    yabanci = c.get(f'/api/invoices/{iid}/einvoice/download?format=pdf', headers=hb)
    assert yabanci.status_code == 404, (yabanci.status_code, yabanci.text)

    # ================= 2) e-ARSIV IPTALI: ONCE RED =================
    IPTAL_KABUL["deger"] = False
    red = c.post(f'/api/invoices/{iid}/cancel', headers=h, json={'reason':'Musteri vazgecti'})
    assert red.status_code == 409, (red.status_code, red.text)
    assert 'EARSIV_IPTAL_BASARISIZ' in red.text, red.text
    # YEREL IPTAL OLMADI: fatura hala ISSUED ve e-belge hala PENDING.
    sonra = c.get(f'/api/invoices/{iid}', headers=h).json()
    assert sonra['status'] == 'ISSUED', sonra['status']
    assert c.get(f'/api/invoices/{iid}/einvoice/status', headers=h).json()['einvoice_status'] == 'PENDING'

    # ================= 3) e-ARSIV IPTALI: SONRA KABUL =================
    IPTAL_KABUL["deger"] = True
    ok = c.post(f'/api/invoices/{iid}/cancel', headers=h, json={'reason':'Musteri vazgecti'})
    assert ok.status_code == 200, (ok.status_code, ok.text)
    assert ok.json()['status'] == 'CANCELLED', ok.json()['status']
    ebelge = c.get(f'/api/invoices/{iid}/einvoice/status', headers=h).json()
    assert ebelge['einvoice_status'] == 'CANCELLED', ebelge

    # ================= 4) e-FATURA (B2B) REDDI =================
    KANAL["deger"] = "EFATURA"
    iid2 = kur(c, h, uid, 'E2 B2B Musteri', 'SN-E2-B')
    assert c.post(f'/api/invoices/{iid2}/einvoice/submit', headers=h).status_code == 200
    b2b = c.post(f'/api/invoices/{iid2}/cancel', headers=h, json={'reason':'Yanlis kesildi'})
    assert b2b.status_code == 409, (b2b.status_code, b2b.text)
    # Govde, ucun KENDI sabitinin TA KENDISI. Metni burada yeniden yazmak
    # (ozellikle Turkce harflerle) kodlama kazasi olurdu; sabitle karsilastirmak
    # hem kesin hem kodlamadan bagimsiz. Metnin SEKIZ GUN kuralini ve TTK
    # dayanagini gercekten tasidigi, statik dosyada AYRICA olculuyor.
    from app.routers.invoices import EFATURA_CANCEL_REFUSED
    assert b2b.json()['detail'] == EFATURA_CANCEL_REFUSED, b2b.text
    assert '18/3' in b2b.text, b2b.text
    assert 'ApplicationResponse' in b2b.text, b2b.text
    # YEREL IPTAL OLMADI.
    assert c.get(f'/api/invoices/{iid2}', headers=h).json()['status'] == 'ISSUED'

    # e-Fatura PDF'i SAGLAYICI BELGE KIMLIGI ile isteniyor (WEB_KEY ile degil):
    # `fetch_pdf` iddiasi bunu kaniti.
    assert c.get(f'/api/invoices/{iid2}/einvoice/download?format=pdf', headers=h).status_code == 200

    # ================= 5) FAILED BELGE: KAPI HIC CALISMAZ =================
    # Gonderim hic inmediyse entegratorde iptal edilecek bir sey YOKTUR: kapi
    # calismaz, yerel iptal NORMAL akar ve `einvoice_status` FAILED KALIR.
    # `IPTAL_KABUL` ACIK birakiliyor ve bu KASITLI: kapi yanlislikla calissaydi
    # saglayici "iptal edildi" derdi ve asagidaki iddia bunu yakalar.
    IPTAL_KABUL["deger"] = True
    KANAL["deger"] = "EARSIV"
    GONDERIM["deger"] = "FAILED"
    iid3 = kur(c, h, uid, 'E2 Inmemis Musteri', 'SN-E2-C')
    assert c.post(f'/api/invoices/{iid3}/einvoice/submit', headers=h).status_code == 200
    inmemis = c.get(f'/api/invoices/{iid3}/einvoice/status', headers=h).json()
    assert inmemis['einvoice_status'] == 'FAILED', inmemis

    iptal3 = c.post(f'/api/invoices/{iid3}/cancel', headers=h, json={'reason':'Hic gitmedi'})
    assert iptal3.status_code == 200, (iptal3.status_code, iptal3.text)
    assert iptal3.json()['status'] == 'CANCELLED', iptal3.json()['status']
    # ASIL IDDIA: e-belge durumu FAILED KALDI. `CANCELLABLE` FAILED'i kabul
    # etseydi burada 'CANCELLED' gorurduk — hic inmemis bir gonderim "iptal
    # edildi" diye yazilmis olurdu ve mesru yeniden gonderim kapanirdi.
    kalan = c.get(f'/api/invoices/{iid3}/einvoice/status', headers=h).json()
    assert kalan['einvoice_status'] == 'FAILED', kalan

    # Gonderilmemis belgenin sureti de YOK: FAILED bir satirda indirme 404.
    assert c.get(f'/api/invoices/{iid3}/einvoice/download?format=pdf', headers=h).status_code == 404

    print('E2_UC_OK')
'''


def _senaryo_kos(tmp_path: Path, mutant: str = "") -> subprocess.CompletedProcess[str]:
    veritabani = tmp_path / f"e2{mutant or 'saglam'}.db"
    ortam = os.environ.copy()
    ortam["DATABASE_URL"] = f"sqlite:///{veritabani.as_posix()}"
    ortam["PYTHONPATH"] = str(BACKEND)
    if mutant:
        ortam["MUTANT"] = mutant
    else:
        ortam.pop("MUTANT", None)
    return subprocess.run(
        [sys.executable, "-c", _SENARYO],
        cwd=str(BACKEND),
        env=ortam,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )


# ==========================================================================
# SAĞLAM KOŞUM
# ==========================================================================
def test_UC_SOZLESMESI_UCTAN_UCA(tmp_path: Path) -> None:
    """İndirme (pdf/xml/404) + e-Arşiv iptali (red/kabul) + e-Fatura reddi."""
    sonuc = _senaryo_kos(tmp_path)
    assert sonuc.returncode == 0, sonuc.stdout + sonuc.stderr
    assert "E2_UC_OK" in sonuc.stdout, sonuc.stdout


# ==========================================================================
# MUTANTLAR — her biri ADIYLA, ve hepsi AYNI senaryoda ölüyor
# ==========================================================================
@pytest.mark.parametrize(
    "mutant, gerekce",
    [
        (
            "YEREL_IPTAL_ONCE",
            "e-belge kapısı kalkarsa, entegratör iptali REDDETMİŞKEN fatura "
            "yerelde iptal olur: ERP 'iptal' derken zarf hâlâ yürürlüktedir.",
        ),
        (
            "INDIRME_KIRACISIZ",
            "`_invoice` firma yüklemini kaybederse, sızmış bir fatura kimliği "
            "BAŞKA firmanın resmî mali belgesini indirtir.",
        ),
        (
            "CANCELLED_FAILEDDEN",
            "`CANCELLABLE` FAILED'i kabul ederse, hiç inmemiş bir gönderim "
            "'iptal edildi' diye yazılır ve meşru yeniden gönderim kapanır.",
        ),
    ],
)
def test_MUTANTLAR_SENARYOYU_KIRMIZI_YAKAR(tmp_path: Path, mutant: str, gerekce: str) -> None:
    """Her mutant, sağlam koşumun geçtiği SENARYONUN TA KENDİSİNDE ölür."""
    bozuk = _senaryo_kos(tmp_path, mutant)
    assert bozuk.returncode != 0, (
        f"MUTANT {mutant} HAYATTA KALDI — test yük taşımıyor. {gerekce}\n"
        + bozuk.stdout
        + bozuk.stderr
    )
    assert "E2_UC_OK" not in bozuk.stdout, bozuk.stdout
