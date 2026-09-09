"""LOT-SUZ YAZICILAR (FAZ 1B-H) — PARTİ DEFTERİ AÇIKSA PARTİSİZ YAZILAMAZ.

Konu: `app/parti_defteri.py` (`_lotsuz_yazmayi_reddet`, `_parti_ayarla`),
`app/routers/imports.py` (`POST /api/imports/products/excel`),
`app/routers/products.py` (`create`, `update_product`, `bulk_stock`),
`app/parti_mutabakat.py` (`BOS_CIFT` elemesi).
GÖÇ YOKTUR: bu dilim tek bir sütun eklemez; iki şema alanı (`ProductCreate.
lot_code`, Excel'in `Parti Kodu` sütunu) ve bir kapı getirir.

--- BU DOSYA HANGİ CÜMLEYİ SAVUNUYOR --------------------------------------

1B-G `docs/PARTI_MUTABAKAT.md` §4a'da BEŞ yazıcıyı ADIYLA saydı ve KAPSAM
DIŞINDA bıraktı. O liste bir borçtu: beşi de `warehouse_stocks`a yazıyor,
hiçbiri deftere dokunmuyordu — yani parti takipli bir üründe herhangi biri
`SAPMA` ÜRETEBİLİRDİ ve mutabakat onu ancak ERTESİ GÜN gösterirdi.

Kural TEK CÜMLEDİR ve bu dosyanın tamamı onu ölçer:

    Lot-suz yazmak TASARIMDIR — AMA YALNIZ defter o (ürün, depo) çifti için
    KAPALIYKEN. Satır varsa yazıcı ya PARTİYİ SORAR ya 409 ile REDDEDER.

--- NEDEN HER YAZICI AYNI CEVABI VERMİYOR ---------------------------------

Karar YAZICI BAŞINADIR ve üslup değil, o yolun NE OLDUĞUDUR:

  * EXCEL İÇE AKTARMA ve ÜRÜN AÇILIŞI **SORAR**. İkisi de bir GİRİŞTİR: mal
    depoya YENİ giriyor ve hangi partiden girdiği SORULABİLİR bir olgudur.
    İsteğe bağlı bir alan (`Parti Kodu` sütunu / `lot_code`) eklemek, bugün
    çalışan partisiz akışı KIRMADAN cevabı mümkün kılar.

  * ÜRÜN KARTI STOK DÜZENLEMESİ ve TOPLU STOK YAZIMI **REDDEDER**. İkisi de
    bir DÜZELTMEDİR, bir mal hareketi değil: operatör bir sayıyı elle
    değiştiriyor. Parti takipli bir üründe o sayı hangi partiye ait olurdu
    sorusunun cevabı YOKTUR ve uydurulamaz. Doğru yol ZATEN VAR — `POST
    /api/products/{id}/stock` (1B-C) parti kodunu SORAR ve defteri yazar.
    Yani red bir yeteneği KALDIRMIYOR, cevaplanabilir olana yönlendiriyor.

Bu ayrım ölçülmeden yazılmadı: dördüne de "sor" demek, toplu stok yazımına
ürün başına parti kodu taşıtmak demekti (o uç TEK bir değeri onlarca ürüne
basar — ürün başına kod alan bir gövde, ucun ne olduğunu değiştirirdi).
Dördüne de "reddet" demek ise Excel ile açılış yapan bir firmanın parti
takibine HİÇ geçememesi demekti: ürünü partili açacak yol kalmazdı.

--- MUTASYONLAR ADIYLA KIRMIZI --------------------------------------------

Ölçüldü (CPython 3.12; kaynak yerinde değiştirildi, dosya sürüldü, geri
alındı). Bir kapının ADI, yakaladığı şeyi söylemek ZORUNDADIR:

  1. `_lotsuz_yazmayi_reddet`in gövdesi erken `return`e çevrildi (RED KALKTI)
     -> `test_1b_h_DAVRANIS` kırmızı (üç ayrı 409 beklentisi birden düşer)
        ve `test_dort_yazicinin_KARARI_ADIYLA` YEŞİL KALIR — çağrı duruyor,
        gövde boşaldı. İkisinin AYRI durması bu yüzden zorunlu: statik kapı
        çağrıyı, davranış kapısı REDDİN KENDİSİNİ ölçer.

  2. `_parti_kalemi` çağrısı `imports.py`nin GÜNCELLEME dalından silindi
     (İÇE AKTARMA DEFTERİ ATLIYOR)
     -> `test_1b_h_DAVRANIS` VE `test_dort_yazicinin_KARARI_ADIYLA` İKİSİ
        BİRDEN kırmızı. Davranış: aynı kodla ikinci dosya stoğu 40'a çeker
        ama parti 15'te kalır ve çift `SAPMA`ya düşer. Statik: iki daldan
        yalnız BİRİ kaldı ve çağrı sayısı 2 değil 1'dir.

  3. `bos_cift_mi` `kova_sec`in ÖNÜNE alındı (SIRA BOZULDU)
     -> YALNIZ `test_bos_cift_ELEMESI_kova_secimden_SONRA` (statik) kırmızı.
        DAVRANIŞ KAPISI BU MUTASYONU TUTMAZ ve tutmadığı ÖLÇÜLDÜ, umut
        edilmedi: bugünkü yüklem `stok == 0` TAM EŞİTLİĞİDİR, yani eksi
        bakiyeli çift (`u6`, stok -3) sıra ters olsa da elenmez ve davranış
        YEŞİL kalır.

        SIRA YİNE DE ZORUNLUDUR ve statik kapı bu yüzden var: yüklem bir gün
        `not stok` ya da `stok <= 0` diye gevşerse — ki ikisi de "boş" gibi
        okunur — sıra TEK savunma olurdu ve o gün eksi bakiye rapordan
        SESSİZCE düşerdi. Kapı, henüz kırmızı olmayan bir kusurun ÖN
        KOŞULUNU çiviliyor; davranışla ölçülemeyeceği için STATİK.

  4. `bos_cift_mi`den `stok == 0` koşulu DÜŞÜRÜLDÜ (yalnız satır sayısına
     bakıldı)
     -> `test_A_dan_F_ye_MUTLU_YOLLAR_SAPMA_URETMIYOR` (1B-G) kırmızı: MALI
        OLAN ama partisiz her çift de "boş" sayılıp rapordan DÜŞERDİ. Eleme o
        gün gürültüyü değil, raporun VAR OLMA SEBEBİNİ silerdi — §4'ün
        partisiz yolları görünmez olurdu.

        KOMŞU DOSYANIN KIRMIZI OLMASI BU DİLİMİN KAPISIDIR: 1B-G'nin
        `lotsuz_ciftler` bölümü beş partisiz yolu ADIYLA sayıyor ve 1B-H o
        listeyi ELEMEMEK zorunda. Kapıyı buraya kopyalamak, aynı olguyu iki
        yerden sormak olurdu.

  5. `_parti_takipli_mi`nin yüklemi `quantity>0` ile daraltıldı
     -> `test_lotsuz_red_SATIR_SAYISINA_bakiyor_TOPLAMA_degil` kırmızı
        (statik). Davranış kapısı bu mutasyonu TEK BAŞINA tutmaz ve tutmadığı
        ÖLÇÜLDÜ: tükenmiş partili bir ürüne lot-suz yazma senaryosu davranış
        bölümünde YOK. Kapının statik durmasının sebebi budur.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
DEFTER = BACKEND / "app" / "parti_defteri.py"
URUNLER = BACKEND / "app" / "routers" / "products.py"
ICE_AKTARMA = BACKEND / "app" / "routers" / "imports.py"
MUTABAKAT = BACKEND / "app" / "parti_mutabakat.py"


def _govde(kaynak: str, ad: str):
    for dugum in ast.walk(ast.parse(kaynak)):
        if isinstance(dugum, (ast.FunctionDef, ast.AsyncFunctionDef)) and dugum.name == ad:
            return dugum
    raise AssertionError(f"fonksiyon YOK: {ad}")


def _cagrilar(dugum: ast.AST) -> set[str]:
    adlar: set[str] = set()
    for alt in ast.walk(dugum):
        if isinstance(alt, ast.Call):
            if isinstance(alt.func, ast.Name):
                adlar.add(alt.func.id)
            elif isinstance(alt.func, ast.Attribute):
                adlar.add(alt.func.attr)
    return adlar


# --------------------------------------------------------------- statik ---

def test_red_TEK_KOPYA_ve_defterde() -> None:
    """Red METNİ ve KODU `app/parti_defteri.py`de, çağıranlarda DEĞİL.

    Dört yazıcı aynı cümleyi kursaydı dördü ayrışabilirdi ve o gün "aynı
    kusur neden iki farklı hata veriyor" sorusu SORULAMAZDI. Ayrıca yüklem
    `product_lots`u OKUR: çağıranın içine yazmak, 1B-A'nın tablo adını ANAN
    dosyalar kümesini `imports.py` ile GENİŞLETİRDİ.
    """
    kaynak = DEFTER.read_text(encoding="utf-8")
    assert "LOT_TAKIPLI_URUN_LOTSUZ_YAZILAMAZ" in kaynak
    for yol in (URUNLER, ICE_AKTARMA):
        metin = yol.read_text(encoding="utf-8")
        kod = "\n".join(
            s for s in metin.splitlines() if not s.strip().startswith("#")
        )
        assert '"LOT_TAKIPLI_URUN_LOTSUZ_YAZILAMAZ"' not in kod, (
            f"{yol.name} hata kodunu KENDİ yazıyor; sabit defterde durmalı"
        )


def test_lotsuz_red_SATIR_SAYISINA_bakiyor_TOPLAMA_degil() -> None:
    """Yüklem `kova_sec`inki ile AYNI: satır VAR MI, toplam > 0 MI DEĞİL.

    Tükenmiş bir parti (`quantity=0`, satır DURUYOR) o ürünün parti TAKİPLİ
    olduğunun kanıtıdır. "Toplam > 0" deseydik, defteri sonuna kadar
    tüketilmiş bir ürüne lot-suz yazmak SERBEST kalırdı ve o yazma çifti
    doğrudan `SAPMA`ya iterdi — kapı, savunması gereken anda açılırdı.
    """
    govde = _govde(DEFTER.read_text(encoding="utf-8"), "_parti_takipli_mi")
    # BELGE METNİ HARİÇ: docstring `COUNT(*)`ı ANMAK zorundadır (ayrımın
    # gerekçesi orada) ve anmak, çağırmak DEĞİLDİR — fark metinde değil
    # AST'DEDİR. `test_uc_KURALI_CAGIRIYOR_YENIDEN_YAZMIYOR`un aynı ayrımı.
    if ast.get_docstring(govde):
        govde.body = govde.body[1:]
    metin = ast.unparse(govde)
    assert "SELECT 1" in metin, "varlık sorgusu bir sayı OKUMAMALI"
    buyuk = metin.upper()
    assert "SUM(" not in buyuk and "COUNT(" not in buyuk, (
        "yüklem TOPLAMA bakıyor; tükenmiş parti kapıyı sessizce AÇARDI"
    )
    assert "QUANTITY" not in buyuk, (
        "yüklem MİKTARI süzüyor; tükenmiş parti kapıyı sessizce AÇARDI"
    )
    assert "warehouse_id" in metin, "yüklem DEPO başına olmalı (0073 tekilliği)"


def test_dort_yazicinin_KARARI_ADIYLA() -> None:
    """Dördü de defteri ANIYOR; ikisi SORUYOR, ikisi REDDEDİYOR.

    Bir yazıcının defteri HİÇ anmaması, 1B-H öncesindeki hâlidir: bu kapı
    tam olarak o geri dönüşü tutar.
    """
    urun = URUNLER.read_text(encoding="utf-8")
    ice = ICE_AKTARMA.read_text(encoding="utf-8")

    # SORAN İKİSİ: parti AÇABİLİR.
    assert "_parti_ac" in _cagrilar(_govde(urun, "create")), (
        "ürün açılışı parti AÇMIYOR"
    )
    assert "_parti_ayarla" in _cagrilar(_govde(ice, "_parti_kalemi")), (
        "Excel içe aktarma parti YAZMIYOR"
    )
    # İKİ DAL DA (352 güncelleme / 360 ekleme) defteri çağırır. Yalnız biri
    # bağlansaydı öteki dal sessizce partisiz yazmaya devam ederdi.
    assert len(
        [d for d in ast.walk(_govde(ice, "import_products"))
         if isinstance(d, ast.Call) and isinstance(d.func, ast.Name)
         and d.func.id == "_parti_kalemi"]
    ) == 2, "Excel içe aktarmanın İKİ dalı da deftere bağlı DEĞİL"

    # REDDEDEN İKİSİ: kapıyı ÇAĞIRIR.
    for ad in ("update_product", "bulk_stock"):
        assert "_lotsuz_yazmayi_reddet" in _cagrilar(_govde(urun, ad)), (
            f"{ad} lot-suz yazmayı REDDETMİYOR"
        )

    # AÇILIŞ REDDETMEZ ve ETMEMELİ: ürün bu istekte DOĞUYOR, defterinde satır
    # BULUNAMAZ. Kapıyı oraya koymak, hiçbir zaman doğru olamayacak bir
    # yüklemi sorgulamak olurdu.
    assert "_lotsuz_yazmayi_reddet" not in _cagrilar(_govde(urun, "create"))


def test_imports_defteri_CAGIRIYOR_ve_KAPALI_KUMEDE() -> None:
    """`imports.py` defteri İTHAL EDİYOR ve 1B-A'nın kapalı kümesinde.

    ÜÇÜNCÜ EKSENİN (CAGIRANLAR) canlı örneği: `imports.py` `product_lots`
    literalini HİÇ taşımıyor, yani ilk iki eksen (yazıcı + tablo adını
    ananlar) onu GÖRMEZ. Yalnız onlar olsaydı bu dilim defteri sessizce
    genişletirdi.
    """
    kaynak = ICE_AKTARMA.read_text(encoding="utf-8")
    assert "product_lots" not in kaynak, (
        "`imports.py` tablo adını ANIYOR; defteri ÇAĞIRMALI, yazmamalı"
    )
    ithal = {
        ad.name
        for dugum in ast.walk(ast.parse(kaynak))
        if isinstance(dugum, ast.ImportFrom)
        and (dugum.module or "").split(".")[-1] == "parti_defteri"
        for ad in dugum.names
    }
    assert ithal == {"_lotsuz_yazmayi_reddet", "_parti_ayarla"}, ithal

    from tests.test_1b_a_alis_lot import CAGIRANLAR

    assert "app/routers/imports.py" in CAGIRANLAR
    assert len(CAGIRANLAR) == 7, (
        f"çağıran kümesi 7 olmalı (1B-H `imports.py`yi ekledi): "
        f"{sorted(CAGIRANLAR)}"
    )


def test_isaret_kurali_TEK_KOPYA() -> None:
    """`_ayarlama_partisi` kuralı YENİDEN YAZMIYOR, deftere DEVREDİYOR.

    Gövde 1B-C'de tek çağıranlıydı; 1B-H ikinci bir çağıran getirdi. İki
    kopya ayrıştığı gün aynı parti kodu iki uçtan iki FARKLI satır üretirdi
    ve geri çağırma kaydı hangisinin doğru olduğunu SÖYLEYEMEZDİ.
    """
    govde = _govde(URUNLER.read_text(encoding="utf-8"), "_ayarlama_partisi")
    cagrilar = _cagrilar(govde)
    assert "_parti_ayarla" in cagrilar
    assert not ({"_parti_ac", "_parti_bul", "_parti_dus"} & cagrilar), (
        "işaret/açma/düşme kuralı uçta TEKRARLANMIŞ: " + str(sorted(cagrilar))
    )


def test_bos_cift_RAPORDAN_DUSER_ama_SAYILIR() -> None:
    """Eleme SESSİZ DEĞİL: düşen sayı `bos_ciftler` ile gövdede taşınır.

    Sessizce atmak, `total`ın deponun çift sayısından neden küçük olduğunu
    SORULAMAZ yapardı — ve "rapor eksik mi, yoksa öyle mi" sorusunu koda
    bakmadan cevaplayamayan bir operatör bırakırdı.
    """
    assert "bos_ciftler" in MUTABAKAT.read_text(encoding="utf-8")
    govde = _govde(URUNLER.read_text(encoding="utf-8"), "parti_mutabakat_raporu")
    assert "bos_ciftler" in ast.unparse(govde), "uç eleme sayısını DÖNDÜRMÜYOR"


def test_kovalar_UC_KALDI_dorduncu_kova_YOK() -> None:
    """Boş çift bir KOVA değil, bir YOKLUK. `sayimlar` hâlâ ÜÇ anahtarlı.

    Dördüncü bir kova, operatörü dört sayıyı toplayıp "hangisi benim
    ürünlerim" diye sormak zorunda bırakırdı ve kovaların ÜÇÜNÜN DE MAL
    hakkında konuştuğu olgusunu bozardı.
    """
    from app.parti_mutabakat import BOS_CIFT, KOVALAR

    assert len(KOVALAR) == 3, KOVALAR
    assert BOS_CIFT not in KOVALAR


def test_bos_cift_ELEMESI_kova_secimden_SONRA() -> None:
    """Eleme `kova_sec`TEN SONRA çağrılır; ÖNCE değil.

    Sıra 1B-G'nin `SAPMA`-önce kuralının aynısıdır: negatif stoklu partisiz
    bir çift `SAPMA`dır (`0 > stok`) ve eleme öne alınsaydı o çift "boş" diye
    rapordan DÜŞERDİ — eksi bakiye bir kova etiketiyle değil, RAPORDAN
    SİLİNEREK gömülürdü.
    """
    govde = _govde(MUTABAKAT.read_text(encoding="utf-8"), "mutabakat")
    sira = [
        dugum.func.id
        for dugum in ast.walk(govde)
        if isinstance(dugum, ast.Call) and isinstance(dugum.func, ast.Name)
        and dugum.func.id in {"kova_sec", "bos_cift_mi"}
    ]
    assert sira == ["kova_sec", "bos_cift_mi"], sira
    # Eleme dalı kovanın CEVABINI da okur: `LOTSUZ_TASARIM` olmayan hiçbir
    # çift elenemez ve bu, sıranın metinden okunabilir olmasıdır.
    metin = ast.unparse(govde)
    assert "kova == LOTSUZ_TASARIM and bos_cift_mi" in metin, metin


# ------------------------------------------------------------- davranış ---

def test_1b_h_DAVRANIS(tmp_path: Path) -> None:
    """Dört yazıcı GERÇEK ŞEMADA ölçülür; sonunda `SAPMA` yalnız eksi bakiye.

    Alt süreçte koşuyor (1B-A..1B-G ikizlerinin kalıbı): `app.main` açılışta
    alembic'i sürüyor ve `app.config.Settings` modül düzeyinde TEK KOPYA
    olduğu için `DATABASE_URL` süreç İÇİNDE değiştirilemez.
    """
    veritabani = tmp_path / "1b-h-lotsuz.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{veritabani.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    tamamlandi = subprocess.run(
        [sys.executable, "-c", _DAVRANIS], cwd=BACKEND, env=env,
        capture_output=True, text=True, timeout=900,
    )
    assert tamamlandi.returncode == 0, tamamlandi.stdout + "\n" + tamamlandi.stderr


_DAVRANIS = r'''
import io
from uuid import uuid4

import openpyxl
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
KOSU = uuid4().hex[:8]


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


baslik, cid = giris('admin', 'admin123', 'LotsuzYazici!123')
ok(client.put('/api/company-settings', headers=baslik,
              json={'negative_stock_policy': 'allow', 'credit_limit_policy': 'block'}))

depo_a = ok(client.get('/api/warehouses', headers=baslik))[0]['id']
tedarikci = ok(client.post('/api/suppliers', headers=baslik,
                           json={'name': 'Lotsuz Tedarikci ' + KOSU}))['id']
musteri = ok(client.post('/api/customers', headers=baslik,
                         json={'name': 'Lotsuz Musteri ' + KOSU}))['id']


def urun_ac(ad, stok=0, kod=None):
    govde = {'name': ad + ' ' + KOSU, 'purchase_price': 10, 'sale_price': 20,
             'vat_rate': 20, 'stock': stok, 'unit': 'KG'}
    if kod is not None:
        govde['lot_code'] = kod
    return client.post('/api/products', headers=baslik, json=govde)


def partiler(pid):
    return ok(client.get('/api/products/' + str(pid) + '/lots',
                         headers=baslik))['lots']


def lot_toplami(pid):
    return sum(float(p['quantity']) for p in partiler(pid))


def stok(pid):
    govde = ok(client.get('/api/products/' + str(pid), headers=baslik))
    return float(govde['product']['stock'])


def rapor(**sorgu):
    return ok(client.get('/api/products/lots/mutabakat', headers=baslik, params=sorgu))


def kovalar():
    ciftler, offset = {}, 0
    while True:
        cev = rapor(limit=1000, offset=offset)
        for s in cev['items']:
            ciftler[(s['product_id'], s['warehouse_id'])] = s['kova']
        if not cev.get('has_more'):
            break
        offset += len(cev['items'])
    return ciftler


BASLIK_KODLU = ['Urun Adi', 'Urun Kodu', 'Stok', 'Parti Kodu']
BASLIK_KODSUZ = ['Urun Adi', 'Urun Kodu', 'Stok']


def excel(satirlar, basliklar):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(basliklar)
    for satir in satirlar:
        ws.append(satir)
    tampon = io.BytesIO()
    wb.save(tampon)
    return client.post(
        '/api/imports/products/excel', headers=baslik,
        files={'file': ('urunler.xlsx', tampon.getvalue(),
                        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})


def urun_bul(ad):
    for u in ok(client.get('/api/products', headers=baslik, params={'q': ad})):
        if u['name'] == ad:
            return u['id']
    raise AssertionError('urun bulunamadi: ' + ad)


# =========================================================================
# (1) URUN ACILISI: `lot_code` ile acilis stogu PARTI ACAR
# =========================================================================
u1 = ok(urun_ac('H1 Acilis Partili', stok=25, kod='H1-LOT'))['id']
p1 = partiler(u1)
assert len(p1) == 1, p1
assert p1[0]['lot_code'] == 'H1-LOT', p1
assert float(p1[0]['quantity']) == 25.0, p1
assert kovalar()[(u1, depo_a)] == 'ESIT', kovalar()[(u1, depo_a)]

# KOD YOKSA 1B-H ONCESI GIBI: hicbir parti satiri DOGMAZ.
u1b = ok(urun_ac('H1b Acilis Partisiz', stok=7))['id']
assert partiler(u1b) == [], partiler(u1b)

# SIFIR MIKTARLI ACILIS PARTI ACMAZ: hic mal girmemis bir partiyi defterde
# VAR gostermek, o urune sonraki her lot-suz yazmayi 409 yapardi.
u1c = ok(urun_ac('H1c Acilis Sifir', stok=0, kod='H1C-LOT'))['id']
assert partiler(u1c) == [], partiler(u1c)


# =========================================================================
# (2) URUN KARTI STOK DUZENLEMESI: PARTILI URUNDE 409
# =========================================================================
def guncelle(pid, ad, yeni_stok):
    return client.put('/api/products/' + str(pid), headers=baslik, json={
        'name': ad + ' ' + KOSU, 'purchase_price': 10, 'sale_price': 20,
        'vat_rate': 20, 'stock': yeni_stok, 'unit': 'KG', 'active': True})


red = guncelle(u1, 'H1 Acilis Partili', 40)
assert red.status_code == 409, (red.status_code, red.text)
assert red.json()['detail']['code'] == 'LOT_TAKIPLI_URUN_LOTSUZ_YAZILAMAZ', red.text
# STOK KIMILDAMADI: red COMMIT'ten once dustu ve tum istek geri alindi.
assert stok(u1) == 25.0, stok(u1)
assert lot_toplami(u1) == 25.0, partiler(u1)

# PARTISIZ URUNDE AYNI ISTEK GECER: kapi CALISAN akisi kirmiyor.
gecer = guncelle(u1b, 'H1b Acilis Partisiz', 12)
assert gecer.status_code < 300, gecer.text
assert stok(u1b) == 12.0, stok(u1b)

# STOK DEGISMEYEN BIR KAYDETME PARTILI URUNDE DE CALISIR: red `if diff`
# ICINDE. Disari alinsaydi partili bir urunun ADI degistirilemezdi.
ad_degis = guncelle(u1, 'H1 Acilis Partili YENI', 25)
assert ad_degis.status_code < 300, ad_degis.text


# =========================================================================
# (3) TOPLU STOK YAZIMI: PARTILI URUNDE 409, TUM PARTIYI DUSURUR
# =========================================================================
def toplu(ids, deger, yontem='set'):
    return client.post('/api/products/bulk-stock', headers=baslik, json={
        'method': yontem, 'value': deger, 'movement_date': '2026-09-10',
        'note': 'toplu', 'product_ids': ids, 'warehouse_id': depo_a})


onceki = stok(u1b)
red2 = toplu([u1b, u1], 99)
assert red2.status_code == 409, (red2.status_code, red2.text)
assert red2.json()['detail']['code'] == 'LOT_TAKIPLI_URUN_LOTSUZ_YAZILAMAZ', red2.text
# KISMEN UYGULANMIS BIR SECIM YOK: partisiz urun de yazilmadi (rollback).
assert stok(u1b) == onceki, (stok(u1b), onceki)
assert stok(u1) == 25.0, stok(u1)

# PARTISIZ URUNLERDEN OLUSAN TOPLU YAZIM GECER.
gecer2 = toplu([u1b], 33)
assert gecer2.status_code < 300, gecer2.text
assert stok(u1b) == 33.0, stok(u1b)


# =========================================================================
# (4) EXCEL ICE AKTARMA: `Parti Kodu` sutunu PARTI ACAR
# =========================================================================
AD4 = 'H4 Excel Ekleme ' + KOSU
KOD4 = 'H4-' + KOSU

# EKLEME DALI (imports.py:360 idi): urun YOK, acilis stogu partili dogar.
cev = ok(excel([[AD4, KOD4, 15, 'H4-LOT']], BASLIK_KODLU))
assert cev['inserted'] == 1, cev
u4 = urun_bul(AD4)
assert lot_toplami(u4) == 15.0, partiler(u4)
assert kovalar()[(u4, depo_a)] == 'ESIT', kovalar()[(u4, depo_a)]

# GUNCELLEME DALI (imports.py:352 idi): AYNI kod, stok 15 -> 40, FARK partiye.
cev = ok(excel([[AD4, KOD4, 40, 'H4-LOT']], BASLIK_KODLU))
assert cev['updated'] == 1, cev
assert lot_toplami(u4) == 40.0, partiler(u4)
assert kovalar()[(u4, depo_a)] == 'ESIT', kovalar()[(u4, depo_a)]

# ISARET KARAR VERIR: bir EXCEL AZALTMASI partiden DUSER, eklemez.
cev = ok(excel([[AD4, KOD4, 30, 'H4-LOT']], BASLIK_KODLU))
assert cev['updated'] == 1, cev
assert lot_toplami(u4) == 30.0, partiler(u4)
assert kovalar()[(u4, depo_a)] == 'ESIT', kovalar()[(u4, depo_a)]

# KODSUZ SATIR, PARTILI URUNDE 409 ve TUM DOSYA DUSER.
red3 = excel([[AD4, KOD4, 77]], BASLIK_KODSUZ)
assert red3.status_code == 409, (red3.status_code, red3.text)
ayrinti = red3.json()['detail']
assert ayrinti['code'] == 'LOT_TAKIPLI_URUN_LOTSUZ_YAZILAMAZ', red3.text
# CARE HANGI SATIRI duzeltecegini SOYLER: yuzlerce satirlik bir dosyada
# "bir urunun defteri acik" demek, dosyayi elle taramak demekti.
assert '2.' in ayrinti['message'], ayrinti['message']
assert lot_toplami(u4) == 30.0, partiler(u4)
assert stok(u4) == 30.0, stok(u4)

# KODSUZ SATIR, PARTISIZ URUNDE GECER: 1B-H oncesi davranis AYNEN duruyor.
AD4B = 'H4b Excel Partisiz ' + KOSU
gecer3 = ok(excel([[AD4B, 'H4B-' + KOSU, 8]], BASLIK_KODSUZ))
assert gecer3['inserted'] == 1, gecer3
u4b = urun_bul(AD4B)
assert partiler(u4b) == [], partiler(u4b)

# KODSUZ DOSYA PARTISIZ URUNU GUNCELLEYEBILIR (defter o cift icin KAPALI).
gecer4 = ok(excel([[AD4B, 'H4B-' + KOSU, 20]], BASLIK_KODSUZ))
assert gecer4['updated'] == 1, gecer4
assert stok(u4b) == 20.0, stok(u4b)
assert partiler(u4b) == [], partiler(u4b)


# =========================================================================
# (5) BOS CIFT ELEMESI: OLCULUR, VARSAYILMAZ
# =========================================================================
# Ikinci depo acildiktan SONRA acilan her urun icin o depoda bir BOS cift
# dogar (urun acilisi AKTIF HER depo icin sifirli bir satir yazar).
depo_b = ok(client.post('/api/warehouses', headers=baslik,
                        json={'name': 'Lotsuz B ' + KOSU,
                              'code': 'LZB-' + KOSU[:6]}))['id']
u5 = ok(urun_ac('H5 Iki Depo', stok=5, kod='H5-LOT'))['id']

son = rapor(limit=1000)
assert son['bos_ciftler'] >= 1, son
# BOS CIFT RAPORDA YOK: iki defter de bos, karsilastirilacak sayi YOK.
assert (u5, depo_b) not in kovalar(), kovalar()[(u5, depo_b)]
# DOLU CIFT RAPORDA VAR.
assert kovalar()[(u5, depo_a)] == 'ESIT', kovalar()[(u5, depo_a)]

# NEGATIF STOK + PARTI SATIRI YOK => `SAPMA`, ELENMEZ. Sira `kova_sec`in
# `SAPMA`-once kuraliyla AYNI: eleme once sorulsaydi eksi bakiye "bos" diye
# rapordan DUSERDI.
u6 = ok(urun_ac('H6 Eksi Bakiye'))['id']
ok(client.post('/api/products/' + str(u6) + '/stock', headers=baslik, json={
    'mode': 'add', 'quantity': -3, 'movement_date': '2026-09-10',
    'warehouse_id': depo_a, 'note': 'eksi'}))
assert kovalar()[(u6, depo_a)] == 'SAPMA', kovalar()[(u6, depo_a)]

# TUKENMIS PARTI BOS CIFT DEGIL: satir DURUYOR, `ESIT`tir ve rapordan DUSMEZ.
u7 = ok(urun_ac('H7 Tukenen', stok=4, kod='H7-LOT'))['id']
ok(client.post('/api/orders', headers=baslik, json={
    'entity_id': musteri, 'transaction_date': '2026-09-11',
    'due_date': '2026-09-30', 'warehouse_id': depo_a,
    'items': [{'product_id': u7, 'quantity': 4, 'unit_price': 20,
               'vat_rate': 20}]}))
assert lot_toplami(u7) == 0.0, partiler(u7)
assert stok(u7) == 0.0, stok(u7)
assert kovalar()[(u7, depo_a)] == 'ESIT', kovalar()[(u7, depo_a)]


# =========================================================================
# (6) BIRLESIK CEVAP: SAPMA yalniz EKSI BAKIYEDEN, baska hicbir yerden
# =========================================================================
son = rapor(limit=1000)
sapan = {c for c, k in kovalar().items() if k == 'SAPMA'}
assert sapan == {(u6, depo_a)}, sapan
assert set(son['counts']) == {'ESIT', 'LOTSUZ_TASARIM', 'SAPMA'}, son['counts']
assert son['counts']['SAPMA'] == 1, son['counts']
print('OLCUM_1B_H', son['counts'], 'bos_ciftler', son['bos_ciftler'],
      'total', son['total'])
'''
