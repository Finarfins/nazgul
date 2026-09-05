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
   hareket eder: taslakta ve `issuing` (CAS ara durumu) aşamasında numara
   OLMAZ, kesilmiş makbuzda numarasızlık OLMAZ. Bu kısıt `issue` yolundaki
   bir hatanın numarasız "issued" satır bırakmasını engeller ve gerçekten
   ısırdığı burada ölçülüyor.

6. **EŞZAMANLI `issue` CAS.** Aynı taslağa iki eşzamanlı `issue` tam bir
   200 + bir 409 üretir ve `document_sequences` her turda YALNIZ 1 artar;
   iki ayrı taslağa iki eşzamanlı `issue` iki AYRI numara üretir. Bu iddia
   yalnız gerçek PostgreSQL satır kilidi + READ COMMITTED altında ayırt
   edicidir (SQLite tek bağlantıda yarışı yeniden üretemez).

--- ÖLÇÜLEN KÖK SEBEP, İDDİA DEĞİL -----------------------------------------

0066'nın dersi: belgelenmiş ama savunmayan bir savunma, savunma
OLMAMASINDAN kötüdür. Aşağıdaki testler kısıtın VARLIĞINI değil GERÇEKTEN
REDDETTİĞİNİ ölçüyor — her biri kısıtı ihlal eden bir yazma deneyip
`IntegrityError` (ya da ölçek için `DataError`) bekliyor.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import Barrier

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DataError, IntegrityError

BACKEND = Path(__file__).resolve().parent

FIRMA_ADI = "MÜSTAHSİL İKİZİ firması"
KOMSU_ADI = "MÜSTAHSİL İKİZİ komşu firması"
ADMIN_PW = "MustahsilRace!123"
RACE_ROUNDS = 20


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
    ve `ck_..._no_follows_status` (durum `draft`/`issuing` ya da
    `issued`/`cancelled`, dördü de değilse yüklem HİÇBİR dalı tutmaz).
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


def test_issuing_numarasiz_KABUL_numarali_RED(motor) -> None:
    """`issuing` CAS ara durumu: numarasız geçer, numaralı CHECK kırar."""
    with motor.begin() as baglanti:
        cid, tedarikci = _firma_kur(baglanti, FIRMA_ADI)
        mid = _makbuz_yaz(baglanti, cid, tedarikci, durum="issuing")
        assert mid is not None

    with pytest.raises(IntegrityError) as hata:
        with motor.begin() as baglanti:
            _makbuz_yaz(
                baglanti, cid, tedarikci, durum="issuing", no="MM-000099",
            )
    assert "ck_producer_receipts_no_follows_status" in str(hata.value)


# ---------------------------------------------------------------------------
# EŞZAMANLI ISSUE / CANCEL — gerçek satır kilidi, gerçek numara serisi.
# ---------------------------------------------------------------------------


def _admin_headers(client: TestClient) -> tuple[dict[str, str], int]:
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


def _sira_degeri(cid: int) -> int:
    from app.db import SessionLocal

    with SessionLocal() as db:
        deger = db.execute(
            text(
                "SELECT current_value FROM document_sequences "
                "WHERE company_id=:cid AND sequence_key='producer_receipts:MM'"
            ),
            {"cid": cid},
        ).scalar()
    return 0 if deger is None else int(deger)


def _taslak_kur(client: TestClient, headers: dict[str, str],
                supplier_id: int, product_id: int) -> int:
    cevap = client.post(
        "/api/producer-receipts",
        headers=headers,
        json={
            "supplier_id": supplier_id,
            "items": [{
                "product_id": product_id,
                "entered_quantity": "100",
                "entered_unit": "KG",
                "unit_price": "10.00",
                "withholding_rate": "2",
                "social_security_rate": "1",
            }],
        },
    )
    assert cevap.status_code == 201, cevap.text
    return int(cevap.json()["id"])


def _hazirlik(
    cid: int, urun_adi: str, ciftci_adi: str, vergi_no: str,
) -> tuple[int, int]:
    """Yarış testine ürün + tedarikçi kurar; id'leri RETURNING ile alır."""
    from app.db import SessionLocal

    with SessionLocal() as db:
        product_id = int(db.execute(
            text(
                "INSERT INTO products (name,purchase_price,sale_price,"
                "vat_rate,stock,unit,price_per,active,critical_stock,"
                "minimum_stock,company_id,base_unit) "
                "VALUES (:n,0,0,0,0,'KG',1,true,0,0,:c,'KG') RETURNING id"
            ),
            {"n": urun_adi, "c": cid},
        ).scalar_one())
        supplier_id = int(db.execute(
            text(
                "INSERT INTO suppliers (name,tax_number,opening_balance,"
                "is_active,company_id) "
                "VALUES (:n,:v,0,true,:c) RETURNING id"
            ),
            {"n": ciftci_adi, "v": vergi_no, "c": cid},
        ).scalar_one())
        db.commit()
    return product_id, supplier_id


@pytest.mark.postgresql
def test_eszamanli_issue_ayni_taslak_tek_kazanan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Aynı taslağa 2 thread × 20: tam bir 200 + bir 409; sira delta == 1."""
    monkeypatch.setenv("DATABASE_URL", _url())

    from app.main import app

    with TestClient(app) as client:
        headers, cid = _admin_headers(client)
        product_id, supplier_id = _hazirlik(
            cid, "Race urun", "Race ciftci", "22222222222"
        )

        # İlk turda `next_document_no` satırı MAX(id) ile TOHUMLAR;
        # tohum+1 sıfırdan 2 gibi görünebilir. Seriyi ÖNCE ısıt ki
        # her turun delta'sı yalnızca CAS tüketimini ölçsün.
        isit = _taslak_kur(client, headers, supplier_id, product_id)
        assert client.post(
            f"/api/producer-receipts/{isit}/issue", headers=headers
        ).status_code == 200

        for tur in range(RACE_ROUNDS):
            makbuz_id = _taslak_kur(client, headers, supplier_id, product_id)
            once = _sira_degeri(cid)
            barrier = Barrier(2)

            def issue_once() -> int:
                with TestClient(app) as concurrent:
                    barrier.wait(timeout=30)
                    return concurrent.post(
                        f"/api/producer-receipts/{makbuz_id}/issue",
                        headers=headers,
                    ).status_code

            with ThreadPoolExecutor(max_workers=2) as pool:
                statuses = sorted(
                    f.result(timeout=60)
                    for f in (pool.submit(issue_once), pool.submit(issue_once))
                )
            assert statuses == [200, 409], (tur, statuses)

            after = _sira_degeri(cid)
            assert after - once == 1, (tur, once, after)

            got = client.get(
                f"/api/producer-receipts/{makbuz_id}", headers=headers
            )
            assert got.status_code == 200, got.text
            body = got.json()
            assert body["status"] == "issued", body
            assert body["receipt_no"] is not None, body


@pytest.mark.postgresql
def test_eszamanli_issue_ayri_taslaklar_iki_numara(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """İki ayrı taslağa eşzamanlı issue → iki AYRI numara, iki 200."""
    monkeypatch.setenv("DATABASE_URL", _url())

    from app.main import app

    with TestClient(app) as client:
        headers, cid = _admin_headers(client)
        product_id, supplier_id = _hazirlik(
            cid, "Race2 urun", "Race2 ciftci", "33333333333"
        )

        a = _taslak_kur(client, headers, supplier_id, product_id)
        b = _taslak_kur(client, headers, supplier_id, product_id)
        once = _sira_degeri(cid)
        # Seri yoksa tohum delta'yı şişirir; iki taslak için ısıtma şart değil
        # ama once==0 iken delta==2 tohum+2 olabilir. Isıt.
        if once == 0:
            isit = _taslak_kur(client, headers, supplier_id, product_id)
            assert client.post(
                f"/api/producer-receipts/{isit}/issue", headers=headers
            ).status_code == 200
            once = _sira_degeri(cid)
            a = _taslak_kur(client, headers, supplier_id, product_id)
            b = _taslak_kur(client, headers, supplier_id, product_id)

        barrier = Barrier(2)

        def issue_id(rid: int) -> tuple[int, str | None]:
            with TestClient(app) as concurrent:
                barrier.wait(timeout=30)
                cevap = concurrent.post(
                    f"/api/producer-receipts/{rid}/issue",
                    headers=headers,
                )
                no = None
                if cevap.status_code == 200:
                    no = cevap.json()["receipt_no"]
                return cevap.status_code, no

        with ThreadPoolExecutor(max_workers=2) as pool:
            r1 = pool.submit(issue_id, a)
            r2 = pool.submit(issue_id, b)
            s1, n1 = r1.result(timeout=60)
            s2, n2 = r2.result(timeout=60)

        assert sorted([s1, s2]) == [200, 200], (s1, s2, n1, n2)
        assert n1 is not None and n2 is not None and n1 != n2, (n1, n2)
        assert _sira_degeri(cid) - once == 2, (once, _sira_degeri(cid))


@pytest.mark.postgresql
def test_eszamanli_cancel_ayni_makbuz_tek_kazanan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancel da CAS: aynı `issued` makbuza iki cancel → 200 + 409."""
    monkeypatch.setenv("DATABASE_URL", _url())

    from app.main import app

    with TestClient(app) as client:
        headers, cid = _admin_headers(client)
        product_id, supplier_id = _hazirlik(
            cid, "CancelRace", "Cancel ciftci", "44444444444"
        )

        for tur in range(RACE_ROUNDS):
            makbuz_id = _taslak_kur(client, headers, supplier_id, product_id)
            kes = client.post(
                f"/api/producer-receipts/{makbuz_id}/issue", headers=headers
            )
            assert kes.status_code == 200, kes.text
            barrier = Barrier(2)

            def cancel_once() -> int:
                with TestClient(app) as concurrent:
                    barrier.wait(timeout=30)
                    return concurrent.post(
                        f"/api/producer-receipts/{makbuz_id}/cancel",
                        headers=headers,
                    ).status_code

            with ThreadPoolExecutor(max_workers=2) as pool:
                statuses = sorted(
                    f.result(timeout=60)
                    for f in (
                        pool.submit(cancel_once),
                        pool.submit(cancel_once),
                    )
                )
            assert statuses == [200, 409], (tur, statuses)
            got = client.get(
                f"/api/producer-receipts/{makbuz_id}", headers=headers
            ).json()
            assert got["status"] == "cancelled", got
            assert got["receipt_no"] is not None, got
