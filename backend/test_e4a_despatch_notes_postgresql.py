"""PostgreSQL ikizi: göç `20260913_0083` ve e-İrsaliye defterinin GERÇEK PG 16 turu.

SQLite ikizi (`tests/test_e4a_despatch_notes.py`) göçün SÖZLEŞMESİNİ ve
uçların DAVRANIŞINI ölçüyor. Bu dosya o sözleşmenin GERÇEK diyalekte
İNDİĞİNİ ölçüyor ve **beş şey YALNIZ burada görünür**:

1. **`VARCHAR(n)` PostgreSQL'de GERÇEK BİR KISITTIR.** SQLite uzunluğu
   ZORLAMAZ: `driver_national_id VARCHAR(11)` sütununa 40 karakter
   yazılabilir ve test yeşil yanar. PG'de aynı yazma
   `StringDataRightTruncation` ile düşer. Sınırların KASITLI olduğu
   (`edespatch_gib_status_code` bir KOD taşır, bir hata GÖVDESİ değil;
   `driver_national_id` bir TCKN taşır, serbest metin değil) ancak burada
   kanıtlanabilir.

2. **CHECK KISITLARI GERÇEKTEN REDDEDİYOR.** İki tane var ve
   `ck_despatch_notes_tasima`nın İKİ DALLI yapısı (plaka+şoför **veya**
   kargo firması) yalnız gerçek bir reddedişle kanıtlanır: dört alanı da
   boş bırakan bir satır DÜŞMELİ, her iki dalın da TEK BAŞINA dolu olduğu
   satırlar GEÇMELİ.

3. **`UNIQUE(company_id, invoice_id)` E4a'nın kuralını VERİTABANI
   SEVİYESİNDE tutuyor.** Uçtaki 409 bir uygulama kararıdır; kısıtın
   gerçekten var olduğu ve ikinci satırı reddettiği ancak burada ölçülür —
   ve tam da bu, iki eşzamanlı POST'a karşı TEK gerçek korumadır.

4. **`TIMESTAMPTZ` offset'i KORUYOR.** SQLite `DateTime(timezone=True)`
   sütununu naive geri verir (sürücü offset saklamaz); PG aynı satırı
   offset'li verir. `edespatch._an_coz`un naive değeri UTC sayan dalı tam
   bu farkı kapatmak için var ve iki diyalektin AYNI XML'i ürettiği
   burada doğrulanıyor.

5. **`downgrade()` GERÇEKTEN geri alınabilir.** `DROP TABLE` + yeniden
   `CREATE TABLE`ın kısıtları ve indeksi İLK HÂLİYLE geri getirdiği,
   dosyayı okuyarak söylenemez; turu KOŞMAK gerekir.

TEMİZLİK: bu dosya KENDİ satırlarını siler, tablo SÜPÜRMEZ. Ölçülmüş tuzak
— PG ikizleri paylaşık bir şemada koşabiliyor ve arkada bırakılan satır
KOMŞU dosyayı kırıyor (`product_lots` artıklarıyla bir kez ölçülmüş hata
sınıfı). `despatch_notes` bu riski AYRICA taşıyor çünkü
`UNIQUE(company_id, invoice_id)` KİRACI KAPSAMLIDIR: temizlenmemiş bir
satır, aynı firmayı yeniden kullanan bir sonraki koşuyu reddeder.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DataError, IntegrityError

BACKEND = Path(__file__).resolve().parent

pytestmark = pytest.mark.postgresql

KOSU = uuid4().hex[:8]
GOC = "20260913_0083"
ONCEKI = "20260912_0082"
#: ZİNCİRİN BAŞI — `GOC`tan AYRI bir sabit ve bu ayrım ZORUNLU
#: (`test_e1_efatura_sertlestirme_postgresql.py`de gerekçesi yazılı).
#: Bugün ikisi AYNI değerdedir çünkü 0083 zincirin ucudur; tek sabitle
#: yazılsaydı, başı güncelleyen biri bu dosyanın göç turunu da farkında
#: olmadan BAŞKA bir göçe çevirirdi.
BAS = "20260914_0084"

IRSALIYE = "despatch_notes"

#: SENTETİK — gerçek bir TCKN/plaka DEĞİL, yalnız biçim.
SOFOR_TCKN = "11111111110"
PLAKA = "34ABC123"

#: INSERT ile yazılan sütunların ilan edilmiş uzunlukları. PG'de bunlar
#: GERÇEK kısıttır.
EKLEME_UZUNLUKLARI = {
    "despatch_uuid": 36,
    "despatch_number": 40,
    "carrier_tax_number": 60,
    "driver_national_id": 11,
    "vehicle_plate": 20,
    "trailer_plate": 20,
    "delivery_postal_code": 10,
}

#: SAĞLAYICI SÜTUNLARI — bunlar INSERT'te YOK, yalnız UPDATE ile yazılıyor
#: (`edespatch/submit` ve `edespatch/sync`). AYRI ölçülmelerinin sebebi bir
#: düzen kaygısı değil bir ÖLÇÜM: ilk yazımda ikisi de yukarıdaki sözlükteydi
#: ve test YEŞİL YANIYORDU — çünkü aşırı uzun değer INSERT'in sütun listesinde
#: HİÇ YER ALMADIĞI için veritabanına ULAŞMIYORDU. "Kısıt çalışıyor" diye
#: okunan şey, kısıtın hiç SINANMADIĞIydı. Yazma yolunu taklit etmeyen bir
#: uzunluk testi hiçbir şey ölçmez.
GUNCELLEME_UZUNLUKLARI = {
    "edespatch_gib_status_code": 10,
    "edespatch_provider_uuid": 64,
}


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("E4a ikizi APP_TEST_DATABASE_URL ister")
    return url


def _temizle(engine) -> None:
    """KENDİ satırlarını siler — ÖNEKLE, tabloyu SÜPÜRMEDEN.

    SIRA ÖNEMLİ: irsaliye faturaya, fatura firmaya bağlı. Ters sıradan
    silmek yabancı anahtara çarpar ve temizlik YARIM kalır — yarım bir
    temizlik, komşu dosyayı kıran artığın ta kendisidir.
    """
    with engine.begin() as baglanti:
        if inspect(baglanti).has_table(IRSALIYE):
            baglanti.execute(
                text(
                    f"DELETE FROM {IRSALIYE} WHERE company_id IN"
                    " (SELECT id FROM companies WHERE name LIKE :onek)"
                ),
                {"onek": f"{KOSU}%"},
            )
        baglanti.execute(
            text(
                "DELETE FROM invoices WHERE company_id IN"
                " (SELECT id FROM companies WHERE name LIKE :onek)"
            ),
            {"onek": f"{KOSU}%"},
        )
        baglanti.execute(
            text("DELETE FROM companies WHERE name LIKE :onek"), {"onek": f"{KOSU}%"}
        )
        baglanti.execute(
            text("DELETE FROM app_users WHERE username LIKE :onek"),
            {"onek": f"{KOSU}%"},
        )


@pytest.fixture()
def motor():
    yapilandirma = Config(str(BACKEND / "alembic.ini"))
    engine = create_engine(_url())
    command.upgrade(yapilandirma, "head")
    _temizle(engine)
    try:
        yield engine
    finally:
        _temizle(engine)
        command.upgrade(yapilandirma, "head")
        engine.dispose()


_SAYAC = {"n": 0}


def _firma_ve_fatura(motor, ad: str, firma: int | None = None) -> tuple[int, int]:
    """Bir firma + bir kullanıcı + bir fatura (``firma`` verilirse VAR OLAN firma).

    `invoices.created_by` NOT NULL — ölçüldü. Satırı EKSİK kurup
    `IntegrityError` almak, uzunluk/CHECK testlerini YANLIŞ SEBEPTEN yeşil
    yapardı: `NotNullViolation` da bir istisnadır ama ölçmek istediğimiz şey
    DEĞİLDİR.
    """
    _SAYAC["n"] += 1
    sira = _SAYAC["n"]
    an = datetime.now(timezone.utc)
    with motor.begin() as baglanti:
        if firma is None:
            firma = baglanti.execute(
                text(
                    "INSERT INTO companies(name,is_active,created_at)"
                    " VALUES(:a,true,:t) RETURNING id"
                ),
                {"a": ad, "t": an},
            ).scalar_one()
        kullanici = baglanti.execute(
            text(
                "INSERT INTO app_users(username,email,email_verified,display_name,"
                "password_hash,role,is_active,must_change_password,created_at)"
                " VALUES(:k,:e,true,:d,'x','admin',true,false,:t) RETURNING id"
            ),
            {
                "k": f"{KOSU}_kullanici_{sira}",
                "e": f"{KOSU}_{sira}@ornek.test",
                "d": KOSU,
                "t": an,
            },
        ).scalar_one()
        fatura = baglanti.execute(
            text(
                "INSERT INTO invoices(company_id,invoice_number,invoice_type,status,"
                "currency,exchange_rate,customer_snapshot,machine_snapshot,"
                "work_order_snapshot,company_snapshot,technician_snapshot,"
                "warranty_snapshot,totals_snapshot,tax_snapshot,created_by,"
                "created_at,updated_at)"
                " VALUES(:c,:n,'INVOICE','ISSUED','TRY',1,'{}','{}','{}','{}','{}',"
                "'{}','{}','{}',:u,:t,:t) RETURNING id"
            ),
            {"c": firma, "n": f"{KOSU}-FTR-{sira}", "u": kullanici, "t": an},
        ).scalar_one()
    return int(firma), int(fatura)


def _irsaliye_degerleri(firma: int, fatura: int, **degisiklikler) -> dict:
    degerler = {
        "cid": firma,
        "invoice_id": fatura,
        "despatch_uuid": str(uuid4()),
        "despatch_number": f"IRS{2026}{_SAYAC['n']:09d}",
        "issue_date": date(2026, 9, 13),
        "actual_shipment_at": datetime(2026, 9, 13, 8, 30, tzinfo=timezone.utc),
        "carrier_name": None,
        "carrier_tax_number": None,
        "driver_name": "Ahmet Yilmaz",
        "driver_national_id": SOFOR_TCKN,
        "vehicle_plate": PLAKA,
        "trailer_plate": None,
        "delivery_address": "Depo Yolu 7",
        "delivery_postal_code": "34000",
        "status": "NONE",
        "now": datetime.now(timezone.utc),
    }
    degerler.update(degisiklikler)
    return degerler


_EKLE = text(
    f"INSERT INTO {IRSALIYE}(company_id,invoice_id,despatch_uuid,despatch_number,"
    "issue_date,actual_shipment_at,carrier_name,carrier_tax_number,driver_name,"
    "driver_national_id,vehicle_plate,trailer_plate,delivery_address,"
    "delivery_postal_code,edespatch_status,created_at,updated_at) "
    "VALUES(:cid,:invoice_id,:despatch_uuid,:despatch_number,:issue_date,"
    ":actual_shipment_at,:carrier_name,:carrier_tax_number,:driver_name,"
    ":driver_national_id,:vehicle_plate,:trailer_plate,:delivery_address,"
    ":delivery_postal_code,:status,:now,:now) RETURNING id"
)


# ==========================================================================
# 1. ŞEMA GERÇEKTEN İNDİ
# ==========================================================================

def test_SEMA_BASI_gercekten_0083(motor) -> None:
    with motor.connect() as baglanti:
        surum = baglanti.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    assert surum == BAS, surum


def test_TABLO_ve_KISITLAR_PGde_var(motor) -> None:
    """24 sütun, 3 UNIQUE, 2 CHECK, biri BİLEŞİK 3 FK, 1 indeks."""
    d = inspect(motor)
    assert d.has_table(IRSALIYE)
    sutunlar = {c["name"] for c in d.get_columns(IRSALIYE)}
    assert len(sutunlar) == 24, sorted(sutunlar)

    assert {u["name"] for u in d.get_unique_constraints(IRSALIYE)} == {
        "uq_despatch_notes_company_id",
        "uq_despatch_notes_company_invoice",
        "uq_despatch_notes_uuid",
    }
    assert {c["name"] for c in d.get_check_constraints(IRSALIYE)} == {
        "ck_despatch_notes_tasima",
        "ck_despatch_notes_durum",
    }
    # BİLEŞİK yabancı anahtar (0062'nin kuralı): teslim müşterisi AYNI
    # firmanın müşterisi olmak ZORUNDA ve bu veritabanı seviyesinde.
    bilesik = [
        f
        for f in d.get_foreign_keys(IRSALIYE)
        if f["name"] == "fk_despatch_notes_delivery_customer"
    ]
    assert len(bilesik) == 1
    assert bilesik[0]["constrained_columns"] == ["company_id", "delivery_customer_id"]
    assert bilesik[0]["referred_table"] == "customers"
    assert "ix_despatch_notes_company_issue" in {
        i["name"] for i in d.get_indexes(IRSALIYE)
    }


def test_ZAMAN_sutunlari_TIMESTAMPTZ(motor) -> None:
    """Beş zaman sütununun BEŞİ de tz-farkındalı olmalı.

    `actual_shipment_at` naive olsaydı UBL'in `ActualDespatchTime`ı sunucu
    saat dilimine göre kayardı ve fiili sevk saati YANLIŞ beyan edilirdi.
    """
    d = inspect(motor)
    tzli = {
        c["name"]
        for c in d.get_columns(IRSALIYE)
        if getattr(c["type"], "timezone", False)
    }
    assert tzli == {
        "actual_shipment_at",
        "created_at",
        "updated_at",
        "edespatch_submitted_at",
        "edespatch_synced_at",
    }, tzli


# ==========================================================================
# 2. KISITLAR GERÇEKTEN REDDEDİYOR — PG'de, SQLite'ta değil
# ==========================================================================

def test_UZUNLUK_kisitlari_PGde_GERCEK(motor) -> None:
    """SQLite'ta sessizce geçen aşırı uzun değer PG'de DÜŞER.

    MUTASYON: göçte `driver_national_id`i `String(11)`den `Text`e çevirmek
    bunu KIRMIZI yapar — ve davranışta bir TCKN sütununa serbest metin
    yazılabilirdi.

    `edespatch_status` bu listede YOK: sınırı CHECK ile ZATEN daha dar,
    yani uzun bir değer uzunluk kısıtına DEĞİL CHECK'e çarpar ve test
    doğru şeyi ölçmezdi. Onun kendi kapısı bir altta.
    """
    firma, fatura = _firma_ve_fatura(motor, f"{KOSU} Uzunluk")
    for sutun, uzunluk in EKLEME_UZUNLUKLARI.items():
        degerler = _irsaliye_degerleri(firma, fatura, **{sutun: "x" * (uzunluk + 1)})
        with pytest.raises(DataError):
            with motor.begin() as baglanti:
                baglanti.execute(_EKLE, degerler)

    # SAĞLAYICI SÜTUNLARI: GERÇEK yazma yolu UPDATE'tir (bkz. sabitin
    # üstündeki not). Önce geçerli bir satır, sonra o satıra taşan bir
    # UPDATE.
    with motor.begin() as baglanti:
        satir_id = baglanti.execute(
            _EKLE, _irsaliye_degerleri(firma, fatura)
        ).scalar_one()
    for sutun, uzunluk in GUNCELLEME_UZUNLUKLARI.items():
        with pytest.raises(DataError):
            with motor.begin() as baglanti:
                baglanti.execute(
                    text(
                        f"UPDATE {IRSALIYE} SET {sutun}=:v"
                        " WHERE id=:i AND company_id=:c"
                    ),
                    {"v": "x" * (uzunluk + 1), "i": satir_id, "c": firma},
                )


def test_DURUM_CHECKi_kapali_kume(motor) -> None:
    """`edespatch_status` kapalı kümededir; sekiz değerin dışı REDDEDİLİR.

    Kapı yalnız REDDETMEYİ değil KABUL ETMEYİ de ölçüyor: yalnız reddi
    ölçseydi, CHECK'i `status = 'NONE'`a daraltan bir mutasyon YEŞİL
    kalırdı ve uygulamanın yazdığı `QUEUED` çalışma zamanında patlardı.
    """
    from app.einvoice import edespatch

    firma, fatura = _firma_ve_fatura(motor, f"{KOSU} Durum")
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            baglanti.execute(_EKLE, _irsaliye_degerleri(firma, fatura, status="MAVI"))

    for durum in sorted(edespatch.BILINEN):
        _, taze_fatura = _firma_ve_fatura(motor, f"{KOSU} Durum {durum}")
        with motor.begin() as baglanti:
            baglanti.execute(
                _EKLE, _irsaliye_degerleri(firma, taze_fatura, status=durum)
            )


def test_TASIMA_CHECKi_iki_dali_da_kabul_bosu_RED(motor) -> None:
    """Plaka+şoför VEYA kargo firması. İkisi de yoksa satır DÜŞER.

    GİB kılavuzunun iki dallı kuralı (keşif §3.2) burada ŞEMADA. Dördünü
    birden NOT NULL yapmak kılavuzun İZİN VERDİĞİ bir sevki imkânsız
    kılardı; dördünü serbest bırakmak taşıma bilgisi TAŞIMAYAN bir
    irsaliye üretirdi.

    MUTASYON: `ck_despatch_notes_tasima`yı göçten düşürmek bunu KIRMIZI
    yapar.
    """
    firma, fatura = _firma_ve_fatura(motor, f"{KOSU} Tasima A")

    # (a) ŞOFÖR+PLAKA dalı — GEÇER.
    with motor.begin() as baglanti:
        baglanti.execute(_EKLE, _irsaliye_degerleri(firma, fatura))

    # (b) KARGO dalı — TEK BAŞINA da GEÇER (şoför/plaka BOŞ).
    _, fatura_b = _firma_ve_fatura(motor, f"{KOSU} Tasima B")
    with motor.begin() as baglanti:
        baglanti.execute(
            _EKLE,
            _irsaliye_degerleri(
                firma,
                fatura_b,
                driver_name=None,
                driver_national_id=None,
                vehicle_plate=None,
                carrier_name="Hizli Kargo A.S.",
                carrier_tax_number="5555555550",
            ),
        )

    # (c) HİÇBİR dal dolu değil — DÜŞER.
    _, fatura_c = _firma_ve_fatura(motor, f"{KOSU} Tasima C")
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            baglanti.execute(
                _EKLE,
                _irsaliye_degerleri(
                    firma,
                    fatura_c,
                    driver_name=None,
                    driver_national_id=None,
                    vehicle_plate=None,
                ),
            )

    # (d) YARIM bir dal da DÜŞER: şoför var, plaka yok.
    _, fatura_d = _firma_ve_fatura(motor, f"{KOSU} Tasima D")
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            baglanti.execute(
                _EKLE, _irsaliye_degerleri(firma, fatura_d, vehicle_plate=None)
            )


def test_BIR_FATURA_BIR_IRSALIYE_veritabaninda(motor) -> None:
    """`UNIQUE(company_id, invoice_id)` — uçtaki 409'un ARKASINDAKİ kısıt.

    Uygulama katmanındaki bir ön sorgu iki EŞZAMANLI POST'u İKİSİNİ DE
    geçirirdi. Bu kısıt ikinciyi reddeder ve E4a'nın "bir fatura bir
    irsaliye" kuralının TEK gerçek koruyucusudur.

    MUTASYON: göçten bu kısıtı düşürmek bunu KIRMIZI yapar.
    """
    firma, fatura = _firma_ve_fatura(motor, f"{KOSU} Tekil")
    with motor.begin() as baglanti:
        baglanti.execute(_EKLE, _irsaliye_degerleri(firma, fatura))
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            baglanti.execute(_EKLE, _irsaliye_degerleri(firma, fatura))


def test_ETTN_KIRACI_KAPSAMLI_TEKIL(motor) -> None:
    """`despatch_uuid` UNIQUE'i `(company_id, despatch_uuid)` — İKİ YÖN ÖLÇÜLÜR.

    Aynı firmada aynı ETTN REDDEDİLİR (çift belge panzehiri yerinde);
    BAŞKA firmada aynı ETTN KABUL EDİLİR. İkinci dal 5.1c'nin "yeni firma"
    geri yüklemesinin ta kendisidir: kaynak satır dururken kopya ikinci
    firmaya yazılır. Gerekçe göçün başlığında (Şef kararı, 2026-09-10).
    MUTASYON: kısıtı tek sütuna geri çevirmek ikinci dalı kırar.
    """
    firma_a, fatura_a = _firma_ve_fatura(motor, f"{KOSU} ETTN A")
    firma_b, fatura_b = _firma_ve_fatura(motor, f"{KOSU} ETTN B")
    ortak = str(uuid4())
    with motor.begin() as baglanti:
        baglanti.execute(
            _EKLE, _irsaliye_degerleri(firma_a, fatura_a, despatch_uuid=ortak)
        )
    # Başka firma, aynı ETTN: geçer (geri yükleme kopyası).
    with motor.begin() as baglanti:
        baglanti.execute(
            _EKLE, _irsaliye_degerleri(firma_b, fatura_b, despatch_uuid=ortak)
        )
    # Aynı firma, aynı ETTN, başka fatura: düşer.
    _, fatura_a2 = _firma_ve_fatura(motor, f"{KOSU} ETTN A2", firma=firma_a)
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            baglanti.execute(
                _EKLE, _irsaliye_degerleri(firma_a, fatura_a2, despatch_uuid=ortak)
            )


# ==========================================================================
# 3. GİDİŞ-DÖNÜŞ: PG'den okunan satır AYNI UBL'i üretiyor
# ==========================================================================

def test_GIDIS_DONUS_PGden_okunan_satir_UBL_uretiyor(motor) -> None:
    """PG'ye yazılan satır geri okunup UBL'e çevrilebiliyor.

    ASIL ÖLÇÜLEN, `TIMESTAMPTZ` FARKI. PG `actual_shipment_at`i OFFSET'Lİ
    verir, SQLite naive; `edespatch._an_coz`un naive dalı tam bu farkı
    kapatmak için var. İki diyalektin AYNI `ActualDespatchTime`ı üretmesi
    ancak burada — gerçek PG'den okunan bir satırla — kanıtlanabilir.
    """
    from xml.etree import ElementTree

    from app.einvoice.edespatch import build_despatch_xml

    firma, fatura = _firma_ve_fatura(motor, f"{KOSU} Gidis Donus")
    degerler = _irsaliye_degerleri(firma, fatura)
    with motor.begin() as baglanti:
        yeni_id = baglanti.execute(_EKLE, degerler).scalar_one()

    with motor.connect() as baglanti:
        satir = dict(
            baglanti.execute(
                text(f"SELECT * FROM {IRSALIYE} WHERE id=:i AND company_id=:c"),
                {"i": yeni_id, "c": firma},
            )
            .mappings()
            .one()
        )
    # PG GERÇEKTEN offset'li veriyor — bu kapının dayandığı olgu.
    assert satir["actual_shipment_at"].tzinfo is not None

    xml = build_despatch_xml(
        {
            "despatch_number": satir["despatch_number"],
            "uuid": satir["despatch_uuid"],
            "issue_date": satir["issue_date"],
            "invoice_number": f"{KOSU}-FTR",
            "supplier": {"vkn": "1234567890", "name": "Sungur", "address": "A"},
            "customer": {"vkn_tckn": "9876543210", "name": "Alici", "address": "B"},
            "shipment": {
                "actual_shipment_at": satir["actual_shipment_at"],
                "vehicle_plate": satir["vehicle_plate"],
                "driver_name": satir["driver_name"],
                "driver_national_id": satir["driver_national_id"],
                "delivery_address": satir["delivery_address"],
                "delivery_postal_code": satir["delivery_postal_code"],
            },
            "lines": [{"id": 1, "name": "Bugday", "quantity": "2.5000"}],
        }
    )
    cbc = "{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}"
    cac = "{urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2}"
    kok = ElementTree.fromstring(xml.decode("utf-8"))
    assert kok.findtext(f"{cbc}UUID") == degerler["despatch_uuid"]
    assert kok.findtext(f"{cbc}IssueDate") == "2026-09-13"
    teslim = kok.find(f"{cac}Shipment/{cac}Delivery/{cac}Despatch")
    assert teslim.findtext(f"{cbc}ActualDespatchDate") == "2026-09-13"
    # BU SATIR BİR KUSUR BULDU ve kusur KODDA DÜZELTİLDİ, testte değil.
    # Yazılan an `08:30+00:00`; PG onu oturum saat diliminde
    # (`Europe/Istanbul`) `11:30+03:00` olarak geri veriyor. `_an_coz`
    # eskiden gelen değeri OLDUĞU GİBİ bırakıyordu, yani `strftime` o
    # offset'in yerel duvar saatini basıyor ve AYNI SATIR SQLite'ta
    # `08:30:00`, PG'de `11:30:00` üretiyordu — belgenin İÇERİĞİ
    # veritabanı ayarına bağlıydı. Düzeltme: `_an_coz` artık her zaman
    # `BELGE_UTC_OFFSET`e (sabit +03:00) çeviriyor.
    #
    # `11:30:00` HEM DOĞRU HEM DETERMİNİSTİK: UBL-TR `ActualDespatchTime`
    # bir YEREL saattir ve 08:30 UTC, Türkiye'de 11:30'dur. SQLite ikizi
    # (`test_SAAT_cevirimi_SABIT_ve_girdi_OFFSETINDEN_BAGIMSIZ`) AYNI
    # değeri bekliyor; iki diyalektin eşitliği ancak ikisi birlikte
    # ölçüldüğünde iddia edilebilir.
    assert teslim.findtext(f"{cbc}ActualDespatchTime") == "11:30:00"
    assert (
        kok.findtext(
            f"{cac}Shipment/{cac}ShipmentStage/{cac}DriverPerson/{cbc}NationalityID"
        )
        == SOFOR_TCKN
    )


# ==========================================================================
# 4. GERİ ALINABİLİR — GERÇEK PG'de up -> down -> up
# ==========================================================================

def test_GOC_TURU_up_down_up(motor) -> None:
    """`downgrade` tabloyu düşürür, tekrar `upgrade` onu İLK HÂLİYLE kurar.

    HEDEF AÇIK YAZILDI, "-1" DEĞİL. "-1" GÖREL bir adımdır ve zincirin
    UCUNU indirir; üstüne bir göç bindiği anda bu kapı BAŞKA bir göçü
    ölçmeye başlar ve "geri alma çalışmıyor" diye kırmızı olur (0072'de
    tam olarak bu oldu).
    """
    yapilandirma = Config(str(BACKEND / "alembic.ini"))

    def anlik() -> dict:
        motor_yerel = create_engine(_url())
        try:
            d = inspect(motor_yerel)
            if not d.has_table(IRSALIYE):
                return {}
            return {
                "sutun": {c["name"] for c in d.get_columns(IRSALIYE)},
                "unique": {u["name"] for u in d.get_unique_constraints(IRSALIYE)},
                "check": {c["name"] for c in d.get_check_constraints(IRSALIYE)},
                "indeks": {i["name"] for i in d.get_indexes(IRSALIYE)},
            }
        finally:
            motor_yerel.dispose()

    once = anlik()
    assert once, "göç uygulanmamış; tur ölçülemez"

    command.downgrade(yapilandirma, ONCEKI)
    assert anlik() == {}, "downgrade tabloyu düşürmedi"
    # KOMŞU GÖÇÜN GETİRDİĞİ YERİNDE: bu göç YALNIZ kendi getirdiğini
    # götürür. 0081'in üç sütunu `invoices`ta DURUYOR.
    kontrol = create_engine(_url())
    try:
        assert {
            "einvoice_web_key",
            "einvoice_gib_status_code",
            "einvoice_pk_alias",
        } <= {c["name"] for c in inspect(kontrol).get_columns("invoices")}
    finally:
        kontrol.dispose()

    command.upgrade(yapilandirma, "head")
    assert anlik() == once, "tur bilgi kaybetti"
