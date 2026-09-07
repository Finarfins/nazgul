"""SATIŞ İADESİ ÇIKTIĞI PARTİYE GERİ VERİR (FAZ 1B-E) — TERS FEFO.

Konu: `app/parti_defteri.py` (`_parti_iade`), `app/routers/workflow.py`
(`CONFIG["sale_return"]["iade_kaynagi"]` ve kalem döngüsünün giriş dalı).
GÖÇ YOKTUR: bu dilim 0067'nin tablosunu, 0073'ün deposunu ve `returns`
tablosunun ZATEN VAR OLAN `source_type`/`source_id` alanlarını olduğu gibi
kullanır, tek bir sütun eklemez.

--- BU DİLİM NEYİ KAPATIYOR ------------------------------------------------

1B-B stoktan ÇIKAN üç yolu (satış, irsaliye, alış iadesi) partiye bağladı ve
GİREN tek yolu — satış iadesini — ADIYLA kapsam dışında bıraktı: "iade edilen
malın hangi partiden çıktığı bu dilimde ÖLÇÜLMEDİ". O gün BUGÜNDÜR. Ölçüm
şudur: mal SATIŞIN hareket satırlarından çıkmıştır ve o satırlar `lot_id`
taşır (1B-B), yani cevap UYDURULMAK zorunda değil OKUNABİLİR.

--- KAYNAK BAĞI VARDI, YENİSİ UYDURULMADI ---------------------------------

İadeyi satışına bağlayan alan ARANDI ve BULUNDU: `returns.source_type` +
`returns.source_id` (`app/workflow.py`nin eski sütun listesi) ve
`movement_references.validate_return_reference` ikisini KAPALI bir eşlemeyle
zaten doğruluyor — satış iadesi için `("order", "customer")`. Yani şema
DEĞİŞMEDİ ve kalem satırına isteğe bağlı bir `lot_code` EKLENMEDİ: operatöre
partiyi yeniden YAZDIRMAK, defterde zaten yazılı olan olguyu ikinci bir
kaynaktan sormak olurdu ve iki kaynak ayrıştığı gün hangisinin doğru olduğu
sorulamazdı.

--- TERS FEFO BİR TERCİH DEĞİL, TERSİNE ÇEVİRMEDİR ------------------------

Satış FEFO ile L1'i bitirip L2'ye geçtiyse, iadenin doğru cevabı ÖNCE L2'dir.
Düz FEFO da "toplamı doğru" bir defter üretirdi ve tam bu yüzden tehlikelidir:
kısmi iadede yanlış partiyi şişirir, toplam bakan bir gözden KAÇAR ve geri
çağırma kaydı — bu deponun bütün parti işinin sebebi — YALAN söyler.

--- BU DOSYA NEYİ ÖLÇMÜYOR ------------------------------------------------

KAYNAKSIZ satış iadesi (iki alan da NULL) BUGÜNKÜ davranışını korur ve o kapı
`tests/test_1b_b_satis_fefo.py`de durur. Miktar kapısı (`IADE_SATISI_ASIYOR`)
da YALNIZ parti borcu OLAN satışlar için ısırır: partisiz bir satıştan fazla
mal iade etmek bu dilimin kapsamı DEĞİLDİR ve aşağıda ADIYLA çivilendi —
kapsam sınırı sessiz bırakılmadı.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
DEFTER = BACKEND / "app" / "parti_defteri.py"
IS_AKISI = BACKEND / "app" / "routers" / "workflow.py"


def _govde(kaynak: str, ad: str) -> ast.FunctionDef:
    for dugum in ast.walk(ast.parse(kaynak)):
        if isinstance(dugum, ast.FunctionDef) and dugum.name == ad:
            return dugum
    raise AssertionError(f"{ad} bulunamadı")


# ------------------------------------------------------------- statik ---

def test_iade_SECMIYOR_HATIRLIYOR_FEFO_secicisini_CAGIRMIYOR() -> None:
    """`_parti_iade` FEFO'yu YENİDEN ÇALIŞTIRMAZ; satışın kaydını OKUR.

    Bu kapı bir çağrı sayımı gibi görünüyor ama savunduğu şey bir CÜMLE:
    "iade edilen malın partisi bir TERCİH değil bir OLGUDUR". `fefo_sec`
    buradan çağrılsaydı iade, malın gerçekte çıktığı partiden BAŞKA bir
    partiye girebilirdi (depoya o arada daha erken SKT'li mal girmişse) ve
    hiçbir toplam bozulmadığı için kusur GÖRÜNMEZDİ.
    """
    fonksiyon = _govde(DEFTER.read_text(encoding="utf-8"), "_parti_iade")
    cagrilar = {
        dugum.func.id
        for dugum in ast.walk(fonksiyon)
        if isinstance(dugum, ast.Call) and isinstance(dugum.func, ast.Name)
    }
    assert "fefo_sec" not in cagrilar, (
        "`_parti_iade` FEFO seçicisini çağırıyor — iade SEÇMEZ, HATIRLAR."
    )
    # Defteri BÜYÜTEN tek yol `_parti_ac`tır: yazma tekeli 1B-A kapısında
    # (`test_parti_geri_alma_YONU_HAREKETIN_ISARETINDEN_gelir`) ölçülüyor,
    # burada ölçülen şey İADENİN o tekeli KULLANDIĞIDIR.
    assert "_parti_ac" in cagrilar


def test_iade_okumalari_KIRACI_YUKLEMINI_TASIYOR() -> None:
    """`_parti_iade`in HER sorgusunda `company_id=:cid` var.

    DAVRANIŞLA ÖLÇÜLEMEDİĞİ İÇİN STATİK ve bu SÖYLENİYOR, gizlenmiyor:
    yüklem düşse bile komşu kiracının satırını bu yola sokmak için aynı
    `product_id` ve aynı belge kimliği gerekir; ikisi de firma başına ayrı
    üretiliyor ve bir testte ÇAKIŞTIRILAMAZ. Kapı bu yüzden ne ölçtüğünü
    SÖYLÜYOR: yüklemin VARLIĞINI, ısırdığını değil. Kiracı sınırının DAVRANIŞ
    tarafı iade yolunda kaynak doğrulamasında (`validate_return_reference`)
    ölçülüyor — aşağıdaki alt süreçte 409 olarak.
    """
    fonksiyon = _govde(DEFTER.read_text(encoding="utf-8"), "_parti_iade")
    metinler = [
        dugum.value
        for dugum in ast.walk(fonksiyon)
        if isinstance(dugum, ast.Constant) and isinstance(dugum.value, str)
        and ("SELECT" in dugum.value or "UPDATE" in dugum.value)
    ]
    assert metinler, "sorgu metni bulunamadı — tarayıcı boşa dönmüş olabilir"
    for metin in metinler:
        assert "company_id=:cid" in metin, metin


def test_iade_kaynagi_CONFIGte_ve_IKI_ADI_AYRI_tasiyor() -> None:
    """Beyan ("order") ile hareket referansı ("orders") AYRI yazılı.

    Birini ötekinden TÜRETMEK (çoğul eki eklemek) bir konvansiyona bel
    bağlardı. `returns.source_type` `movement_references.py`nin kapalı
    eşlemesinden, `stock_movements.reference_type` ise `CONFIG['head']`ten
    geliyor; ikisi ayrıştığı gün iade satışın hareketlerini HİÇ bulamaz ve —
    en kötüsü — hata vermeden PARTİSİZ yazardı.
    """
    kaynak = IS_AKISI.read_text(encoding="utf-8")
    assert 'iade_kaynagi=("order", "orders")' in kaynak
    # Alış iadesi bu beyanı TAŞIMAZ: onun yolu 1B-B'nin TÜKETİMİDİR ve
    # beyanı oraya da yazmak, aynı belgeyi hem tüketen hem geri veren iki
    # dalın altına sokardı.
    satis = kaynak[
        kaynak.index('"sale_return": dict('):kaynak.index('"purchase_return": dict(')
    ]
    alis = kaynak[kaynak.index('"purchase_return": dict('):kaynak.index("RETURN_LABELS")]
    assert "iade_kaynagi" in satis and "iade_kaynagi" not in alis


# ---------------------------------------------------------- davranış ---

def test_satis_iadesi_CIKTIGI_PARTIYE_TERS_FEFO_ile_geri_veriyor(tmp_path: Path) -> None:
    """Uçtan uca: ters sırala, kısmi ver, aş ve reddet, geri al, depoyu ayır.

    Alt süreçte GERÇEK ŞEMA ile koşuyor (1B-A/1B-B ikizlerinin kalıbı):
    `app.main` açılışta alembic'i sürüyor.
    """
    veritabani = tmp_path / "1b-e-iade-lot.db"
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

# TARİHLER UZAK UÇLARDA (1B-B'nin gerekçesi): `bugun` bağımlılığı kapatılıyor.
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


baslik, cid = giris('admin', 'admin123', 'IadeLot!123')
# NEGATİF STOK SERBEST: bu dosya PARTİ defterini ölçüyor, stok politikasını
# DEĞİL (1B-B'nin aynı gerekçesi).
ok(client.put('/api/company-settings', headers=baslik,
              json={'negative_stock_policy': 'allow', 'credit_limit_policy': 'block'}))

depo_a = ok(client.get('/api/warehouses', headers=baslik))[0]['id']
depo_b = ok(client.post('/api/warehouses', headers=baslik,
                        json={'name': 'İade B Deposu', 'code': 'IBD'}))['id']
tedarikci = ok(client.post('/api/suppliers', headers=baslik,
                           json={'name': 'İade Tedarikçisi'}))['id']
musteri = ok(client.post('/api/customers', headers=baslik,
                         json={'name': 'İade Müşterisi'}))['id']
oteki = ok(client.post('/api/customers', headers=baslik,
                       json={'name': 'İade Öteki Müşteri'}))['id']


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


def satis(kalemler, depo=None, cari=None):
    return ok(client.post('/api/orders', headers=baslik, json={
        'entity_id': cari or musteri, 'transaction_date': '2026-09-09',
        'due_date': '2026-09-30', 'warehouse_id': depo or depo_a,
        'items': kalemler}))


def iade_istegi(kalemler, kaynak=None, depo=None, cari=None, **fazla):
    govde = {'entity_id': cari or musteri, 'document_date': '2026-09-12',
             'status': 'completed', 'warehouse_id': depo or depo_a,
             'items': kalemler}
    if kaynak is not None:
        govde['source_type'] = 'order'
        govde['source_id'] = kaynak
    govde.update(fazla)
    return client.post('/api/workflow/sale_return', headers=baslik, json=govde)


def iade(kalemler, kaynak=None, depo=None, cari=None, **fazla):
    return ok(iade_istegi(kalemler, kaynak, depo, cari, **fazla))


def partiler(pid, depo=None):
    satirlar = ok(client.get(f'/api/products/{pid}/lots', headers=baslik))['lots']
    if depo is not None:
        satirlar = [s for s in satirlar if s['warehouse_id'] == depo]
    return {s['lot_code']: Decimal(str(s['quantity'])) for s in satirlar}


def hareketler(tur, belge_id):
    """Belgenin hareket satırları: (lot_code, miktar). YAZILDIKLARI sırada."""
    with SessionLocal() as db:
        return [
            (satir[0], Decimal(str(satir[1])))
            for satir in db.execute(text(
                "SELECT l.lot_code, h.quantity FROM stock_movements h "
                "LEFT JOIN product_lots l ON l.id=h.lot_id "
                "WHERE h.company_id=:cid AND h.reference_type=:rt "
                "AND h.reference_id=:rid ORDER BY h.id"
            ), {'cid': cid, 'rt': tur, 'rid': belge_id}).all()
        ]


# =========================================================================
# 1. BÖLÜNMÜŞ SATIŞ -> KISMİ İADE: SON TÜKETİLEN İLK GERİ VERİLİR
#
# Satış FEFO ile önce L1'i (YAKIN) bitirdi, sonra L2'den (UZAK) 2 aldı.
# İadenin 3 birimi TERS sırayla dağıtılmalı: önce L2'nin 2'si, sonra L1'den 1.
# DÜZ FEFO burada `[('LOT-E1', 3)]` yazardı — toplam yine 3'tür ve tam bu
# yüzden yalnız TOPLAMA bakan bir kapı kusuru GÖRMEZ.
# =========================================================================
bol = urun_ac('İade Bölünmüş Ürün')
alis([kalem(bol, 3, 'LOT-E1', YAKIN)])
alis([kalem(bol, 3, 'LOT-E2', UZAK)])
satis_belgesi = satis([kalem(bol, 5, fiyat=20)])
assert hareketler('orders', satis_belgesi['id']) == [
    ('LOT-E1', Decimal('-3')),
    ('LOT-E2', Decimal('-2')),
], hareketler('orders', satis_belgesi['id'])
assert partiler(bol) == {'LOT-E1': Decimal('0'), 'LOT-E2': Decimal('1')}

kismi = iade([kalem(bol, 3, fiyat=20)], kaynak=satis_belgesi['id'])
assert hareketler('returns', kismi['id']) == [
    ('LOT-E2', Decimal('2')),
    ('LOT-E1', Decimal('1')),
], hareketler('returns', kismi['id'])
# BİR PAY = BİR HAREKET SATIRI ve payların TOPLAMI istenene TAM eşit.
assert sum(m for _, m in hareketler('returns', kismi['id'])) == Decimal('3')
assert partiler(bol) == {'LOT-E1': Decimal('1'), 'LOT-E2': Decimal('3')}

# =========================================================================
# 2. KALANIN İADESİ: DEFTER SATIŞ ÖNCESİNE TAM DÖNER
#
# İKİNCİ iade, BİRİNCİSİNİN geri verdiğini SAYIYOR: L2'nin kapasitesi artık
# 0'dır (2 satıldı, 2 iade edildi) ve pay L1'den çıkmalı. Kapasite "satılan"
# olsaydı L2 bir kez daha şişerdi ve hiçbir kapı ısırmazdı.
# =========================================================================
kalan = iade([kalem(bol, 2, fiyat=20)], kaynak=satis_belgesi['id'])
assert hareketler('returns', kalan['id']) == [
    ('LOT-E1', Decimal('2')),
], hareketler('returns', kalan['id'])
assert partiler(bol) == {'LOT-E1': Decimal('3'), 'LOT-E2': Decimal('3')}

# ÜÇÜNCÜ İADE: GERİ VERİLECEK BİR ŞEY KALMADI -> 422, SAYILARLA.
reddedildi = iade_istegi([kalem(bol, 1, fiyat=20)], kaynak=satis_belgesi['id'])
assert reddedildi.status_code == 422, (reddedildi.status_code, reddedildi.text)
ayrinti = reddedildi.json()['detail']
assert ayrinti['code'] == 'IADE_SATISI_ASIYOR', ayrinti
assert (Decimal(ayrinti['istenen']), Decimal(ayrinti['mevcut']),
        Decimal(ayrinti['fazla'])) == (Decimal('1'), Decimal('0'),
                                       Decimal('1')), ayrinti
# RED BİR DURDURMADIR: defter OYNAMADI.
assert partiler(bol) == {'LOT-E1': Decimal('3'), 'LOT-E2': Decimal('3')}

# =========================================================================
# 3. SATILANDAN FAZLASI TEK SEFERDE -> 422 IADE_SATISI_ASIYOR
#
# Sayılar gövdede DURUYOR: "olmaz" tek başına operatöre kaç birimin fazla
# olduğunu SÖYLEMEZ.
# =========================================================================
asan = urun_ac('İade Aşan Ürün')
alis([kalem(asan, 6, 'LOT-AS', UZAK)])
asan_satis = satis([kalem(asan, 2, fiyat=20)])
assert partiler(asan) == {'LOT-AS': Decimal('4')}
reddedildi = iade_istegi([kalem(asan, 5, fiyat=20)], kaynak=asan_satis['id'])
assert reddedildi.status_code == 422, (reddedildi.status_code, reddedildi.text)
ayrinti = reddedildi.json()['detail']
assert ayrinti['code'] == 'IADE_SATISI_ASIYOR', ayrinti
assert (Decimal(ayrinti['istenen']), Decimal(ayrinti['mevcut']),
        Decimal(ayrinti['fazla'])) == (Decimal('5'), Decimal('2'),
                                       Decimal('3')), ayrinti
assert partiler(asan) == {'LOT-AS': Decimal('4')}
# BELGE DE YAZILMADI: red belgenin TAMAMINI geri alır, yarım bir iade kalmaz.
kesilenler = {satir['id'] for satir
              in ok(client.get('/api/workflow/sale_return', headers=baslik))}
assert kesilenler == {kismi['id'], kalan['id']}, kesilenler

# O SATIŞTA HİÇ GEÇMEYEN ÜRÜN: AYNI kod, çünkü aynı cümle — "bu satıştan bu
# kadar mal geri verilemez". Kapının bu yarısı AYRI durmalı: kaynak sorgusu
# `lot_id IS NOT NULL` süzgecini SQL'de taşısaydı bu durum "partisiz satış"
# ile AYNI boş sonuca düşer ve 422 yerine SESSİZCE partisiz yazılırdı.
yabanci = urun_ac('İade Yabancı Ürün')
alis([kalem(yabanci, 5, 'LOT-YB', UZAK)])
reddedildi = iade_istegi([kalem(yabanci, 1, fiyat=20)], kaynak=asan_satis['id'])
assert reddedildi.status_code == 422, (reddedildi.status_code, reddedildi.text)
ayrinti = reddedildi.json()['detail']
assert ayrinti['code'] == 'IADE_SATISI_ASIYOR', ayrinti
assert Decimal(ayrinti['mevcut']) == Decimal('0'), ayrinti
assert partiler(yabanci) == {'LOT-YB': Decimal('5')}

# =========================================================================
# 4. PARTİSİZ SATIŞIN İADESİ BUGÜNKÜ DAVRANIŞINI KORUR
#
# `_parti_iade` `None` döner ("bu satışın bu üründe parti borcu YOK") ve
# çağıran tek satır, `lot_id` NULL yazar. `None` ile BOŞ dağıtım AYRI iki
# cevaptır: boş dağıtım 422'dir ve buraya HİÇ ulaşmaz.
# =========================================================================
partisiz = urun_ac('İade Partisiz Ürün')
alis([kalem(partisiz, 9)])
partisiz_satis = satis([kalem(partisiz, 4, fiyat=20)])
partisiz_iade = iade([kalem(partisiz, 2, fiyat=20)], kaynak=partisiz_satis['id'])
assert hareketler('returns', partisiz_iade['id']) == [
    (None, Decimal('2')),
], hareketler('returns', partisiz_iade['id'])
assert partiler(partisiz) == {}

# PARTİSİZ SATIŞTA MİKTAR KAPISI ISIRMAZ — KAPSAM SINIRI, ADIYLA ÇİVİLİ.
# `_parti_iade` daha ilk okumada `None` dönüyor ve sayı HİÇ sorulmuyor. Bu
# dilim partisiz yolun sayısal doğrulamasını GETİRMEDİ; getirmiş gibi
# görünmemesi için satılandan FAZLASI burada AÇIKÇA geçiyor.
asiri = iade([kalem(partisiz, 99, fiyat=20)], kaynak=partisiz_satis['id'])
assert hareketler('returns', asiri['id']) == [(None, Decimal('99'))]

# =========================================================================
# 5. GERİ ALMA: İADE SİLİNİRSE PARTİ YENİDEN DÜŞER
#
# `_parti_geri_al` ÇOĞALTILMADI: iade hareketleri POZİTİF yazılıyor ve tek
# `quantity=quantity-:miktar` ifadesi onları DÜŞÜRÜYOR. Yön hareketin
# İŞARETİNDEN geliyor (1B-A/1B-B sözleşmesi) ve bu satır o sözleşmenin
# ÜÇÜNCÜ yönünü — GİRİŞİ — ölçüyor: pozitif bir hareketin geri alınması.
# =========================================================================
geri = urun_ac('İade Geri Alma Ürünü')
alis([kalem(geri, 4, 'LOT-G1', YAKIN)])
alis([kalem(geri, 4, 'LOT-G2', UZAK)])
geri_satis = satis([kalem(geri, 6, fiyat=20)])
assert partiler(geri) == {'LOT-G1': Decimal('0'), 'LOT-G2': Decimal('2')}
geri_iade = iade([kalem(geri, 5, fiyat=20)], kaynak=geri_satis['id'])
assert hareketler('returns', geri_iade['id']) == [
    ('LOT-G2', Decimal('2')),
    ('LOT-G1', Decimal('3')),
], hareketler('returns', geri_iade['id'])
assert partiler(geri) == {'LOT-G1': Decimal('3'), 'LOT-G2': Decimal('4')}

assert client.delete(f"/api/workflow/sale_return/{geri_iade['id']}",
                     headers=baslik).status_code in (200, 204)
assert partiler(geri) == {'LOT-G1': Decimal('0'), 'LOT-G2': Decimal('2')}
# GERİ ALMA KAPASİTEYİ DE GERİ VERİR: aynı iade yeniden kesilebiliyor.
tekrar = iade([kalem(geri, 5, fiyat=20)], kaynak=geri_satis['id'])
assert hareketler('returns', tekrar['id']) == [
    ('LOT-G2', Decimal('2')),
    ('LOT-G1', Decimal('3')),
], hareketler('returns', tekrar['id'])

# GÜNCELLEME AYNI YOLDAN GEÇİYOR: önce geri al, sonra YENİDEN dağıt. Sıra
# ters olsaydı belge KENDİ iadesini "önceden iade edilmiş" sayar ve 422
# alırdı — güncelleme kendi kendini reddederdi.
ok(client.put(f"/api/workflow/sale_return/{tekrar['id']}", headers=baslik, json={
    'entity_id': musteri, 'document_date': '2026-09-12', 'status': 'completed',
    'warehouse_id': depo_a, 'source_type': 'order', 'source_id': geri_satis['id'],
    'items': [kalem(geri, 2, fiyat=20)]}))
assert hareketler('returns', tekrar['id']) == [
    ('LOT-G2', Decimal('2')),
], hareketler('returns', tekrar['id'])
assert partiler(geri) == {'LOT-G1': Decimal('0'), 'LOT-G2': Decimal('4')}

# =========================================================================
# 6. GERİ ALINACAK MAL YOKSA 409 — SESSİZ EKSİYE DÜŞME YOK
#
# İade partiyi büyüttü, ARDINDAN o mal satıldı. İadeyi silmek partiyi eksiye
# düşürürdü; `_parti_geri_al`ın koruması POZİTİF hareketlerde de ısırıyor ve
# bu, 1B-B'nin "kapı negatifte KAPSAM DIŞIDIR" cümlesinin ÖTEKİ yarısıdır.
# =========================================================================
kilit = urun_ac('İade Kilit Ürünü')
alis([kalem(kilit, 3, 'LOT-K', UZAK)])
kilit_satis = satis([kalem(kilit, 3, fiyat=20)])
kilit_iade = iade([kalem(kilit, 3, fiyat=20)], kaynak=kilit_satis['id'])
assert partiler(kilit) == {'LOT-K': Decimal('3')}
satis([kalem(kilit, 3, fiyat=20)])
assert partiler(kilit) == {'LOT-K': Decimal('0')}
engellendi = client.delete(f"/api/workflow/sale_return/{kilit_iade['id']}",
                           headers=baslik)
assert engellendi.status_code == 409, (engellendi.status_code, engellendi.text)
assert engellendi.json()['detail']['code'] == 'LOT_MIKTARI_EKSIYE_DUSER', engellendi.text
assert partiler(kilit) == {'LOT-K': Decimal('0')}

# =========================================================================
# 7. DEPO: İADE BELGESİNİN DEPOSUNA GİRER, SATIŞINKİNE DEĞİL
#
# Stok `adjust_warehouse_stock` ile iade belgesinin deposuna yazılıyor; parti
# başka bir depoya açılsaydı mal bir depoda, defter başka bir depoda olurdu.
# Parti KODU ve SKT ise KAYNAK partiden KOPYALANIYOR — aynı mal, aynı SKT.
# =========================================================================
depolu = urun_ac('İade Depolu Ürün')
alis([kalem(depolu, 5, 'LOT-D', UZAK)], depo=depo_a)
depolu_satis = satis([kalem(depolu, 4, fiyat=20)], depo=depo_a)
assert partiler(depolu, depo=depo_a) == {'LOT-D': Decimal('1')}
iade([kalem(depolu, 3, fiyat=20)], kaynak=depolu_satis['id'], depo=depo_b)
assert partiler(depolu, depo=depo_a) == {'LOT-D': Decimal('1')}
assert partiler(depolu, depo=depo_b) == {'LOT-D': Decimal('3')}
with SessionLocal() as db:
    skt_b = db.execute(text(
        "SELECT expiry_date FROM product_lots WHERE company_id=:cid "
        "AND product_id=:pid AND warehouse_id=:wid AND lot_code='LOT-D'"
    ), {'cid': cid, 'pid': depolu, 'wid': depo_b}).scalar_one()
assert str(skt_b)[:10] == UZAK, skt_b

# =========================================================================
# 8. KAYNAK BAĞI: BAŞKA CARİNİN SATIŞI İADEYE KAYNAK OLAMAZ
#
# `validate_return_reference` ısırıyor ve iade PARTİYE HİÇ ULAŞMIYOR. Kapı
# 1B-E'nin GETİRDİĞİ bir şey değil; ölçülmesinin sebebi, iadenin ARTIK o
# kaynağın hareketlerini OKUYOR olmasıdır: bağ gevşerse başka bir belgenin
# partisi bu iadeyle şişerdi.
# =========================================================================
capraz = urun_ac('İade Çapraz Ürün')
alis([kalem(capraz, 4, 'LOT-C', UZAK)])
capraz_satis = satis([kalem(capraz, 2, fiyat=20)], cari=oteki)
assert partiler(capraz) == {'LOT-C': Decimal('2')}
reddedildi = iade_istegi([kalem(capraz, 1, fiyat=20)], kaynak=capraz_satis['id'])
assert reddedildi.status_code == 409, (reddedildi.status_code, reddedildi.text)
assert partiler(capraz) == {'LOT-C': Decimal('2')}

# =========================================================================
# 9. TASLAK İADE DEFTERE DOKUNMAZ
#
# Geri verme `payload.status in STOCK_STATUSES` dalının İÇİNDEDİR. Dışına
# çıksaydı taslak bir iade partiyi büyütür, stoğu büyütmezdi.
# =========================================================================
taslak_urun = urun_ac('İade Taslak Ürünü')
alis([kalem(taslak_urun, 4, 'LOT-TS', UZAK)])
taslak_satis = satis([kalem(taslak_urun, 3, fiyat=20)])
taslak = iade([kalem(taslak_urun, 2, fiyat=20)], kaynak=taslak_satis['id'],
              status='draft')
assert hareketler('returns', taslak['id']) == []
assert partiler(taslak_urun) == {'LOT-TS': Decimal('1')}

print('1B-E DAVRANIŞ TURU TAMAM')
'''
