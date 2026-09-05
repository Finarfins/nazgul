"""PostgreSQL ikizi: kantar fişi ŞEMASININ gerçek sayılarla ve gerçek
kısıtlarla eşi.

Göç `20260904_0069`. Bu dilim kağıdı SAKLAR ve deftere hiçbir şey yazmaz;
bu dosya da o saklamanın ÜÇ iddiasını ölçer — üçü de SQLite'ta GERÇEKTEN
SINANAMAZ.

--- BU İKİZ NEDEN VAR ------------------------------------------------------

1. **BİLEŞİK YABANCI ANAHTARIN GERÇEKTEN ISIRMASI.** SQLite'ta yabancı
   anahtar uygulaması varsayılan olarak KAPALIDIR (`PRAGMA foreign_keys`),
   yani çapraz kiracı bir referans orada YEŞİL kalırdı. Bu göçün iki bileşik
   anahtarı var (`fiş -> hasat` ve `kesinti -> fiş`) ve ikisinin de TEK işi
   bir kiracının satırının BAŞKA kiracının satırını işaret etmesini
   engellemektir. Uygulanmayan bir yabancı anahtar, savunma DEĞİL süstür.

2. **`NUMERIC(24,10)` KATSAYI ÖLÇEĞİ.** `entered_factor` "o gün neye
   inanıldığının" kanıtıdır ve YUVARLANMAMASI şarttır. SQLite `NUMERIC`i
   tür/ölçek DAYATMAZ: yanlış ölçekli ya da kayan noktaya dönmüş bir katsayı
   orada SESSİZCE geçer ve kanıt sessizce bozulur.

3. **`NUMERIC(18,4)` MİKTAR ÖLÇEĞİ.** `base_quantity` saklanan TEK türevdir
   ve satırın kendi kendini doğrulaması onun ölçeğine bağlıdır: çarpımı
   yeniden yapan biri saklanan değerle karşılaştıracaksa, saklanan değerin
   ölçeği dayatılmış olmalıdır.

--- ÖLÇÜLEN KÖK SEBEP, İDDİA DEĞİL -----------------------------------------

0066'nın dersi: belgelenmiş ama savunmayan bir savunma, savunma
OLMAMASINDAN kötüdür. Bu yüzden aşağıdaki testler kısıtın VARLIĞINI değil
GERÇEKTEN REDDETTİĞİNİ ölçüyor — her biri kısıtı ihlal eden bir yazma
deneyip `IntegrityError` bekliyor.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DataError, IntegrityError

BACKEND = Path(__file__).resolve().parent

FIRMA_ADI = "KANTAR FİŞİ İKİZİ firması"
KOMSU_ADI = "KANTAR FİŞİ İKİZİ komşu firması"

# DONMUŞ GÜN — `date.today()` bu dosyada GEÇMEZ; takvime bağlı bir test
# altı ay sonra kusuru DEĞİL takvimi gösterir.
BUGUN = date(2026, 9, 4)


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("Kantar fişi ikizi APP_TEST_DATABASE_URL ister")
    return url


def _temizle(engine) -> None:
    with engine.begin() as baglanti:
        for deyim in (
            "DELETE FROM field_harvest_ticket_deductions WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM field_harvest_tickets WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM field_harvests WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM crop_seasons WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM farm_parcels WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM farms WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM companies WHERE name IN (:a, :b)",
        ):
            baglanti.execute(text(deyim), {"a": FIRMA_ADI, "b": KOMSU_ADI})


@pytest.fixture()
def motor():
    config = Config(str(BACKEND / "alembic.ini"))
    engine = create_engine(_url())
    command.upgrade(config, "head")
    _temizle(engine)
    try:
        yield engine
    finally:
        _temizle(engine)
        engine.dispose()


def _hasat_kur(baglanti, firma_adi: str) -> tuple[int, int]:
    """Bir firma ve ona ait BİR hasat satırı kurar; (company_id, harvest_id)."""
    simdi = datetime.now(timezone.utc)
    cid = baglanti.execute(
        text(
            "INSERT INTO companies (name, is_active, created_at) "
            "VALUES (:ad, true, :simdi) RETURNING id"
        ),
        {"ad": firma_adi, "simdi": simdi},
    ).scalar_one()
    ciftlik = baglanti.execute(
        text(
            "INSERT INTO farms (company_id, code, name, status, created_at, updated_at) "
            "VALUES (:cid, 'K1', 'İkiz Çiftlik', 'ACTIVE', :s, :s) RETURNING id"
        ),
        {"cid": cid, "s": simdi},
    ).scalar_one()
    parsel = baglanti.execute(
        text(
            "INSERT INTO farm_parcels "
            "(company_id, farm_id, code, name, area_decare, status, created_at, updated_at) "
            "VALUES (:cid, :f, 'KP', 'İkiz Parsel', 10, 'ACTIVE', :s, :s) RETURNING id"
        ),
        {"cid": cid, "f": ciftlik, "s": simdi},
    ).scalar_one()
    sezon = baglanti.execute(
        text(
            "INSERT INTO crop_seasons "
            "(company_id, parcel_id, season_year, crop, status, created_at, updated_at) "
            "VALUES (:cid, :p, 2026, 'Buğday', 'ACTIVE', :s, :s) RETURNING id"
        ),
        {"cid": cid, "p": parsel, "s": simdi},
    ).scalar_one()
    hasat = baglanti.execute(
        text(
            "INSERT INTO field_harvests "
            "(company_id, season_id, harvested_on, quantity, unit, status, created_at, updated_at) "
            "VALUES (:cid, :sz, :g, 1000, 'KG', 'RECORDED', :s, :s) RETURNING id"
        ),
        {"cid": cid, "sz": sezon, "g": BUGUN, "s": simdi},
    ).scalar_one()
    return cid, hasat


def _fis_yaz(baglanti, cid: int, hasat_id: int, **fazla):
    simdi = datetime.now(timezone.utc)
    alanlar = {
        "cid": cid,
        "h": hasat_id,
        "brut": Decimal("1000.0000"),
        "birim": "KG",
        "katsayi": Decimal("1.0000000000"),
        "taban": Decimal("1000.0000"),
        "s": simdi,
    }
    alanlar.update(fazla)
    return baglanti.execute(
        text(
            "INSERT INTO field_harvest_tickets "
            "(company_id, harvest_id, gross_entered_quantity, entered_unit, "
            " entered_factor, base_quantity, created_at, updated_at) "
            "VALUES (:cid, :h, :brut, :birim, :katsayi, :taban, :s, :s) RETURNING id"
        ),
        alanlar,
    ).scalar_one()


# ===========================================================================
# 1. BİLEŞİK YABANCI ANAHTAR GERÇEKTEN ISIRIYOR
# ===========================================================================

def test_FIS_baska_kiracinin_HASADINI_isaret_EDEMEZ(motor) -> None:
    """`(company_id, harvest_id) -> field_harvests(company_id, id)`.

    SQLite'ta bu yazma YEŞİL kalırdı (`PRAGMA foreign_keys` kapalı), yani
    iddia YALNIZ burada ölçülebilir. Kusur teorik değil: hasat kimliği
    tahmin edilebilir bir tam sayıdır ve kiracı yüklemi olmayan tek bir
    uç, komşunun hasadına fiş yazdırırdı.
    """
    with motor.begin() as baglanti:
        cid, _ = _hasat_kur(baglanti, FIRMA_ADI)
        _, komsu_hasat = _hasat_kur(baglanti, KOMSU_ADI)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _fis_yaz(baglanti, cid, komsu_hasat)


def test_KESINTI_baska_kiracinin_FISINI_isaret_EDEMEZ(motor) -> None:
    """`(company_id, ticket_id) -> field_harvest_tickets(company_id, id)`.

    İkinci bileşik anahtar AYRICA ölçülüyor: birincisinin çalışması
    ikincisini KANITLAMAZ, ve kesinti satırı fişin netini belirlediği için
    çapraz kiracı bir kesinti komşunun fişinin netini DEĞİŞTİRİRDİ.
    """
    with motor.begin() as baglanti:
        cid, hasat = _hasat_kur(baglanti, FIRMA_ADI)
        komsu_cid, komsu_hasat = _hasat_kur(baglanti, KOMSU_ADI)
        komsu_fis = _fis_yaz(baglanti, komsu_cid, komsu_hasat)
        _fis_yaz(baglanti, cid, hasat)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            baglanti.execute(
                text(
                    "INSERT INTO field_harvest_ticket_deductions "
                    "(company_id, ticket_id, label, rate_percent, created_at, updated_at) "
                    "VALUES (:cid, :t, 'rutubet', 2, :s, :s)"
                ),
                {"cid": cid, "t": komsu_fis, "s": datetime.now(timezone.utc)},
            )


# ===========================================================================
# 2. ÖLÇEK GERÇEKTEN DAYATILIYOR
# ===========================================================================

def test_KATSAYI_ON_BASAMAGI_KORUNUYOR_yuvarlanmiyor(motor) -> None:
    """`entered_factor NUMERIC(24,10)` — on ondalık basamak AYNEN geri gelir.

    Katsayı bu satırın KANITIDIR; yuvarlanmış bir kanıt kanıt değildir.
    SQLite ölçeği dayatmadığı için bu iddia orada SESSİZCE geçerdi.
    """
    katsayi = Decimal("1.2345678901")
    with motor.begin() as baglanti:
        cid, hasat = _hasat_kur(baglanti, FIRMA_ADI)
        fis = _fis_yaz(baglanti, cid, hasat, katsayi=katsayi)

    with motor.connect() as baglanti:
        okunan = baglanti.execute(
            text("SELECT entered_factor FROM field_harvest_tickets WHERE id=:i"),
            {"i": fis},
        ).scalar_one()
    assert okunan == katsayi, (okunan, katsayi)
    # Ölçek de AYNEN duruyor: `Decimal` eşitliği 1.23 ile 1.2300000000'i
    # eşit sayar, bu yüzden basamak sayısı AYRICA ölçülüyor.
    assert okunan.as_tuple().exponent == -10, okunan.as_tuple()


def test_MIKTAR_ONDORT_BASAMAGI_ASAMAZ_tasma_REDDEDILIR(motor) -> None:
    """`NUMERIC(18,4)` — 14 tam basamaktan büyük miktar REDDEDİLİR.

    SQLite'ta bu yazma geçer ve sayı olduğu gibi saklanır; PostgreSQL
    taşmayı reddeder. Ölçek dayatılmasaydı `base_quantity`nin satırı
    doğrulama işlevi çökerdi.
    """
    with motor.begin() as baglanti:
        cid, hasat = _hasat_kur(baglanti, FIRMA_ADI)

    with pytest.raises((DataError, IntegrityError)):
        with motor.begin() as baglanti:
            _fis_yaz(
                baglanti, cid, hasat,
                brut=Decimal("123456789012345.0000"),
                taban=Decimal("123456789012345.0000"),
            )


# ===========================================================================
# 3. CHECK KISITLARI GERÇEKTEN REDDEDİYOR
# ===========================================================================

@pytest.mark.parametrize(
    "alan,deger",
    [
        ("brut", Decimal("0")),
        ("brut", Decimal("-1")),
        ("katsayi", Decimal("0")),
        ("katsayi", Decimal("-1")),
        ("taban", Decimal("0")),
    ],
)
def test_POZITIFLIK_kisitlari_GERCEKTEN_REDDEDER(motor, alan, deger) -> None:
    """Üç `> 0` kısıtı, ihlal eden yazma ile ölçülüyor.

    Sıfır brüt "tartılmamış" demektir ve tartılmamış bir fiş kağıt DEĞİLDİR;
    sıfır katsayı ürünü YOK EDER; sıfır taban miktar ise sıfır olmayan bir
    girişin sessizce kaybolmasıdır (`app/units.py`nin `UrunTemsilEdilemez`
    reddinin şemadaki karşılığı).
    """
    with motor.begin() as baglanti:
        cid, hasat = _hasat_kur(baglanti, FIRMA_ADI)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _fis_yaz(baglanti, cid, hasat, **{alan: deger})


@pytest.mark.parametrize("oran", [Decimal("-0.0001"), Decimal("100.0001")])
def test_KESINTI_ORANI_araligi_GERCEKTEN_REDDEDER(motor, oran) -> None:
    """`rate_percent >= 0 AND rate_percent <= 100`, TEK SATIRIN sınırı.

    TOPLAMIN 100'ü aşmaması SATIRLAR ARASI bir kuraldır ve `CHECK` ile
    ifade EDİLEMEZ; onu uç doğrular. Bu test yalnız tek satırın sınırını
    ölçüyor ve o sınırın GERÇEKTEN ısırdığını gösteriyor.
    """
    with motor.begin() as baglanti:
        cid, hasat = _hasat_kur(baglanti, FIRMA_ADI)
        fis = _fis_yaz(baglanti, cid, hasat)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            baglanti.execute(
                text(
                    "INSERT INTO field_harvest_ticket_deductions "
                    "(company_id, ticket_id, label, rate_percent, created_at, updated_at) "
                    "VALUES (:cid, :t, 'sınır', :o, :s, :s)"
                ),
                {"cid": cid, "t": fis, "o": oran, "s": datetime.now(timezone.utc)},
            )


# ===========================================================================
# 4. KAĞIDIN KİMLİĞİ: NUMARASIZ FİŞ İKİ KEZ GİRİLEBİLİR
# ===========================================================================

def test_NUMARASIZ_fis_IKI_KEZ_girilebilir_NULL_CAKISMAZ(motor) -> None:
    """`UNIQUE(company_id, harvest_id, ticket_no)` ve NULL'ların çakışmaması.

    Bu bir KUSUR DEĞİL, adı konmuş bir BEDELDİR: numarasız iki fiş aynı
    hasada girilebilir. Alternatif numara UYDURMAKTI ve o, tekrar korumasını
    YANLIŞ bir kimliğe bağlardı. Bedel burada ÖLÇÜLÜ duruyor ki bir gün
    değiştirilirse bu test kırmızı olsun ve karar GÖRÜNSÜN.
    """
    with motor.begin() as baglanti:
        cid, hasat = _hasat_kur(baglanti, FIRMA_ADI)
        _fis_yaz(baglanti, cid, hasat)
        _fis_yaz(baglanti, cid, hasat)

    with motor.connect() as baglanti:
        adet = baglanti.execute(
            text(
                "SELECT count(*) FROM field_harvest_tickets "
                "WHERE company_id=:cid AND harvest_id=:h AND ticket_no IS NULL"
            ),
            {"cid": cid, "h": hasat},
        ).scalar_one()
    assert adet == 2, adet

    # Ama NUMARALI fiş iki kez girilemez.
    with motor.begin() as baglanti:
        _fis_yaz(baglanti, cid, hasat, **{})
        baglanti.execute(
            text("UPDATE field_harvest_tickets SET ticket_no='A-1' WHERE id="
                 "(SELECT max(id) FROM field_harvest_tickets WHERE company_id=:cid)"),
            {"cid": cid},
        )
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _fis_yaz(baglanti, cid, hasat)
            baglanti.execute(
                text("UPDATE field_harvest_tickets SET ticket_no='A-1' WHERE id="
                     "(SELECT max(id) FROM field_harvest_tickets WHERE company_id=:cid)"),
                {"cid": cid},
            )


# ===========================================================================
# 5. UÇ SMOKE'LARININ PG İKİZİ — AYRI DOSYA AÇILMADI
# ===========================================================================
#
# Üç davranış smoke'u (`tests/test_kantar_fisi_defter.py`,
# `tests/test_kantar_fisi_sozlesme.py`, `tests/test_kantar_fisi_sonluluk.py`)
# SQLite'ta koşuyor; burada AYNI gövde gerçek PostgreSQL'e karşı koşturulur
# (`test_farm_harvest_revenue_postgresql.py`nin kalıbı: importlib ile yükle,
# URL'yi ver). YENİ `*_postgresql.py` DOSYASI AÇILMADI — PG popülasyonu üç
# yerde 102'ye çivili ve bu dilim o sayıyı oynatmıyor.
#
# ÜÇ SMOKE AYNI VERİTABANINI VE AYNI BOOTSTRAP FİRMASINI PAYLAŞIR: giriş aday
# döngüsüyle (ortak `KantarFisi!123`), sayaçlar tabana göre FARK olarak
# yazıldı. Sıra bağımlılığı burada değil smoke'larda çözüldü ki SQLite ile PG
# aynı gövdeyi koşsun.


def _smoke(dosya: str, ad: str):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        f"kantar_{dosya}", BACKEND / "tests" / f"test_kantar_fisi_{dosya}.py"
    )
    modul = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(modul)
    return getattr(modul, ad)


@pytest.mark.postgresql
def test_kantar_fisi_defter_postgresql() -> None:
    """Beş senaryo, gerçek defter: PG'de de fiş stok hareketini oynatmıyor."""
    _smoke("defter", "run_defter_smoke")(_url())


@pytest.mark.postgresql
def test_kantar_fisi_sozlesme_postgresql() -> None:
    """Uç sözleşmesi + taban birim iki bacağı, gerçek NUMERIC ölçekleriyle."""
    _smoke("sozlesme", "run_sozlesme_smoke")(_url())


@pytest.mark.postgresql
def test_kantar_fisi_sonluluk_uc_postgresql() -> None:
    """Sonlu olmayan girdi: PG'de de 422 ve iki tabloda SIFIR satır."""
    _smoke("sonluluk", "run_sonluluk_smoke")(_url())
