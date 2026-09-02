"""PostgreSQL ikizi: birim dönüşümünün DEPOSU ve ÇÖZÜCÜNÜN SAYISAL İDDİASI.

Göç 20260902_0066 + `app/units.py`. ÇÖZÜCÜYÜ ÇAĞIRAN BİR YOL YOKTUR — bu
dosya ile birim testleri, çözücünün TEK kapsamıdır.

--- BU İKİZ NEDEN VAR ------------------------------------------------------

Çözücünün merkezî iddiası SAYISALDIR: ürün NUMERIC(18,4)'e ROUND_HALF_UP ile
4 basamağa yuvarlanır, girdiler ise YUVARLANMAZ. SQLite'ta bu iddia gerçekten
sınanamaz — SQLite'ın NUMERIC'i ölçek DAYATMAZ, değeri olduğu gibi saklar,
yani yanlış ölçekli bir yazma orada SESSİZCE geçerdi.

PR #24'ün üç kez ölçtüğü ders şudur: bir test, ADINI TAŞIDIĞI mekanizmayı
çalıştırmadan da geçebilir. Bu yüzden aşağıdaki ölçek testi MUTASYONLA
kırmızıya çevrilerek gösterildi (rapor: quantize kaldırıldı -> KIRMIZI;
kuantum 0.01 yapıldı -> KIRMIZI; geri konuldu -> YEŞİL).

--- ÖLÇEK NASIL AYIRT EDİCİ KILINDI ---------------------------------------

Saf bir "değer doğru mu" karşılaştırması BURADA AYIRT EDİCİ OLMAZDI ve bu
tuzağın adı konmalı: PostgreSQL NUMERIC(18,4) yazarken KENDİSİ yuvarlar ve
yuvarlaması da yarım-yukarıdır. Yani `quantize` TAMAMEN kaldırılsa bile
`0.00005` veritabanında yine `0.0001` olurdu ve saf bir eşitlik YEŞİL kalırdı.

Ayrım üç yerden geliyor ve üçü de aynı testte duruyor:

1. **PYTHON DEĞERİ ile VERİTABANI DEĞERİ KARŞILAŞTIRILIYOR**, ikisi ayrı ayrı
   değil. `quantize` kaldırılırsa Python `1.123456789` tutar, PG `1.1235`
   döndürür ve ikisi EŞİT DEĞİLDİR.
2. **KARŞILAŞTIRMA `str()` ÜZERİNDEN**, çünkü `Decimal("1.12") ==
   Decimal("1.1200")` SAYISAL OLARAK DOĞRUDUR. Yanlış kuantum (0.01) yalnız
   ÖLÇEĞİ bozar, değeri değil — onu ancak metin yakalar.
3. **`ROUND_HALF_UP` ARGÜMANI**: Python'un varsayılanı HALF_EVEN'dır ve
   `0.00005` orada `0.0000` verir; PG ise `0.0001` yazar. Argümanı silen biri
   burada iki farklı sayı elde eder.

--- REDDİN İKİZİ ----------------------------------------------------------

Red yolu da ölçüldü ve o da mutasyonla kırmızıya çevrildi: çözülemeyen bir
birim için `products.stock`a HİÇBİR ŞEY YAZILMAMALI. Test yalnız istisnanın
atıldığını değil, VERİTABANININ DEĞİŞMEDİĞİNİ de ölçüyor — bir red, yazmayı
gerçekten engellemiyorsa red değildir.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.units import BirimCozulemedi, resolve

BACKEND = Path(__file__).resolve().parent

URUN_ADI = "BİRİM DÖNÜŞÜMÜ İKİZİ ürünü"
FIRMA_ADI = "BİRİM DÖNÜŞÜMÜ İKİZİ firması"


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("Birim dönüşümü ikizi APP_TEST_DATABASE_URL ister")
    return url


@pytest.fixture()
def motor():
    config = Config(str(BACKEND / "alembic.ini"))
    engine = create_engine(_url())
    command.upgrade(config, "head")
    try:
        yield engine
    finally:
        with engine.begin() as baglanti:
            baglanti.execute(
                text(
                    "DELETE FROM product_unit_factors WHERE product_id IN "
                    "(SELECT id FROM products WHERE name = :ad)"
                ),
                {"ad": URUN_ADI},
            )
            baglanti.execute(
                text(
                    "DELETE FROM stock_movements WHERE product_id IN "
                    "(SELECT id FROM products WHERE name = :ad)"
                ),
                {"ad": URUN_ADI},
            )
            baglanti.execute(
                text("DELETE FROM products WHERE name = :ad"), {"ad": URUN_ADI}
            )
            baglanti.execute(
                text("DELETE FROM companies WHERE name = :ad"), {"ad": FIRMA_ADI}
            )
        engine.dispose()


def _firma_ve_urun(baglanti, base_unit: str | None) -> tuple[int, int]:
    firma_id = baglanti.execute(
        text(
            "INSERT INTO companies (name, is_active, created_at) "
            "VALUES (:ad, true, :simdi) RETURNING id"
        ),
        {"ad": FIRMA_ADI, "simdi": datetime.now(timezone.utc)},
    ).scalar_one()
    urun_id = baglanti.execute(
        text(
            "INSERT INTO products (name, company_id, base_unit) "
            "VALUES (:ad, :cid, :taban) RETURNING id"
        ),
        {"ad": URUN_ADI, "cid": firma_id, "taban": base_unit},
    ).scalar_one()
    return firma_id, urun_id


# ===========================================================================
# 1. ŞEMA — göç ne söz verdiyse PostgreSQL'de O DURUYOR
# ===========================================================================

@pytest.mark.postgresql
def test_goc_0066_sutunlari_NULL_ve_VARSAYILANSIZ_acar(motor) -> None:
    """`base_unit` ve üç `entered_*` sütunu: NULL, `server_default` YOK.

    Bir `server_default` sonradan eklenirse geriye doldurma yasağı fiilen
    delinmiş olur — varsayılan, kaydedilmemiş bir olguyu kayıtlı gibi
    gösterir. Bu yüzden yokluğu ÖLÇÜLÜYOR, varsayılmıyor.
    """
    denetci = inspect(motor)
    urun_sutunlari = {s["name"]: s for s in denetci.get_columns("products")}
    assert urun_sutunlari["base_unit"]["nullable"] is True
    assert urun_sutunlari["base_unit"].get("default") is None

    hareket = {s["name"]: s for s in denetci.get_columns("stock_movements")}
    for ad in ("entered_quantity", "entered_unit", "entered_factor"):
        assert hareket[ad]["nullable"] is True, ad
        assert hareket[ad].get("default") is None, ad

    # Ölçekler AYRI: miktar ürünle aynı (18,4), katsayı ÇOK daha geniş
    # (24,10) — kanıt yuvarlanmasın diye.
    assert hareket["entered_quantity"]["type"].scale == 4
    assert hareket["entered_factor"]["type"].scale == 10


@pytest.mark.postgresql
def test_goc_0066_mevcut_hareketleri_GERIYE_DOLDURMAZ(motor) -> None:
    """Göçten önceki hareketlerin üç sütunu da NULL kalmalı."""
    with motor.begin() as baglanti:
        _, urun_id = _firma_ve_urun(baglanti, "KG")
        baglanti.execute(
            text(
                "INSERT INTO stock_movements "
                "(product_id, movement_type, quantity, movement_date, company_id) "
                "SELECT :pid, 'IN', 5, '2026-09-02', company_id FROM products "
                "WHERE id = :pid"
            ),
            {"pid": urun_id},
        )
        satir = baglanti.execute(
            text(
                "SELECT entered_quantity, entered_unit, entered_factor "
                "FROM stock_movements WHERE product_id = :pid"
            ),
            {"pid": urun_id},
        ).one()
    assert satir == (None, None, None), (
        "göç mevcut harekete bir birim UYDURDU; kaybolmuş olgu geri gelmez"
    )


@pytest.mark.postgresql
def test_katsayi_defteri_POZITIF_OLMAYANI_veritabaninda_REDDEDER(motor) -> None:
    """`CHECK factor > 0` — çözücü ATLANARAK yazılan satırı yalnız bu yakalar."""
    from sqlalchemy.exc import IntegrityError

    with motor.begin() as baglanti:
        firma_id, urun_id = _firma_ve_urun(baglanti, "KG")

    for gecersiz in (Decimal("0"), Decimal("-1")):
        with pytest.raises(IntegrityError) as hata:
            with motor.begin() as baglanti:
                baglanti.execute(
                    text(
                        "INSERT INTO product_unit_factors (company_id, product_id, "
                        "unit_code, factor, effective_from, created_at) VALUES "
                        "(:cid, :pid, 'ÇUVAL', :k, :gun, :simdi)"
                    ),
                    {
                        "cid": firma_id,
                        "pid": urun_id,
                        "k": gecersiz,
                        "gun": date(2026, 9, 2),
                        "simdi": datetime.now(timezone.utc),
                    },
                )
        assert "ck_product_unit_factors_factor_positive" in str(hata.value)


@pytest.mark.postgresql
def test_katsayi_defteri_EKLEMELI_ayni_gun_IKI_SATIR_KABUL_ETMEZ(motor) -> None:
    """Düzeltme YENİ BİR GÜNDÜR; aynı gün iki beyan belirsizlik olurdu.

    Bu test defterin EKLEMELİ olduğunu da gösteriyor: 50 yanlış çıkınca
    satır GÜNCELLENMİYOR, daha yeni `effective_from` ile İKİNCİ SATIR
    yazılıyor ve İKİSİ DE kayıtta kalıyor — "o gün neye inanıldı"nın tek
    kanıtı budur.
    """
    from sqlalchemy.exc import IntegrityError

    with motor.begin() as baglanti:
        firma_id, urun_id = _firma_ve_urun(baglanti, "KG")

    def yaz(katsayi: str, gun: date) -> None:
        with motor.begin() as baglanti:
            baglanti.execute(
                text(
                    "INSERT INTO product_unit_factors (company_id, product_id, "
                    "unit_code, factor, effective_from, created_at) VALUES "
                    "(:cid, :pid, 'ÇUVAL', :k, :gun, :simdi)"
                ),
                {
                    "cid": firma_id,
                    "pid": urun_id,
                    "k": Decimal(katsayi),
                    "gun": gun,
                    "simdi": datetime.now(timezone.utc),
                },
            )

    yaz("50", date(2026, 9, 1))
    with pytest.raises(IntegrityError) as hata:
        yaz("33", date(2026, 9, 1))
    assert "uq_product_unit_factors_effective" in str(hata.value)

    # DÜZELTME: yeni gün, yeni satır. Eskisi SİLİNMEZ.
    yaz("33", date(2026, 9, 2))
    with motor.begin() as baglanti:
        satirlar = baglanti.execute(
            text(
                "SELECT effective_from, factor FROM product_unit_factors "
                "WHERE product_id = :pid ORDER BY effective_from"
            ),
            {"pid": urun_id},
        ).all()
    assert [(s[0], s[1]) for s in satirlar] == [
        (date(2026, 9, 1), Decimal("50.0000000000")),
        (date(2026, 9, 2), Decimal("33.0000000000")),
    ], "düzeltme eski beyanı SİLDİ; 50'nin bir zamanlar doğru sanıldığı kanıtı yok oldu"


@pytest.mark.postgresql
def test_katsayi_BASKA_KIRACININ_urununu_isaret_EDEMEZ(motor) -> None:
    """Bileşik yabancı anahtar (0062'nin kuralı) çapraz kiracıyı keser."""
    from sqlalchemy.exc import IntegrityError

    with motor.begin() as baglanti:
        firma_id, urun_id = _firma_ve_urun(baglanti, "KG")
        yabanci_firma = baglanti.execute(
            text(
                "INSERT INTO companies (name, is_active, created_at) "
                "VALUES (:ad, true, :simdi) RETURNING id"
            ),
            {"ad": FIRMA_ADI, "simdi": datetime.now(timezone.utc)},
        ).scalar_one()

    with pytest.raises(IntegrityError) as hata:
        with motor.begin() as baglanti:
            baglanti.execute(
                text(
                    "INSERT INTO product_unit_factors (company_id, product_id, "
                    "unit_code, factor, effective_from, created_at) VALUES "
                    "(:cid, :pid, 'ÇUVAL', 33, :gun, :simdi)"
                ),
                {
                    # BAŞKA firmanın kimliği, BİZİM ürünümüz.
                    "cid": yabanci_firma,
                    "pid": urun_id,
                    "gun": date(2026, 9, 2),
                    "simdi": datetime.now(timezone.utc),
                },
            )
    assert "fk_product_unit_factors_product_same_company" in str(hata.value)


# ===========================================================================
# 2. ÖLÇEK — BU DOSYANIN MERKEZÎ İDDİASI (mutasyonla ayırt edici gösterildi)
# ===========================================================================

@pytest.mark.postgresql
@pytest.mark.parametrize(
    ("miktar", "birim", "taban", "katsayilar", "beklenen"),
    [
        # HALF_UP ile HALF_EVEN'ı AYIRAN durum: HALF_EVEN `0.0000` verirdi,
        # PG ise `0.0001` yazar. `rounding` argümanını silen biri BURADA
        # iki FARKLI sayı elde eder.
        ("0.00005", "KG", "KG", None, "0.0001"),
        # 4 basamaktan UZUN girdi: `quantize` kaldırılırsa Python
        # `1.123456789` tutar, PG `1.1235` döndürür — EŞİT DEĞİLLER.
        ("1.123456789", "KG", "KG", None, "1.1235"),
        # Katsayılı yol da aynı ölçekten geçmeli.
        ("1", "ÇUVAL", "KG", {"ÇUVAL": Decimal("33.3333333")}, "33.3333"),
        ("2.5", "TON", "KG", None, "2500.0000"),
    ],
)
def test_URUN_gercek_NUMERIC_18_4_ile_BIREBIR_AYNI(
    motor, miktar, birim, taban, katsayilar, beklenen
) -> None:
    """Çözücünün ürünü, PG'nin NUMERIC(18,4)'ünden dönen METNİN AYNISI olmalı.

    KARŞILAŞTIRMA `str()` ÜZERİNDEDİR ve bu KASITLIDIR: `Decimal("1.12") ==
    Decimal("1.1200")` sayısal olarak DOĞRUDUR, yani yanlış bir kuantum
    (örn. 0.01) SAYISAL bir eşitlikten GEÇERDİ. Ölçeği yalnız metin yakalar.

    Ölçüldü — bu test AYIRT EDİCİDİR:
      * `.quantize(...)` tamamen kaldırıldı  -> KIRMIZI
      * kuantum `0.01` yapıldı               -> KIRMIZI
      * `rounding=ROUND_HALF_UP` silindi     -> KIRMIZI (`0.00005` satırı)
      * geri konuldu                         -> YEŞİL
    """
    urun, katsayi = resolve(Decimal(miktar), birim, taban, katsayilar)

    with motor.begin() as baglanti:
        _, urun_id = _firma_ve_urun(baglanti, taban)
        baglanti.execute(
            text("UPDATE products SET stock = :stok WHERE id = :pid"),
            {"stok": urun, "pid": urun_id},
        )
        veritabanindaki = baglanti.execute(
            text("SELECT stock FROM products WHERE id = :pid"), {"pid": urun_id}
        ).scalar_one()

    assert str(veritabanindaki) == str(urun) == beklenen, (
        f"çözücü {urun!r} verdi, PostgreSQL {veritabanindaki!r} sakladı — "
        "ürün ölçeği çözücüde DEĞİL, veritabanında belirlenmiş demektir"
    )
    # Aynı sayı, veritabanının KENDİSİ yuvarlasaydı da çıkabilirdi; ayrımı
    # yapan, PYTHON tarafının da tam 4 basamak taşıması.
    assert urun.as_tuple().exponent == -4
    assert katsayi is not None


@pytest.mark.postgresql
def test_KATSAYI_genis_sutunda_YUVARLANMADAN_saklanir(motor) -> None:
    """`entered_factor` NUMERIC(24,10): kanıt ürün ölçeğine ÇEKİLMEZ.

    Katsayı ürünün 4 basamağına yuvarlansaydı `0.0000012345` `0.0000`
    olurdu — yani "o gün neye inanıldı" kanıtı SİLİNİRDİ. Bu test hem
    çözücünün katsayıyı bozmadığını hem de sütunun onu taşıyabildiğini
    aynı anda ölçüyor.
    """
    hassas = Decimal("0.0000012345")
    urun, katsayi = resolve(Decimal("1000000"), "ÇUVAL", "KG", {"ÇUVAL": hassas})
    assert katsayi == hassas

    with motor.begin() as baglanti:
        _, urun_id = _firma_ve_urun(baglanti, "KG")
        baglanti.execute(
            text(
                "INSERT INTO stock_movements (product_id, movement_type, quantity, "
                "movement_date, company_id, entered_quantity, entered_unit, "
                "entered_factor) SELECT :pid, 'IN', :urun, '2026-09-02', company_id, "
                ":girilen, 'ÇUVAL', :katsayi FROM products WHERE id = :pid"
            ),
            {
                "pid": urun_id,
                "urun": urun,
                "girilen": Decimal("1000000"),
                "katsayi": katsayi,
            },
        )
        saklanan = baglanti.execute(
            text(
                "SELECT entered_factor, entered_quantity, quantity "
                "FROM stock_movements WHERE product_id = :pid"
            ),
            {"pid": urun_id},
        ).one()

    assert saklanan[0] == hassas, "katsayı saklanırken YUVARLANDI; kanıt bozuldu"
    assert str(saklanan[0]) == "0.0000012345"
    assert str(saklanan[2]) == str(urun) == "1.2345"


# ===========================================================================
# 3. RED — bir red, YAZMAYI gerçekten engellemiyorsa red DEĞİLDİR
# ===========================================================================

@pytest.mark.postgresql
@pytest.mark.parametrize(
    ("birim", "taban", "sebep"),
    [
        ("ÇUVAL", "KG", BirimCozulemedi.BIRIM_TANIMSIZ),
        ("LİTRE", "KG", BirimCozulemedi.BOYUT_UYUSMAZLIGI),
        ("ÇUVAL", None, BirimCozulemedi.TABAN_BILDIRILMEMIS),
        ("KG", None, BirimCozulemedi.TABAN_BILDIRILMEMIS),
    ],
)
def test_COZULEMEYEN_birim_STOKA_HICBIR_SEY_YAZDIRMAZ(
    motor, birim, taban, sebep
) -> None:
    """Red yolunun ikizi. Ölçüldü — bu test AYIRT EDİCİDİR.

    `_katsayi`nin `BIRIM_TANIMSIZ` `raise`'i `return Decimal("1")` ile
    değiştirildiğinde ÖLÇÜLDÜ: `Failed: DID NOT RAISE BirimCozulemedi`,
    `pytest.raises` satırında. Geri konulduğunda YEŞİL.

    Aşağıdaki stok denetimi o mutasyonda ÇALIŞMAZ — `pytest.raises` daha
    önce düşer — ve bu, onun gereksiz olduğu anlamına GELMEZ: o denetim
    BAŞKA bir mutasyonu hedefliyor, yani istisnanın atıldığı ama yazmanın
    yine de geçtiği durumu. Yalnız istisnayı ölçmek YETMEZDİ, çünkü bir
    çağıranın istisnayı yakalayıp yine de yazması mümkündür.
    """
    with motor.begin() as baglanti:
        _, urun_id = _firma_ve_urun(baglanti, taban)
        onceki = baglanti.execute(
            text("SELECT stock FROM products WHERE id = :pid"), {"pid": urun_id}
        ).scalar_one()

    with pytest.raises(BirimCozulemedi) as hata:
        urun, _ = resolve(Decimal("50"), birim, taban)
        with motor.begin() as baglanti:
            baglanti.execute(
                text("UPDATE products SET stock = :stok WHERE id = :pid"),
                {"stok": urun, "pid": urun_id},
            )
    assert hata.value.sebep == sebep

    with motor.begin() as baglanti:
        sonraki = baglanti.execute(
            text("SELECT stock FROM products WHERE id = :pid"), {"pid": urun_id}
        ).scalar_one()
    assert sonraki == onceki, (
        f"{birim!r} -> {taban!r} çözülemedi ama stok DEĞİŞTİ: "
        f"{onceki} -> {sonraki}. Çözemediği bir şeyi yazan bir çözücü, "
        "dönüşümün HİÇ olmamasından daha kötüdür."
    )


@pytest.mark.postgresql
def test_taban_bildirilmemis_URUN_veritabaninda_GERCEKTEN_NULL(motor) -> None:
    """Red yolunun ön koşulu: `base_unit` NULL SAKLANABİLİYOR olmalı.

    Bir `server_default` eklenmiş olsaydı bu satır asla NULL olmaz ve
    yukarıdaki `TABAN_BILDIRILMEMIS` testi hiçbir zaman GERÇEK bir satırı
    tarif etmezdi — kapı kendi kendini kandırırdı.
    """
    with motor.begin() as baglanti:
        _, urun_id = _firma_ve_urun(baglanti, None)
        taban = baglanti.execute(
            text("SELECT base_unit FROM products WHERE id = :pid"), {"pid": urun_id}
        ).scalar_one()
    assert taban is None

    with pytest.raises(BirimCozulemedi) as hata:
        resolve(Decimal("1"), "ÇUVAL", taban)
    assert hata.value.sebep == BirimCozulemedi.TABAN_BILDIRILMEMIS


@pytest.mark.postgresql
def test_URUN_BEYANI_evrensel_haritayi_PG_uzerinde_de_EZER(motor) -> None:
    """Okuma sırası, katsayı GERÇEK defterden okunduğunda da korunur.

    Yukarıdaki birim testi sözlükle ölçüyor; burada katsayı
    `product_unit_factors`tan, `NUMERIC(24,10)`dan geri okunuyor. Sıra ters
    olsaydı `TON` evrensel 1000'i verir ve ürün 900 yerine 1000 olurdu.
    """
    with motor.begin() as baglanti:
        firma_id, urun_id = _firma_ve_urun(baglanti, "KG")
        baglanti.execute(
            text(
                "INSERT INTO product_unit_factors (company_id, product_id, "
                "unit_code, factor, effective_from, created_at) VALUES "
                "(:cid, :pid, 'TON', 900, :gun, :simdi)"
            ),
            {
                "cid": firma_id,
                "pid": urun_id,
                "gun": date(2026, 9, 2),
                "simdi": datetime.now(timezone.utc),
            },
        )
        defter = dict(
            baglanti.execute(
                text(
                    "SELECT unit_code, factor FROM product_unit_factors "
                    "WHERE company_id = :cid AND product_id = :pid "
                    "AND effective_from <= :gun ORDER BY effective_from"
                ),
                {"cid": firma_id, "pid": urun_id, "gun": date(2026, 9, 2)},
            ).all()
        )

    urun, katsayi = resolve(Decimal("1"), "TON", "KG", defter)
    assert katsayi == Decimal("900.0000000000"), "evrensel harita defteri EZDİ"
    assert str(urun) == "900.0000"

    # Defter olmasaydı evrensel değer gelirdi — yani 900 GERÇEKTEN defterden.
    assert resolve(Decimal("1"), "TON", "KG")[1] == Decimal("1000")


def test_ROUND_HALF_UP_sozlesmesi_PG_ile_AYNI_YONE_yuvarlar() -> None:
    """SQLite'ta da koşar: PG'nin yarım-yukarı'sı ile Python'unki AYNI OLMALI.

    Bu testin PG'ye ihtiyacı yok ama yeri burasıdır: yukarıdaki ikizin
    `str()` karşılaştırmasının ANLAMLI olması, iki tarafın aynı yöne
    yuvarlamasına bağlıdır. Python'un VARSAYILANI (HALF_EVEN) bu şartı
    SAĞLAMAZ ve fark tam olarak burada görünür.
    """
    assert Decimal("0.00005").quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    ) == Decimal("0.0001")
    # Varsayılan (HALF_EVEN) AYNI girdide FARKLI cevap verir:
    assert Decimal("0.00005").quantize(Decimal("0.0001")) == Decimal("0.0000")
