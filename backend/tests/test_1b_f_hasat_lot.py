"""HASAT PARTİ AÇAR (FAZ 1B-F) — outbox tüketicisi defterin ALTINCI çağıranı.

Konu: `app/field_stok_tuketici.py` (hasat dalı) ve `app/parti_defteri.py`
(`_parti_ac`). GÖÇ YOKTUR: `product_lots` 0067'de, deposu 0073'te,
`stock_movements.lot_id` 0067'de kuruldu; `crop_seasons.product_id` 0062'de.
Bu dilim TEK BİR SÜTUN eklemez, VAR OLAN yolları birbirine bağlar.

--- BU DİLİM NEYİ KAPATIYOR ------------------------------------------------

1B-A alışı, 1B-B satışı, 1B-C sayımı, 1B-D transferi, 1B-E iadeyi partiye
bağladı. Stoğa mal GİREN bir yol daha vardı ve hiçbirinde anılmadı: HASAT.
Tarla tüketicisi (`field_stok_tuketici`) hasadı stok hareketine çeviriyor ama
yazdığı satırın `lot_id`si NULL kalıyordu. Sonucu ÖLÇÜLDÜ ve iki cümlesi var:

  * GERİ ÇAĞIRMA SORUSU SORULAMAZ: "bu buğdayın hangi hasadı sahada"
    sorusunun cevabı defterde YOKTU. Aynı ürün alışla da giriyor ve alış
    partili; yani defter "bir kısmı partili, bir kısmı partisiz" diyordu.
  * SATIŞ FEFO'SU PARTİSİZ MALI HİÇ GÖRMEZ (1B-B): `_parti_tuket` yalnız
    `product_lots` satırlarından seçer. Hasat malı deftere hiç girmediği için
    satış onu tüketemez; stok 1000 der, parti defteri 0 der ve ikisi SESSİZCE
    ayrışır — `Tuketim.defter_bosaldi` bayrağının ADIYLA reddettiği durum.

--- ÜÇ ALANIN ÜÇÜ DE ÖLÇÜLDÜ, HİÇBİRİ UYDURULMADI --------------------------

`_parti_ac` üç şey ister: KOD, DEPO, SKT. Hasat üçünü de TAŞIMIYOR (göç
`20260807_0044`ün sütun listesi ölçüldü: id, company_id, season_id,
harvested_on, quantity, unit, harvested_area_decare, quality_grade,
moisture_percent, notes, status; 0046/0047/0048 yalnız güvenlik ve gelir
alanları ekledi). Üçü için verilen cevaplar:

  * KOD: `HASAT-<harvest_id>`, hasadın KİMLİĞİNDEN TÜRETİLİR. Operatöre
    SORULAMAZ — tüketici bir HTTP isteği değil arka plan döngüsüdür ve
    soracağı kimse yoktur. Sayaç/rastgele kod ise "bir hasat = bir parti"
    cümlesini yalanlardı: iki farklı hasat aynı koda düşerse geri çağırma
    kaydı hangi hasadın sahada olduğunu SÖYLEYEMEZ.
  * DEPO: hareketin deposu (`inventory.default_warehouse`). Hasadın HEDEF
    DEPO sütunu YOKTUR ve UYDURULMADI. Ayrı bir depo seçmek malı bir depoya
    sokup partiyi başkasında açardı; `_parti_ac`ın tekilliği depoyu İÇERİR,
    yani ikisi sessizce ayrışırdı.
  * SKT: `SKT_SORULMADI`. `None` göndermek "SKT'si YOKTUR" BEYANIDIR ve
    `_parti_ac`ın çatışma denetimini çalıştırırdı; sentinel hiçbir şey
    söylemez. Ayrım 1B-C'de ölçüldü ve burada BİREBİR geçerlidir.

--- SKT'SİZ PARTİ FEFO'DA SONA GİDER (1B-B ÖLÇÜMÜ) ------------------------

`app/parti.py::_sira_anahtari`in ilk elemanı NULL-SON kuralıdır: SKT'si olan
partilerin HEPSİ, tarihi ne kadar uzak olursa olsun, SKT'si olmayanların
ÖNÜNDE gelir. Yani hasat partisi ancak tarihli partiler bittikten sonra
tüketilir. Bu bir yan etki değil, bu dilimin DAVRANIŞIDIR ve aşağıda
`ALIS-1` (2098 SKT'li) ile `HASAT-<id>` (SKT'siz) yan yana çivileniyor.

--- İDEMPOTENSİ: ÜÇ KATMAN, HİÇBİRİ PARTİ KODU DEĞİL ----------------------

Ölçüldü ve İLK VARSAYIM YANLIŞ ÇIKTI. "Belirlenimci kod tekrar teslimde
ikinci partiyi engeller" cümlesi DOĞRU ama SINANAMAZ, çünkü tekrar teslim
parti katmanına VARMADAN ÖNCE düşüyor:

  1. Olay yalnız `PENDING` iken alınır; talep + hareket + sonlandırma TEK
     işlemdir (`_bir_olayi_isle`).
  2. `SENT` olay yeniden kuyruklanamaz: uç 409 `EVENT_ALREADY_SENT` verir
     (#55, `tests/test_outbox_requeue.py`).
  3. Olay ELLE `PENDING`e çekilse bile göç `20260821_0060`ın kısmi benzersiz
     indeksi (olay + ürün başına EN FAZLA BİR hareket) hareketi reddeder,
     `_kurtar` `db.rollback()` yapar ve PARTİ ARTIŞI DA GERİ ALINIR.

Aşağıdaki `test_SENT_olay...` ikinci ve üçüncü katmanı ölçüyor ve sonucu tek
cümledir: bir hasat, bir parti satırı, bir hareket. Kodun belirlenimciliği bu
yüzden tekrar teslimle DEĞİL, İKİ AYRI HASATLA sınanıyor (`test_hasat_PARTI...`
ve onun mutasyonu) — sınanabilir olan iddia odur.

--- BU DOSYANIN ÖLÇMEDİĞİ -------------------------------------------------

FAALİYET GİRDİSİ ve KANTAR FİŞİ FARKI partisiz kalır (`lot_id` NULL) ve bu
bir gözden kaçma DEĞİL bildirilmiş bir sınırdır; aşağıda ADIYLA çivili.
Fiş farkı EKSİ olabilir ve eksi bir farkı partiye uygulamak bir DÜŞME'dir:
yetersiz partide 409 üretir, yani bir kantar düzeltmesi olayı `DEAD` kovasına
atardı. O yol ayrı ölçülmeden açılmaz.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
TUKETICI = BACKEND / "app" / "field_stok_tuketici.py"


def _govde(kaynak: str, ad: str) -> ast.FunctionDef:
    for dugum in ast.walk(ast.parse(kaynak)):
        if isinstance(dugum, ast.FunctionDef) and dugum.name == ad:
            return dugum
    raise AssertionError(f"{ad} bulunamadı")


def _parti_ac_cagrisi() -> ast.Call:
    """`_bir_olayi_isle` içindeki TEK `_parti_ac` çağrısı."""
    cagrilar = [
        dugum
        for dugum in ast.walk(
            _govde(TUKETICI.read_text(encoding="utf-8"), "_bir_olayi_isle")
        )
        if isinstance(dugum, ast.Call)
        and getattr(dugum.func, "id", None) == "_parti_ac"
    ]
    assert len(cagrilar) == 1, (
        "`_bir_olayi_isle` içinde BİR TANE `_parti_ac` çağrısı bekleniyor; "
        f"bulunan: {len(cagrilar)}. İki çağrı, iki yolun sessizce ayrışacağı "
        "yerdir (bkz. `app/parti_defteri.py` başlığı)."
    )
    return cagrilar[0]


# ------------------------------------------------------------- statik ---

def test_tuketici_DEFTERI_CAGIRIYOR_dogrudan_YAZMIYOR() -> None:
    """Tüketici `product_lots`a HİÇ dokunmaz; `_parti_ac`ı ÇAĞIRIR.

    1B-A'nın yazıcı tekeli (`test_product_lots_YAZICISI_YALNIZ_parti_defteri_py`)
    zaten `app/` genelinde ölçüyor; bu kapı AYNI ŞEYİ TEKRARLAMAZ, ONUN
    GÖREMEDİĞİNİ ölçer: tekelin bu dosyada GERÇEKTEN KULLANILDIĞINI. Çağrı
    kaybolursa oradaki kapı YEŞİL kalır (ihlal yok, çünkü yazan da yok) ve
    hasat sessizce partisiz yazmaya döner — tam olarak bu dilimin kapattığı
    kusur.
    """
    kaynak = TUKETICI.read_text(encoding="utf-8")
    agac = ast.parse(kaynak)
    ithal = {
        ad.name
        for dugum in ast.walk(agac)
        if isinstance(dugum, ast.ImportFrom)
        and (dugum.module or "").split(".")[-1] == "parti_defteri"
        for ad in dugum.names
    }
    assert {"_parti_ac", "SKT_SORULMADI"} <= ithal, (
        f"tüketici parti defterinden yalnız {sorted(ithal)} ithal ediyor; "
        "`_parti_ac` ve `SKT_SORULMADI` ikisi de gerekli."
    )
    _parti_ac_cagrisi()  # varlığı VE tekliği burada ölçülüyor
    # Tabloyu ANAN çalıştırılabilir metin YOK: düzyazıda anmak çağırmak
    # değildir (1B-A kapısının ölçülmüş dersi), ama bir SQL metni olsaydı
    # İKİNCİ BİR YAZICI demek olurdu.
    govde_dizgileri = {
        id(dugum.value)
        for dugum in ast.walk(agac)
        if isinstance(dugum, ast.Expr) and isinstance(dugum.value, ast.Constant)
    }
    calistirilabilir = [
        dugum.value
        for dugum in ast.walk(agac)
        if isinstance(dugum, ast.Constant)
        and isinstance(dugum.value, str)
        and "product_lots" in dugum.value
        and id(dugum) not in govde_dizgileri
    ]
    assert calistirilabilir == [], (
        "tüketici `product_lots`u çalıştırılabilir bir metinde anıyor: "
        f"{calistirilabilir}. Defterin TEK yazıcısı `app/parti_defteri.py`dir."
    )


def test_parti_kodu_HASADIN_KIMLIGINDEN_ve_BELIRLENIMCI() -> None:
    """`HASAT-<id>`: aynı hasat HER ZAMAN aynı kod, farklı hasat FARKLI kod.

    İki yönde de ölçülüyor çünkü iki ayrı kusuru var: kod hasada bağlı
    OLMASAYDI iki hasat tek partide birleşirdi (geri çağırma hangi hasadı
    işaret edeceğini söyleyemez); çağrı başına DEĞİŞSEYDİ bir hasat iki
    partiye bölünürdü. İkisi de sessizdir — toplam miktar her iki durumda da
    DOĞRU kalır.
    """
    from app.field_stok_tuketici import PARTI_KODU_ONEKI, _parti_kodu

    assert _parti_kodu(7) == _parti_kodu(7) == f"{PARTI_KODU_ONEKI}7"
    assert _parti_kodu(7) != _parti_kodu(8)
    # Kimlik kodun İÇİNDE okunabilir olmalı: raporu okuyan kişi partiden
    # hasada geri dönebilmeli.
    assert "7" in _parti_kodu(7)


def test_hasat_dali_UC_ALANI_da_ACIK_yaziyor() -> None:
    """Çağrı yerinde: depo HAREKETİN deposu, SKT SORULMADI, kod TÜRETİLMİŞ.

    Bir AST okuması ama savunduğu şey üç ayrı DAVRANIŞ ve üçünün de yanlışı
    SESSİZDİR: başka bir depo verilirse mal bir depoya girer parti başkasında
    açılır; `expiry_date`e `None` yazılırsa "SKT'si yoktur" BEYANI olur ve
    `_parti_ac`ın çatışma denetimi bu yolda çalışmaya başlar; kod satır içinde
    kurulursa okuyan ile yazanın ayrışabileceği ikinci bir yer açılır.
    """
    cagri = _parti_ac_cagrisi()
    anahtarlar = {kw.arg: kw.value for kw in cagri.keywords}
    for ad in ("product_id", "warehouse_id", "lot_code", "expiry_date", "miktar"):
        assert ad in anahtarlar, f"`_parti_ac` çağrısında `{ad}` YOK"
    depo_dugumu = anahtarlar["warehouse_id"]
    assert isinstance(depo_dugumu, ast.Name) and depo_dugumu.id == "depo", (
        "parti HAREKETİN deposunda açılmalı; çağrıda kullanılan ifade: "
        f"{ast.dump(depo_dugumu)}"
    )
    skt_dugumu = anahtarlar["expiry_date"]
    assert isinstance(skt_dugumu, ast.Name) and skt_dugumu.id == "SKT_SORULMADI", (
        "SKT UYDURULAMAZ: hasadın son kullanma tarihi YOKTUR ve `None` bir "
        f"BEYANDIR. Çağrıdaki ifade: {ast.dump(skt_dugumu)}"
    )
    kod = anahtarlar["lot_code"]
    assert isinstance(kod, ast.Call) and getattr(kod.func, "id", None) == "_parti_kodu", (
        "parti kodu `_parti_kodu` ile TÜRETİLMELİ; satır içinde kurulan bir "
        f"kod ikinci bir üretim yeri açar. Çağrıdaki ifade: {ast.dump(kod)}"
    )


def test_PARTI_YOLU_YALNIZ_HASAT_kaynaginda_KAPSAM_SINIRI() -> None:
    """Sınır SESSİZ DEĞİL: parti dalı `tip == KAYNAK_HASAT` ile korunuyor.

    Faaliyet girdisi (tüketim) ve kantar fişi FARKI bugünkü davranışlarını
    korur. Koşul kaldırılırsa fiş farkı da parti açardı ve EKSİ bir fark
    `_parti_ac`a negatif miktar geçirirdi: var olan satırda `quantity + (-x)`
    SQLite'ta sessizce eksiye iner, PostgreSQL'de 0067'nin `CHECK`i ile 500
    olur. Yani sınırın kalkması bir GENİŞLEME değil bir KUSURDUR ve burada
    ADIYLA duruyor.
    """
    from app.field_stok_tuketici import (
        _FARK_KAYNAKLARI,
        KAYNAK_FIS,
        KAYNAK_HASAT,
    )

    assert KAYNAK_HASAT == "field_harvest"
    assert KAYNAK_FIS in _FARK_KAYNAKLARI and KAYNAK_HASAT not in _FARK_KAYNAKLARI

    govde = _govde(TUKETICI.read_text(encoding="utf-8"), "_bir_olayi_isle")
    koruyanlar = [
        dugum
        for dugum in ast.walk(govde)
        if isinstance(dugum, ast.If)
        and any(
            isinstance(alt, ast.Call)
            and getattr(alt.func, "id", None) == "_parti_ac"
            for alt in ast.walk(dugum)
        )
        and "KAYNAK_HASAT" in ast.dump(dugum.test)
    ]
    assert koruyanlar, (
        "`_parti_ac` çağrısı `tip == KAYNAK_HASAT` koşulunun İÇİNDE değil; "
        "kapsam sınırı düşmüş demektir."
    )


# ---------------------------------------------------------- davranış ---

_KURULUM = r'''
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text as _sql

from app.config import settings
from app.db import SessionLocal
from app import field_stok_tuketici as C
from app.field_stok_tuketici import olaylari_isle
from app.main import app

# ALIS partisinin SKT'si UZAK bir uçta (1B-B'nin gerekçesi): sıra `bugun`e
# bağlı OLMAMALI, NULL-SON kuralına bağlı olmalı.
UZAK = '2098-01-31'
ADMIN_PW = 'HasatLot!123'

client = TestClient(app)


def ok(cevap):
    assert cevap.status_code < 300, (cevap.status_code, cevap.text)
    return cevap.json() if cevap.content else None


def giris():
    for aday in ('admin123', ADMIN_PW):
        cevap = client.post('/api/auth/login',
                            json={'username': 'admin', 'password': aday})
        if cevap.status_code == 200:
            break
    assert cevap.status_code == 200, cevap.text
    govde = cevap.json()
    baslik = {'Authorization': 'Bearer ' + govde['access_token'],
              'X-Company-ID': str(govde['companies'][0]['id'])}
    if aday != ADMIN_PW:
        degisti = client.post('/api/auth/change-password', headers=baslik,
                              json={'current_password': aday,
                                    'new_password': ADMIN_PW})
        assert degisti.status_code == 200, degisti.text
        baslik['Authorization'] = 'Bearer ' + degisti.json()['access_token']
    return baslik, int(govde['companies'][0]['id'])


baslik, cid = giris()
# NEGATİF STOK SERBEST: bu dosya PARTİ defterini ölçüyor, stok politikasını
# DEĞİL (1B-B/1B-E'nin aynı gerekçesi).
ok(client.put('/api/company-settings', headers=baslik,
              json={'negative_stock_policy': 'allow',
                    'credit_limit_policy': 'block'}))
depo = ok(client.get('/api/warehouses', headers=baslik))[0]['id']
ciftlik = ok(client.post('/api/farms', headers=baslik,
                         json={'code': 'hlot', 'name': 'Hasat Lot'}))['id']


def urun_ac(ad, taban='KG'):
    govde = {'name': ad, 'purchase_price': 10, 'sale_price': 20,
             'vat_rate': 20, 'stock': 0, 'unit': 'KG'}
    if taban is not None:
        govde['base_unit'] = taban
    return ok(client.post('/api/products', headers=baslik, json=govde))['id']


def sezon_ac(kod, urun_id):
    parsel = ok(client.post('/api/farm-parcels', headers=baslik,
                            json={'farm_id': ciftlik, 'code': kod, 'name': kod,
                                  'area_decare': '100.0000'}))['id']
    return ok(client.post('/api/crop-seasons', headers=baslik,
                          json={'parcel_id': parsel, 'season_year': 2026,
                                'crop': 'Bugday', 'product_id': urun_id,
                                'started_on': '2026-03-01',
                                'planted_area_decare': '100.0000'}))['id']


def hasat_ac(sezon_id, miktar, birim='KG'):
    return ok(client.post('/api/field-harvests', headers=baslik,
                          json={'season_id': sezon_id,
                                'harvested_on': '2026-07-10',
                                'quantity': miktar, 'unit': birim}))['id']


def tuket(firma=None):
    with SessionLocal() as db:
        sayac = olaylari_isle(db, firma if firma is not None else cid)
        db.commit()
        return sayac


def partiler(urun_id):
    """`lot_code -> (miktar, SKT, depo)`. OKUMA UCUNDAN, ham SQL'den DEĞİL."""
    return {
        satir['lot_code']: (Decimal(str(satir['quantity'])),
                            satir['expiry_date'], satir['warehouse_id'])
        for satir in ok(client.get('/api/products/%d/lots' % urun_id,
                                   headers=baslik))['lots']
    }


def parti_satir_sayisi(firma=None):
    with SessionLocal() as db:
        return db.execute(_sql(
            "SELECT COUNT(*) FROM product_lots WHERE company_id = :c"),
            {'c': firma if firma is not None else cid}).scalar_one()


def hareketler(tur, belge_id):
    """(lot_code, miktar) — YAZILDIKLARI sırada; partisiz satır `None` verir."""
    with SessionLocal() as db:
        return [
            (satir[0], Decimal(str(satir[1])))
            for satir in db.execute(_sql(
                "SELECT l.lot_code, h.quantity FROM stock_movements h "
                "LEFT JOIN product_lots l ON l.id = h.lot_id "
                "WHERE h.company_id = :c AND h.reference_type = :rt "
                "AND h.reference_id = :rid ORDER BY h.id"),
                {'c': cid, 'rt': tur, 'rid': belge_id}).all()
        ]


def hasat_olayi(hasat_id):
    with SessionLocal() as db:
        return db.execute(_sql(
            "SELECT id, status, attempts FROM field_integration_events "
            "WHERE company_id = :c AND source_type = 'field_harvest' "
            "AND source_id = :s"), {'c': cid, 's': hasat_id}).mappings().first()


def tarla_hareket_sayisi():
    with SessionLocal() as db:
        return db.execute(_sql(
            "SELECT COUNT(*) FROM stock_movements WHERE company_id = :c "
            "AND reference_type = 'field_integration_event'"),
            {'c': cid}).scalar_one()
'''


#: MUTLU YOLUN ÇEKİRDEĞİ — her senaryonun başında aynı üç satır.
_HASAT_UYGULA = r'''
urun = urun_ac('Hasat Bugday')
sezon = sezon_ac('hl1', urun)
hasat = hasat_ac(sezon, '1000')
sayac = tuket()
assert sayac['SENT'] == 1, sayac
'''


def _cevre(db_yolu: Path) -> dict:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_yolu.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _kos(kaynak: str, db_yolu: Path, imza: str) -> str:
    """Senaryoyu AYRI süreçte, KENDİ taze veritabanında koşar.

    ALT SÜREÇ ZORUNLU ve gerekçe 1B-A..1B-E ile AYNI: davranış smoke'u kendi
    `DATABASE_URL`iyle TAZE bir şema kurar ve `app.config.Settings` modül
    düzeyinde TEK KOPYADIR — süreç İÇİNDE değiştirilemez.
    """
    tamam = subprocess.run(
        [sys.executable, "-c", _KURULUM + kaynak], cwd=BACKEND,
        env=_cevre(db_yolu), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=900,
    )
    assert tamam.returncode == 0, tamam.stdout + "\n" + tamam.stderr
    assert imza in tamam.stdout, tamam.stdout
    return tamam.stdout


def _kos_kirmizi(kaynak: str, db_yolu: Path, beklenen: str) -> None:
    """Senaryo KIRMIZI olmalı VE kırmızı, kapının KENDİ metnini taşımalı."""
    tamam = subprocess.run(
        [sys.executable, "-c", _KURULUM + kaynak], cwd=BACKEND,
        env=_cevre(db_yolu), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=900,
    )
    cikti = tamam.stdout + "\n" + tamam.stderr
    assert "MUTASYON-KURULDU" in tamam.stdout, (
        "Mutasyon uygulanmadı; bu koşum sınamıyor.\n" + cikti)
    assert tamam.returncode != 0, "Beklenen kırmızı GELMEDİ.\n" + cikti
    assert beklenen in cikti, (
        "Kırmızı geldi ama kapının KENDİ metni yok; başka bir yerden "
        f"(kısıt, doğrulama, çökme) gelmiş olabilir. Beklenen: {beklenen!r}\n"
        + cikti)


def test_hasat_PARTI_ACIYOR_ve_satis_FEFO_SKT_SIZI_SONRA_TUKETIYOR(
    tmp_path: Path,
) -> None:
    """Uçtan uca: hasat partiyi açar, alış partisi ÖNCE tükenir, hasat SONRA.

    İKİ HASAT İKİ PARTİDİR (kodun belirlenimciliğinin SINANABİLİR hâli) ve
    satışın dağılımı NULL-SON kuralını ADIYLA ölçer: `ALIS-1` 2098 SKT'lidir
    ve SKT'siz hasat partisinden ÖNCE gider — tarihi ne kadar uzak olursa
    olsun.
    """
    cikti = _kos(_HASAT_UYGULA + r'''
# --- 1. PARTİ AÇILDI: KOD, DEPO, SKT, MİKTAR ------------------------------
assert partiler(urun) == {'HASAT-%d' % hasat: (Decimal('1000'), None, depo)}, (
    partiler(urun))
olay = hasat_olayi(hasat)
assert hareketler('field_integration_event', olay['id']) == [
    ('HASAT-%d' % hasat, Decimal('1000'))], (
    'HAREKET PARTİSİZ YAZILDI',
    hareketler('field_integration_event', olay['id']))

# --- 2. İKİNCİ HASAT = İKİNCİ PARTİ ---------------------------------------
# Aynı ürün, aynı sezon, aynı depo. Kod hasada bağlı olmasaydı ikisi TEK
# satırda birleşirdi ve toplam (1000+400) yine DOĞRU görünürdü.
hasat2 = hasat_ac(sezon, '400')
assert tuket()['SENT'] == 1
assert partiler(urun) == {
    'HASAT-%d' % hasat: (Decimal('1000'), None, depo),
    'HASAT-%d' % hasat2: (Decimal('400'), None, depo),
}, ('İKİ HASAT TEK PARTİYE DÜŞTÜ', partiler(urun))
assert parti_satir_sayisi() == 2, parti_satir_sayisi()

# --- 3. ALIŞ SKT'Lİ PARTİ AÇAR, SATIŞ ÖNCE ONU TÜKETİR --------------------
tedarikci = ok(client.post('/api/suppliers', headers=baslik,
                           json={'name': 'Hasat Tedarikçisi'}))['id']
musteri = ok(client.post('/api/customers', headers=baslik,
                         json={'name': 'Hasat Müşterisi'}))['id']
ok(client.post('/api/purchases', headers=baslik, json={
    'entity_id': tedarikci, 'transaction_date': '2026-09-08',
    'warehouse_id': depo,
    'items': [{'product_id': urun, 'quantity': 300, 'unit_price': 10,
               'vat_rate': 20, 'lot_code': 'ALIS-1', 'expiry_date': UZAK}]}))
assert partiler(urun)['ALIS-1'] == (Decimal('300'), UZAK, depo), partiler(urun)

satis = ok(client.post('/api/orders', headers=baslik, json={
    'entity_id': musteri, 'transaction_date': '2026-09-09',
    'due_date': '2026-09-30', 'warehouse_id': depo,
    'items': [{'product_id': urun, 'quantity': 400, 'unit_price': 20,
               'vat_rate': 20}]}))
dagitim = hareketler('orders', satis['id'])
print('DAGITIM %r' % (dagitim,))
assert dagitim == [
    ('ALIS-1', Decimal('-300')),
    ('HASAT-%d' % hasat, Decimal('-100')),
], ('SKT-SIZ PARTİ SIRAYA YANLIŞ GİRDİ', dagitim)
# İKİNCİ hasat partisine HİÇ dokunulmadı: FEFO'nun üçüncü anahtarı
# `created_at`tir ve iki SKT'siz parti arasında ÖNCE GİREN seçilir.
assert partiler(urun)['HASAT-%d' % hasat2] == (Decimal('400'), None, depo)
assert partiler(urun)['HASAT-%d' % hasat] == (Decimal('900'), None, depo)
print('HASAT-LOT-FEFO-TAMAM')
''', tmp_path / "1b-f-fefo.db", "HASAT-LOT-FEFO-TAMAM")
    assert "('ALIS-1'" in cikti


def test_SENT_olay_YENIDEN_KUYRUKLANAMAZ_ve_TEKRAR_TESLIM_PARTIYI_BUYUTMEZ(
    tmp_path: Path,
) -> None:
    """İdempotensinin iki gözlenebilir katmanı; sonuç: bir parti, bir hareket.

    Uç 409 verir (katman 2) VE — uç atlanıp olay ELLE `PENDING`e çekilse bile
    — göç 0060'ın kısmi benzersiz indeksi hareketi reddeder, `_kurtar`
    `db.rollback()` yapar ve PARTİ ARTIŞI DA GERİ ALINIR (katman 3). İkinci
    bacak olmadan birincisi yalnız UCU ölçerdi; at-least-once bir kuyrukta
    asıl soru ucun atlandığı yolda ne olduğudur.
    """
    _kos(_HASAT_UYGULA + r'''
olay = hasat_olayi(hasat)
assert olay['status'] == 'SENT', olay
onceki_parti = partiler(urun)
assert tarla_hareket_sayisi() == 1, tarla_hareket_sayisi()

# --- KATMAN 2: UÇ REDDEDER ------------------------------------------------
cevap = client.post('/api/field-integration-events/%d/requeue' % olay['id'],
                    headers=baslik)
assert cevap.status_code == 409, (cevap.status_code, cevap.text)
assert cevap.json()['detail']['code'] == 'EVENT_ALREADY_SENT', cevap.text
# Durum kodu tek başına reddin ETKİSİZ olduğunu SÖYLEMEZ.
assert partiler(urun) == onceki_parti, partiler(urun)
assert tarla_hareket_sayisi() == 1

# --- KATMAN 3: UÇ ATLANSA BİLE --------------------------------------------
with SessionLocal() as db:
    db.execute(_sql("UPDATE field_integration_events SET status = 'PENDING' "
                    "WHERE company_id = :c AND id = :i"),
               {'c': cid, 'i': olay['id']})
    db.commit()
sayac = tuket()
print('YENIDEN %r' % (sayac,))
assert sayac['SENT'] == 0, ('ikinci hareket YAZILDI', sayac)
assert partiler(urun) == onceki_parti, (
    'TEKRAR TESLİM PARTİYİ BÜYÜTTÜ', onceki_parti, partiler(urun))
assert parti_satir_sayisi() == 1, ('İKİNCİ PARTİ SATIRI AÇILDI',
                                   parti_satir_sayisi())
assert tarla_hareket_sayisi() == 1, tarla_hareket_sayisi()
print('HASAT-LOT-IDEMPOTENSI-TAMAM')
''', tmp_path / "1b-f-idem.db", "HASAT-LOT-IDEMPOTENSI-TAMAM")


def test_KIRACI_hasat_partileri_AYRI(tmp_path: Path) -> None:
    """Bir firmanın tüketimi ötekinin partisini AÇMAZ, GÖRMEZ.

    Kiracı yüklemleri `_parti_ac`ın hem okuma hem yazma sorgusundadır ve o
    dosyada 1B-A'dan beri çivili; BURADA ölçülen şey tüketicinin o yüklemi
    OLAYIN firmasıyla beslediğidir. Yüklem doğru ama besleme yanlış olsaydı
    parti komşunun defterinde açılırdı ve hiçbir statik kapı bunu görmezdi.
    """
    _kos(r'''
ZAMAN = '2026-08-01T00:00:00'
DIGER = 2

# İKİNCİ FİRMA HAM SQL İLE: bu dosyanın konusu firma açma ucu değil, defter.
with SessionLocal() as db:
    db.execute(_sql(
        "INSERT INTO companies (id,name,is_active,negative_stock_policy,"
        "credit_limit_policy,farm_area_override_policy,"
        "farm_early_harvest_policy,farm_spraying_dose_required,"
        "service_parts_mode,created_at) VALUES (:i,'Firma B',:a,'ALLOW',"
        "'BLOCK','BLOCK','BLOCK',:f,'IMMEDIATE',:z)"),
        {'i': DIGER, 'a': True, 'f': False, 'z': ZAMAN})
    db.execute(_sql(
        "INSERT INTO warehouses (company_id,name,is_default,is_active,"
        "warehouse_type) VALUES (:c,'Depo B',:d,:a,'FIXED')"),
        {'c': DIGER, 'd': True, 'a': True})
    db.execute(_sql(
        "INSERT INTO products (id,name,purchase_price,sale_price,vat_rate,"
        "stock,unit,price_per,active,critical_stock,minimum_stock,company_id,"
        "base_unit) VALUES (:i,'Bugday B',0,0,0,'0.0000','KG','unit',:a,0,0,"
        ":c,'KG')"), {'i': 9001, 'a': True, 'c': DIGER})
    db.execute(_sql(
        "INSERT INTO farms (id,company_id,code,name,status,created_at,"
        "updated_at) VALUES (:i,:c,'fb','Ciftlik B','ACTIVE',:z,:z)"),
        {'i': 9001, 'c': DIGER, 'z': ZAMAN})
    db.execute(_sql(
        "INSERT INTO farm_parcels (id,company_id,farm_id,code,name,"
        "area_decare,status,created_at,updated_at) VALUES (:i,:c,:f,'pb',"
        "'Parsel B','40.0000','ACTIVE',:z,:z)"),
        {'i': 9001, 'c': DIGER, 'f': 9001, 'z': ZAMAN})
    db.execute(_sql(
        "INSERT INTO crop_seasons (id,company_id,parcel_id,season_year,crop,"
        "product_id,status,created_at,updated_at) VALUES (:i,:c,:p,2026,"
        "'Bugday',:u,'ACTIVE',:z,:z)"),
        {'i': 9001, 'c': DIGER, 'p': 9001, 'u': 9001, 'z': ZAMAN})
    db.execute(_sql(
        "INSERT INTO field_harvests (id,company_id,season_id,harvested_on,"
        "quantity,unit,status,created_at,updated_at) VALUES (:i,:c,:s,"
        "'2026-08-15','777.0000','KG','RECORDED',:z,:z)"),
        {'i': 9001, 'c': DIGER, 's': 9001, 'z': ZAMAN})
    db.execute(_sql(
        "INSERT INTO field_integration_events (company_id,source_type,"
        "source_id,target,idempotency_key,status,attempts,created_at,"
        "updated_at) VALUES (:c,'field_harvest',:s,'stock',:k,'PENDING',0,"
        ":z,:z)"),
        {'c': DIGER, 's': 9001, 'k': 'field_harvest:9001:stock', 'z': ZAMAN})
    db.commit()
''' + _HASAT_UYGULA + r'''
# A FİRMASININ TÜKETİMİ B'NİN OLAYINA DOKUNMADI.
assert parti_satir_sayisi(DIGER) == 0, (
    'A firmasının tüketimi B firmasında parti açtı', parti_satir_sayisi(DIGER))

sayac_b = tuket(DIGER)
assert sayac_b['SENT'] == 1, sayac_b
assert parti_satir_sayisi(DIGER) == 1, parti_satir_sayisi(DIGER)
assert parti_satir_sayisi() == 1, parti_satir_sayisi()

with SessionLocal() as db:
    satir = db.execute(_sql(
        "SELECT company_id, product_id, lot_code, quantity, expiry_date "
        "FROM product_lots WHERE company_id = :c"),
        {'c': DIGER}).mappings().one()
assert int(satir['company_id']) == DIGER, satir
assert int(satir['product_id']) == 9001, satir
assert satir['lot_code'] == 'HASAT-9001', satir
assert Decimal(str(satir['quantity'])) == Decimal('777.0000'), satir
assert satir['expiry_date'] is None, satir

# A'nın OKUMA UCU B'nin partisini GÖRMEZ.
assert set(partiler(urun)) == {'HASAT-%d' % hasat}, partiler(urun)
print('HASAT-LOT-KIRACI-TAMAM')
''', tmp_path / "1b-f-kiraci.db", "HASAT-LOT-KIRACI-TAMAM")


def test_bayrak_KAPALIYKEN_hicbir_PARTI_acilmiyor(tmp_path: Path) -> None:
    """VARSAYILAN KAPALI: tüketici koşmaz, olay PENDING, DEFTER BOŞ.

    `field_stock_outbox_enabled` üretimde `False`tur ve bu dilim onu
    AÇMIYOR. Bayrak kapalıyken hasat yine yazılır ve olayı yine üretilir ama
    HİÇBİR parti satırı açılmaz — yani bu dilim kapalı bayrağın altında
    DAVRANIŞ DEĞİŞTİRMEZ. Ölçüm zamanlayıcı thread'inin YOKLUĞUNU da soruyor:
    yalnız satır saymak, döngünün henüz dönmediği bir pencerede de yeşil
    olurdu (`tests/test_field_stok_zamanlayici.py`nin ölçülmüş kalıbı).
    """
    env = _cevre(tmp_path / "1b-f-bayrak.db")
    env["FIELD_STOCK_OUTBOX_INTERVAL_SECONDS"] = "1"
    env.pop("FIELD_STOCK_OUTBOX_ENABLED", None)
    kaynak = _KURULUM + r'''
import threading
import time

assert settings.field_stock_outbox_enabled is False, (
    'bu koşum bayrağın KAPALI olduğu varsayımı üzerine kurulu')

urun = urun_ac('Bayrak Bugday')
sezon = sezon_ac('hlb', urun)
hasat = hasat_ac(sezon, '1000')

# TestClient BAĞLAM YÖNETİCİSİ OLARAK: lifespan burada koşar, yani
# zamanlayıcı AÇILACAKSA burada açılır.
with TestClient(app) as canli:
    assert canli.get('/api/live').status_code == 200
    # Aralık 1 saniye; bayrak açık olsaydı bu pencerede KESİNLİKLE işlerdi.
    time.sleep(3)
    calisan = [t for t in threading.enumerate()
               if t.name == 'field-stock-outbox-scheduler' and t.is_alive()]
    assert calisan == [], calisan

olay = hasat_olayi(hasat)
assert olay['status'] == 'PENDING', olay
assert int(olay['attempts']) == 0, olay
assert parti_satir_sayisi() == 0, ('BAYRAK KAPALIYKEN PARTİ AÇILDI',
                                   parti_satir_sayisi())
assert tarla_hareket_sayisi() == 0, tarla_hareket_sayisi()
assert partiler(urun) == {}, partiler(urun)
print('HASAT-LOT-BAYRAK-KAPALI-TAMAM')
'''
    tamam = subprocess.run(
        [sys.executable, "-c", kaynak], cwd=BACKEND, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=900,
    )
    assert tamam.returncode == 0, tamam.stdout + "\n" + tamam.stderr
    assert "HASAT-LOT-BAYRAK-KAPALI-TAMAM" in tamam.stdout, tamam.stdout


# --------------------------------------------------------- mutasyonlar ---
#
# HEPSİ MODÜLÜ YAMALAYARAK kuruluyor, kaynağı yeniden yazarak DEĞİL: yama
# sürecin içinde kalır, çalışma ağacında iz bırakmaz ve hangi sözleşmenin
# kırıldığı çağrı yerinde okunur.


def test_MUTASYON_parti_kodu_HASADA_BAGLI_DEGILSE_KIRMIZI(tmp_path: Path) -> None:
    """Kod sabitlenirse İKİ HASAT TEK PARTİDE birleşir — toplam yine DOĞRU.

    Bu mutasyonun tehlikesi tam olarak sessizliğidir: `product_lots` toplamı
    1400 kalır, stok 1400 kalır, hiçbir kısıt ısırmaz. Kaybolan tek şey
    "hangi hasat sahada" sorusunun cevabıdır — bu dilimin BÜTÜN sebebi.
    """
    _kos_kirmizi(r'''
C._parti_kodu = lambda hasat_id: 'HASAT'
print('MUTASYON-KURULDU')
''' + _HASAT_UYGULA + r'''
hasat2 = hasat_ac(sezon, '400')
assert tuket()['SENT'] == 1
assert parti_satir_sayisi() == 2, (
    'IKI HASAT TEK PARTIYE DUSTU: parti kodu hasada bagli degil; geri '
    'cagirma kaydi hangi hasadi isaret edecegini SOYLEYEMEZ. Defterde %d '
    'satir var.' % parti_satir_sayisi())
print('BEKLENMEYEN-YESIL')
''', tmp_path / "1b-f-mut-kod.db", "IKI HASAT TEK PARTIYE DUSTU")


def test_MUTASYON_SKT_UYDURULURSA_KIRMIZI(tmp_path: Path) -> None:
    """Hasada SKT yazmak bir OLGU UYDURMAKTIR.

    Uydurulan tarih hasat partisini FEFO sırasının ÖNÜNE taşır (NULL-son
    kuralından çıkarır), yani satış yanlış malı çıkarır. Hiçbir hata çıkmaz
    ve geri çağırma yanlış partiyi işaret eder.
    """
    _kos_kirmizi(r'''
_gercek_ac = C._parti_ac
C._parti_ac = lambda db, cid, **k: _gercek_ac(
    db, cid, **dict(k, expiry_date='2027-01-01'))
print('MUTASYON-KURULDU')
''' + _HASAT_UYGULA + r'''
kod = 'HASAT-%d' % hasat
skt = partiler(urun)[kod][1]
assert skt is None, (
    'SKT UYDURULDU: hasadin son kullanma tarihi YOKTUR ve defter %r diyor. '
    '`None` bir BEYANDIR, sentinel ise hicbir sey soylemez.' % (skt,))
print('BEKLENMEYEN-YESIL')
''', tmp_path / "1b-f-mut-skt.db", "SKT UYDURULDU")


def test_MUTASYON_DEFTER_ATLANIRSA_KIRMIZI(tmp_path: Path) -> None:
    """Parti açılmazsa hareket `lot_id` NULL yazılır — dilimden ÖNCEKİ kusur.

    Mutasyon `_parti_ac`ı `None` döndürür yapıyor: hasat yine stok yazar,
    sayaç yine `SENT` der, hiçbir kova dolmaz. Kırmızının kapının KENDİ
    metniyle gelmesi bu yüzden şart — sessizce eski davranışa dönüş.
    """
    _kos_kirmizi(r'''
C._parti_ac = lambda *a, **k: None
print('MUTASYON-KURULDU')
''' + _HASAT_UYGULA + r'''
olay = hasat_olayi(hasat)
dagitim = hareketler('field_integration_event', olay['id'])
assert dagitim == [('HASAT-%d' % hasat, Decimal('1000'))], (
    'HAREKET PARTISIZ YAZILDI: defter atlandi, stok oynadi. Yazilan: %r'
    % (dagitim,))
print('BEKLENMEYEN-YESIL')
''', tmp_path / "1b-f-mut-defter.db", "HAREKET PARTISIZ YAZILDI")


def test_MUTASYON_YANLIS_DEPODA_ACILIRSA_KIRMIZI(tmp_path: Path) -> None:
    """Parti başka depoda açılırsa mal bir depoya girer, defter başkasına.

    `_parti_ac`ın tekilliği depoyu İÇERİR, yani yanlış depo bir HATA değil
    AYRI BİR SATIR üretir: toplam yine 1000'dir ve satış — depo yüklemi
    `_parti_tuket`in sorgusunda olduğu için — o partiyi HİÇ göremez.
    """
    _kos_kirmizi(r'''
ikinci = ok(client.post('/api/warehouses', headers=baslik,
                        json={'name': 'Hasat Yedek Depo', 'code': 'HYD'}))['id']
_gercek_ac = C._parti_ac
C._parti_ac = lambda db, cid, **k: _gercek_ac(
    db, cid, **dict(k, warehouse_id=ikinci))
print('MUTASYON-KURULDU')
''' + _HASAT_UYGULA + r'''
kod = 'HASAT-%d' % hasat
parti_deposu = partiler(urun)[kod][2]
assert parti_deposu == depo, (
    'PARTI YANLIS DEPODA ACILDI: mal %d numarali depoya girdi, parti %d '
    'numarada acildi; ikisi sessizce ayrisir.' % (depo, parti_deposu))
print('BEKLENMEYEN-YESIL')
''', tmp_path / "1b-f-mut-depo.db", "PARTI YANLIS DEPODA ACILDI")
