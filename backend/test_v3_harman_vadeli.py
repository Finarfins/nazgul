"""V3 Harman Vadeli (harvest-term) finance — Increment 1 tests (SQLite).

Covers the payment-term extension on sales and the receivables-by-due-date view:
- PESIN sales are unchanged and never appear as harman receivables.
- HARMAN_VADELI sales persist a due_date and require one (422 otherwise).
- Outstanding uses the shared movement model (linked movements plus customer
  FIFO credits), and status moves ACIK -> VADESI_GECTI -> ODENDI.
- The /receivables endpoint lists rows sorted by due date with correct
  total-outstanding / total-overdue summaries, honours status + due_before
  filters, and is company-scoped (multi-tenant isolation).

The PostgreSQL 16 twin lives in test_harman_vadeli_postgresql.py.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.auth import required_permission


BACKEND = Path(__file__).resolve().parent


def test_harman_vadeli_rbac_map() -> None:
    assert required_permission("GET", "/api/receivables") == "payments"
    assert required_permission("POST", "/api/orders") == "sales"
    assert required_permission("PUT", "/api/orders/1") == "sales"


def test_harman_vadeli_flow(tmp_path: Path) -> None:
    database = tmp_path / "harman-vadeli.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE], env=env, capture_output=True, text=True, cwd=str(BACKEND)
    )
    assert "HARMAN_VADELI_FLOW_OK" in completed.stdout, completed.stdout + completed.stderr


# Shared, DB-agnostic scenario. test_harman_vadeli_postgresql.py runs the exact
# same script against PostgreSQL 16 so SQLite/PG parity is proven, not assumed.
_SMOKE = r'''
from datetime import timedelta
from decimal import Decimal
from fastapi.testclient import TestClient
from app.business_time import business_today
from app.main import app


def dec(value):
    return Decimal(str(value))


# --- VADESİ AÇIK BELGENİN TARİHİ SABİT YAZILAMAZ ---------------------------
#
# `_receivable_status` (routers/transactions.py) kararı `due_date < today` ile
# verir ve `today` `business_today()`ten, yani İSTANBUL takvim gününden gelir.
# Bu yüzden "vadesi gelmemiş" anlamı bir SABİT tarihle ifade edilemez: sabit,
# duvar saatinin ona yetiştiği gün sessizce anlamını değiştirir.
#
# ÖLÇÜLDÜ (2026-09-01, İstanbul 00:39, bu depo): burada eskiden
# `2026-08-01`/`2026-09-01` sabitleri vardı. İstanbul 2026-09-02'ye döndüğü
# anda smoke KIRMIZIYA döndü — `status=VADESI_GECTI` süzgeci iki kimlik
# döndürdü (bir tane bekleniyordu) ve ödeme sonrası `total_overdue` 0 yerine
# 800 çıktı. Kusur `origin/develop` (aa3d577 ve cfe7362) ağacında da AYNEN
# üretildi; yani dalın değil deponun kusuruydu ve o gece açılan HER PR'ı
# kırmızıya çevirecekti.
#
# ÇARE SUNUCUNUN KENDİ SAATİ. Vade aynı süreçteki `business_today()`ten
# türetiliyor — testin okuduğu gün ile sunucunun karşılaştırdığı gün AYNI
# fonksiyondan gelir, dolayısıyla ikisi ayrışamaz.
#
# KIRAN SABİT YALNIZ `second`DI; ÇİFT DEĞİL. Bu satırların ilk hâli "ikisi de
# AÇIK olmalı, yalnız birini ileri almak yetmez" diyordu. YANLIŞTI ve
# ÖLÇÜLDÜ (2026-09-02, taban 7498ab3, iki motor):
#
#     `first='2026-08-01'` SABİT + `second` göreli .. SQLite YEŞİL, PG YEŞİL
#     çiftin İKİSİ de eski sabitlerinde ............ İKİSİ DE `only_overdue`
#                                                    iddiasında KIRMIZI
#
# GEREKÇE KODDA: `_receivable_status` ÖNCE `outstanding <= 0` bakıp `ODENDI`
# döner; vade karşılaştırmasına ancak AÇIK kalan belge ulaşır. FIFO 1200'lük
# tahsilatı EN ESKİ vadeye verdiği için `first`, süzgeç okunmadan ÖNCE
# (`first ... == 'ODENDI'` iddiası) kapanır ve ÖDENMİŞ belge hiçbir zaman
# `VADESI_GECTI` sayılmaz. Yani `first`in vadesi geçmiş olması ne
# `?status=VADESI_GECTI` süzgecini ne de ödeme sonrası `total_overdue`yu
# kırabilirdi. Değişikliği ZORUNLU kılan tek şey `second`ın `VADESI_GECTI`ye
# düşmesiydi.
#
# `first`İN DE GÖRELİ OLMASI BİR ZORUNLULUK DEĞİL, TERCİHTİR — ve burada
# yazılı olmasının sebebi, ölçülmemiş bir gerekçenin bir daha yazılmaması:
# çift tek kaynaktan türeyince SIRA (365 < 730) aritmetikle garanti olur,
# bir sabitin hareketli bir tarihin altında kalmasına GÜVENMEZ.
#
# ÜÇ İDDİA AYNI ANDA TUTMALI:
#
#   (1) `second` AÇIK KALMALI. `only_overdue` iddiası `?status=VADESI_GECTI`
#       süzgecinin TEK kimlik (2019 belgesi) döndürmesini istiyor; ödeme
#       sonrası `total_overdue` da 0 bekleniyor. Kısıt YALNIZ `second`
#       üzerindedir; `first` için ölçülen karşılığı YOKTUR.
#   (2) SIRA. FIFO en eski vadeye dağıtır; 1200'lük tahsilat sonrası `first`
#       ÖDENDI (outstanding 0), `second` 800 açık kalır. `first < second`
#       KESİN olmalı. Mutlak değerler değil, aralarındaki SIRA taşıyıcıdır.
#   (3) ÇAPALAR. İkisi de `2099-12-31` belgesinin altında kalmalı; azalan
#       sıralama iddiası (`desc_ids[0] == open_id`) en büyüğün o olmasına
#       dayanıyor. Alttaki `2019-06-30` da en küçük kalmalı
#       (`desc_ids[-1] == overdue_id`) — bu alt çapayı ayrıca API'nin kendisi
#       koruyor: vade belge tarihinden ÖNCE olamaz (422) ve belge tarihi
#       `2026-01-01`.
#
# BİLİNEN SON KULLANMA TARİHİ: `second = business_today() + 730 gün` 2099-12-31
# çapasına 2097-12-31'de YETİŞİR ve `desc_ids[0] == open_id` iddiası o gün
# düşer. ÖLÇÜLDÜ (donmuş saat): 2097-12-30 YEŞİL, 2097-12-31 KIRMIZI. Yani bu
# düzeltme 2097 sonuna kadar geçerlidir; o tarihte 2099 çapası da ileri
# alınmalıdır. Bir düzeltmenin bilinen ömrü, okuyanın bulacağı yerde yazmalı.
#
# 365 GÜN, 30 DEĞİL: koşum İstanbul gece yarısını geçerse test ile sunucu bir
# gün ayrışabilir; bu pay onu yutar.
#
# GEÇMİŞTE KALAN SABİT (2019-06-30) BİLEREK DEĞİŞTİRİLMEDİ: onun anlamı
# "vadesi geçmiş"tir ve zaman ilerledikçe daha da doğru olur — atıl sabittir,
# bomba değil.
FIFO_ERKEN_VADE = (business_today() + timedelta(days=365)).isoformat()
FIFO_GEC_VADE = (business_today() + timedelta(days=730)).isoformat()


with TestClient(app) as client:
    body = client.post('/api/auth/login', json={'username':'admin','password':'admin123'}).json()
    headers = {'Authorization':'Bearer '+body['access_token'],'X-Company-ID':str(body['companies'][0]['id'])}
    changed = client.post('/api/auth/change-password', headers=headers, json={
        'current_password':'admin123','new_password':'HarmanVadeli123!'})
    assert changed.status_code == 200, changed.text
    headers['Authorization'] = 'Bearer ' + changed.json()['access_token']

    # Work inside a freshly created company so the run is isolated even on the
    # shared PostgreSQL 16 database used by the CI parity job.
    company_a = client.post('/api/companies', headers=headers, json={'name':'Harman Firma A'}).json()['id']
    headers['X-Company-ID'] = str(company_a)

    customer = client.post('/api/customers', headers=headers, json={'name':'Ciftci Ahmet'}).json()['id']
    product = client.post('/api/products', headers=headers,
                          json={'name':'Gubre','sale_price':'100','stock':'1000'}).json()['id']

    def sale(term, due=None, paid='0', tx='2026-01-05', qty='10'):
        payload = {'entity_id':customer,'transaction_date':tx,'status':'completed',
                   'payment_method':'credit','payment_term':term,'paid_amount':paid,
                   'items':[{'product_id':product,'quantity':qty,'unit_price':'100','vat_rate':20}]}
        if due is not None:
            payload['due_date'] = due
        return client.post('/api/orders', headers=headers, json=payload)

    # --- PESIN is unchanged and non-breaking -------------------------------
    # A sale with no payment_term at all defaults to PESIN (legacy behaviour).
    # Unit prices are VAT-inclusive, so 2 x 100 fully settles at 200.
    legacy = client.post('/api/orders', headers=headers, json={
        'entity_id':customer,'transaction_date':'2026-07-01','status':'completed',
        'payment_method':'cash','paid_amount':'200',
        'items':[{'product_id':product,'quantity':'2','unit_price':'100','vat_rate':20}]})
    assert legacy.status_code == 201, legacy.text
    legacy_doc = client.get(f"/api/orders/{legacy.json()['id']}", headers=headers).json()['document']
    assert legacy_doc['payment_term'] == 'PESIN'
    # An explicit PESIN sale is also fine and carries no receivable obligation.
    assert sale('PESIN', tx='2026-07-01', paid='0', qty='1').status_code == 201

    # --- HARMAN_VADELI requires a due date ---------------------------------
    missing_due = sale('HARMAN_VADELI')
    assert missing_due.status_code == 422, missing_due.text

    # --- Two harman-vadeli receivables: one overdue, one open --------------
    overdue = sale('HARMAN_VADELI', due='2019-06-30', tx='2019-01-05')   # past -> VADESI_GECTI
    assert overdue.status_code == 201, overdue.text
    overdue_id = overdue.json()['id']
    assert dec(overdue.json()['final_total']) == Decimal('1000')
    assert dec(overdue.json()['remaining_amount']) == Decimal('1000')

    open_future = sale('HARMAN_VADELI', due='2099-12-31', tx='2026-01-05')  # future -> ACIK
    assert open_future.status_code == 201, open_future.text
    open_id = open_future.json()['id']

    # The harman-vadeli sale persisted its term + due date.
    overdue_doc = client.get(f'/api/orders/{overdue_id}', headers=headers).json()['document']
    assert overdue_doc['payment_term'] == 'HARMAN_VADELI'
    assert overdue_doc['due_date'] == '2019-06-30'

    # --- Receivables view: only harman sales, sorted by due date -----------
    listing = client.get('/api/receivables', headers=headers)
    assert listing.status_code == 200, listing.text
    data = listing.json()
    rows = data['receivables']
    # PESIN sales are excluded; exactly the two harman-vadeli sales are listed.
    assert [r['id'] for r in rows] == [overdue_id, open_id]  # due_asc: 2019 before 2099
    by_id = {r['id']: r for r in rows}
    assert by_id[overdue_id]['status'] == 'VADESI_GECTI'
    assert by_id[open_id]['status'] == 'ACIK'
    assert dec(by_id[overdue_id]['outstanding']) == Decimal('1000')
    assert dec(by_id[open_id]['total']) == Decimal('1000')
    assert by_id[overdue_id]['customer_name'] == 'Ciftci Ahmet'

    # Summary: both open (2000 outstanding), one of them overdue (1000).
    assert dec(data['summary']['total_outstanding']) == Decimal('2000')
    assert dec(data['summary']['total_overdue']) == Decimal('1000')

    # A customer-level collection has no document reference. The shared
    # receivables engine allocates it to the oldest due harman document first.
    fifo_customer = client.post(
        '/api/customers', headers=headers, json={'name':'FIFO Çiftçisi'}
    ).json()['id']
    # `second` AÇIK kalmalı (kıran sabit oydu), `first` KESİN daha erken —
    # gerekçe yukarıda (`FIFO_ERKEN_VADE` / `FIFO_GEC_VADE` başlığı).
    first = sale('HARMAN_VADELI', due=FIFO_ERKEN_VADE, tx='2026-01-01')
    second = sale('HARMAN_VADELI', due=FIFO_GEC_VADE, tx='2026-01-01')
    # Reassign the helper-created documents to the dedicated FIFO customer.
    from app.db import SessionLocal
    from sqlalchemy import text
    with SessionLocal() as db:
        db.execute(
            text("UPDATE orders SET customer_id=:customer_id WHERE id IN (:first,:second) AND company_id=:cid"),
            {'customer_id':fifo_customer,'first':first.json()['id'],'second':second.json()['id'],'cid':company_a},
        )
        db.commit()
    collection = client.post('/api/payments', headers=headers, json={
        'entity_type':'customer','entity_id':fifo_customer,'amount':'1200',
        'payment_date':'2026-02-01','payment_method':'cash',
    })
    assert collection.status_code == 201, collection.text
    fifo_rows = {
        row['id']:row for row in client.get('/api/receivables', headers=headers).json()['receivables']
    }
    assert fifo_rows[first.json()['id']]['status'] == 'ODENDI'
    assert dec(fifo_rows[first.json()['id']]['outstanding']) == Decimal('0')
    assert dec(fifo_rows[second.json()['id']]['outstanding']) == Decimal('800')
    assert dec(fifo_rows[second.json()['id']]['paid']) == Decimal('200')

    # A sale return may only point to an order. A wrong source type must fail
    # before it can be misclassified as an unlinked FIFO customer credit.
    before_invalid_return = client.get('/api/receivables', headers=headers).json()
    invalid_return = client.post('/api/workflow/sale_return', headers=headers, json={
        'entity_id':fifo_customer,
        'document_date':'2026-02-02',
        'status':'completed',
        'source_type':'purchase',
        'source_id':first.json()['id'],
        'items':[{
            'product_id':product,
            'quantity':'1',
            'unit_price':'100',
            'vat_rate':20,
        }],
    })
    assert invalid_return.status_code == 422, invalid_return.text
    after_invalid_return = client.get('/api/receivables', headers=headers).json()
    assert after_invalid_return == before_invalid_return

    # --- Filters -----------------------------------------------------------
    only_overdue = client.get('/api/receivables?status=VADESI_GECTI', headers=headers).json()
    assert [r['id'] for r in only_overdue['receivables']] == [overdue_id]
    # due_before is inclusive; 2020-01-01 keeps only the overdue receivable.
    before = client.get('/api/receivables?due_before=2020-01-01', headers=headers).json()
    assert [r['id'] for r in before['receivables']] == [overdue_id]
    # desc sort flips the order.
    desc = client.get('/api/receivables?sort=due_desc', headers=headers).json()
    desc_ids = [r['id'] for r in desc['receivables']]
    assert desc_ids[0] == open_id
    assert desc_ids[-1] == overdue_id
    assert client.get('/api/receivables?status=BOGUS', headers=headers).status_code == 400

    # --- Status transition to ODENDI after full payment --------------------
    paid = client.put(f'/api/orders/{overdue_id}', headers=headers, json={
        'entity_id':customer,'transaction_date':'2019-01-05','due_date':'2019-06-30',
        'payment_term':'HARMAN_VADELI','status':'completed','payment_method':'cash',
        'paid_amount':'1000',
        'items':[{'product_id':product,'quantity':'10','unit_price':'100','vat_rate':20}]})
    assert paid.status_code == 200, paid.text
    after = client.get('/api/receivables', headers=headers).json()
    after_by_id = {r['id']: r for r in after['receivables']}
    assert after_by_id[overdue_id]['status'] == 'ODENDI'
    assert dec(after_by_id[overdue_id]['outstanding']) == Decimal('0')
    # The original future sale plus FIFO customer's second document remain open.
    assert dec(after['summary']['total_outstanding']) == Decimal('1800')
    assert dec(after['summary']['total_overdue']) == Decimal('0')
    paid_only = client.get('/api/receivables?status=ODENDI', headers=headers).json()
    assert [r['id'] for r in paid_only['receivables']] == [
        overdue_id,
        first.json()['id'],
    ]

    # --- Multi-tenant isolation --------------------------------------------
    company_b = client.post('/api/companies', headers=headers, json={'name':'Firma B'}).json()['id']
    headers_b = {**headers, 'X-Company-ID':str(company_b)}
    isolated = client.get('/api/receivables', headers=headers_b)
    assert isolated.status_code == 200, isolated.text
    assert isolated.json()['receivables'] == []
    assert dec(isolated.json()['summary']['total_outstanding']) == Decimal('0')

    print('HARMAN_VADELI_FLOW_OK')
'''
