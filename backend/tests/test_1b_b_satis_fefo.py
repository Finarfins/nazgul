"""SATIŞ PARTİ TÜKETİR (FAZ 1B-B) — FEFO seçicisinin İLK ÇAĞIRANI.

Konu: `app/parti_defteri.py` (`_parti_tuket`, `_hareket_notu`),
`app/routers/transactions.py` (satış yolu), `app/routers/workflow.py`
(irsaliye ve alış iadesi), `app/schemas.py` (`allow_expired_lots`).
GÖÇ YOKTUR: bu dilim 0067'nin tablosunu ve 0073'ün deposunu OLDUĞU GİBİ
kullanır, tek bir sütun eklemez.

--- BU DİLİM NEYİ KAPATIYOR ------------------------------------------------

0067 `app/parti.py` FEFO seçicisini kurdu ve ÇAĞIRANINI YAZMADI; 1B-A alışın
parti AÇMASINI getirdi ama TÜKETMESİNİ getirmedi ve
`test_fefo_sec_HALA_CAGIRANSIZ` bunu adıyla çiviliyordu. O gün BUGÜNDÜR:
kapı emekli edildi ve yerine DAHA DAR olanı geldi
(`test_fefo_sec_CAGIRANI_YALNIZ_parti_defteri_py`).

--- BU DOSYA NEYİ ÖLÇÜYOR, NEYİ ÖLÇMÜYOR ----------------------------------

Seçicinin KENDİ birim testleri `tests/test_parti_skt.py`dedir ve sıralamayı,
NaN kapısını, bölüştürmeyi orada ölçüyor. BURASI o seçicinin UÇTAN UCA
davranışını ölçer: hangi hareket satırları yazıldı, defterden ne düşüldü,
geri alma neyi geri verdi. İkisi ayrı durmalı — birim testine HTTP sokmak
sıralama kusurunu bir yönlendirici kusuru gibi gösterirdi.

--- SATIŞ NEDEN İKİ DOSYADAN GEÇİYOR --------------------------------------

Stoktan DÜŞEN üç yol var ve ÜÇÜ DE parti tüketir: `transactions.py`nin satış
yolu (`orders`), `workflow.py`nin irsaliyesi (`delivery`) ve alış iadesi
(`purchase_return`). Yalnız birincisini ölçmek, ötekilerin defteri sessizce
atlamasına izin verirdi — ve atladıkları gün stok düşer, parti defteri
düşmez; ikisi AYRIŞIR.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_satis_yolu_partiyi_FEFO_ile_TUKETIYOR(tmp_path: Path) -> None:
    """Uçtan uca: sırala, böl, reddet, izin ver, geri al, depoyu ayır.

    Alt süreçte GERÇEK ŞEMA ile koşuyor (deponun mevcut kalıbı, 1B-A'nın
    ikizi): `app.main` açılışta alembic'i sürüyor.
    """
    veritabani = tmp_path / "1b-b-satis-fefo.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{veritabani.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    tamamlandi = subprocess.run(
        [sys.executable, "-c", _DAVRANIS], cwd=BACKEND, env=env,
        capture_output=True, text=True, timeout=900,
    )
    assert tamamlandi.returncode == 0, tamamlandi.stdout + "\n" + tamamlandi.stderr


_DAVRANIS = r'''
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import SessionLocal
from app.main import app

client = TestClient(app)

# TARİHLER UZAK UÇLARDA SEÇİLDİ ve bu bir kolaylık değil, `bugun`
# bağımlılığının kapatılmasıdır: `_parti_tuket` `business_today()` çağırıyor
# (İstanbul takvim günü) ve testin cevabı makinenin gününe göre DEĞİŞMEMELİ.
# 2020 her koşuda geçmiş, 2098/2099 her koşuda gelecektir.
GECMIS = '2020-01-01'
YAKIN = '2098-01-31'
UZAK = '2099-01-31'


def ok(cevap):
    assert cevap.status_code < 300, (cevap.status_code, cevap.text)
    return cevap.json() if cevap.content else None


def giris(kullanici, sifre, yeni=None):
    cevap = client.post('/api/auth/login',
                        json={'username': kullanici, 'password': sifre})
    assert cevap.status_code == 200, cevap.text
    govde = cevap.json()
    baslik = {'Authorization': 'Bearer ' + govde['access_token'],
              'X-Company-ID': str(govde['companies'][0]['id'])}
    if yeni:
        degisti = client.post('/api/auth/change-password', headers=baslik,
                              json={'current_password': sifre, 'new_password': yeni})
        assert degisti.status_code == 200, degisti.text
        baslik['Authorization'] = 'Bearer ' + degisti.json()['access_token']
    return baslik, int(govde['companies'][0]['id'])


baslik, cid = giris('admin', 'admin123', 'SatisFefo!123')
# NEGATİF STOK SERBEST: bu dosya PARTİ defterini ölçüyor, stok politikasını
# DEĞİL. Politika kapalı kalsaydı bazı satışlar partiye HİÇ ulaşmadan 409
# alırdı ve testin neyi ölçtüğü sorulamaz olurdu.
ok(client.put('/api/company-settings', headers=baslik,
              json={'negative_stock_policy': 'allow', 'credit_limit_policy': 'block'}))

depo_a = ok(client.get('/api/warehouses', headers=baslik))[0]['id']
depo_b = ok(client.post('/api/warehouses', headers=baslik,
                        json={'name': 'FEFO B Deposu', 'code': 'FBD'}))['id']
tedarikci = ok(client.post('/api/suppliers', headers=baslik,
                           json={'name': 'FEFO Tedarikçisi'}))['id']
musteri = ok(client.post('/api/customers', headers=baslik,
                         json={'name': 'FEFO Müşterisi'}))['id']


def urun_ac(ad):
    return ok(client.post('/api/products', headers=baslik,
                          json={'name': ad, 'purchase_price': 10, 'sale_price': 20,
                                'vat_rate': 20, 'stock': 0, 'unit': 'Adet'}))['id']


def kalem(pid, adet, kod=None, skt=None, fiyat=10):
    satir = {'product_id': pid, 'quantity': adet, 'unit_price': fiyat, 'vat_rate': 20}
    if kod is not None:
        satir['lot_code'] = kod
    if skt is not None:
        satir['expiry_date'] = skt
    return satir


def alis(kalemler, depo=None):
    return ok(client.post('/api/purchases', headers=baslik, json={
        'entity_id': tedarikci, 'transaction_date': '2026-09-08',
        'warehouse_id': depo or depo_a, 'items': kalemler}))


def satis_istegi(kalemler, depo=None, **fazla):
    # VADE AÇIKÇA YAZILIYOR: yazılmazsa türetilen vade güncellemede kayar ve
    # satış yolu "onaylı satışın vade değişikliği için gerekçe zorunludur"
    # diye 422 döner — bu dosyanın ölçtüğü şeyle ilgisiz bir kapı.
    govde = {'entity_id': musteri, 'transaction_date': '2026-09-09',
             'due_date': '2026-09-30',
             'warehouse_id': depo or depo_a, 'items': kalemler}
    govde.update(fazla)
    return client.post('/api/orders', headers=baslik, json=govde)


def satis(kalemler, depo=None, **fazla):
    return ok(satis_istegi(kalemler, depo, **fazla))


def partiler(pid, depo=None):
    satirlar = ok(client.get(f'/api/products/{pid}/lots', headers=baslik))['lots']
    if depo is not None:
        satirlar = [s for s in satirlar if s['warehouse_id'] == depo]
    return {s['lot_code']: Decimal(str(s['quantity'])) for s in satirlar}


def hareketler(tur, belge_id):
    """Belgenin hareket satırları: (lot_code, miktar, not). FEFO sırasında."""
    with SessionLocal() as db:
        return [
            (satir[0], Decimal(str(satir[1])), satir[2])
            for satir in db.execute(text(
                "SELECT l.lot_code, h.quantity, h.note FROM stock_movements h "
                "LEFT JOIN product_lots l ON l.id=h.lot_id "
                "WHERE h.company_id=:cid AND h.reference_type=:rt "
                "AND h.reference_id=:rid ORDER BY h.id"
            ), {'cid': cid, 'rt': tur, 'rid': belge_id}).all()
        ]


# =========================================================================
# 1. FEFO SIRASI: EN ERKEN SKT ÖNCE — GİRİŞ SIRASINDAN BAĞIMSIZ
#
# `LOT-UZAK` ÖNCE alındı (yani `created_at`i daha küçük) ama SKT'si DAHA
# GEÇ. Doğru cevap `LOT-YAKIN`la başlamaktır; giriş sırası kazansaydı bu
# FEFO değil FIFO olurdu ve bozulmaya en yakın mal rafta KALIRDI.
# =========================================================================
sira = urun_ac('FEFO Sıra Ürünü')
alis([kalem(sira, 5, 'LOT-UZAK', UZAK)])
alis([kalem(sira, 3, 'LOT-YAKIN', YAKIN)])
assert partiler(sira) == {'LOT-UZAK': Decimal('5'), 'LOT-YAKIN': Decimal('3')}

belge = satis([kalem(sira, 6, fiyat=20)])
# BİR PAY = BİR SATIR ve SIRA FEFO'nundur.
assert hareketler('orders', belge['id']) == [
    ('LOT-YAKIN', Decimal('-3'), f"sale #{belge['id']}"),
    ('LOT-UZAK', Decimal('-3'), f"sale #{belge['id']}"),
], hareketler('orders', belge['id'])
# Payların TOPLAMI istenene TAM eşit: 3 + 3 = 6.
assert sum(m for _, m, _ in hareketler('orders', belge['id'])) == Decimal('-6')
assert partiler(sira) == {'LOT-UZAK': Decimal('2'), 'LOT-YAKIN': Decimal('0')}

# =========================================================================
# 2. SKT'si OLMAYAN PARTİ EN SONA — giriş sırası onu ÖNE alamaz
#
# `LOT-SKTSIZ` ÖNCE alındı. Sıra `created_at`e düşseydi ya da NULL için
# `date.min` yer tutucusu sıralamaya GİRSEYDİ o önce çıkardı; doğru cevap
# SKT'si OLANIN önce çıkmasıdır — SKT ne kadar uzak olursa olsun.
# =========================================================================
bos = urun_ac('FEFO SKT’siz Ürün')
alis([kalem(bos, 4, 'LOT-SKTSIZ')])
alis([kalem(bos, 4, 'LOT-VAR', UZAK)])
belge = satis([kalem(bos, 5, fiyat=20)])
assert [(k, m) for k, m, _ in hareketler('orders', belge['id'])] == [
    ('LOT-VAR', Decimal('-4')),
    ('LOT-SKTSIZ', Decimal('-1')),
], hareketler('orders', belge['id'])
assert partiler(bos) == {'LOT-SKTSIZ': Decimal('3'), 'LOT-VAR': Decimal('0')}

# =========================================================================
# 3. AYNI SKT -> EŞİTLİK BOZUCU `created_at`: ÖNCE GİREN ÖNCE ÇIKAR
# =========================================================================
esit = urun_ac('FEFO Eşit SKT Ürünü')
alis([kalem(esit, 2, 'LOT-ILK', UZAK)])
alis([kalem(esit, 2, 'LOT-SONRA', UZAK)])
belge = satis([kalem(esit, 1, fiyat=20)])
assert [(k, m) for k, m, _ in hareketler('orders', belge['id'])] == [
    ('LOT-ILK', Decimal('-1'))
], hareketler('orders', belge['id'])

# =========================================================================
# 4. YETMEYEN PARTİ -> 409 PARTI_YETERSIZ, SAYILARLA
#
# Sayılar gövdede DURUYOR: "yetmedi" tek başına operatöre ne kadar mal
# alması gerektiğini SÖYLEMEZ.
# =========================================================================
az = urun_ac('FEFO Az Ürün')
alis([kalem(az, 2, 'LOT-AZ', UZAK)])
reddedildi = satis_istegi([kalem(az, 5, fiyat=20)])
assert reddedildi.status_code == 409, (reddedildi.status_code, reddedildi.text)
ayrinti = reddedildi.json()['detail']
assert ayrinti['code'] == 'PARTI_YETERSIZ', ayrinti
assert (Decimal(ayrinti['istenen']), Decimal(ayrinti['mevcut']),
        Decimal(ayrinti['eksik'])) == (Decimal('5'), Decimal('2'), Decimal('3')), ayrinti
# RED BİR DURDURMADIR: defter OYNAMADI ve hareket YAZILMADI.
assert partiler(az) == {'LOT-AZ': Decimal('2')}

# =========================================================================
# 5. SÜRESİ GEÇMİŞ PARTİ -> 422, BAYRAKLA GEÇER VE NOTA DAMGALANIR
#
# İki cümle ayrı ayrı ölçülüyor: (a) "mal yok" DEĞİL "mal var ama süresi
# geçmiş" — gövde partileri LİSTELİYOR; (b) izin AÇIKÇA yazılmadan mal
# ÇIKMAZ ve yazıldığında hareket notu bunu SÖYLER.
# =========================================================================
gecmis = urun_ac('FEFO Süresi Geçmiş Ürün')
alis([kalem(gecmis, 4, 'LOT-BOZUK', GECMIS)])
reddedildi = satis_istegi([kalem(gecmis, 3, fiyat=20)])
assert reddedildi.status_code == 422, (reddedildi.status_code, reddedildi.text)
ayrinti = reddedildi.json()['detail']
assert ayrinti['code'] == 'PARTI_SURESI_GECMIS', ayrinti
assert len(ayrinti['suresi_gecmis']) == 1, ayrinti
assert ayrinti['suresi_gecmis'][0]['expiry_date'] == GECMIS, ayrinti
assert Decimal(ayrinti['suresi_gecmis'][0]['quantity']) == Decimal('4'), ayrinti
# "Mevcut" SIFIRDIR ama parti VARDIR: iki cümlenin farkı tam burada.
assert Decimal(ayrinti['mevcut']) == Decimal('0'), ayrinti
assert partiler(gecmis) == {'LOT-BOZUK': Decimal('4')}

belge = satis([kalem(gecmis, 3, fiyat=20)], allow_expired_lots=True)
damgali = hareketler('orders', belge['id'])
assert [(k, m) for k, m, _ in damgali] == [('LOT-BOZUK', Decimal('-3'))], damgali
assert 'SURESI GECMIS PARTI' in damgali[0][2], damgali
assert partiler(gecmis) == {'LOT-BOZUK': Decimal('1')}

# TAZE PARTİ YETİYORSA SÜRESİ GEÇMİŞİN VARLIĞI SATIŞI DURDURMAZ ve süresi
# geçmiş maldan HİÇBİR ŞEY çıkmaz — damga da BASILMAZ.
alis([kalem(gecmis, 10, 'LOT-TAZE', UZAK)])
belge = satis([kalem(gecmis, 2, fiyat=20)])
temiz = hareketler('orders', belge['id'])
assert [(k, m) for k, m, _ in temiz] == [('LOT-TAZE', Decimal('-2'))], temiz
assert 'SURESI GECMIS' not in temiz[0][2], temiz

# =========================================================================
# 6. PARTİSİZ ÜRÜN BUGÜNKÜ DAVRANIŞINI KORUR: TEK satır, `lot_id` NULL
# =========================================================================
partisiz = urun_ac('FEFO Partisiz Ürün')
alis([kalem(partisiz, 9)])
belge = satis([kalem(partisiz, 4, fiyat=20)])
assert hareketler('orders', belge['id']) == [
    (None, Decimal('-4'), f"sale #{belge['id']}")
], hareketler('orders', belge['id'])
assert partiler(partisiz) == {}

# =========================================================================
# 7. BAŞKA DEPONUN PARTİSİ TÜKETİLMEZ
#
# Depo yüklemi `_parti_tuket`in SORGUSUNDADIR (seçicide değil, çünkü `Parti`
# bilerek `warehouse_id` taşımaz). Düşerse B deposunun malı A deposundan
# çıkmış görünür ve iki depo sessizce ayrışır.
#
# KURULUM İKİ KEZ ÖLÇÜLDÜ. İLK HÂLİ SAHTE YEŞİLDİ ve mutasyon bataryası onu
# yakaladı: B deposunun partisi SÜRESİ GEÇMİŞ yazılmıştı, yani yüklem düşse
# BİLE o parti seçime giremezdi (SKT onu zaten dışlıyordu) ve kapı yeşil
# kalıyordu. Kapı, ölçtüğünü sandığı şeyi ÖLÇMÜYORDU.
#
# Şimdi B deposunun partisi TAZE ve SKT'si A'nınkinden DAHA ERKEN, yani
# yüklem düşerse FEFO onu A'nınkine TERCİH EDER — kusur GÖRÜNÜR olur.
# =========================================================================
depolu = urun_ac('FEFO Depolu Ürün')
alis([kalem(depolu, 6, 'LOT-A-DEPO', UZAK)], depo=depo_a)
alis([kalem(depolu, 6, 'LOT-B-DEPO', YAKIN)], depo=depo_b)
belge = satis([kalem(depolu, 2, fiyat=20)], depo=depo_a)
assert [(k, m) for k, m, _ in hareketler('orders', belge['id'])] == [
    ('LOT-A-DEPO', Decimal('-2'))
], hareketler('orders', belge['id'])
assert partiler(depolu, depo=depo_b) == {'LOT-B-DEPO': Decimal('6')}
assert partiler(depolu, depo=depo_a) == {'LOT-A-DEPO': Decimal('4')}

# İKİNCİ YARI — AYRI BİR CÜMLE: komşu deponun malı YETERLİLİĞE de sayılmaz.
# Yukarısı "yanlış partiden çıkmadı" diyor; burası "var olmayan malı VAR
# saymadı" diyor. Yüklem düşerse bu satış 409 yerine BAŞARILI olurdu ve
# eksiklik hiç fark edilmezdi.
kit = urun_ac('FEFO Kıt Depo Ürünü')
alis([kalem(kit, 1, 'LOT-KIT-A', UZAK)], depo=depo_a)
alis([kalem(kit, 10, 'LOT-KIT-B', YAKIN)], depo=depo_b)
reddedildi = satis_istegi([kalem(kit, 5, fiyat=20)], depo=depo_a)
assert reddedildi.status_code == 409, (reddedildi.status_code, reddedildi.text)
ayrinti = reddedildi.json()['detail']
assert ayrinti['code'] == 'PARTI_YETERSIZ', ayrinti
# "Mevcut" YALNIZ A deposunu sayar: 1, komşunun 10'u DEĞİL.
assert Decimal(ayrinti['mevcut']) == Decimal('1'), ayrinti
assert Decimal(ayrinti['eksik']) == Decimal('4'), ayrinti
assert partiler(kit, depo=depo_b) == {'LOT-KIT-B': Decimal('10')}

# =========================================================================
# 8. GÜNCELLEME: MİKTAR AZALIR / ARTAR -> DEFTER TAM OLARAK GERİ VERİR
#
# Geri alma HAREKETLERDEN okunuyor ve hareketin miktarı NEGATİF; tek bir
# `quantity=quantity-:miktar` ifadesi çıkarmayı EKLEMEYE çeviriyor. Yön
# yanlış olsaydı burada parti İKİ KAT düşerdi.
# =========================================================================
guncel_urun = urun_ac('FEFO Güncelleme Ürünü')
alis([kalem(guncel_urun, 10, 'LOT-G', UZAK)])
belge = satis([kalem(guncel_urun, 6, fiyat=20)])
assert partiler(guncel_urun) == {'LOT-G': Decimal('4')}

ok(client.put(f"/api/orders/{belge['id']}", headers=baslik, json={
    'entity_id': musteri, 'transaction_date': '2026-09-09',
    'due_date': '2026-09-30',
    'warehouse_id': depo_a, 'items': [kalem(guncel_urun, 2, fiyat=20)]}))
# 10 -> (6 düşüldü) 4 -> (6 geri verildi) 10 -> (2 düşüldü) 8
assert partiler(guncel_urun) == {'LOT-G': Decimal('8')}

ok(client.put(f"/api/orders/{belge['id']}", headers=baslik, json={
    'entity_id': musteri, 'transaction_date': '2026-09-09',
    'due_date': '2026-09-30',
    'warehouse_id': depo_a, 'items': [kalem(guncel_urun, 9, fiyat=20)]}))
assert partiler(guncel_urun) == {'LOT-G': Decimal('1')}

# =========================================================================
# 9. SİLME: PARTİ TAM OLARAK GERİ DÖNER, SATIR KALIR
# =========================================================================
assert client.delete(f"/api/orders/{belge['id']}", headers=baslik).status_code == 204
assert partiler(guncel_urun) == {'LOT-G': Decimal('10')}

# İKİ PARTİYE BÖLÜNMÜŞ bir satışın silinmesi İKİSİNİ DE geri verir — N
# hareket satırı geri almayı bozmuyor, çünkü geri alma REFERANSTAN
# anahtarlanıyor.
bolunmus = urun_ac('FEFO Bölünmüş Ürün')
alis([kalem(bolunmus, 3, 'LOT-B1', YAKIN)])
alis([kalem(bolunmus, 3, 'LOT-B2', UZAK)])
belge = satis([kalem(bolunmus, 5, fiyat=20)])
assert partiler(bolunmus) == {'LOT-B1': Decimal('0'), 'LOT-B2': Decimal('1')}
assert client.delete(f"/api/orders/{belge['id']}", headers=baslik).status_code == 204
assert partiler(bolunmus) == {'LOT-B1': Decimal('3'), 'LOT-B2': Decimal('3')}

# =========================================================================
# 10. İRSALİYE (workflow, `stock=-1`) DE PARTİ TÜKETİR VE GERİ VERİR
#
# İkinci çağıran ölçülmeseydi irsaliye stoktan düşer, parti defterinden
# DÜŞMEZDİ — ve ikisi sessizce ayrışırdı.
# =========================================================================
irsaliye_urun = urun_ac('FEFO İrsaliye Ürünü')
alis([kalem(irsaliye_urun, 4, 'LOT-I1', YAKIN)])
alis([kalem(irsaliye_urun, 4, 'LOT-I2', UZAK)])
irsaliye = ok(client.post('/api/workflow/delivery', headers=baslik, json={
    'entity_id': musteri, 'document_date': '2026-09-10', 'status': 'completed',
    'warehouse_id': depo_a, 'items': [kalem(irsaliye_urun, 6, fiyat=20)]}))
assert [(k, m) for k, m, _ in hareketler('delivery_notes', irsaliye['id'])] == [
    ('LOT-I1', Decimal('-4')),
    ('LOT-I2', Decimal('-2')),
], hareketler('delivery_notes', irsaliye['id'])
assert partiler(irsaliye_urun) == {'LOT-I1': Decimal('0'), 'LOT-I2': Decimal('2')}

assert client.delete(f"/api/workflow/delivery/{irsaliye['id']}",
                     headers=baslik).status_code in (200, 204)
assert partiler(irsaliye_urun) == {'LOT-I1': Decimal('4'), 'LOT-I2': Decimal('4')}

# İRSALİYE DE SÜRESİ GEÇMİŞİ SESSİZCE ÇIKARMAZ.
irsaliye_bozuk = urun_ac('FEFO İrsaliye Bozuk Ürünü')
alis([kalem(irsaliye_bozuk, 5, 'LOT-IB', GECMIS)])
reddedildi = client.post('/api/workflow/delivery', headers=baslik, json={
    'entity_id': musteri, 'document_date': '2026-09-10', 'status': 'completed',
    'warehouse_id': depo_a, 'items': [kalem(irsaliye_bozuk, 1, fiyat=20)]})
assert reddedildi.status_code == 422, (reddedildi.status_code, reddedildi.text)
assert reddedildi.json()['detail']['code'] == 'PARTI_SURESI_GECMIS', reddedildi.text
assert partiler(irsaliye_bozuk) == {'LOT-IB': Decimal('5')}

# =========================================================================
# 11. TASLAK SATIŞ STOĞA DOKUNMAZ -> PARTİYE DE DOKUNMAZ
#
# Parti tüketimi `apply_stock` dalının İÇİNDEDİR. Dışına çıksaydı taslak bir
# belge defteri düşürür, stoğu düşürmezdi.
# =========================================================================
taslak_urun = urun_ac('FEFO Taslak Ürünü')
alis([kalem(taslak_urun, 5, 'LOT-T', UZAK)])
taslak = satis([kalem(taslak_urun, 3, fiyat=20)], status='draft')
assert hareketler('orders', taslak['id']) == []
assert partiler(taslak_urun) == {'LOT-T': Decimal('5')}

print('1B-B DAVRANIŞ TURU TAMAM')
'''
