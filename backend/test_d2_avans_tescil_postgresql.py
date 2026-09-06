"""PostgreSQL ikizi: D2 ŞEMASININ gerçek kısıtlarla eşi.

Göç `20260906_0071`. SQLite ikizi `tests/test_d2_avans_tescil.py` mahsup
aritmetiğini ve uçların sözleşmesini ölçüyor; bu dosya yalnız ŞEMANIN
GERÇEKTEN ISIRAN kısımlarını ölçer — hepsi SQLite'ta GERÇEKTEN SINANAMAZ.

--- BU İKİZ NEDEN VAR ------------------------------------------------------

1. **BİLEŞİK YABANCI ANAHTARIN GERÇEKTEN ISIRMASI.** SQLite'ta yabancı
   anahtar uygulaması varsayılan olarak KAPALIDIR (temiz şemada
   `PRAGMA foreign_keys` **0** döner), yani çapraz kiracı bir referans
   orada YEŞİL kalırdı. Bu göçün yeni tabloları YEDİ bileşik anahtar
   taşıyor (avans -> tedarikçi/ödeme/makbuz, yükümlülük -> makbuz/kapatma
   ödemesi, tescil -> makbuz) ve hepsinin TEK işi bir kiracının satırının
   BAŞKA kiracının satırını işaret etmesini engellemektir. Uygulanmayan
   bir yabancı anahtar, savunma DEĞİL süstür.

2. **`payments(company_id, id)` TEKİLLİĞİ.** Bu göçün kurduğu UNIQUE,
   avansın ve yükümlülüğün bileşik yabancı anahtarlarının HEDEFİDİR.
   Eksik olsaydı PostgreSQL göçü REDDEDERDİ ("there is no unique
   constraint matching given keys"); SQLite ise yabancı anahtarı hiç
   uygulamadığı için SESSİZCE geçerdi.

3. **`0 <= remaining_amount <= amount` ARALIĞI.** Kalanın avansı AŞMASI
   ya da negatife düşmesi, mahsubun defteri uydurmaya başladığı andır.

4. **`advance_applied_total >= 0`.** Bu CHECK var olan bir tabloya
   `batch_alter_table` ile eklendi; gerçekten ISIRDIĞI burada ölçülüyor.

5. **`UNIQUE(company_id, receipt_id, kind)` ve
   `UNIQUE(company_id, receipt_id)`.** Aynı makbuzun aynı stopajı İKİ KEZ
   doğurması ya da İKİ KEZ borsaya tescil edilmesi yalnız şema
   seviyesinde kesin olarak engellenir; uygulama kontrolü iki EŞZAMANLI
   isteği ayırt EDEMEZ.

6. **`UNIQUE(company_id, payment_id)`.** Bir ödeme satırını iki avansın
   göstermesi, kasadan BİR KEZ çıkan parayı İKİ KEZ mahsup ettirirdi.

7. **`NUMERIC(18,2)` TUTAR ÖLÇEĞİ.** SQLite `NUMERIC`i tür/ölçek
   DAYATMAZ: üç basamaklı bir kuruş orada SESSİZCE geçer.

--- ÖLÇÜLEN KÖK SEBEP, İDDİA DEĞİL -----------------------------------------

Aşağıdaki testler kısıtın VARLIĞINI değil GERÇEKTEN REDDETTİĞİNİ ölçüyor —
her biri kısıtı ihlal eden bir yazma deneyip `IntegrityError` (ölçek için
`DataError`) bekliyor.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from threading import Barrier
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DataError, IntegrityError

BACKEND = Path(__file__).resolve().parent

FIRMA_ADI = "D2 İKİZİ firması"
KOMSU_ADI = "D2 İKİZİ komşu firması"


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("D2 ikizi APP_TEST_DATABASE_URL ister")
    return url


def _temizle(engine) -> None:
    with engine.begin() as baglanti:
        for deyim in (
            "DELETE FROM producer_receipt_exchange_registrations WHERE "
            "company_id IN (SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM tax_liabilities WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM supplier_advances WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM payments WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
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
            "VALUES (:cid, 'D2 İkiz Çiftçi', '22222222222', 0, true) "
            "RETURNING id"
        ),
        {"cid": cid},
    ).scalar_one()
    return cid, tedarikci


def _makbuz_yaz(baglanti, cid: int, tedarikci: int, **ustune) -> int:
    simdi = datetime.now(timezone.utc)
    degerler = {
        "cid": cid,
        "sid": tedarikci,
        "no": None,
        "brut": Decimal("100.00"),
        "stopaj": Decimal("4.00"),
        "sgk": Decimal("2.00"),
        "net": Decimal("94.00"),
        "durum": "draft",
        "mahsup": Decimal("0.00"),
        "s": simdi,
    }
    degerler.update(ustune)
    return baglanti.execute(
        text(
            "INSERT INTO producer_receipts (company_id, supplier_id, "
            "receipt_no, gross_amount, withholding_total, "
            "social_security_total, net_payable, status, "
            "advance_applied_total, created_at, updated_at) "
            "VALUES (:cid, :sid, :no, :brut, :stopaj, :sgk, :net, :durum, "
            ":mahsup, :s, :s) RETURNING id"
        ),
        degerler,
    ).scalar_one()


def _odeme_yaz(baglanti, cid: int, tedarikci: int, **ustune) -> int:
    degerler = {
        "cid": cid,
        "sid": tedarikci,
        "tutar": Decimal("100.00"),
        "tarih": "2026-09-06",
        "rtype": "supplier_advance",
        "rid": None,
    }
    degerler.update(ustune)
    return baglanti.execute(
        text(
            "INSERT INTO payments (company_id, entity_type, entity_id, amount, "
            "payment_date, payment_method, reference_type, reference_id) "
            "VALUES (:cid, 'supplier', :sid, :tutar, :tarih, 'cash', :rtype, "
            ":rid) RETURNING id"
        ),
        degerler,
    ).scalar_one()


def _avans_yaz(baglanti, cid: int, tedarikci: int, odeme: int, **ustune) -> int:
    simdi = datetime.now(timezone.utc)
    degerler = {
        "cid": cid,
        "sid": tedarikci,
        "pid": odeme,
        "tutar": Decimal("100.00"),
        "kalan": Decimal("100.00"),
        "rid": None,
        "s": simdi,
    }
    degerler.update(ustune)
    return baglanti.execute(
        text(
            "INSERT INTO supplier_advances (company_id, supplier_id, "
            "payment_id, amount, remaining_amount, receipt_id, created_at, "
            "updated_at) VALUES (:cid, :sid, :pid, :tutar, :kalan, :rid, :s, "
            ":s) RETURNING id"
        ),
        degerler,
    ).scalar_one()


# ---------------------------------------------------------------------------
# 1) ŞEMA GERÇEKTEN KURULDU
# ---------------------------------------------------------------------------


def test_uc_tablo_ve_payments_tekilligi_KURULDU(motor) -> None:
    """Üç tablo VAR ve `payments`ın bileşik hedefi KURULDU.

    `uq_payments_company_id` olmasaydı PostgreSQL bu göçü ZATEN
    reddederdi ("there is no unique constraint matching given keys");
    kapı yine de burada, çünkü SQLite onu istemeden geçirir ve iki
    diyalektin AYRI şemaya gitmesi sessiz bir ayrışma olurdu.
    """
    denetci = inspect(motor)
    tablolar = set(denetci.get_table_names())
    for ad in (
        "supplier_advances",
        "tax_liabilities",
        "producer_receipt_exchange_registrations",
    ):
        assert ad in tablolar, ad
    adlar = {k["name"] for k in denetci.get_unique_constraints("payments")}
    assert "uq_payments_company_id" in adlar, sorted(adlar)
    sutunlar = {s["name"] for s in denetci.get_columns("producer_receipts")}
    assert "advance_applied_total" in sutunlar


def test_kismi_indeks_ve_yabanci_anahtarlar_SAG(motor) -> None:
    """`advance_applied_total`ın CHECK'i eklenirken makbuz tablosu BOZULMADI.

    Kısıt `batch_alter_table` ile geldi ve o yol (SQLite'ta) tabloyu
    YENİDEN KURAR. Korkulan şey kısmi benzersiz indeksin `WHERE`
    yükleminin kaybolmasıydı — o zaman numara tekilliği SESSİZCE
    gevşerdi. PostgreSQL yeniden kurulum YAPMAZ ama kapı iki diyalektte
    de AYNI şeyi ölçsün diye burada duruyor.
    """
    denetci = inspect(motor)
    indeksler = {i["name"]: i for i in denetci.get_indexes("producer_receipts")}
    kismi = indeksler.get("ux_producer_receipts_company_receipt_no")
    assert kismi is not None, sorted(indeksler)
    assert kismi["unique"] is True, kismi
    yabancilar = {
        f["name"] for f in denetci.get_foreign_keys("producer_receipts")
    }
    for ad in (
        "fk_producer_receipts_supplier_same_company",
        "fk_producer_receipts_purchase_same_company",
        "fk_producer_receipts_ticket_same_company",
    ):
        assert ad in yabancilar, sorted(yabancilar)


# ---------------------------------------------------------------------------
# 2) ÇAPRAZ KİRACI — VERİTABANI REDDEDİYOR
# ---------------------------------------------------------------------------


def test_baska_firmanin_tedarikcisine_avans_reddedilir(motor) -> None:
    """Avansın tedarikçisi BAŞKA firmanın olamaz. SQLite'ta bu SESSİZCE geçer."""
    with motor.begin() as baglanti:
        cid, _ = _firma_kur(baglanti, FIRMA_ADI)
        _, komsu_tedarikci = _firma_kur(baglanti, KOMSU_ADI)
        odeme = _odeme_yaz(baglanti, cid, komsu_tedarikci)
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _avans_yaz(baglanti, cid, komsu_tedarikci, odeme)


def test_baska_firmanin_odemesine_bagli_avans_reddedilir(motor) -> None:
    """Avansın ödeme satırı BAŞKA firmanın olamaz.

    Bu, `uq_payments_company_id`nin VAR OLMA sebebidir: hedef tekillik
    olmadan bu bileşik yabancı anahtar KURULAMAZDI.
    """
    with motor.begin() as baglanti:
        cid, tedarikci = _firma_kur(baglanti, FIRMA_ADI)
        komsu_cid, komsu_tedarikci = _firma_kur(baglanti, KOMSU_ADI)
        komsu_odeme = _odeme_yaz(baglanti, komsu_cid, komsu_tedarikci)
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _avans_yaz(baglanti, cid, tedarikci, komsu_odeme)


def test_baska_firmanin_makbuzuna_yukumluluk_reddedilir(motor) -> None:
    """Yükümlülüğün makbuzu BAŞKA firmanın olamaz."""
    with motor.begin() as baglanti:
        cid, _ = _firma_kur(baglanti, FIRMA_ADI)
        komsu_cid, komsu_tedarikci = _firma_kur(baglanti, KOMSU_ADI)
        komsu_makbuz = _makbuz_yaz(baglanti, komsu_cid, komsu_tedarikci)
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            baglanti.execute(
                text(
                    "INSERT INTO tax_liabilities (company_id, kind, "
                    "receipt_id, amount, due_period, created_at) "
                    "VALUES (:cid, 'withholding', :rid, 4.00, '2026-09', :s)"
                ),
                {
                    "cid": cid,
                    "rid": komsu_makbuz,
                    "s": datetime.now(timezone.utc),
                },
            )


def test_baska_firmanin_makbuzuna_tescil_reddedilir(motor) -> None:
    """Tescilin makbuzu BAŞKA firmanın olamaz."""
    with motor.begin() as baglanti:
        cid, _ = _firma_kur(baglanti, FIRMA_ADI)
        komsu_cid, komsu_tedarikci = _firma_kur(baglanti, KOMSU_ADI)
        komsu_makbuz = _makbuz_yaz(baglanti, komsu_cid, komsu_tedarikci)
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            baglanti.execute(
                text(
                    "INSERT INTO producer_receipt_exchange_registrations "
                    "(company_id, receipt_id, registration_no, exchange_name, "
                    "registered_on, fee_amount, created_at) "
                    "VALUES (:cid, :rid, 'X', 'Borsa', :g, 0, :s)"
                ),
                {
                    "cid": cid,
                    "rid": komsu_makbuz,
                    "g": date(2026, 9, 6),
                    "s": datetime.now(timezone.utc),
                },
            )


def test_baska_firmanin_makbuzuna_mahsup_edilmis_avans_reddedilir(motor) -> None:
    """Avansın mahsup edildiği makbuz BAŞKA firmanın olamaz."""
    with motor.begin() as baglanti:
        cid, tedarikci = _firma_kur(baglanti, FIRMA_ADI)
        komsu_cid, komsu_tedarikci = _firma_kur(baglanti, KOMSU_ADI)
        komsu_makbuz = _makbuz_yaz(baglanti, komsu_cid, komsu_tedarikci)
        odeme = _odeme_yaz(baglanti, cid, tedarikci)
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _avans_yaz(baglanti, cid, tedarikci, odeme, rid=komsu_makbuz)


# ---------------------------------------------------------------------------
# 3) ARALIK VE TEKİLLİK KISITLARI GERÇEKTEN ISIRIYOR
# ---------------------------------------------------------------------------


def test_kalan_avansi_ASAMAZ(motor) -> None:
    """`remaining_amount <= amount` — kalan avanstan büyük olamaz."""
    with motor.begin() as baglanti:
        cid, tedarikci = _firma_kur(baglanti, FIRMA_ADI)
        odeme = _odeme_yaz(baglanti, cid, tedarikci)
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _avans_yaz(
                baglanti,
                cid,
                tedarikci,
                odeme,
                tutar=Decimal("100.00"),
                kalan=Decimal("100.01"),
            )


def test_kalan_NEGATIF_olamaz(motor) -> None:
    """`remaining_amount >= 0` — aşırı mahsup defteri uydurmaya başlar."""
    with motor.begin() as baglanti:
        cid, tedarikci = _firma_kur(baglanti, FIRMA_ADI)
        odeme = _odeme_yaz(baglanti, cid, tedarikci)
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _avans_yaz(baglanti, cid, tedarikci, odeme, kalan=Decimal("-0.01"))


def test_avans_tutari_POZITIF_olmali(motor) -> None:
    """`amount > 0` — sıfır tutarlı avans kasadan hiçbir şey çıkarmaz."""
    with motor.begin() as baglanti:
        cid, tedarikci = _firma_kur(baglanti, FIRMA_ADI)
        odeme = _odeme_yaz(baglanti, cid, tedarikci)
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _avans_yaz(
                baglanti,
                cid,
                tedarikci,
                odeme,
                tutar=Decimal("0.00"),
                kalan=Decimal("0.00"),
            )


def test_bir_odeme_IKI_avans_olamaz(motor) -> None:
    """`UNIQUE(company_id, payment_id)` — bir para İKİ KEZ mahsup edilemez."""
    with motor.begin() as baglanti:
        cid, tedarikci = _firma_kur(baglanti, FIRMA_ADI)
        odeme = _odeme_yaz(baglanti, cid, tedarikci)
        _avans_yaz(baglanti, cid, tedarikci, odeme)
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _avans_yaz(baglanti, cid, tedarikci, odeme)


def test_mahsup_toplami_NEGATIF_olamaz(motor) -> None:
    """`advance_applied_total >= 0` — batch ile eklenen CHECK GERÇEKTEN ısırıyor."""
    with motor.begin() as baglanti:
        cid, tedarikci = _firma_kur(baglanti, FIRMA_ADI)
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _makbuz_yaz(baglanti, cid, tedarikci, mahsup=Decimal("-0.01"))


def test_ayni_makbuz_ayni_CINSTEN_IKI_yukumluluk_DOGURAMAZ(motor) -> None:
    """`UNIQUE(company_id, receipt_id, kind)` — aynı stopaj iki kez beyan edilemez.

    Uygulama kontrolü İKİ EŞZAMANLI yazmayı ayırt EDEMEZ; kesinlik yalnız
    burada.
    """
    with motor.begin() as baglanti:
        cid, tedarikci = _firma_kur(baglanti, FIRMA_ADI)
        makbuz = _makbuz_yaz(baglanti, cid, tedarikci)
        baglanti.execute(
            text(
                "INSERT INTO tax_liabilities (company_id, kind, receipt_id, "
                "amount, due_period, created_at) "
                "VALUES (:cid, 'withholding', :rid, 4.00, '2026-09', :s)"
            ),
            {"cid": cid, "rid": makbuz, "s": datetime.now(timezone.utc)},
        )
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            baglanti.execute(
                text(
                    "INSERT INTO tax_liabilities (company_id, kind, "
                    "receipt_id, amount, due_period, created_at) "
                    "VALUES (:cid, 'withholding', :rid, 4.00, '2026-09', :s)"
                ),
                {"cid": cid, "rid": makbuz, "s": datetime.now(timezone.utc)},
            )


def test_yukumluluk_CINSI_kapali_kume(motor) -> None:
    """`kind IN ('withholding','social_security')` — üçüncü bir tür YOK."""
    with motor.begin() as baglanti:
        cid, tedarikci = _firma_kur(baglanti, FIRMA_ADI)
        makbuz = _makbuz_yaz(baglanti, cid, tedarikci)
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            baglanti.execute(
                text(
                    "INSERT INTO tax_liabilities (company_id, kind, "
                    "receipt_id, amount, due_period, created_at) "
                    "VALUES (:cid, 'kdv', :rid, 4.00, '2026-09', :s)"
                ),
                {"cid": cid, "rid": makbuz, "s": datetime.now(timezone.utc)},
            )


def test_bir_makbuz_IKI_KEZ_tescil_EDILEMEZ(motor) -> None:
    """`UNIQUE(company_id, receipt_id)` — aynı mal iki kez borsaya girmez."""
    with motor.begin() as baglanti:
        cid, tedarikci = _firma_kur(baglanti, FIRMA_ADI)
        makbuz = _makbuz_yaz(baglanti, cid, tedarikci)
        baglanti.execute(
            text(
                "INSERT INTO producer_receipt_exchange_registrations "
                "(company_id, receipt_id, registration_no, exchange_name, "
                "registered_on, fee_amount, created_at) "
                "VALUES (:cid, :rid, 'BRS-1', 'Borsa', :g, 0, :s)"
            ),
            {
                "cid": cid,
                "rid": makbuz,
                "g": date(2026, 9, 6),
                "s": datetime.now(timezone.utc),
            },
        )
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            baglanti.execute(
                text(
                    "INSERT INTO producer_receipt_exchange_registrations "
                    "(company_id, receipt_id, registration_no, exchange_name, "
                    "registered_on, fee_amount, created_at) "
                    "VALUES (:cid, :rid, 'BRS-2', 'Baska', :g, 0, :s)"
                ),
                {
                    "cid": cid,
                    "rid": makbuz,
                    "g": date(2026, 9, 6),
                    "s": datetime.now(timezone.utc),
                },
            )


def test_tescil_ucreti_SIFIR_SERBEST_negatif_DEGIL(motor) -> None:
    """`fee_amount >= 0` — ücretsiz tescil GERÇEKTİR, eksi ücret değil.

    Kapı `ge=0` ile `gt=0` arasındaki AYRIMI ölçüyor: sıfır KABUL edilmeli
    (yoksa ücretsiz tescil kaydedilemez olurdu), eksi REDDEDİLMELİ.
    """
    with motor.begin() as baglanti:
        cid, tedarikci = _firma_kur(baglanti, FIRMA_ADI)
        makbuz = _makbuz_yaz(baglanti, cid, tedarikci)
        baglanti.execute(
            text(
                "INSERT INTO producer_receipt_exchange_registrations "
                "(company_id, receipt_id, registration_no, exchange_name, "
                "registered_on, fee_amount, created_at) "
                "VALUES (:cid, :rid, 'BRS-0', 'Borsa', :g, 0, :s)"
            ),
            {
                "cid": cid,
                "rid": makbuz,
                "g": date(2026, 9, 6),
                "s": datetime.now(timezone.utc),
            },
        )
        makbuz2 = _makbuz_yaz(baglanti, cid, tedarikci)
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            baglanti.execute(
                text(
                    "INSERT INTO producer_receipt_exchange_registrations "
                    "(company_id, receipt_id, registration_no, exchange_name, "
                    "registered_on, fee_amount, created_at) "
                    "VALUES (:cid, :rid, 'BRS-X', 'Borsa', :g, -0.01, :s)"
                ),
                {
                    "cid": cid,
                    "rid": makbuz2,
                    "g": date(2026, 9, 6),
                    "s": datetime.now(timezone.utc),
                },
            )


# ---------------------------------------------------------------------------
# 4) ÖLÇEK — SQLite'ta SESSİZCE GEÇEN
# ---------------------------------------------------------------------------


def test_avans_tutarinin_OLCEGI_18_2(motor) -> None:
    """`NUMERIC(18,2)` taşması REDDEDİLİR; SQLite ölçeği DAYATMAZ."""
    with motor.begin() as baglanti:
        cid, tedarikci = _firma_kur(baglanti, FIRMA_ADI)
        odeme = _odeme_yaz(baglanti, cid, tedarikci)
    with pytest.raises((DataError, IntegrityError)):
        with motor.begin() as baglanti:
            _avans_yaz(
                baglanti,
                cid,
                tedarikci,
                odeme,
                tutar=Decimal("1" * 17 + ".00"),
                kalan=Decimal("1" * 17 + ".00"),
            )


def test_tedarikci_bakiyesi_makbuz_BORCUNU_sayiyor(motor) -> None:
    """Ekstre PostgreSQLde de makbuzu BORC sayar ve gun suzgeci PATLAMAZ.

    BU IKIZ ZORUNLU cunku iddia YALNIZ uretim diyalektinde sinanabilir:
    `issued_at` bir `timestamptz`tir ve oteki tarih sutunlari GUN
    dizgisidir; pencere karsilastirmasini onlarla ayni bicimde yazmak
    (`COALESCE(issued_at, '')`) PostgreSQLde TIP HATASI verir, SQLitede
    ise SESSIZCE gecer. Yani `SUBSTR(CAST(... AS TEXT),1,10)` cozumunun
    dogrulugu SQLite kosumunda GORUNMEZ.

    Akis, SQLite ikizindekiyle AYNI: avans 100 + makbuz net 300
    (brut 400, stopaj 80, sgk 20) + odeme 200 -> bakiye 0.
    """
    from sqlalchemy.orm import Session as _Session

    from app.statement import build_statement

    with motor.begin() as baglanti:
        cid, tedarikci = _firma_kur(baglanti, FIRMA_ADI)
        makbuz = _makbuz_yaz(
            baglanti, cid, tedarikci,
            no="MM-PG-1", durum="issued",
            brut=Decimal("400.00"), stopaj=Decimal("80.00"),
            sgk=Decimal("20.00"), net=Decimal("300.00"),
            mahsup=Decimal("100.00"),
        )
        baglanti.execute(
            text(
                "UPDATE producer_receipts SET issued_at=:t "
                "WHERE company_id=:cid AND id=:rid"
            ),
            {"t": datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc),
             "cid": cid, "rid": makbuz},
        )
        # Avans 100 + makbuz odemesi 200 = 300 alacak.
        _odeme_yaz(baglanti, cid, tedarikci, tutar=Decimal("100.00"),
                   tarih="2026-09-09", rtype="supplier_advance")
        _odeme_yaz(baglanti, cid, tedarikci, tutar=Decimal("200.00"),
                   tarih="2026-09-11", rtype="producer_receipt", rid=makbuz)

    with _Session(motor) as oturum:
        ekstre = build_statement(
            oturum, cid, "supplier", tedarikci,
            date(2026, 1, 1), date(2026, 12, 31),
        )
    assert Decimal(str(ekstre.closing_balance)) == Decimal("0"), (
        ekstre.closing_balance
    )
    satirlar = [x for x in ekstre.lines if x.kind == "producer_receipt"]
    assert len(satirlar) == 1, [x.kind for x in ekstre.lines]
    # NET, BRUT DEGIL: brut sayilsaydi kapanis 100 fazla cikardi.
    assert Decimal(str(satirlar[0].debit)) == Decimal("300"), satirlar[0]
    assert Decimal(str(satirlar[0].credit)) == Decimal("0"), satirlar[0]


def test_TASLAK_ve_IPTAL_makbuz_bakiyeye_GIRMEZ(motor) -> None:
    """Yalniz `issued` borc dogurur; `draft`/`cancelled`/`issuing` GIRMEZ.

    Olumsuz liste (`NOT IN ('draft','cancelled')`) `issuing` ARA DURUMUNU
    sessizce borc sayardi; olumlu liste onu da diser.
    """
    from sqlalchemy.orm import Session as _Session

    from app.statement import build_statement

    with motor.begin() as baglanti:
        cid, tedarikci = _firma_kur(baglanti, FIRMA_ADI)
        for i, durum in enumerate(("draft", "issuing", "cancelled")):
            no = None if durum in ("draft", "issuing") else "MM-PG-C%d" % i
            rid = _makbuz_yaz(
                baglanti, cid, tedarikci, no=no, durum=durum,
                net=Decimal("500.00"),
            )
            baglanti.execute(
                text(
                    "UPDATE producer_receipts SET issued_at=:t "
                    "WHERE company_id=:cid AND id=:rid"
                ),
                {"t": datetime(2026, 9, 10, tzinfo=timezone.utc),
                 "cid": cid, "rid": rid},
            )

    with _Session(motor) as oturum:
        ekstre = build_statement(
            oturum, cid, "supplier", tedarikci,
            date(2026, 1, 1), date(2026, 12, 31),
        )
    assert Decimal(str(ekstre.closing_balance)) == Decimal("0"), (
        "borc dogurmayan makbuzlar bakiyeye girdi: %s" % ekstre.closing_balance
    )
    assert [x for x in ekstre.lines if x.kind == "producer_receipt"] == []


RACE_ROUNDS = 20
ADMIN_PW = "D2Race!123"


def _admin_headers(client):
    for candidate in ("admin123", ADMIN_PW):
        login = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": candidate},
        )
        if login.status_code == 200:
            break
    assert login.status_code == 200, login.text
    body = login.json()
    headers = {
        "Authorization": "Bearer " + body["access_token"],
        "X-Company-ID": str(body["companies"][0]["id"]),
    }
    if candidate != ADMIN_PW:
        changed = client.post(
            "/api/auth/change-password",
            headers=headers,
            json={"current_password": candidate, "new_password": ADMIN_PW},
        )
        assert changed.status_code == 200, changed.text
        headers["Authorization"] = "Bearer " + changed.json()["access_token"]
    return headers, int(body["companies"][0]["id"])


def test_ayni_makbuza_eszamanli_odeme_TEK_kez_gecer(motor) -> None:
    """`cash_due` kadar İKİ eşzamanlı `/pay` -> TAM BİR 200 ve BİR 422.

    ÖLÇÜLEN KUSUR (mercek, READ COMMITTED): kilit yokken İKİSİ DE 200
    aldı ve `payments` toplamı 2×`cash_due` çıktı — tavan denetimi
    OKU-SONRA-YAZ'dır ve kendi başına bir kilit DEĞİLDİR.

    BU İKİZ ZORUNLU VE ASİMETRİ ADIYLA YAZILIYOR: SQLite TEK YAZARDIR,
    yarış orada ÜRETİLEMEZ ve `FOR UPDATE` sözdizimi bile REDDEDİLİR —
    yani hem kusur hem de düzeltmesi geliştirme diyalektinde GÖRÜNMEZ.
    Kilit kaldırıldığında SQLite süiti YEŞİL KALIR, burası KIRMIZI olur.

    `RACE_ROUNDS` tur koşuluyor: tek turluk bir yeşil, yarışın hiç
    tetiklenmediği anlamına da gelebilirdi.
    """
    from fastapi.testclient import TestClient

    from app.db import SessionLocal
    from app.main import app

    with TestClient(app) as client:
        headers, cid = _admin_headers(client)
        with SessionLocal() as db:
            tedarikci = db.execute(
                text(
                    "INSERT INTO suppliers (company_id, name, opening_balance,"
                    " is_active) VALUES (:cid, :ad, 0, true) RETURNING id"
                ),
                {"cid": cid, "ad": FIRMA_ADI + " yaris"},
            ).scalar_one()
            db.commit()

        for tur in range(RACE_ROUNDS):
            with SessionLocal() as db:
                makbuz = db.execute(
                    text(
                        "INSERT INTO producer_receipts (company_id,"
                        " supplier_id, receipt_no, issued_at, gross_amount,"
                        " withholding_total, social_security_total,"
                        " net_payable, status, advance_applied_total,"
                        " created_at, updated_at) VALUES (:cid, :sid, :no,"
                        " :t, 100, 0, 0, 100, 'issued', 0, :t, :t)"
                        " RETURNING id"
                    ),
                    {
                        "cid": cid,
                        "sid": tedarikci,
                        "no": "MM-RACE-%d" % tur,
                        "t": datetime.now(timezone.utc),
                    },
                ).scalar_one()
                db.commit()

            barrier = Barrier(2)

            def pay_once() -> int:
                with TestClient(app) as concurrent:
                    barrier.wait(timeout=30)
                    return concurrent.post(
                        "/api/producer-receipts/%d/pay" % makbuz,
                        headers=headers,
                        json={
                            "amount": "100.00",
                            "payment_method": "cash",
                            "payment_date": "2026-09-12",
                        },
                    ).status_code

            with ThreadPoolExecutor(max_workers=2) as pool:
                durumlar = sorted(
                    f.result(timeout=60)
                    for f in (pool.submit(pay_once), pool.submit(pay_once))
                )
            assert durumlar == [200, 422], (tur, durumlar)

            with SessionLocal() as db:
                toplam = db.execute(
                    text(
                        "SELECT COALESCE(SUM(amount),0) FROM payments"
                        " WHERE company_id=:cid"
                        " AND reference_type='producer_receipt'"
                        " AND reference_id=:rid"
                    ),
                    {"cid": cid, "rid": makbuz},
                ).scalar()
            assert Decimal(str(toplam)) == Decimal("100.00"), (tur, toplam)

        with SessionLocal() as db:
            db.execute(
                text(
                    "DELETE FROM payments WHERE company_id=:cid"
                    " AND entity_id=:sid AND entity_type='supplier'"
                ),
                {"cid": cid, "sid": tedarikci},
            )
            db.execute(
                text(
                    "DELETE FROM producer_receipts WHERE company_id=:cid"
                    " AND supplier_id=:sid"
                ),
                {"cid": cid, "sid": tedarikci},
            )
            db.execute(
                text("DELETE FROM suppliers WHERE company_id=:cid AND id=:sid"),
                {"cid": cid, "sid": tedarikci},
            )
            db.commit()


def test_kurus_UCUNCU_basamagi_OLCEGE_oturur(motor) -> None:
    """Üç basamaklı kuruş `NUMERIC(18,2)`de İKİYE yuvarlanır.

    SQLite ölçeği taşımadığı için orada `100.005` OLDUĞU GİBİ durur ve iki
    diyalekt AYNI satırdan AYRI cevap verirdi. Kapı ölçeğin GERÇEKTEN
    dayatıldığını ölçüyor.
    """
    with motor.begin() as baglanti:
        cid, tedarikci = _firma_kur(baglanti, FIRMA_ADI)
        odeme = _odeme_yaz(baglanti, cid, tedarikci)
        avans = _avans_yaz(
            baglanti,
            cid,
            tedarikci,
            odeme,
            tutar=Decimal("100.005"),
            kalan=Decimal("100.005"),
        )
        okunan = baglanti.execute(
            text(
                "SELECT amount FROM supplier_advances "
                "WHERE company_id=:cid AND id=:aid"
            ),
            {"cid": cid, "aid": avans},
        ).scalar_one()
    assert okunan == Decimal("100.01"), okunan
    assert okunan.as_tuple().exponent == -2, okunan
