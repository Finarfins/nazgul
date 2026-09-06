"""PostgreSQL ikizi: parti/SKT DEPOSU ve FEFO seçicisinin GERÇEK sayılarla eşi.

Göç `20260903_0067` + `app/parti.py`.

--- YASAK KAPISI EMEKLİ EDİLDİ (FAZ 1B-A, göç 20260908_0073) --------------

Bu dosyada `test_PARTI_MIKTARI_bu_PR_da_HICBIR_YERDEN_guncellenmiyor`
adında bir kapı vardı ve şunu söylüyordu: `backend/app` altında (`app/parti.py`
hariç) `product_lots` LİTERALİ YOKTUR. Kendi düzyazısı o kapının nasıl
biteceğini ADIYLA yazmıştı — "bir çağıran eklendiği gün İKİSİNDEN BİRİ
kırmızı olur ve bu DOĞRUDUR."

O gün geldi: 1B-A alış yolunu parti defterine bağladı. Kapı GEVŞETİLMEDİ,
DARALTILARAK devredildi — `tests/test_1b_a_alis_lot.py` içindeki
`test_product_lots_YAZICISI_YALNIZ_transactions_py` artık "hiç yazıcı yok"
yerine "TEK yazıcı var ve adı `app/routers/transactions.py`" diyor, ayrıca
tabloyu ANAN dosyaların kümesini de KAPALI tutuyor. İkinci yarı (`app.parti`
ithali ve `fefo_sec` referansı) DEĞİŞMEDEN devam ediyor:
`test_fefo_sec_HALA_CAGIRANSIZ`. Bu dosyada kalanlar ŞEMA kapılarıdır.

--- BU İKİZ NEDEN VAR ------------------------------------------------------

Üç iddia SQLite'ta GERÇEKTEN SINANAMAZ ve üçü de bu göçün merkezinde:

1. `CHECK (quantity >= 0 AND quantity <> 'NaN'::numeric)`. NaN yarısı YALNIZ
   PostgreSQL'de vardır (göçün `_miktar_kisit_metni`si) çünkü savunduğu kusur
   yalnız orada: PostgreSQL `numeric` NaN SAKLAR ve onu her sonlu sayının
   ÜSTÜNE sıralar. SQLite'ın `NUMERIC`i tür/ölçek dayatmaz.

2. `NUMERIC(18,4)` ÖLÇEĞİ. Seçicinin dağıtım payları o sütuna yazılacak;
   SQLite'ta yanlış ölçekli bir yazma SESSİZCE geçerdi.

3. BİLEŞİK YABANCI ANAHTARIN GERÇEKTEN ISIRMASI. SQLite'ta yabancı anahtar
   uygulaması varsayılan olarak KAPALIDIR (`PRAGMA foreign_keys`), yani
   çapraz kiracı bir referans orada YEŞİL kalırdı.

--- ÖLÇÜLEN KÖK SEBEPLER, İDDİA DEĞİL --------------------------------------

Aşağıdaki testler kusurun KÖKÜNÜ de ayrıca ölçüyor, çünkü 0066'nın dersi
şuydu: belgelenmiş ama savunmayan bir savunma, savunma OLMAMASINDAN kötüdür.
Gerçek PostgreSQL 16.14'te ölçüldü ve testlerde iddia olarak duruyor:

    SELECT 'NaN'::numeric >= 0              ->  t    (yalın kısıt KABUL EDER)
    SELECT 'NaN'::numeric <> 'NaN'::numeric ->  f    (bu yüzden `<>` işe yarar)
    ORDER BY <date> ASC                     ->  NULL SONA   (PostgreSQL)
    (SQLite'ta AYNI sorgu                   ->  NULL BAŞA)

Sonuncusu, NULL-son kuralının neden `ORDER BY`a BIRAKILMADIĞININ kanıtıdır ve
`test_NULL_SON_kurali_DIYALEKTE_BIRAKILAMAZ_olcum` içinde duruyor.

--- MUTASYON 4'ÜN ADRESİ ---------------------------------------------------

`test_CHECK_kisiti_NaN_MIKTARI_GERCEKTEN_REDDEDER`. Kısıt yalın
`quantity >= 0` hâline döndürülürse O TEST kırmızı olur; kardeşi
`test_YALIN_KISIT_NaN_i_KABUL_EDERDI_olcum` ise neden kırmızı olduğunu
sayıyla söyler.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.parti import Parti, ParticiYetersiz, PartiSecilemedi, fefo_sec

BACKEND = Path(__file__).resolve().parent

FIRMA_ADI = "PARTİ SKT İKİZİ firması"
KOMSU_ADI = "PARTİ SKT İKİZİ komşu firması"
URUN_ADI = "PARTİ SKT İKİZİ ürünü"
KOMSU_URUN_ADI = "PARTİ SKT İKİZİ komşu ürünü"

# DONMUŞ GÜN — `date.today()` bu dosyada da GEÇMEZ. Gerekçe birim testlerinin
# başlığındakiyle aynı: takvime bağlı bir SKT testi altı ay sonra kusuru
# DEĞİL takvimi gösterir.
BUGUN = date(2026, 9, 3)


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("Parti/SKT ikizi APP_TEST_DATABASE_URL ister")
    return url


def _temizle(engine) -> None:
    with engine.begin() as baglanti:
        for deyim in (
            "DELETE FROM stock_movements WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM product_lots WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM products WHERE company_id IN "
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


def _firma_ve_urun(
    baglanti, firma_adi: str = FIRMA_ADI, urun_adi: str = URUN_ADI
) -> tuple[int, int]:
    firma_id = baglanti.execute(
        text(
            "INSERT INTO companies (name, is_active, created_at) "
            "VALUES (:ad, true, :simdi) RETURNING id"
        ),
        {"ad": firma_adi, "simdi": datetime.now(timezone.utc)},
    ).scalar_one()
    urun_id = baglanti.execute(
        text(
            "INSERT INTO products (name, company_id, base_unit) "
            "VALUES (:ad, :cid, 'KG') RETURNING id"
        ),
        {"ad": urun_adi, "cid": firma_id},
    ).scalar_one()
    return firma_id, urun_id


def _parti_yaz(
    baglanti,
    firma_id: int,
    urun_id: int,
    kod: str,
    miktar: str,
    skt: date | None,
    olusma: datetime | None = None,
) -> int:
    return baglanti.execute(
        text(
            "INSERT INTO product_lots "
            "(company_id, product_id, lot_code, expiry_date, quantity, created_at) "
            "VALUES (:cid, :pid, :kod, :skt, :mik, :olusma) RETURNING id"
        ),
        {
            "cid": firma_id,
            "pid": urun_id,
            "kod": kod,
            "skt": skt,
            "mik": Decimal(miktar),
            "olusma": olusma or datetime(2026, 1, 1, tzinfo=timezone.utc),
        },
    ).scalar_one()


def _partileri_oku(baglanti, firma_id: int) -> list[Parti]:
    satirlar = baglanti.execute(
        text(
            "SELECT id, quantity, expiry_date, created_at FROM product_lots "
            "WHERE company_id = :cid"
        ),
        {"cid": firma_id},
    ).all()
    return [Parti(*satir) for satir in satirlar]


# ===========================================================================
# 1. ŞEMA — göç ne söz verdiyse PostgreSQL'de O DURUYOR
# ===========================================================================

@pytest.mark.postgresql
def test_goc_0067_SKT_NULL_kabul_eder_VARSAYILANI_YOK(motor) -> None:
    """`expiry_date` NULL, `server_default` YOK — sahip kararı 2.

    Bir `server_default` (örneğin uzak bir tarih) sonradan eklenirse "SKT'si
    yoktur" olgusu SESSİZCE "SKT'si 9999'dur"a döner ve uydurma kayıtta
    ÖLÇÜLMÜŞ gibi görünür. Bu yüzden yokluğu ÖLÇÜLÜYOR, varsayılmıyor.
    """
    denetci = inspect(motor)
    sutunlar = {s["name"]: s for s in denetci.get_columns("product_lots")}

    assert sutunlar["expiry_date"]["nullable"] is True
    assert sutunlar["expiry_date"].get("default") is None

    # `quantity` ise NULL KABUL ETMEZ: "miktarı bilinmeyen parti" diye bir şey
    # yoktur — sıfır bir miktardır, NULL bir boşluktur.
    assert sutunlar["quantity"]["nullable"] is False
    assert sutunlar["quantity"]["type"].scale == 4, sutunlar["quantity"]["type"]

    assert sutunlar["lot_code"]["nullable"] is False


@pytest.mark.postgresql
def test_goc_0067_hareketin_lot_id_si_NULL_ve_GERIYE_DOLDURULMAZ(motor) -> None:
    """`stock_movements.lot_id` NULL kabul eder ve mevcut satırlara HİÇBİR
    ŞEY yazılmaz: bir hareketin hangi partiden çıktığı KAYITTA YOKTU ve
    uydurulamaz. NULL "bu hareket parti öncesindendir" der; bu DOĞRUDUR.
    """
    denetci = inspect(motor)
    hareket = {s["name"]: s for s in denetci.get_columns("stock_movements")}
    assert hareket["lot_id"]["nullable"] is True
    assert hareket["lot_id"].get("default") is None

    with motor.begin() as baglanti:
        _, urun_id = _firma_ve_urun(baglanti)
        baglanti.execute(
            text(
                "INSERT INTO stock_movements "
                "(product_id, movement_type, quantity, movement_date, company_id) "
                "SELECT :pid, 'IN', 5, '2026-09-03', company_id FROM products "
                "WHERE id = :pid"
            ),
            {"pid": urun_id},
        )
        lot_id = baglanti.execute(
            text("SELECT lot_id FROM stock_movements WHERE product_id = :pid"),
            {"pid": urun_id},
        ).scalar_one()
    assert lot_id is None, "göç mevcut harekete bir parti UYDURDU"


# ===========================================================================
# 2. `CHECK` KISITI — MUTASYON 4'ÜN ADRESİ
# ===========================================================================

@pytest.mark.postgresql
def test_CHECK_kisiti_NaN_MIKTARI_GERCEKTEN_REDDEDER(motor) -> None:
    """MUTASYON 4'ÜN ADRESİ — kısıt yalın `quantity >= 0`a döndürülürse
    BURASI kırmızı olur.

    0066 bu dersi PAHALIYA öğrendi: `CHECK (factor > 0)` kendi düzyazısında
    "çözücü çağrılmadan yazılan satırı yalnız veritabanı yakalar" diye
    BELGELENMİŞTİ ve tam da en sessiz değer için YANLIŞTI. Burada kısıt ilk
    seferde `<> 'NaN'::numeric` ile yazıldı ve bu test onu çiviliyor.
    """
    with motor.begin() as baglanti:
        firma_id, urun_id = _firma_ve_urun(baglanti)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            baglanti.execute(
                text(
                    "INSERT INTO product_lots (company_id, product_id, lot_code, "
                    "quantity, created_at) VALUES "
                    "(:cid, :pid, 'NAN-1', 'NaN'::numeric, now())"
                ),
                {"cid": firma_id, "pid": urun_id},
            )


@pytest.mark.postgresql
def test_YALIN_KISIT_NaN_i_KABUL_EDERDI_olcum(motor) -> None:
    """KUSURUN KÖKÜ, iddia değil ÖLÇÜM olarak.

    Kardeş test kısıtın NaN'ı reddettiğini gösteriyor. Bu test NEDEN yalın
    bir `quantity >= 0`ın YETMEYECEĞİNİ gösteriyor: PostgreSQL `NaN`ı her
    sonlu sayının ÜSTÜNE sıralar, yani `'NaN' >= 0` TRUE'dur ve yalın kısıt
    satırı KABUL EDERDİ.

    İkinci ölçüm `<>`in neden seçildiğini söyler: PostgreSQL'de `NaN = NaN`
    TRUE'dur (IEEE 754'ten ayrılır), bu yüzden `quantity <> 'NaN'` NaN için
    FALSE döner ve satır reddedilir.
    """
    with motor.begin() as baglanti:
        yalin_kabul, esitsizlik = baglanti.execute(
            text("SELECT 'NaN'::numeric >= 0, 'NaN'::numeric <> 'NaN'::numeric")
        ).one()
    assert yalin_kabul is True, (
        "`'NaN' >= 0` FALSE döndü. Bu İYİ haber olurdu ama bu testin ve "
        "kardeşinin gerekçesini DEĞİŞTİRİR: yalın kısıt yeterli demektir."
    )
    assert esitsizlik is False, "PostgreSQL'de `NaN = NaN` TRUE olmalıydı"


@pytest.mark.postgresql
def test_NEGATIF_miktar_veritabaninda_REDDEDILIR(motor) -> None:
    """Bir partide EKSİ mal olamaz. Seçici ATLANARAK yazılan satırı yalnız
    bu kısıt yakalar."""
    with motor.begin() as baglanti:
        firma_id, urun_id = _firma_ve_urun(baglanti)
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _parti_yaz(baglanti, firma_id, urun_id, "NEG-1", "-1", None)


@pytest.mark.postgresql
def test_SIFIR_miktar_KABUL_EDILIR_tukenmis_parti_SILINMEZ(motor) -> None:
    """`>= 0`, `> 0` DEĞİL — VE BU AYRIM ÖLÇÜLÜYOR.

    Tükenmiş bir parti satırı geri çağırmanın KANITIDIR: "bu partiden mal
    girdi ve bitti" cümlesi, satır silinirse "bu parti HİÇ OLMADI"ya dönüşür.
    `> 0` yazan biri burada kırmızı alır.
    """
    with motor.begin() as baglanti:
        firma_id, urun_id = _firma_ve_urun(baglanti)
        parti_id = _parti_yaz(baglanti, firma_id, urun_id, "BITMIS-1", "0", None)
    with motor.begin() as baglanti:
        miktar = baglanti.execute(
            text("SELECT quantity::text FROM product_lots WHERE id = :id"),
            {"id": parti_id},
        ).scalar_one()
    assert miktar == "0.0000", miktar


@pytest.mark.postgresql
def test_AYNI_urunde_AYNI_parti_kodu_IKI_SATIR_OLAMAZ(motor) -> None:
    """İki satır miktarı İKİYE bölerdi ve hangisinin gerçek olduğu
    SORULAMAZDI."""
    with motor.begin() as baglanti:
        firma_id, urun_id = _firma_ve_urun(baglanti)
        _parti_yaz(baglanti, firma_id, urun_id, "A-2026-01", "10", None)
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _parti_yaz(baglanti, firma_id, urun_id, "A-2026-01", "5", None)


# ===========================================================================
# 3. KİRACI — bileşik yabancı anahtar GERÇEKTEN ısırıyor
# ===========================================================================

@pytest.mark.postgresql
def test_PARTI_BASKA_KIRACININ_urununu_isaret_EDEMEZ(motor) -> None:
    """0062'nin kuralı: çıplak `product_id -> products.id` bunu ENGELLEMEZDİ.

    SQLite'ta bu test AYIRT EDİCİ OLMAZDI: orada yabancı anahtar uygulaması
    varsayılan olarak KAPALIDIR ve satır sessizce yazılırdı.
    """
    with motor.begin() as baglanti:
        firma_id, _ = _firma_ve_urun(baglanti)
        _, komsu_urun_id = _firma_ve_urun(baglanti, KOMSU_ADI, KOMSU_URUN_ADI)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            # KENDİ company_id'si, BAŞKASININ product_id'si.
            _parti_yaz(baglanti, firma_id, komsu_urun_id, "CAPRAZ-1", "10", None)


@pytest.mark.postgresql
def test_HAREKET_BASKA_KIRACININ_partisini_isaret_EDEMEZ(motor) -> None:
    """`stock_movements.lot_id` de BİLEŞİK bağlıdır: `(company_id, lot_id)`."""
    with motor.begin() as baglanti:
        firma_id, urun_id = _firma_ve_urun(baglanti)
        komsu_id, komsu_urun_id = _firma_ve_urun(
            baglanti, KOMSU_ADI, KOMSU_URUN_ADI
        )
        komsu_parti_id = _parti_yaz(
            baglanti, komsu_id, komsu_urun_id, "KOMSU-1", "10", None
        )

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            baglanti.execute(
                text(
                    "INSERT INTO stock_movements (product_id, movement_type, "
                    "quantity, movement_date, company_id, lot_id) VALUES "
                    "(:pid, 'OUT', 1, '2026-09-03', :cid, :lot)"
                ),
                {"pid": urun_id, "cid": firma_id, "lot": komsu_parti_id},
            )


@pytest.mark.postgresql
def test_lot_id_NULL_iken_bilesik_anahtar_DENETLENMEZ(motor) -> None:
    """`MATCH SIMPLE`: bir sütun NULL ise kısıt denetlenmez.

    Bu tam olarak İSTENEN davranıştır — partisi olmayan hareket geçerli bir
    harekettir ve mevcut satırların HEPSİ öyledir. Kısıt eklenirken bunun
    bozulmadığı ÖLÇÜLÜYOR, varsayılmıyor.
    """
    with motor.begin() as baglanti:
        firma_id, urun_id = _firma_ve_urun(baglanti)
        baglanti.execute(
            text(
                "INSERT INTO stock_movements (product_id, movement_type, "
                "quantity, movement_date, company_id, lot_id) VALUES "
                "(:pid, 'IN', 5, '2026-09-03', :cid, NULL)"
            ),
            {"pid": urun_id, "cid": firma_id},
        )
        sayi = baglanti.execute(
            text(
                "SELECT count(*) FROM stock_movements "
                "WHERE product_id = :pid AND lot_id IS NULL"
            ),
            {"pid": urun_id},
        ).scalar_one()
    assert sayi == 1


# ===========================================================================
# 4. SEÇİCİ + GERÇEK SÜTUN — sıra ve ölçek birlikte
# ===========================================================================

@pytest.mark.postgresql
def test_SECICI_GERCEK_satirlarla_FEFO_sirasini_koruyor(motor) -> None:
    """Depodan okunan satırlar seçiciye verilince sıra AYNI çıkmalı.

    Birim testleri sırayı elde kurulmuş demetlerle ölçüyor; bu test aynı
    sırayı GERÇEK `DATE`, `TIMESTAMPTZ` ve `NUMERIC(18,4)` değerleriyle
    ölçüyor — tip dönüşümü sırayı bozarsa burada görünür.
    """
    with motor.begin() as baglanti:
        firma_id, urun_id = _firma_ve_urun(baglanti)
        gec = _parti_yaz(baglanti, firma_id, urun_id, "GEC", "10", date(2027, 1, 1))
        erken = _parti_yaz(
            baglanti, firma_id, urun_id, "ERKEN", "10", date(2026, 10, 1)
        )
        sktsiz = _parti_yaz(baglanti, firma_id, urun_id, "SKTSIZ", "10", None)

    with motor.begin() as baglanti:
        partiler = _partileri_oku(baglanti, firma_id)

    secim = fefo_sec(partiler, Decimal("30"), bugun=BUGUN)
    assert [kimlik for kimlik, _ in secim.dagitim] == [erken, gec, sktsiz], (
        "gerçek satırlarla FEFO sırası bozuldu"
    )


@pytest.mark.postgresql
def test_NULL_SON_kurali_DIYALEKTE_BIRAKILAMAZ_olcum(motor) -> None:
    """NULL-son kuralının neden `ORDER BY`a BIRAKILMADIĞININ KANITI.

    PostgreSQL artan sırada NULL'ları SONA koyar; SQLite ise BAŞA koyar
    (ikisi de burada ölçülüyor). Yani sırayı veritabanına bırakmak İKİ
    DİYALEKTTE İKİ FARKLI CEVAP verirdi — ve hiçbir test bunu görmezdi,
    çünkü her biri kendi diyalektinde yeşil kalırdı.

    Kural bu yüzden Python'da, TEK bir yerde duruyor: `_sira_anahtari`.
    """
    import sqlite3

    with motor.begin() as baglanti:
        pg_sirasi = [
            satir[0]
            for satir in baglanti.execute(
                text(
                    "SELECT d FROM (VALUES (DATE '2030-01-01'), (NULL), "
                    "(DATE '2026-01-01')) v(d) ORDER BY d ASC"
                )
            ).all()
        ]
    assert pg_sirasi[-1] is None, f"PostgreSQL NULL'u sona koymadı: {pg_sirasi}"

    baglanti_sqlite = sqlite3.connect(":memory:")
    sqlite_sirasi = [
        satir[0]
        for satir in baglanti_sqlite.execute(
            "SELECT d FROM (SELECT '2030-01-01' d UNION ALL SELECT NULL "
            "UNION ALL SELECT '2026-01-01') ORDER BY d ASC"
        )
    ]
    baglanti_sqlite.close()
    assert sqlite_sirasi[0] is None, f"SQLite NULL'u başa koymadı: {sqlite_sirasi}"

    # İKİ DİYALEKT AYRIŞIYOR — kuralın kod tarafında olmasının gerekçesi.
    # Bir gün ayrışmazlarsa bu testin GEREKÇESİ değişmiştir, susmamalıdır.
    assert pg_sirasi[-1] is None and sqlite_sirasi[0] is None, (
        "iki diyalekt aynı sırayı verdi; bu testin gerekçesi değişmiştir"
    )


@pytest.mark.postgresql
def test_DAGITIM_PAYLARI_gercek_NUMERIC_18_4_ile_BIREBIR_AYNI(motor) -> None:
    """Seçicinin payları sütuna yazılıp geri okununca DEĞİŞMEMELİ.

    Karşılaştırma `str()` ÜZERİNDEN, çünkü `Decimal("3.3333") ==
    Decimal("3.33330")` sayısal olarak DOĞRUDUR ve ölçek bozulmasını ancak
    METİN yakalar (0066 ikizinin dersi).
    """
    with motor.begin() as baglanti:
        firma_id, urun_id = _firma_ve_urun(baglanti)
        birinci = _parti_yaz(
            baglanti, firma_id, urun_id, "P1", "3.3333", date(2026, 10, 1)
        )
        _parti_yaz(baglanti, firma_id, urun_id, "P2", "6.6667", date(2026, 11, 1))

    with motor.begin() as baglanti:
        partiler = _partileri_oku(baglanti, firma_id)

    secim = fefo_sec(partiler, Decimal("10.0000"), bugun=BUGUN)
    toplam = sum((pay for _, pay in secim.dagitim), Decimal("0"))
    assert str(toplam) == "10.0000", str(toplam)

    # İlk payı GERÇEK sütuna yaz ve geri oku: ölçek korunuyor mu?
    ilk_kimlik, ilk_pay = secim.dagitim[0]
    assert ilk_kimlik == birinci
    with motor.begin() as baglanti:
        baglanti.execute(
            text(
                "INSERT INTO stock_movements (product_id, movement_type, "
                "quantity, movement_date, company_id, lot_id) VALUES "
                "(:pid, 'OUT', :mik, '2026-09-03', :cid, :lot)"
            ),
            {"pid": urun_id, "mik": ilk_pay, "cid": firma_id, "lot": ilk_kimlik},
        )
    with motor.begin() as baglanti:
        geri = baglanti.execute(
            text("SELECT quantity::text FROM stock_movements WHERE lot_id = :lot"),
            {"lot": ilk_kimlik},
        ).scalar_one()
    assert geri == str(ilk_pay), f"pay {ilk_pay} yazıldı, {geri} geri okundu"


@pytest.mark.postgresql
def test_SURESI_GECMIS_parti_GERCEK_DATE_ile_de_dislaniyor(motor) -> None:
    """Süresi geçmiş reddi, `DATE` sütunundan okunan gerçek tarihlerle.

    SINIR de burada: bugün biten parti SÜRESİ GEÇMİŞ DEĞİLDİR (`<`, `<=`
    değil) ve dağıtıma GİRER.
    """
    with motor.begin() as baglanti:
        firma_id, urun_id = _firma_ve_urun(baglanti)
        gecmis = _parti_yaz(
            baglanti, firma_id, urun_id, "GECMIS", "10", BUGUN - timedelta(days=1)
        )
        bugun_biten = _parti_yaz(
            baglanti, firma_id, urun_id, "BUGUN", "10", BUGUN
        )
        saglam = _parti_yaz(
            baglanti, firma_id, urun_id, "SAGLAM", "10", BUGUN + timedelta(days=1)
        )

    with motor.begin() as baglanti:
        partiler = _partileri_oku(baglanti, firma_id)

    secim = fefo_sec(partiler, Decimal("20"), bugun=BUGUN)
    secilenler = [kimlik for kimlik, _ in secim.dagitim]
    assert gecmis not in secilenler, "süresi geçmiş parti seçildi"
    assert secilenler == [bugun_biten, saglam], secilenler
    assert [p.id for p in secim.suresi_gecmis] == [gecmis]


@pytest.mark.postgresql
def test_VERITABANINDAN_gelen_NaN_miktar_SECICIYI_gecemez(motor) -> None:
    """İKİ KATMAN, ve ikisi de ölçülüyor.

    Kısıt NaN'ı zaten reddediyor (yukarıda), yani böyle bir satır bu şemada
    DOĞAMAZ. Seçicinin kapısı yine de ayrı ölçülüyor: kısıt bir gün
    gevşetilirse ya da veri başka bir yoldan gelirse ikinci katman ORADA
    olmalı. Tek katmana güvenmek, 0066'nın düzelttiği hatanın aynısı olurdu.
    """
    with pytest.raises(PartiSecilemedi) as yakalanan:
        fefo_sec(
            [
                Parti(
                    1,
                    Decimal("NaN"),
                    None,
                    datetime(2026, 1, 1, tzinfo=timezone.utc),
                )
            ],
            Decimal("1"),
            bugun=BUGUN,
        )
    assert yakalanan.value.sebep == PartiSecilemedi.PARTI_MIKTARI_GECERSIZ


@pytest.mark.postgresql
def test_YETERSIZLIK_gercek_satirlarla_da_ParticiYetersiz(motor) -> None:
    """`mevcut` GERÇEK sütun ölçeğinde döner: `4.0000`, `4` değil."""
    with motor.begin() as baglanti:
        firma_id, urun_id = _firma_ve_urun(baglanti)
        _parti_yaz(baglanti, firma_id, urun_id, "AZ", "4", date(2026, 10, 1))
    with motor.begin() as baglanti:
        partiler = _partileri_oku(baglanti, firma_id)
    with pytest.raises(ParticiYetersiz) as yakalanan:
        fefo_sec(partiler, Decimal("10"), bugun=BUGUN)
    assert yakalanan.value.mevcut == Decimal("4.0000")


# ===========================================================================
# 5. ÇİVİLENMİŞ DELİK — ikinci katmanın YOKLUĞU
# ===========================================================================

@pytest.mark.postgresql
def test_stok_ile_parti_toplami_AYRISABILIR_ikinci_katman_YOK(motor) -> None:
    """`products.stock` ile `SUM(product_lots.quantity)` AYRIŞABİLİR.

    Bu test bir DAVRANIŞI değil, BİR DELİĞİ çiviliyor — 0066 ikizindeki
    `test_products_stock_NaN_KABUL_EDER_ikinci_katman_YOK` ile aynı gerekçe:
    okuyucu `product_lots` üzerindeki `CHECK` kısıtını ve bileşik yabancı
    anahtarları görüp "veritabanı tutarlılığı bana garanti ediyor" diye
    GENELLEYEBİLİR. GENELLEME YANLIŞTIR.

    Parti miktarları BU PR'DA HİÇBİR YERDE GÜNCELLENMİYOR (sahip kararı 3:
    tüketim yolu YOKTUR). Yani stok 100 iken parti toplamı 30 olabilir ve
    HİÇBİR ŞEY ŞİKÂYET ETMEZ. ÖLÇÜLDÜ, aşağıda.

    KISIT BU PR'DA EKLENMEDİ VE BU BİR KARARDIR:
      * Bugün eklenirse MEVCUT HER ÜRÜNÜ bozardı — bugün hiçbir ürünün
        partisi yoktur, yani `SUM(lots) = 0 <> stock` her satır için doğru.
      * Kısıt ancak partiler stoğun TAMAMINI kapsadığında anlamlıdır ve o gün
        BAĞLAMA (wiring) PR'ının içindedir.

    Delik KABUL EDİLDİ ve kaydına yazıldı; bu test onun sessizce
    unutulmasını engelliyor. Kısıt (ya da tetikleyici) bir gün eklenirse BU
    TEST KIRMIZI OLUR ve bu DOĞRUDUR: o gün burası, kısıtı doğrulayan teste
    döner.
    """
    with motor.begin() as baglanti:
        firma_id, urun_id = _firma_ve_urun(baglanti)
        # Stok 100, parti toplamı 30 — KASITLI olarak AYRIŞIK.
        baglanti.execute(
            text("UPDATE products SET stock = 100 WHERE id = :pid"),
            {"pid": urun_id},
        )
        _parti_yaz(baglanti, firma_id, urun_id, "AYRISIK-1", "30", None)

    with motor.begin() as baglanti:
        stok, parti_toplami = baglanti.execute(
            text(
                "SELECT p.stock, "
                "(SELECT coalesce(sum(l.quantity), 0) FROM product_lots l "
                " WHERE l.product_id = p.id) "
                "FROM products p WHERE p.id = :pid"
            ),
            {"pid": urun_id},
        ).one()

    assert stok == Decimal("100.0000"), stok
    assert parti_toplami == Decimal("30.0000"), parti_toplami
    assert stok != parti_toplami, (
        "stok ile parti toplamı AYRIŞAMADI — bir tutarlılık katmanı DOĞMUŞ "
        "demektir. Bu İYİ bir haberdir ama bu testin gerekçesini DEĞİŞTİRİR: "
        "burası artık o katmanı DOĞRULAYAN teste dönüşmelidir."
    )
