"""SONLU OLMAYAN SAYILAR kantar fişi yoluna GİREMEZ — ÜÇ KATMAN, ÜÇ MUTASYON.

Bu dosya bir HATA DÜZELTMESİ DEĞİL, bir SÖZLEŞMEDİR. Ölçüldü ve raporlandı:
bugün hiçbir katman 500 üretmiyor. Ama korumanın İKİ katmanı da BAŞKA bir
kararın yan ürünü, yani kırılgan:

  (a) ŞEMA — Pydantic 2.13.x dördünü de 422 yapıyor ve koruma İKİ BAĞIMSIZ
      nöbetçiden geliyor. ÖLÇÜLDÜ, İKİ ALAN İÇİN AYRI AYRI (ilk yazım
      2.13.5, bu tur 2.13.4 — aynı sonuç; sınırlar GERÇEK alanlardan
      okunarak, `test_SEMA_GRID_*` bu tabloyu her koşumda YENİDEN ÖLÇER):

          gross_entered_quantity (gt=0, le=MAX_MIKTAR)
          allow_inf_nan  üst sınır   NaN     sNaN    Infinity  -Infinity
          -----------------------------------------------------------------
          varsayılan     var         finite  finite  finite    finite
          varsayılan     yok         finite  finite  finite    finite
          True           var         le      le      le        gt
          True           yok         gt      gt      KABUL     gt

          rate_percent (ge=0, le=100)
          allow_inf_nan  üst sınır   NaN     sNaN    Infinity  -Infinity
          -----------------------------------------------------------------
          varsayılan     var         finite  finite  finite    finite
          varsayılan     yok         finite  finite  finite    finite
          True           var         le      le      le        ge
          True           yok         ge      ge      KABUL     ge

      (hücrede Pydantic hata tipi: `finite` = finite_number, `le` =
      less_than_equal, `gt`/`ge` = greater_than / greater_than_equal.)

      Okuma: `allow_inf_nan` VARSAYILANDA dördü de SINIRA HİÇ ULAŞMADAN
      `finite_number` ile düşer — birinci nöbetçi. `allow_inf_nan=True`
      açılırsa sınırlar devreye girer ve Pydantic ÖNCE üst sınırı sorar:
      NaN/sNaN karşılaştırması bir cevap değil bir HATADIR, üst sınır onu
      "geçmedi" sayar (`le`); Infinity üst sınırda düpedüz düşer; -Infinity
      üst sınırı geçip ALT sınırda düşer. Üst sınır da kaldırılırsa
      NaN/sNaN/-Infinity alt sınırda düşer ama Infinity `Inf > 0` DOĞRU
      olduğu için GEÇER — ikinci nöbetçi de gitmiştir.

      Yani "Infinity" YALNIZ İKİSİ BİRDEN bozulursa geçer: `allow_inf_nan`
      açılacak VE üst sınır kaldırılacak. İKİ ALANDA DA AYNI; oran için üst
      sınır `le=100`dür.

      İLK YAZIMIN "NaN ALT sınırda düşer" cümlesi DÜZELTİLDİ: Python'da
      `Decimal("NaN") > 0` gerçekten `InvalidOperation` atar (aşağıda hâlâ
      çivili) ama Pydantic üst sınırı ÖNCE sorduğu için NaN'ın takıldığı
      nöbetçi `le`dir. Sonuç aynı (422), hikâye farklı; hikâye ölçümden
      yazılır.

  (b) ÇÖZÜCÜ — `units.resolve` kendi sonluluk kapısını taşıyor ve reddi
      AİLE İÇİNDEDİR (`BirimCozulemedi.MIKTAR_SONLU_DEGIL`). (a) gevşetilse
      bile bu katman tutar; `decimal.InvalidOperation` DIŞARI SIZMAZ.

  (c) UÇ — ikisinin BİRLEŞİK sonucu: istek 4xx alır, gövde REDDİN YERİNİ
      söyler ve HİÇBİR SATIR YAZILMAZ (iki tablo da önce/sonra sayılır).
      Üçüncü katman ayrıca ölçülüyor çünkü (a) ve (b) ayrı ayrı yeşilken uç
      yine de yarım bir satır bırakabilirdi — çözüm SQL'den ÖNCE
      çağrılmazsa.

      GÖVDE NE TAŞIR, ÖLÇÜLDÜ: bu girdiler için ateşleyen nöbetçi (a)'dır,
      yani gövde Pydantic `detail` listesidir ve `loc` ALANI ADIYLA gösterir
      (`gross_entered_quantity`, `deductions.<i>.rate_percent`,
      `ticket_net_quantity`). `sebep` sözcüğü (b) katmanının dilidir ve (a)
      ayaktayken bu girdiler oraya HİÇ ULAŞMAZ; (b)'nin uçtaki yüzü
      (`detail.sebep`) `test_kantar_fisi_sozlesme.py`de TABAN_BILDIRILMEMIS /
      BIRIM_TANIMSIZ / BOYUT_UYUSMAZLIGI bacaklarıyla ölçülüyor. Burada
      "sebep var" diye YAZMAK, ölçülmemiş bir şeyi iddia etmek olurdu.
"""
from __future__ import annotations

import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.farm_schemas import HarvestTicketDeductionWrite, HarvestTicketWrite
from app.units import BirimCozulemedi, resolve

BACKEND = Path(__file__).resolve().parents[1]

SONLU_OLMAYANLAR = ["NaN", "sNaN", "Infinity", "-Infinity"]


# ===========================================================================
# (a) ŞEMA KATMANI
# ===========================================================================

@pytest.mark.parametrize("ham", SONLU_OLMAYANLAR)
def test_SEMA_brut_SONLU_OLMAYANI_REDDEDER(ham: str) -> None:
    """`gross_entered_quantity` dördünü de 422 yapar.

    MUTASYON (ÖLÇÜLDÜ, İKİSİ BİRDEN gerekir): `_Taban`ın `ConfigDict`ine
    `allow_inf_nan=True` ekle VE `gross_entered_quantity`den
    `le=MAX_MIKTAR`ı kaldır -> YALNIZ `[Infinity]` durumu KIRMIZI olur.
    Tek başına `allow_inf_nan=True` HİÇBİR ŞEYİ kırmızıya çevirmez — üst
    sınır bağımsız bir nöbetçidir ve `test_SEMA_GRID_*` bunu her koşumda
    yeniden ölçer.
    """
    with pytest.raises(ValidationError):
        HarvestTicketWrite(
            harvest_id=1, gross_entered_quantity=ham, entered_unit="KG"
        )


@pytest.mark.parametrize("ham", SONLU_OLMAYANLAR)
def test_SEMA_kesinti_ORANI_da_SONLU_OLMAYANI_REDDEDER(ham: str) -> None:
    """Oran da brüt kadar korunur.

    Sonsuz bir oran türetilen neti EKSİ SONSUZA götürürdü; brütü koruyup
    oranı korumamak, kapıyı yarım açık bırakmak olurdu.
    """
    with pytest.raises(ValidationError):
        HarvestTicketDeductionWrite(label="rutubet", rate_percent=ham)


def test_SEMA_asimetrisi_NaN_ile_Infinity_AYNI_SEBEPLE_DUSMUYOR() -> None:
    """Python `Decimal` karşılaştırmalarının kendisi — ÖLÇÜLDÜ, akıl yürütülmedi.

      * `NaN > 0`      bir cevap değil bir HATADIR (`InvalidOperation`).
      * `-Infinity > 0` düpedüz YANLIŞTIR.
      * `Infinity > 0`  DOĞRUDUR, `Infinity <= MAX` YANLIŞTIR.

    Bu, grid tablosunun ham malzemesidir: Infinity'yi eleyebilecek TEK sınır
    üst sınırdır. Hangi nöbetçinin ÖNCE sorulduğu (Pydantic'te üst sınır)
    ise `test_SEMA_GRID_*`da alan alan ölçülüyor.
    """
    import decimal

    assert Decimal("Infinity") > 0
    assert not (Decimal("Infinity") <= Decimal("99999999999999.9999"))
    assert not (Decimal("-Infinity") > 0)
    with pytest.raises(decimal.InvalidOperation):
        _ = Decimal("NaN") > 0


# --- GRID: dört kombinasyon × iki alan, GERÇEK sınırlarla -------------------

def _sinirlar(model: type[BaseModel], alan: str) -> dict[str, Decimal]:
    """Gerçek alanın `gt/ge/le` sınırlarını metadata'dan okur.

    Sınırlar buradan geldiği için grid şemadan KOPAMAZ: biri `le`yi
    kaldırırsa bu yardımcı onu görmez, `test_SEMA_ust_siniri_ve_varsayilan_
    allow_inf_nan_YERINDE` kırmızı olur ve grid o hâliyle koşmaz.
    """
    sinir: dict[str, Decimal] = {}
    for meta in model.model_fields[alan].metadata:
        for ad in ("gt", "ge", "le"):
            if hasattr(meta, ad):
                sinir[ad] = Decimal(str(getattr(meta, ad)))
    return sinir


def _sonda(sinir: dict[str, Decimal], allow_inf_nan: bool, ust_sinir: bool):
    kw = {k: v for k, v in sinir.items() if k != "le" or ust_sinir}

    class Sonda(BaseModel):
        model_config = ConfigDict(allow_inf_nan=True) if allow_inf_nan else ConfigDict()
        v: Decimal = Field(**kw)

    return Sonda


def _olc(model: type[BaseModel], ham: str) -> str:
    try:
        model(v=ham)
    except ValidationError as exc:
        tipler = sorted({e["type"] for e in exc.errors()})
        assert len(tipler) == 1, tipler
        return tipler[0]
    return "KABUL"


# (alan sahibi, alan, alt sınır hata tipi)
_ALANLAR = [
    (HarvestTicketWrite, "gross_entered_quantity", "greater_than"),
    (HarvestTicketDeductionWrite, "rate_percent", "greater_than_equal"),
]

# (allow_inf_nan, üst sınır var) -> {ham: beklenen}; `ALT` alanın alt sınır tipi
_BEKLENEN = {
    (False, True):  {"NaN": "finite_number", "sNaN": "finite_number",
                     "Infinity": "finite_number", "-Infinity": "finite_number"},
    (False, False): {"NaN": "finite_number", "sNaN": "finite_number",
                     "Infinity": "finite_number", "-Infinity": "finite_number"},
    (True, True):   {"NaN": "less_than_equal", "sNaN": "less_than_equal",
                     "Infinity": "less_than_equal", "-Infinity": "ALT"},
    (True, False):  {"NaN": "ALT", "sNaN": "ALT",
                     "Infinity": "KABUL", "-Infinity": "ALT"},
}


@pytest.mark.parametrize("sahip,alan,alt", _ALANLAR, ids=["gross", "rate"])
@pytest.mark.parametrize("allow_inf_nan", [False, True], ids=["inf_nan=varsayilan", "inf_nan=True"])
@pytest.mark.parametrize("ust_sinir", [True, False], ids=["le=var", "le=yok"])
def test_SEMA_GRID_dort_kombinasyon_iki_alan(
    sahip: type[BaseModel], alan: str, alt: str, allow_inf_nan: bool, ust_sinir: bool
) -> None:
    """Başlıktaki iki tabloyu YENİDEN ÖLÇER; hücreler hata TİPİYLE çivili.

    Sonda modeli gerçek alanın sınırlarıyla kurulur (kopya sınır YOK).
    Tablonun anlamı: Infinity yalnız `allow_inf_nan=True` VE üst sınır yok
    ise geçer; iki alanda da böyle. Pydantic sürümü bu sırayı değiştirirse
    (örn. alt sınırı önce sorarsa) hücreler burada kırmızı olur ve tablo
    ölçümle yeniden yazılır — tahminle değil.
    """
    sonda = _sonda(_sinirlar(sahip, alan), allow_inf_nan, ust_sinir)
    beklenen = {
        ham: (alt if b == "ALT" else b)
        for ham, b in _BEKLENEN[(allow_inf_nan, ust_sinir)].items()
    }
    olculen = {ham: _olc(sonda, ham) for ham in SONLU_OLMAYANLAR}
    assert olculen == beklenen, (alan, allow_inf_nan, ust_sinir, olculen)


def test_SEMA_ust_siniri_ve_varsayilan_allow_inf_nan_YERINDE() -> None:
    """Gridin dayandığı iki olgu GERÇEK şemada duruyor.

    Grid "mutasyon olursa ne olur"u ölçer; bu test mutasyonun OLMADIĞINI.
    İkisi ayrı: biri tabloyu, öteki tablonun hangi satırında olduğumuzu
    söyler.
    """
    for sahip, alan, _ in _ALANLAR:
        assert sahip.model_config.get("allow_inf_nan") is not True, (sahip, alan)
        assert "le" in _sinirlar(sahip, alan), (sahip, alan, "üst sınır kaldırılmış")


# ===========================================================================
# (b) ÇÖZÜCÜ KATMANI — (a) gevşetilirse ARKA DURAK
# ===========================================================================

@pytest.mark.parametrize("ham", SONLU_OLMAYANLAR)
def test_COZUCU_SONLU_OLMAYANI_AILE_ICINDE_REDDEDER(ham: str) -> None:
    """`units.resolve` -> `BirimCozulemedi(MIKTAR_SONLU_DEGIL)`.

    AİLE İÇİ olması şart: `decimal.InvalidOperation` sızsaydı uç 500
    verirdi ve red, belgelenmiş `except BirimCozulemedi:` sözleşmesinden
    KAÇARDI.

    MUTASYON (ÖLÇÜLDÜ): `units.resolve`daki `is_finite()` kapısını kaldır ->
    bu dört durum KIRMIZI olur; (c) katmanı YEŞİL KALIR çünkü (a) önde
    durur. Yani uç testi çözücünün kapısını ÖLÇMEZ — onu yalnız bu test
    ölçer.
    """
    with pytest.raises(BirimCozulemedi) as hata:
        resolve(Decimal(ham), "KG", "KG")
    assert hata.value.sebep == BirimCozulemedi.MIKTAR_SONLU_DEGIL, hata.value.sebep


def test_COZUCU_reddi_InvalidOperation_SIZDIRMIYOR() -> None:
    """Red `BirimCozulemedi` ailesinin İÇİNDE; `decimal` istisnası dışarı çıkmaz."""
    import decimal

    for ham in SONLU_OLMAYANLAR:
        try:
            resolve(Decimal(ham), "KG", "KG")
        except BirimCozulemedi:
            pass
        except decimal.InvalidOperation as exc:  # pragma: no cover
            pytest.fail(f"{ham}: aile DIŞI istisna sızdı: {exc!r}")


# ===========================================================================
# (c) UÇ KATMANI — gerçek istek, taze veritabanı, SIFIR SATIR
# ===========================================================================

def run_sonluluk_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    assert "KANTAR FISI SONLULUK UC TAMAM" in completed.stdout, completed.stdout


def test_UC_sonlu_olmayan_brut_oran_ve_kagit_neti_4xx_ve_SIFIR_satir_sqlite(
    tmp_path: Path,
) -> None:
    """Dört değer × üç alan (brüt, HER kesinti oranı, kağıdın neti) -> 422,
    `loc` alanı adıyla gösterir, iki tabloda da satır sayısı DEĞİŞMEZ.

    Fikstür CANLIDIR: aynı yol sağlıklı bir istekle 201 veriyor, yani
    "sıfır satır" yazamayan bir yolun sessizliği değil, REDDİN sonucu.
    """
    run_sonluluk_smoke(f"sqlite:///{(tmp_path / 'kantar-sonluluk.db').as_posix()}")


_SMOKE = r'''
from fastapi.testclient import TestClient
from sqlalchemy import text as _sql

from app.db import SessionLocal
from app.main import app

# AILENIN ORTAK SIFRESI — bkz. `test_kantar_fisi_defter.py`: uc kantar
# smoke'u PG ikizinde ayni veritabanini paylasir, giris aday dongusuyle.
ADMIN_PW = 'KantarFisi!123'
URUN_ID = 4102
SONLU_OLMAYANLAR = ['NaN', 'sNaN', 'Infinity', '-Infinity']


def admin_headers(client):
    for candidate in ('admin123', ADMIN_PW):
        login = client.post('/api/auth/login',
                            json={'username':'admin','password':candidate})
        if login.status_code == 200:
            break
    assert login.status_code == 200, login.text
    body = login.json()
    h = {'Authorization':'Bearer '+body['access_token'],
         'X-Company-ID':str(body['companies'][0]['id'])}
    if candidate != ADMIN_PW:
        ch = client.post('/api/auth/change-password', headers=h,
                         json={'current_password':candidate,'new_password':ADMIN_PW})
        assert ch.status_code == 200, ch.text
        h['Authorization'] = 'Bearer '+ch.json()['access_token']
    return h, int(body['companies'][0]['id'])


def satir_sayilari(cid):
    """(fis, kesinti) — IKI TABLO DA, kiraci kapsamli."""
    with SessionLocal() as db:
        f = db.execute(_sql(
            "SELECT COUNT(*) FROM field_harvest_tickets WHERE company_id=:c"),
            {'c': cid}).scalar_one()
        k = db.execute(_sql(
            "SELECT COUNT(*) FROM field_harvest_ticket_deductions WHERE company_id=:c"),
            {'c': cid}).scalar_one()
    return int(f), int(k)


def loclar(cevap):
    return {tuple(str(p) for p in e['loc']) for e in cevap.json()['detail']}


with TestClient(app) as client:
    h, cid = admin_headers(client)
    with SessionLocal() as db:
        db.execute(_sql(
            "INSERT INTO products (id,name,purchase_price,sale_price,vat_rate,"
            "stock,unit,price_per,active,critical_stock,minimum_stock,company_id,"
            "base_unit) "
            "VALUES (:i,'Bugday',0,0,0,'0.0000','kg','unit',:aktif,0,0,:c,'KG')"),
            {'i': URUN_ID, 'c': cid, 'aktif': True})
        db.commit()
    ciftlik = client.post('/api/farms', headers=h,
                          json={'code':'kn','name':'Kantar Sonluluk'}).json()
    parsel = client.post('/api/farm-parcels', headers=h,
                         json={'farm_id':ciftlik['id'],'code':'knp','name':'Parsel',
                               'area_decare':'100.0000'}).json()
    sezon = client.post('/api/crop-seasons', headers=h,
                        json={'parcel_id':parsel['id'],'season_year':2026,
                              'crop':'Bugday','product_id':URUN_ID,
                              'started_on':'2026-03-01',
                              'planted_area_decare':'100.0000'})
    assert sezon.status_code == 201, sezon.text
    hasat = client.post('/api/field-harvests', headers=h,
                        json={'season_id':sezon.json()['id'],'harvested_on':'2026-07-10',
                              'quantity':'1000','unit':'KG'})
    assert hasat.status_code == 201, hasat.text
    hid = hasat.json()['id']

    def saglikli():
        return {'harvest_id': hid, 'gross_entered_quantity': '1000',
                'entered_unit': 'KG', 'ticket_net_quantity': '950',
                'deductions': [{'label': 'Rutubet', 'rate_percent': '2'},
                               {'label': 'Yabanci madde', 'rate_percent': '3'}]}

    # FIKSTUR CANLI: ayni yol 201 verebiliyor. Sayaclar TABANA GORE FARK:
    # PG ikizinde ayni firma baska kantar smoke'lariyla paylasiliyor.
    taban = satir_sayilari(cid)
    canli = client.post('/api/field-harvest-tickets', headers=h, json=saglikli())
    assert canli.status_code == 201, canli.text
    once = satir_sayilari(cid)
    assert once == (taban[0] + 1, taban[1] + 2), (taban, once)

    # Bozuk deger uc alana, her kesinti satirina AYRI AYRI konuyor.
    yerler = [
        ('gross_entered_quantity', ('body', 'gross_entered_quantity')),
        ('ticket_net_quantity',    ('body', 'ticket_net_quantity')),
        ('deductions.0',           ('body', 'deductions', '0', 'rate_percent')),
        ('deductions.1',           ('body', 'deductions', '1', 'rate_percent')),
    ]
    for ham in SONLU_OLMAYANLAR:
        for yer, loc in yerler:
            govde = saglikli()
            if yer.startswith('deductions.'):
                govde['deductions'][int(yer.split('.')[1])]['rate_percent'] = ham
            else:
                govde[yer] = ham
            cevap = client.post('/api/field-harvest-tickets', headers=h, json=govde)
            assert cevap.status_code == 422, (ham, yer, cevap.status_code, cevap.text)
            # Govde REDDIN YERINI soyler: Pydantic `detail`, `loc` alan adiyla.
            assert loc in loclar(cevap), (ham, yer, cevap.text)
    sonra = satir_sayilari(cid)
    assert sonra == once, ('RED SATIR BIRAKTI', once, sonra)

    print('KANTAR FISI SONLULUK UC TAMAM')
'''
