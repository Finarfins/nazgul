"""PostgreSQL ikizi: müstahsil makbuzu ŞEMASININ gerçek kısıtlarla eşi.

Göç `20260905_0070`. SQLite ikizi `tests/test_mustahsil_makbuzu.py`
aritmetiği ve uçların sözleşmesini ölçüyor; bu dosya yalnız ŞEMANIN
GERÇEKTEN ISIRAN kısımlarını ölçer — hepsi SQLite'ta GERÇEKTEN SINANAMAZ.

--- BU İKİZ NEDEN VAR ------------------------------------------------------

1. **BİLEŞİK YABANCI ANAHTARIN GERÇEKTEN ISIRMASI.** SQLite'ta yabancı
   anahtar uygulaması varsayılan olarak KAPALIDIR (`PRAGMA foreign_keys`),
   yani çapraz kiracı bir referans orada YEŞİL kalırdı. Bu göçün BEŞ
   bileşik anahtarı var (makbuz -> tedarikçi/alım/fiş, kalem ->
   makbuz/ürün) ve hepsinin TEK işi bir kiracının satırının BAŞKA
   kiracının satırını işaret etmesini engellemektir. Uygulanmayan bir
   yabancı anahtar, savunma DEĞİL süstür.

   Uygulama katmanı da bu sızıntıyı kapatıyor (uç 404 veriyor, SQLite
   ikizinde ölçülü) — ama uygulama kapısı TEK savunma olsaydı, ham SQL
   yazan bir göç ya da bir içe aktarım onu ATLAYABİLİRDİ.

2. **`ORAN` ARALIK KISITLARI (0..100).** SQLite CHECK'i tanır ama
   `NUMERIC(7,4)` ölçeğini DAYATMAZ; oranın hem aralığı hem ölçeği
   ancak burada gerçekten ölçülür.

3. **`NUMERIC(18,2)` TUTAR ÖLÇEĞİ.** SQLite `NUMERIC`i tür/ölçek
   DAYATMAZ: üç basamaklı bir kuruş orada SESSİZCE geçer ve makbuzun
   toplamı satırlarıyla tutmaz.

4. **KISMİ BENZERSİZ İNDEKS.** `receipt_no` TASLAKTA NULL'dur ve numara
   atanınca firma içinde TEK olmalıdır. Kısmi indeksin (`WHERE receipt_no
   IS NOT NULL`) hem tekilliği DAYATTIĞI hem de numarasız taslakları
   SINIRSIZ bıraktığı, iki ayrı yazma ile ölçülüyor.

5. **`ck_producer_receipts_no_follows_status`.** Numara ile durum birlikte
   hareket eder: taslakta numara OLMAZ, kesilmiş makbuzda numarasızlık
   OLMAZ. Bu kısıt `issue` yolundaki bir hatanın numarasız "issued" satır
   bırakmasını engeller ve gerçekten ısırdığı burada ölçülüyor.

--- ÖLÇÜLEN KÖK SEBEP, İDDİA DEĞİL -----------------------------------------

0066'nın dersi: belgelenmiş ama savunmayan bir savunma, savunma
OLMAMASINDAN kötüdür. Aşağıdaki testler kısıtın VARLIĞINI değil GERÇEKTEN
REDDETTİĞİNİ ölçüyor — her biri kısıtı ihlal eden bir yazma deneyip
`IntegrityError` (ya da ölçek için `DataError`) bekliyor.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DataError, IntegrityError

BACKEND = Path(__file__).resolve().parent

FIRMA_ADI = "MÜSTAHSİL İKİZİ firması"
KOMSU_ADI = "MÜSTAHSİL İKİZİ komşu firması"


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("Müstahsil ikizi APP_TEST_DATABASE_URL ister")
    return url


def _temizle(engine) -> None:
    with engine.begin() as baglanti:
        for deyim in (
            "DELETE FROM producer_receipt_items WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM producer_receipts WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM suppliers WHERE company_id IN "
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


def _firma_kur(baglanti, firma_adi: str) -> tuple[int, int]:
    """Bir firma ve ona ait BİR tedarikçi kurar; (company_id, supplier_id)."""
    simdi = datetime.now(timezone.utc)
    cid = baglanti.execute(
        text(
            "INSERT INTO companies (name, is_active, created_at) "
            "VALUES (:ad, true, :simdi) RETURNING id"
        ),
        {"ad": firma_adi, "simdi": simdi},
    ).scalar_one()
    tedarikci = baglanti.execute(
        text(
            "INSERT INTO suppliers (company_id, name, tax_number, "
            "opening_balance, is_active) "
            "VALUES (:cid, 'İkiz Çiftçi', '11111111111', 0, true) RETURNING id"
        ),
        {"cid": cid},
    ).scalar_one()
    return cid, tedarikci


def _makbuz_yaz(baglanti, cid: int, tedarikci: int, **ustune):
    simdi = datetime.now(timezone.utc)
    degerler = {
        "cid": cid,
        "sid": tedarikci,
        "no": None,
        "issued_at": None,
        "brut": Decimal("0.00"),
        "stopaj": Decimal("0.00"),
        "sgk": Decimal("0.00"),
        "net": Decimal("0.00"),
        "durum": "draft",
        "s": simdi,
    }
    degerler.update(ustune)
    return baglanti.execute(
        text(
            "INSERT INTO producer_receipts (company_id, supplier_id, "
            "receipt_no, issued_at, gross_amount, withholding_total, "
            "social_security_total, net_payable, status, created_at, "
            "updated_at) VALUES (:cid, :sid, :no, :issued_at, :brut, :stopaj, "
            ":sgk, :net, :durum, :s, :s) RETURNING id"
        ),
        degerler,
    ).scalar_one()


def _kalem_yaz(baglanti, cid: int, makbuz: int, **ustune):
    simdi = datetime.now(timezone.utc)
    degerler = {
        "cid": cid,
        "rid": makbuz,
        "pid": None,
        "girilen": Decimal("10.0000"),
        "birim": "KG",
        "katsayi": Decimal("1.0000000000"),
        "taban": Decimal("10.0000"),
        "fiyat": Decimal("2.50"),
        "brut": Decimal("25.00"),
        "stopaj_o": Decimal("2.0000"),
        "stopaj": Decimal("0.50"),
        "sgk_o": Decimal("1.0000"),
        "sgk": Decimal("0.25"),
        "net": Decimal("24.25"),
        "s": simdi,
    }
    degerler.update(ustune)
    return baglanti.execute(
        text(
            "INSERT INTO producer_receipt_items (company_id, receipt_id, "
            "product_id, entered_quantity, entered_unit, entered_factor, "
            "base_quantity, unit_price, line_gross, withholding_rate, "
            "withholding_amount, social_security_rate, "
            "social_security_amount, line_net, created_at, updated_at) "
            "VALUES (:cid, :rid, :pid, :girilen, :birim, :katsayi, :taban, "
            ":fiyat, :brut, :stopaj_o, :stopaj, :sgk_o, :sgk, :net, :s, :s) "
            "RETURNING id"
        ),
        degerler,
    ).scalar_one()


# ---------------------------------------------------------------------------
# 1. BİLEŞİK YABANCI ANAHTARLAR GERÇEKTEN ISIRIYOR
# ---------------------------------------------------------------------------


def test_baska_firmanin_tedarikcisi_VERITABANINDA_reddedilir(motor) -> None:
    """A firmasının makbuzu B firmasının tedarikçisini işaret EDEMEZ.

    Uygulama katmanı da bunu 404 ile kapatıyor (SQLite ikizinde ölçülü);
    bu test kapının VERİTABANINDA da kapalı olduğunu ölçer. Sızıntının
    gerçek biçimi "VAR OLAN ama BAŞKA FİRMAYA ait" tedarikçidir: tekil bir
    `supplier_id` yabancı anahtarı bunu KABUL EDERDİ.
    """
    with motor.begin() as baglanti:
        cid, _ = _firma_kur(baglanti, FIRMA_ADI)
        _, komsu_tedarikci = _firma_kur(baglanti, KOMSU_ADI)

    with pytest.raises(IntegrityError) as hata:
        with motor.begin() as baglanti:
            _makbuz_yaz(baglanti, cid, komsu_tedarikci)
    assert "fk_producer_receipts_supplier_same_company" in str(hata.value)


def test_baska_firmanin_makbuzuna_kalem_YAZILAMAZ(motor) -> None:
    """B firmasının makbuzuna A firmasının kalemi bağlanamaz."""
    with motor.begin() as baglanti:
        cid, tedarikci = _firma_kur(baglanti, FIRMA_ADI)
        komsu_cid, komsu_tedarikci = _firma_kur(baglanti, KOMSU_ADI)
        komsu_makbuz = _makbuz_yaz(baglanti, komsu_cid, komsu_tedarikci)

    with pytest.raises(IntegrityError) as hata:
        with motor.begin() as baglanti:
            _kalem_yaz(baglanti, cid, komsu_makbuz)
    assert "fk_producer_receipt_items_receipt_same_company" in str(hata.value)


def test_tedarikci_bilesik_tekilligi_VAR(motor) -> None:
    """`uq_suppliers_company_id` bu göçte kuruldu ve YERİNDE.

    Bileşik yabancı anahtarın hedefi bu kısıttır; `purchases` ve `products`
    onu zaten taşıyordu, `suppliers` TAŞIMIYORDU (ölçüldü, göç 0070
    başlığında). Kısıt düşerse yukarıdaki iki test de anlamını yitirir —
    bu yüzden ayrıca ve ADIYLA ölçülüyor.
    """
    with motor.connect() as baglanti:
        satir = baglanti.execute(
            text(
                "SELECT 1 FROM pg_constraint WHERE conname = "
                "'uq_suppliers_company_id'"
            )
        ).first()
    assert satir is not None, (
        "`uq_suppliers_company_id` YOK: makbuzun tedarikçiye giden bileşik "
        "yabancı anahtarının hedefi kalmamış demektir."
    )


# ---------------------------------------------------------------------------
# 2. ORAN ARALIĞI VE ÖLÇEĞİ
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "alan,kisit",
    [
        ("stopaj_o", "ck_producer_receipt_items_withholding_rate_range"),
        ("sgk_o", "ck_producer_receipt_items_ss_rate_range"),
    ],
)
@pytest.mark.parametrize("bozuk", [Decimal("-0.0001"), Decimal("100.0001")])
def test_oran_araligi_VERITABANINDA_dayatiliyor(motor, alan, kisit, bozuk) -> None:
    """0..100 dışındaki oran REDDEDİLİR — iki alanda ve iki uçta.

    Oran KULLANICIDAN gelir ve kodda yasal bir sabit YOKTUR (göç 0070
    başlığı); şemanın zorlayabildiği TEK şey aralıktır ve o aralığın
    gerçekten ısırdığı burada ölçülüyor.
    """
    with motor.begin() as baglanti:
        cid, tedarikci = _firma_kur(baglanti, FIRMA_ADI)
        makbuz = _makbuz_yaz(baglanti, cid, tedarikci)

    with pytest.raises(IntegrityError) as hata:
        with motor.begin() as baglanti:
            _kalem_yaz(baglanti, cid, makbuz, **{alan: bozuk})
    assert kisit in str(hata.value)


def test_oran_ucundaki_100_KABUL_EDILIR(motor) -> None:
    """%100 stopaj ŞEMACA geçerlidir: aralık 100'ü İÇERİR.

    Aralığın ucunu ayrıca ölçmek gerekiyor çünkü `<= 100` yerine `< 100`
    yazmak yukarıdaki reddi BOZMAZ — yalnız geçerli bir satırı sessizce
    reddeder ve kusur ancak sahada görülürdü.
    """
    with motor.begin() as baglanti:
        cid, tedarikci = _firma_kur(baglanti, FIRMA_ADI)
        makbuz = _makbuz_yaz(baglanti, cid, tedarikci)
        _kalem_yaz(
            baglanti, cid, makbuz,
            stopaj_o=Decimal("100.0000"), stopaj=Decimal("25.00"),
            sgk_o=Decimal("0.0000"), sgk=Decimal("0.00"),
            net=Decimal("0.00"),
        )


def test_oran_olcegi_NUMERIC_7_4(motor) -> None:
    """`NUMERIC(7,4)` taşması REDDEDİLİR — SQLite bunu sessizce alırdı."""
    with motor.begin() as baglanti:
        cid, tedarikci = _firma_kur(baglanti, FIRMA_ADI)
        makbuz = _makbuz_yaz(baglanti, cid, tedarikci)

    with pytest.raises((DataError, IntegrityError)):
        with motor.begin() as baglanti:
            _kalem_yaz(baglanti, cid, makbuz, stopaj_o=Decimal("1000.00001"))


# ---------------------------------------------------------------------------
# 3. TUTAR ÖLÇEĞİ
# ---------------------------------------------------------------------------


def test_tutar_olcegi_NUMERIC_18_2_YUVARLANARAK_saklanir(motor) -> None:
    """Üç basamaklı bir kuruş 2 basamağa İNER; sütun ölçeği DAYATILIR.

    SQLite bu satırı OLDUĞU GİBİ saklardı ve makbuzun toplamı satırlarıyla
    tutmazdı. Burada ölçülen şey PostgreSQL'in `NUMERIC(18,2)`ye
    yuvarlamasıdır — yani ölçek gerçekten SÜTUNDA duruyor.
    """
    with motor.begin() as baglanti:
        cid, tedarikci = _firma_kur(baglanti, FIRMA_ADI)
        makbuz = _makbuz_yaz(baglanti, cid, tedarikci)
        kalem = _kalem_yaz(baglanti, cid, makbuz, fiyat=Decimal("2.505"))

    with motor.connect() as baglanti:
        saklanan = baglanti.execute(
            text(
                "SELECT unit_price FROM producer_receipt_items "
                "WHERE company_id = :c AND id = :i"
            ),
            {"c": cid, "i": kalem},
        ).scalar_one()
    assert saklanan == Decimal("2.51"), saklanan
    assert saklanan.as_tuple().exponent == -2, saklanan


# ---------------------------------------------------------------------------
# 4. KISMİ BENZERSİZ İNDEKS
# ---------------------------------------------------------------------------


def test_ayni_firmada_ayni_numara_IKI_KEZ_olamaz(motor) -> None:
    with motor.begin() as baglanti:
        cid, tedarikci = _firma_kur(baglanti, FIRMA_ADI)
        _makbuz_yaz(
            baglanti, cid, tedarikci, no="MM-000001", durum="issued",
            issued_at=datetime.now(timezone.utc),
        )

    with pytest.raises(IntegrityError) as hata:
        with motor.begin() as baglanti:
            _makbuz_yaz(
                baglanti, cid, tedarikci, no="MM-000001", durum="issued",
                issued_at=datetime.now(timezone.utc),
            )
    assert "ux_producer_receipts_company_receipt_no" in str(hata.value)


def test_NUMARASIZ_taslaklar_SINIRSIZ(motor) -> None:
    """Kısmi indeksin İKİNCİ yarısı: `WHERE receipt_no IS NOT NULL`.

    Yüklem düşerse (düz bir UNIQUE olsaydı) PostgreSQL NULL'ları
    çakıştırmadığı için bu test YİNE geçerdi — ama niyet kayda geçmemiş
    olurdu. Bu yüzden yukarıdaki test indeksin ADINI da ölçüyor.
    """
    with motor.begin() as baglanti:
        cid, tedarikci = _firma_kur(baglanti, FIRMA_ADI)
        for _ in range(3):
            _makbuz_yaz(baglanti, cid, tedarikci)

    with motor.connect() as baglanti:
        sayi = baglanti.execute(
            text(
                "SELECT COUNT(*) FROM producer_receipts "
                "WHERE company_id = :c AND receipt_no IS NULL"
            ),
            {"c": cid},
        ).scalar_one()
    assert sayi == 3, sayi


def test_AYRI_firmalar_AYNI_numarayi_tasiyabilir(motor) -> None:
    """Tekillik FİRMA İÇİNDEDİR: iki kiracı aynı seriyi kullanır."""
    with motor.begin() as baglanti:
        cid, tedarikci = _firma_kur(baglanti, FIRMA_ADI)
        komsu_cid, komsu_tedarikci = _firma_kur(baglanti, KOMSU_ADI)
        simdi = datetime.now(timezone.utc)
        _makbuz_yaz(
            baglanti, cid, tedarikci, no="MM-000001", durum="issued",
            issued_at=simdi,
        )
        _makbuz_yaz(
            baglanti, komsu_cid, komsu_tedarikci, no="MM-000001",
            durum="issued", issued_at=simdi,
        )


# ---------------------------------------------------------------------------
# 5. NUMARA DURUMLA BİRLİKTE HAREKET EDER
# ---------------------------------------------------------------------------


def test_TASLAKTA_numara_OLAMAZ(motor) -> None:
    with motor.begin() as baglanti:
        cid, tedarikci = _firma_kur(baglanti, FIRMA_ADI)

    with pytest.raises(IntegrityError) as hata:
        with motor.begin() as baglanti:
            _makbuz_yaz(baglanti, cid, tedarikci, no="MM-000001")
    assert "ck_producer_receipts_no_follows_status" in str(hata.value)


def test_KESILMIS_makbuz_NUMARASIZ_olamaz(motor) -> None:
    """`issue` yolundaki bir hata numarasız "issued" satır BIRAKAMAZ."""
    with motor.begin() as baglanti:
        cid, tedarikci = _firma_kur(baglanti, FIRMA_ADI)

    with pytest.raises(IntegrityError) as hata:
        with motor.begin() as baglanti:
            _makbuz_yaz(
                baglanti, cid, tedarikci, durum="issued",
                issued_at=datetime.now(timezone.utc),
            )
    assert "ck_producer_receipts_no_follows_status" in str(hata.value)


def test_bilinmeyen_DURUM_reddedilir(motor) -> None:
    """Kapalı küme dışındaki durum REDDEDİLİR.

    HANGİ kısıtın bağırdığı SÖZLEŞME DEĞİLDİR ve bu bilinçli: bilinmeyen
    bir durum İKİ kısıtı birden ihlal eder — `ck_..._status` (kapalı küme)
    ve `ck_..._no_follows_status` (durum ya `draft`tır ya da
    `issued`/`cancelled`, üçü de değilse yüklem HİÇBİR dalı tutmaz).
    Örtüşme kısıtların TANIMINDAN gelir, bir kusurdan değil.

    ÖLÇÜLDÜ (PostgreSQL 16.13): `'paid'` için önce
    `ck_producer_receipts_no_follows_status` bağırıyor. Bu sıra
    PostgreSQL'in kısıtları değerlendirme sırasına bağlıdır ve sürümler
    arasında DEĞİŞEBİLİR; teste tek bir ad çivilemek, o gün kusuru değil
    değerlendirme sırasını ölçerdi. Bu yüzden kapı İKİSİNDEN BİRİNİ
    kabul ediyor — ölçtüğü şey satırın GİRMEDİĞİDİR.
    """
    with motor.begin() as baglanti:
        cid, tedarikci = _firma_kur(baglanti, FIRMA_ADI)

    with pytest.raises(IntegrityError) as hata:
        with motor.begin() as baglanti:
            _makbuz_yaz(baglanti, cid, tedarikci, durum="paid")
    mesaj = str(hata.value)
    assert (
        "ck_producer_receipts_status" in mesaj
        or "ck_producer_receipts_no_follows_status" in mesaj
    ), mesaj

    # Satır GERÇEKTEN girmedi: kısıt adı ne olursa olsun ölçülen budur.
    with motor.connect() as baglanti:
        sayi = baglanti.execute(
            text(
                "SELECT COUNT(*) FROM producer_receipts "
                "WHERE company_id = :c AND status = 'paid'"
            ),
            {"c": cid},
        ).scalar_one()
    assert sayi == 0, sayi
