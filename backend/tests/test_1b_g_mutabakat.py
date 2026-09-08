"""PARTİ MUTABAKATI (FAZ 1B-G) — İKİ DEFTERİN FARKI ÖLÇÜLÜR, GİZLENMEZ.

Konu: `app/parti_mutabakat.py` (saf sorgu + kova kuralı),
`app/routers/products.py` (`GET /api/products/lots/mutabakat`).
GÖÇ YOKTUR: bu dilim 0067'nin tablosunu, 0073'ün deposunu ve
`warehouse_stocks`u OLDUKLARI GİBİ okur, tek bir sütun eklemez.

--- BU DOSYA HANGİ CÜMLEYİ SAVUNUYOR --------------------------------------

1B-A'dan 1B-F'ye kadar altı dilim, stoğa dokunan yolları TEK TEK partiye
bağladı. Her dilim KENDİ yolunu ölçtü ve hiçbiri şunu ölçemedi: "ALTISI
BİRDEN koştuktan sonra iki defter hâlâ uyuşuyor mu?"

Aradaki fark bir üslup farkı DEĞİLDİR. Altı dilim ayrı ayrı yeşil olabilir ve
BİRLEŞTİKLERİNDE ayrışabilirler — çünkü hiçbiri ötekinin yazdığını okumaz.
Bu dosyanın DAVRANIŞ bölümü tam olarak o boşluğu kapatıyor: A–F'nin MUTLU
yolları TEK bir veritabanında arka arkaya koşuyor ve sonunda TEK bir sayı
soruluyor — `SAPMA == 0`.

--- NEDEN `SAPMA == 0` VE "FARK == 0" DEĞİL -------------------------------

"İki defter her yerde eşit olsun" DİYE ölçseydik kapı DOĞDUĞU GÜN kırmızı
olurdu ve kırmızılığı bir kusuru değil, deponun TASARIMINI gösterirdi:
stoğa partisiz yazan yollar (faaliyet girdileri, kantar farkları, iş emri
parçaları, partisiz transfer/sayım/iade/alış/satış, kaynaksız satış iadesi)
BUGÜN ÇALIŞAN iş akışlarıdır ve 1B-G onları KAPATMIYOR.

Doğru soru bu yüzden "fark var mı" değil, "farkın SEBEBİ BİLİNİYOR mu"dur.
Kapı `LOTSUZ_TASARIM`ı SAYAR ama KIRMIZI YAPMAZ; kırmızı yalnız `SAPMA`dır —
yani parti defteri AÇILMIŞ olduğu hâlde tutmayan çift.

--- MUTASYONLAR ADIYLA KIRMIZI — HANGİSİNİ HANGİSİNİN YAKALADIĞI ÖLÇÜLDÜ ---

Aşağıdaki eşleşme VARSAYILMADI, KOŞULDU (CPython 3.12; kaynak yerinde
değiştirildi, dosyanın TAMAMI sürüldü, sonra geri alındı). Ölçüm gerekliydi
çünkü bir kapının ADI, yakaladığı şeyi söylemek ZORUNDADIR:

  1. `SAPMA` TAMAMEN `LOTSUZ_TASARIM`a çevrildi (GERÇEK birleşme)
     -> `test_SAPMA_LOTSUZa_KARISTIRILAMAZ`,
        `test_parti_VARSA_ve_TOPLAM_TUTMUYORSA_SAPMA`,
        `test_parti_stogu_ASIYORSA_parti_satiri_OLMASA_BILE_SAPMA`
        ve DAVRANIŞ testi — DÖRDÜ BİRDEN kırmızı.

  2. `parti_toplami > stok` dalı SİLİNDİ
     -> `test_parti_stogu_ASIYORSA_parti_satiri_OLMASA_BILE_SAPMA` kırmızı.

     AYNI KAPI, `LOTSUZ_TASARIM` denetimi `SAPMA`nın ÖNÜNE alındığında da
     kırmızıdır ve BÖYLE OLMASI GEREKİR: sıra değişimi YALNIZ o dalı etkiler
     (satır sayısı 0 iken), çünkü satır sayısı > 0 iken `LOTSUZ` dalı zaten
     ateşlemez. "Sırayı boz" ile "dalı sil" AYNI kusurun iki yüzüdür ve tek
     kapı ikisini de tutar — bu ÖLÇÜLDÜ, umut edilmedi.

  3. KİRACI yüklemi `UNION` kollarının HERHANGİ BİRİNDEN düşürüldü
     -> `test_mutabakat_KIRACI_YUKLEMINI_UC_YERDE_de_tasiyor` (statik) VE
        alt süreçteki komşu-firma bölümü (davranış) — İKİSİ BİRDEN kırmızı.

     ÜÇÜNCÜ ESAS (parti toplamı alt sorgusu) düşürüldüğünde YALNIZ STATİK
     kapı kırmızıdır; davranış YEŞİL KALIR. Bu bir kapı zayıflığı DEĞİL,
     ÖLÇÜLMÜŞ bir olgudur: `lt` birleştirmesi kiracıyı `p`den DEVRALIYOR
     (`lt.company_id = p.company_id`), yani oradaki yüklem doğruluk için
     GEREKSİZ, derinlemesine savunma için VARDIR. Statik kapının o esası ayrı
     sayması tam bu yüzden anlamlı: davranışın GÖREMEYECEĞİ bir gevşemeyi
     yalnız o görebilir. Gerekçe burada YAZILI ki, biri o satırı "zaten
     gereksiz" diye sildiğinde neyin kaybolduğu bilinsin.

--- BU DOSYA NEYİ ÖLÇMÜYOR ------------------------------------------------

Partisiz yazan yolların KENDİLERİ bu dilimde partiye BAĞLANMIYOR; onlar
1B-H'nin işidir ve `docs/PARTI_MUTABAKAT.md` hepsini dosya/satır ile ADIYLA
listeler. Burada ölçülen tek şey, o yolların `LOTSUZ_TASARIM` kovasına
düştüğü ve `SAPMA`yı KİRLETMEDİĞİDİR.
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
MUTABAKAT = BACKEND / "app" / "parti_mutabakat.py"
URUNLER = BACKEND / "app" / "routers" / "products.py"

sys.path.insert(0, str(BACKEND))

from app.parti_mutabakat import (  # noqa: E402
    ESIT,
    KOVALAR,
    LOTSUZ_TASARIM,
    SAPMA,
    kova_sec,
)


def _govde(kaynak: str, ad: str) -> ast.FunctionDef:
    for dugum in ast.walk(ast.parse(kaynak)):
        if isinstance(dugum, ast.FunctionDef) and dugum.name == ad:
            return dugum
    raise AssertionError(f"{ad} bulunamadı")


def _d(deger: str) -> Decimal:
    return Decimal(deger)


# ----------------------------------------------------------- kova kuralı ---

def test_kovalar_UCTUR_ve_ADLARI_SABIT() -> None:
    """Kova adları SÖZLEŞMEDİR: uç onları JSON'da AYNEN yayınlıyor.

    Ad değişirse ekran ve rapor sessizce boş kova gösterirdi; sayı yine
    "0 sapma" derdi ve bu, ölçülmemiş olmakla AYNI şey olurdu.
    """
    assert KOVALAR == (ESIT, LOTSUZ_TASARIM, SAPMA)
    assert (ESIT, LOTSUZ_TASARIM, SAPMA) == ("ESIT", "LOTSUZ_TASARIM", "SAPMA")


def test_parti_satiri_YOKSA_LOTSUZ_TASARIM() -> None:
    """Hiç parti açılmamış çift: fark BEKLENENDİR, kusur değil."""
    assert kova_sec(_d("7"), _d("0"), 0) == LOTSUZ_TASARIM
    # Stok da 0 olabilir (ürün var, hareket yok): yine LOTSUZ, yine kusursuz.
    assert kova_sec(_d("0"), _d("0"), 0) == LOTSUZ_TASARIM


def test_TUKENMIS_parti_LOTSUZ_DEGIL_ESITtir() -> None:
    """1B-B partiyi 0'a çeker ve SATIRI SİLMEZ — o satır kanıttır.

    Mekanizma `parti_toplami == 0` olsaydı bu çift `LOTSUZ_TASARIM`a düşerdi
    ve kova "bu çift için hiç parti açılmadı" DERKEN, açılmış VE düzgün
    kapanmış bir partiyi gösterirdi. Kovanın ADI yalan söylerdi.

    Ayrım SATIR SAYISINDADIR: aynı iki sayı (0, 0), satır sayısı 1 iken
    `ESIT`, 0 iken `LOTSUZ_TASARIM`dır.
    """
    assert kova_sec(_d("0"), _d("0"), 1) == ESIT
    assert kova_sec(_d("0"), _d("0"), 0) == LOTSUZ_TASARIM


def test_parti_VARSA_ve_TOPLAM_TUTMUYORSA_SAPMA() -> None:
    """İki yön de sapmadır; biri ötekinden daha tehlikelidir, ikisi de kırmızı."""
    # Stok partiden FAZLA: partisiz bir giriş partili bir çifte karışmış.
    assert kova_sec(_d("9"), _d("5"), 2) == SAPMA
    # Parti stoktan FAZLA: defter, elde OLMAYAN malı VAR gösteriyor.
    assert kova_sec(_d("5"), _d("9"), 2) == SAPMA


def test_SAPMA_LOTSUZa_KARISTIRILAMAZ() -> None:
    """MUTASYON KAPISI: `SAPMA` `LOTSUZ_TASARIM` ile BİRLEŞTİRİLİRSE kırmızı.

    Ölçülen cümle: parti satırı OLAN bir çift, toplam tutmadığında
    `LOTSUZ_TASARIM` DEĞİLDİR. İki kovayı birleştiren (ya da `SAPMA`yı
    `LOTSUZ`a döndüren) her değişiklik burada ADIYLA düşer.
    """
    assert kova_sec(_d("5"), _d("9"), 2) == SAPMA
    assert kova_sec(_d("5"), _d("9"), 2) != LOTSUZ_TASARIM
    assert kova_sec(_d("9"), _d("5"), 1) == SAPMA
    assert kova_sec(_d("9"), _d("5"), 1) != LOTSUZ_TASARIM
    # Ve ters yön: gerçekten partisiz olan çift `SAPMA` diye ETİKETLENMEZ —
    # aksi hâlde kapı, kovaları birleştirmenin ÖTEKİ yönünü kaçırırdı.
    assert kova_sec(_d("7"), _d("0"), 0) == LOTSUZ_TASARIM


def test_parti_stogu_ASIYORSA_parti_satiri_OLMASA_BILE_SAPMA() -> None:
    """MUTASYON KAPISI: `parti_toplami > stok` dalı KOŞULSUZDUR.

    Parti satırı yokken toplam 0'dır ve `0 > stok` ANCAK stok NEGATİFSE
    doğrudur. Negatif stok politikayla serbest bırakılabilir ama "eksi
    bakiye" ile "bu çift bilinçli olarak partisizdir" AYNI CÜMLE DEĞİLDİR.
    Dal `LOTSUZ_TASARIM`ın ARKASINA konsaydı eksi bakiye "tasarım" etiketiyle
    GÖMÜLÜRDÜ; bu kapı tam o gömmeyi ölçüyor.
    """
    assert kova_sec(_d("-3"), _d("0"), 0) == SAPMA
    assert kova_sec(_d("-3"), _d("0"), 0) != LOTSUZ_TASARIM
    # Sınır DAR: sıfır stok + sıfır parti SAPMA DEĞİLDİR (`>`, `>=` değil).
    assert kova_sec(_d("0"), _d("0"), 0) == LOTSUZ_TASARIM


def test_kova_kurali_OLCEGI_DEGIL_DEGERI_okur() -> None:
    """`5` ile `5.0000` aynı sayıdır ve sahte bir `SAPMA` üretmemelidir.

    `money.quantity` her iki tarafı da aynı kuantuma çekiyor; bu kapı o
    çekmenin gerçekten yapıldığını kova katmanından ölçer.
    """
    assert kova_sec(_d("5.0000"), _d("5"), 1) == ESIT


# --------------------------------------------------------------- statik ---

def test_mutabakat_YAZMIYOR_YALNIZ_OKUYOR() -> None:
    """`product_lots`un yazma tekeli `app/parti_defteri.py`dedir.

    Bu modül oraya İKİNCİ bir kapı açsaydı 1B-A'nın AST kapısı kırılırdı ve
    daha kötüsü: mutabakat, ölçtüğü defteri DEĞİŞTİREBİLİRDİ — bir raporun
    yapabileceği en kötü şey, raporladığı olguyu üretmektir.
    """
    kaynak = MUTABAKAT.read_text(encoding="utf-8")
    metinler = [
        dugum.value
        for dugum in ast.walk(ast.parse(kaynak))
        if isinstance(dugum, ast.Constant) and isinstance(dugum.value, str)
    ]
    calistirilabilir = [m for m in metinler if "SELECT" in m.upper()]
    assert calistirilabilir, "sorgu metni bulunamadı — tarayıcı boşa dönmüş olabilir"
    for metin in calistirilabilir:
        buyuk = metin.upper()
        for fiil in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER "):
            assert fiil not in buyuk, f"mutabakat YAZIYOR: {fiil!r} -> {metin!r}"


def test_mutabakat_KIRACI_YUKLEMINI_UC_YERDE_de_tasiyor() -> None:
    """`company_id = :cid` ÜÇ KEZ: iki `UNION` kolu + parti toplamı alt sorgusu.

    Sayı ÇİVİLİ ve gerekçesi şudur: üçünden BİRİ düşerse sorgu hâlâ ÇALIŞIR
    ve hâlâ satır döndürür — yalnız komşu kiracının satırlarını da döndürür.
    Sessiz sızıntı; çökme yok. `LEFT JOIN`ler yüklemi sürücü kümeden DEVRALIR
    (`ws.company_id=p.company_id`) ve o yüzden ayrıca sayılmaz.
    """
    from app.parti_mutabakat import _MUTABAKAT_SQL

    assert len(re.findall(r"company_id\s*=\s*:cid", _MUTABAKAT_SQL)) == 3
    # Devralma da ölçülür: dış birleştirmelerin kiracı bağı BİRLEŞTİRMENİN
    # İÇİNDEDİR. `WHERE`e taşınsaydı dış birleştirme sessizce İÇ birleştirmeye
    # dönerdi ve partisi olmayan çift raporda HİÇ GÖRÜNMEZDİ.
    assert _MUTABAKAT_SQL.count("company_id = p.company_id") == 2


def test_sorgu_DIYALEKT_DALI_TASIMIYOR() -> None:
    """TEK metin, İKİ diyalekt. `FULL OUTER JOIN` de YOK.

    SQLite `FULL OUTER JOIN`i 3.39'dan önce tanımıyor; yazılsaydı kapı
    koştuğu MAKİNEYE göre yeşil olurdu. Diyalekt adı geçen bir dal ise
    iki diyalektin iki farklı kod yolundan geçmesi demekti — PostgreSQL
    ikizi o gün SQLite'ın ölçtüğü şeyi ölçmeyi bırakırdı.
    """
    from app.parti_mutabakat import _MUTABAKAT_SQL

    buyuk = _MUTABAKAT_SQL.upper()
    assert "FULL OUTER" not in buyuk and "FULL JOIN" not in buyuk
    assert "UNION ALL" not in buyuk, "UNION ALL aynı çifti İKİ satır yapardı"
    assert "UNION" in buyuk
    kaynak = MUTABAKAT.read_text(encoding="utf-8")
    kod = "\n".join(
        satir for satir in kaynak.splitlines() if not satir.strip().startswith("#")
    )
    agac = ast.parse(kod)
    for dugum in ast.walk(agac):
        if isinstance(dugum, ast.Attribute) and dugum.attr in {"name", "dialect"}:
            raise AssertionError(f"diyalekt dalı: {ast.dump(dugum)}")


def test_kova_kurali_SQLde_TEKRARLANMADI() -> None:
    """Kural Python'da ve YALNIZ orada. SQL'de `CASE` YOKTUR.

    İkinci bir kopya (SQL `CASE`i) sayımı ucuzlatırdı ve tam bu yüzden
    reddedildi: iki kopya ayrıştığı gün rapor KENDİ SAYACIYLA çelişirdi ve
    hangisinin doğru olduğu SORULAMAZDI. Bedel (`sayimlar` için tam tarama)
    modül başlığında YAZILI, gizli değil.
    """
    from app.parti_mutabakat import _MUTABAKAT_SQL

    buyuk = _MUTABAKAT_SQL.upper()
    assert "CASE" not in buyuk
    for kova in KOVALAR:
        assert kova not in _MUTABAKAT_SQL


def test_uc_KURALI_CAGIRIYOR_YENIDEN_YAZMIYOR() -> None:
    """Yönlendirici kovayı KENDİ hesaplamıyor; modülü çağırıyor.

    Uç kendi `if`ini yazsaydı kural iki dosyada olurdu — bu deponun parti
    işinin tamamı, aynı olgunun iki kaynaktan sorulmasına karşı kurulu.
    """
    fonksiyon = _govde(URUNLER.read_text(encoding="utf-8"), "parti_mutabakat_raporu")
    cagrilar = {
        dugum.func.id
        for dugum in ast.walk(fonksiyon)
        if isinstance(dugum, ast.Call) and isinstance(dugum.func, ast.Name)
    }
    assert "parti_mutabakat" in cagrilar
    # DOC-STRING HARİÇ: gövde kovaların ADINI bir DEĞER olarak taşımamalı.
    # Belge metni onları ANMAK zorundadır (uç ne döndürdüğünü söylemeli) ve
    # anmak, hesaplamak DEĞİLDİR — ayrım metinde değil AST'DEDİR.
    govde = fonksiyon.body[1:] if ast.get_docstring(fonksiyon) else fonksiyon.body
    sabitler = {
        dugum.value
        for govde_dugumu in govde
        for dugum in ast.walk(govde_dugumu)
        if isinstance(dugum, ast.Constant) and isinstance(dugum.value, str)
    }
    assert not (sabitler & set(KOVALAR)), (
        f"uç kova adını KENDİ yazıyor: {sorted(sabitler & set(KOVALAR))}"
    )
    # Kovayı seçen karşılaştırma da uçta OLMAMALI: kural tek dosyada durur.
    karsilastirmalar = [
        dugum
        for govde_dugumu in govde
        for dugum in ast.walk(govde_dugumu)
        if isinstance(dugum, ast.Compare)
    ]
    assert not karsilastirmalar, (
        "uç kova kuralını yeniden kuruyor olabilir; karşılaştırma "
        f"`app/parti_mutabakat.py`ye ait: {len(karsilastirmalar)} adet"
    )


def test_uc_LOTS_YOLUNDAN_ONCE_yaziliyor() -> None:
    """Sıra bilinçli: `/lots/mutabakat`, `/{product_id}/lots`tan ÖNCE.

    Bugün çakışma yok; sıra, ileride SERBEST ikinci parçalı iki parçalı bir
    desen eklenirse onun bu ucu YUTMASINI engeller. FastAPI ilk EŞLEŞENİ
    seçer ve o gün rapor BAŞKA BİR UCUN cevabını verirdi.
    """
    kaynak = URUNLER.read_text(encoding="utf-8")
    assert kaynak.index('@router.get("/lots/mutabakat")') < kaynak.index(
        '@router.get("/{product_id}/lots")'
    )


# -------------------------------------------------------------- davranış ---

def test_A_dan_F_ye_MUTLU_YOLLAR_SAPMA_URETMIYOR(tmp_path: Path) -> None:
    """Altı dilim TEK veritabanında arka arkaya koşar; sonunda `SAPMA == 0`.

    Alt süreçte GERÇEK ŞEMA ile koşuyor (1B-A..1B-F ikizlerinin kalıbı):
    `app.main` açılışta alembic'i sürüyor ve `app.config.Settings` modül
    düzeyinde TEK KOPYA olduğu için `DATABASE_URL` süreç İÇİNDE değiştirilemez.
    """
    veritabani = tmp_path / "1b-g-mutabakat.db"
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
from app.field_stok_tuketici import olaylari_isle
from app.main import app

client = TestClient(app)

# TARİHLER UZAK UÇLARDA (1B-B'nin gerekçesi): `bugun` bağımlılığı kapatılıyor.
YAKIN = '2098-01-31'
UZAK = '2099-01-31'


def ok(cevap):
    assert cevap.status_code < 300, (cevap.status_code, cevap.text)
    return cevap.json() if cevap.content else None


cevap = client.post('/api/auth/login',
                    json={'username': 'admin', 'password': 'admin123'})
assert cevap.status_code == 200, cevap.text
govde = cevap.json()
baslik = {'Authorization': 'Bearer ' + govde['access_token'],
          'X-Company-ID': str(govde['companies'][0]['id'])}
degisti = ok(client.post('/api/auth/change-password', headers=baslik,
                         json={'current_password': 'admin123',
                               'new_password': 'Mutabakat!123'}))
baslik['Authorization'] = 'Bearer ' + degisti['access_token']
cid = int(govde['companies'][0]['id'])

# NEGATİF STOK SERBEST: bu dosya MUTABAKATI ölçüyor, stok politikasını DEĞİL
# (1B-B/1B-E'nin aynı gerekçesi).
ok(client.put('/api/company-settings', headers=baslik,
              json={'negative_stock_policy': 'allow', 'credit_limit_policy': 'block'}))

depo_a = ok(client.get('/api/warehouses', headers=baslik))[0]['id']
depo_b = ok(client.post('/api/warehouses', headers=baslik,
                        json={'name': 'Mutabakat B Deposu', 'code': 'MBD'}))['id']
tedarikci = ok(client.post('/api/suppliers', headers=baslik,
                           json={'name': 'Mutabakat Tedarikçisi'}))['id']
musteri = ok(client.post('/api/customers', headers=baslik,
                         json={'name': 'Mutabakat Müşterisi'}))['id']
ciftlik = ok(client.post('/api/farms', headers=baslik,
                         json={'code': 'mtb', 'name': 'Mutabakat Çiftliği'}))['id']


def urun_ac(ad, taban=None):
    govde = {'name': ad, 'purchase_price': 10, 'sale_price': 20,
             'vat_rate': 20, 'stock': 0, 'unit': 'KG'}
    if taban is not None:
        govde['base_unit'] = taban
    return ok(client.post('/api/products', headers=baslik, json=govde))['id']


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


def satis(kalemler, depo=None):
    return ok(client.post('/api/orders', headers=baslik, json={
        'entity_id': musteri, 'transaction_date': '2026-09-09',
        'due_date': '2026-09-30', 'warehouse_id': depo or depo_a,
        'items': kalemler}))


def ayarla(pid, mod, adet, kod=None, skt=None, depo=None):
    govde = {'mode': mod, 'quantity': adet, 'movement_date': '2026-09-10',
             'warehouse_id': depo or depo_a, 'note': 'mutabakat'}
    if kod is not None:
        govde['lot_code'] = kod
    if skt is not None:
        govde['expiry_date'] = skt
    return ok(client.post('/api/products/' + str(pid) + '/stock',
                          headers=baslik, json=govde))


def say(satirlar, depo=None):
    return ok(client.post('/api/warehouses/counts', headers=baslik, json={
        'warehouse_id': depo or depo_a, 'count_date': '2026-09-10',
        'note': None, 'items': satirlar}))


def transfer(pid, adet, kod=None):
    satir = {'product_id': pid, 'quantity': adet}
    if kod is not None:
        satir['lot_code'] = kod
    return ok(client.post('/api/warehouses/transfers', headers=baslik, json={
        'source_warehouse_id': depo_a, 'target_warehouse_id': depo_b,
        'transfer_date': '2026-09-11', 'items': [satir]}))


def iade(kalemler, kaynak=None):
    govde = {'entity_id': musteri, 'document_date': '2026-09-12',
             'status': 'completed', 'warehouse_id': depo_a, 'items': kalemler}
    if kaynak is not None:
        govde['source_type'] = 'order'
        govde['source_id'] = kaynak
    return ok(client.post('/api/workflow/sale_return', headers=baslik, json=govde))


def sezon_ac(kod, urun_id):
    parsel = ok(client.post('/api/farm-parcels', headers=baslik,
                            json={'farm_id': ciftlik, 'code': kod, 'name': kod,
                                  'area_decare': '100.0000'}))['id']
    return ok(client.post('/api/crop-seasons', headers=baslik,
                          json={'parcel_id': parsel, 'season_year': 2026,
                                'crop': 'Bugday', 'product_id': urun_id,
                                'started_on': '2026-03-01',
                                'planted_area_decare': '100.0000'}))['id']


def hasat_ac(sezon_id, miktar):
    return ok(client.post('/api/field-harvests', headers=baslik,
                          json={'season_id': sezon_id, 'harvested_on': '2026-07-10',
                                'quantity': miktar, 'unit': 'KG'}))['id']


def tuket():
    with SessionLocal() as db:
        sayac = olaylari_isle(db, cid)
        db.commit()
        return sayac


def rapor(headers=None, **sorgu):
    return ok(client.get('/api/products/lots/mutabakat',
                         headers=headers or baslik, params=sorgu))


def kovalar(headers=None):
    """`(product_id, warehouse_id) -> kova`. RAPORUN KENDİ CEVABINDAN."""
    return {(s['product_id'], s['warehouse_id']): s['kova']
            for s in rapor(headers, limit=1000)['items']}


def sapmalar(headers=None):
    return {cift for cift, kova in kovalar(headers).items() if kova == 'SAPMA'}


# =========================================================================
# 1B-A .. 1B-F: ALTI MUTLU YOL, TEK VERİTABANI
#
# Her dilim KENDİ ürününü kullanıyor ve bu bilinçli: tek bir ürün üzerinden
# koşsalardı bir dilimin bıraktığı fark ötekinin yazdığıyla TESADÜFEN
# kapanabilir ve altı yol da yeşil görünürken defter yine ayrışmış olabilirdi.
# =========================================================================

# --- 1B-A: ALIŞ KALEMİ PARTİ AÇAR ---------------------------------------
urun_a = urun_ac('Mutabakat A - Alış')
# AYNI ÜRÜNÜN İKİ PARTİSİ İKİ AYRI BELGEDİR: alış şeması aynı ürünü tek
# belgede iki satırda REDDEDER (422). Kısıt 1B-A'dan eskidir ve parti
# defterine ait değildir; iki belge yazmak onu AŞMAZ, ona UYAR.
alis([kalem(urun_a, 5, 'MG-A1', YAKIN)])
alis([kalem(urun_a, 3, 'MG-A2', UZAK)])
assert kovalar()[(urun_a, depo_a)] == 'ESIT', kovalar()[(urun_a, depo_a)]

# --- 1B-B: SATIŞ FEFO İLE PARTİ TÜKETİR ---------------------------------
# ÖNEMLİ: satış L1'i (YAKIN) TAM tüketiyor. Tükenen parti satırı SİLİNMEZ,
# 0'a iner — ve bu çift hâlâ `ESIT` olmalıdır. `LOTSUZ_TASARIM`a düşseydi
# kovanın adı ("hiç parti açılmadı") YALAN olurdu.
urun_b = urun_ac('Mutabakat B - Satış')
alis([kalem(urun_b, 4, 'MG-B1', YAKIN)])
alis([kalem(urun_b, 6, 'MG-B2', UZAK)])
satis_belgesi = satis([kalem(urun_b, 4, fiyat=20)])
partiler_b = ok(client.get('/api/products/' + str(urun_b) + '/lots',
                           headers=baslik))['lots']
tukenmis = [s for s in partiler_b if s['lot_code'] == 'MG-B1']
assert len(tukenmis) == 1 and Decimal(str(tukenmis[0]['quantity'])) == Decimal('0'), tukenmis
assert kovalar()[(urun_b, depo_a)] == 'ESIT', kovalar()[(urun_b, depo_a)]

# TÜKENMİŞ PARTİNİN TEK BAŞINA HALİ: ürünün TEK partisi 0'a inerse çift
# (0, 0) olur. `ESIT` beklenir; `LOTSUZ_TASARIM` gelirse tükenmiş parti
# "hiç açılmamış" sayılıyor demektir.
urun_tuk = urun_ac('Mutabakat B - Tükenen')
alis([kalem(urun_tuk, 2, 'MG-T1', UZAK)])
satis([kalem(urun_tuk, 2, fiyat=20)])
assert kovalar()[(urun_tuk, depo_a)] == 'ESIT', kovalar()[(urun_tuk, depo_a)]

# --- 1B-C: AYARLAMA VE SAYIM PARTİ FARKINDA -----------------------------
urun_c = urun_ac('Mutabakat C - Ayarlama')
ayarla(urun_c, 'add', 10, kod='MG-C1', skt=UZAK)
assert kovalar()[(urun_c, depo_a)] == 'ESIT', kovalar()[(urun_c, depo_a)]
say([{'product_id': urun_c, 'counted_quantity': 7, 'lot_code': 'MG-C1'}])
assert kovalar()[(urun_c, depo_a)] == 'ESIT', kovalar()[(urun_c, depo_a)]

# --- 1B-D: TRANSFER PARTİYİ SKT'SİYLE TAŞIR -----------------------------
# İKİ çift birden ölçülür: kaynak depo VE hedef depo. Transfer yalnız
# kaynağı düşürüp hedefi partisiz yazsaydı, kaynak `ESIT` kalır ve kusur
# YALNIZ hedef çiftte görünürdü.
urun_d = urun_ac('Mutabakat D - Transfer')
alis([kalem(urun_d, 8, 'MG-D1', UZAK)])
transfer(urun_d, 3, 'MG-D1')
d_kovalar = kovalar()
assert d_kovalar[(urun_d, depo_a)] == 'ESIT', d_kovalar[(urun_d, depo_a)]
assert d_kovalar[(urun_d, depo_b)] == 'ESIT', d_kovalar[(urun_d, depo_b)]

# --- 1B-E: SATIŞ İADESİ ÇIKTIĞI PARTİYE GERİ VERİR ----------------------
urun_e = urun_ac('Mutabakat E - İade')
alis([kalem(urun_e, 3, 'MG-E1', YAKIN)])
alis([kalem(urun_e, 3, 'MG-E2', UZAK)])
e_satis = satis([kalem(urun_e, 5, fiyat=20)])
iade([kalem(urun_e, 3, fiyat=20)], kaynak=e_satis['id'])
assert kovalar()[(urun_e, depo_a)] == 'ESIT', kovalar()[(urun_e, depo_a)]

# --- 1B-F: HASAT PARTİ AÇAR ---------------------------------------------
urun_f = urun_ac('Mutabakat F - Hasat', taban='KG')
sezon = sezon_ac('MGF', urun_f)
hasat_ac(sezon, '120.0000')
assert tuket()['SENT'] == 1
f_kovalar = kovalar()
# Hasat partiyi HAREKETİN deposunda açar (`inventory.default_warehouse`) ve
# ÖLÇÜLEN çift O ÇİFTTİR. Ürünün ÖTEKİ depodaki çifti (ürün açılışında sıfır
# miktarla doğan satır) `LOTSUZ_TASARIM`dır ve OLMASI GEREKEN budur: o depoda
# hiç parti AÇILMADI. İkisini tek bir döngüde `ESIT` beklemek, kovanın
# tanımını değil TESTİN kapsamını yanlış kurmak olurdu.
assert f_kovalar[(urun_f, depo_a)] == 'ESIT', f_kovalar[(urun_f, depo_a)]
assert f_kovalar[(urun_f, depo_b)] == 'LOTSUZ_TASARIM', f_kovalar[(urun_f, depo_b)]

# =========================================================================
# ALTI DİLİMİN BİRLEŞİK CEVABI: SAPMA YOK
#
# Bu dosyanın var oluş sebebi olan TEK satır. Altı dilim ayrı ayrı yeşildi;
# ölçülmemiş olan, BİRLİKTE koştuklarında da yeşil kaldıklarıydı.
# =========================================================================
mutlu = rapor(limit=1000)
assert mutlu['counts']['SAPMA'] == 0, mutlu
assert not sapmalar(), sapmalar()
# Kova sözlüğü HER ZAMAN ÜÇ anahtar taşır: eksik anahtar, sıfır sayıyı
# "ölçülmedi"den ayırt edilemez yapardı.
assert set(mutlu['counts']) == {'ESIT', 'LOTSUZ_TASARIM', 'SAPMA'}, mutlu['counts']
assert mutlu['counts']['ESIT'] >= 6, mutlu['counts']

# =========================================================================
# BİLEREK PARTİSİZ YOLLAR: `LOTSUZ_TASARIM`, ADIYLA
#
# Bunlar KUSUR DEĞİLDİR ve bir veritabanı kısıtı onları REDDEDERDİ — o yüzden
# kısıt yerine RAPOR var (gerekçe `docs/PARTI_MUTABAKAT.md`). Ölçülen şey,
# hepsinin `LOTSUZ_TASARIM`a düştüğü ve `SAPMA`yı KİRLETMEDİĞİDİR.
# =========================================================================
lotsuz_ciftler = {}

# (1) PARTİSİZ ALIŞ: kalemde `lot_code` yok.
urun_l1 = urun_ac('Mutabakat L1 - Partisiz Alış')
alis([kalem(urun_l1, 9)])
lotsuz_ciftler['partisiz alış'] = (urun_l1, depo_a)

# (2) PARTİSİZ ELLE AYARLAMA.
urun_l2 = urun_ac('Mutabakat L2 - Partisiz Ayarlama')
ayarla(urun_l2, 'add', 4)
lotsuz_ciftler['partisiz ayarlama'] = (urun_l2, depo_a)

# (3) PARTİSİZ TRANSFER: hedef depo çifti partisiz doğar.
urun_l3 = urun_ac('Mutabakat L3 - Partisiz Transfer')
ayarla(urun_l3, 'add', 6)
transfer(urun_l3, 2)
lotsuz_ciftler['partisiz transfer kaynağı'] = (urun_l3, depo_a)
lotsuz_ciftler['partisiz transfer hedefi'] = (urun_l3, depo_b)

# (4) KAYNAKSIZ SATIŞ İADESİ: `source_type`/`source_id` YOK, yani iadenin
#     hangi partiden çıktığı SORULAMAZ ve uydurulmaz (1B-E'nin kapsam sınırı).
urun_l4 = urun_ac('Mutabakat L4 - Kaynaksız İade')
iade([kalem(urun_l4, 2, fiyat=20)])
lotsuz_ciftler['kaynaksız satış iadesi'] = (urun_l4, depo_a)

# (5) PARTİSİZ SATIŞ: partisi hiç açılmamış üründen çıkış.
urun_l5 = urun_ac('Mutabakat L5 - Partisiz Satış')
ayarla(urun_l5, 'add', 5)
satis([kalem(urun_l5, 2, fiyat=20)])
lotsuz_ciftler['partisiz satış'] = (urun_l5, depo_a)

son_kovalar = kovalar()
for ad, cift in sorted(lotsuz_ciftler.items()):
    assert cift in son_kovalar, (ad, cift, sorted(son_kovalar))
    assert son_kovalar[cift] == 'LOTSUZ_TASARIM', (ad, cift, son_kovalar[cift])

# Partisiz yollar `SAPMA`yı KİRLETMEDİ: kırmızı hâlâ boş.
lotsuz_rapor = rapor(limit=1000)
assert lotsuz_rapor['counts']['SAPMA'] == 0, lotsuz_rapor['counts']
assert lotsuz_rapor['counts']['LOTSUZ_TASARIM'] >= len(set(lotsuz_ciftler.values())), \
    lotsuz_rapor['counts']

# =========================================================================
# ENJEKSİYON: HAM `UPDATE` -> TAM OLARAK BİR `SAPMA` ve O ÇİFTİ ADIYLA
#
# Ham SQL BİLEREK: uygulamanın hiçbir yolu iki defteri ayıramaz (altı dilim
# tam bunun için var), yani sapmayı ancak defteri UYGULAMANIN DIŞINDAN
# bozarak üretebiliriz. Bu, mutabakatın ölçtüğü GERÇEK olgudur: elle veri
# düzeltmesi, göç kazası, yarım kalan bir betik.
# =========================================================================
with SessionLocal() as db:
    hedef = db.execute(text(
        "SELECT id, product_id, warehouse_id FROM product_lots "
        "WHERE company_id=:cid AND lot_code=:kod"
    ), {'cid': cid, 'kod': 'MG-A1'}).mappings().first()
    assert hedef is not None
    db.execute(text("UPDATE product_lots SET quantity=quantity+1 WHERE id=:id"),
               {'id': hedef['id']})
    db.commit()

bozuk_cift = (int(hedef['product_id']), int(hedef['warehouse_id']))
bozuk = rapor(limit=1000)
assert bozuk['counts']['SAPMA'] == 1, bozuk['counts']
assert sapmalar() == {bozuk_cift}, (sapmalar(), bozuk_cift)
# SATIR KENDİ KANITINI TAŞIYOR: fark İŞARETLİDİR ve YÖNÜ anlamlıdır.
# `fark < 0` = parti stoktan FAZLA = defter ELDE OLMAYAN malı VAR gösteriyor.
bozuk_satir = [s for s in bozuk['items']
               if (s['product_id'], s['warehouse_id']) == bozuk_cift][0]
assert Decimal(str(bozuk_satir['fark'])) == Decimal('-1'), bozuk_satir
assert Decimal(str(bozuk_satir['parti_toplami'])) - \
       Decimal(str(bozuk_satir['stok'])) == Decimal('1'), bozuk_satir

# =========================================================================
# KİRACI: KOMŞU FİRMANIN RAPORU BU SATIRLARIN HİÇBİRİNİ GÖRMEZ
#
# Kiracı yüklemi düşerse sorgu ÇÖKMEZ — yalnız komşunun satırlarını da
# döndürür. Sessiz sızıntı; bu yüzden DAVRANIŞLA ölçülüyor, statik kapı
# (`test_mutabakat_KIRACI_YUKLEMINI_UC_YERDE_de_tasiyor`) TEK BAŞINA yetmez.
# =========================================================================
komsu = ok(client.post('/api/companies', headers=baslik,
                       json={'name': 'Mutabakat Komşu Firma'}))['id']
komsu_baslik = {**baslik, 'X-Company-ID': str(komsu)}
komsu_rapor = rapor(komsu_baslik, limit=1000)
assert komsu_rapor['counts']['SAPMA'] == 0, komsu_rapor
bizim_ciftler = set(kovalar())
assert not (set(kovalar(komsu_baslik)) & bizim_ciftler), komsu_rapor

# =========================================================================
# SAYFALAMA: SAYIMLAR SAYFANIN DEĞİL KİRACININ TAMAMINDANDIR
#
# İkinci sayfadaki tek `SAPMA`yı ilk sayfaya bakan operatör GÖRMEZDİ ve
# "sapma yok" diye okurdu. Sayım bu yüzden sayfadan BAĞIMSIZ ölçülür.
# =========================================================================
ilk_sayfa = rapor(limit=1, offset=0)
assert len(ilk_sayfa['items']) == 1, ilk_sayfa
assert ilk_sayfa['has_more'] is True, ilk_sayfa
assert ilk_sayfa['total'] == bozuk['total'], (ilk_sayfa['total'], bozuk['total'])
assert ilk_sayfa['counts'] == bozuk['counts'], (ilk_sayfa['counts'], bozuk['counts'])
# İlk sayfada sapma OLMASA BİLE sayaç onu SÖYLÜYOR.
assert ilk_sayfa['counts']['SAPMA'] == 1, ilk_sayfa['counts']

son_sayfa = rapor(limit=1000, offset=bozuk['total'])
assert son_sayfa['items'] == [] and son_sayfa['has_more'] is False, son_sayfa

print('1B-G SQLITE OK')
'''
