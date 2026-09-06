"""PostgreSQL ikizi: 1B-C'nin HAREKET–PARTİ BAĞININ gerçek kısıtlarla eşi.

GÖÇ YOK. `stock_movements.lot_id` 0067'den beri var; bu dilim onu
`app/core_schema.py`de BİLDİRDİ ve üç yazıcı yolunu ona bağladı. SQLite ikizi
`tests/test_1b_c_ayarlama_lot.py` DAVRANIŞI ölçüyor (ayarlama, sayım, 409,
kiracı sınırı); bu dosya yalnız ŞEMANIN GERÇEKTEN ISIRAN kısımlarını ve
GELİŞTİRME DİYALEKTİNDE GÖRÜNMEYEN tuzakları ölçer.

--- BU İKİZ NEDEN VAR: DÖRT SEBEP, DÖRDÜ DE YALNIZ ÜRETİMDE GÖRÜNÜR --------

1. **`fk_stock_movements_lot_same_company` GERÇEKTEN ISIRIYOR MU.** Bu
   dilimin EN CİDDİ ölçülmüş riski budur ve SQLite'ta GÖRÜNMEZ: temiz bir
   SQLite şemasında `PRAGMA foreign_keys` **0** döner, yani bir kiracının
   hareketi BAŞKA kiracının partisini işaret edebilir ve orada SESSİZCE
   kabul edilir.

   RİSK BU TURDA GERÇEKTEN DOĞDU: `lot_id` `core_schema`da bildirilince taze
   veritabanında sütunu `20260712_0000`ın `create_all`ı açıyor ve 0067'nin
   TEK koşulu (`if "lot_id" not in ...`) yanlış olup FK'yi HİÇ KURMUYORDU.
   Ölçüldü (taze SQLite, `alembic upgrade head`): bildirimden önce 1 yabancı
   anahtar, tek koşulla 0. Koşul İKİYE AYRILDI. Ama "kısıt duruyor" ile
   "kısıt REDDEDİYOR" aynı cümle değildir ve ikincisi YALNIZ BURADA
   sorulabilir.

2. **`CHECK (quantity >= 0 ...)` GERÇEKTEN ISIRIYOR MU.** `parti_dus`un 409'u
   bir UYGULAMA kapısıdır ve tek başına yeterli DEĞİLDİR: iki eşzamanlı
   düşme ikisi de "yeterli" okur ve ikisi de yazar. Eksiye düşmeyi ayıran
   son şey ŞEMADIR ve SQLite'ta bu `CHECK` dayatılmaz.

3. **`NUMERIC(18,4)` ÖLÇEĞİ.** Sayım yolu partiye FARK yazıyor ve fark
   `products.stock` ile `product_lots.quantity` arasında dolaşıyor. Yanlış
   ölçekli bir yazma SQLite'ta SESSİZCE geçerdi; ölçek ayrışması iki
   defterin sayılarını ayrıştırırdı.

4. **CORE `insert(stock_movements)` GERÇEK ŞEMAYA UYUYOR MU.** Sayım yolunun
   `lot_id` yazabilmesinin TEK sebebi sütunun `core_schema`da bildirilmesi.
   Bildirim ile üretim şemasının AYRIŞMASI (sütun bildirilmiş ama göç onu
   açmamış, ya da tersi) yalnız gerçek şemaya karşı koşarken görülür.

--- ÖLÇÜLEN KÖK SEBEP, İDDİA DEĞİL -----------------------------------------

Testler kısıtın VARLIĞINI değil GERÇEKTEN REDDETTİĞİNİ ölçüyor: her biri
kısıtı ihlal eden bir yazma deneyip `IntegrityError` bekliyor.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

BACKEND = Path(__file__).resolve().parent

FIRMA_ADI = "1B-C İKİZİ firması"
KOMSU_ADI = "1B-C İKİZİ komşu firması"


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("1B-C ikizi APP_TEST_DATABASE_URL ister")
    return url


def _temizle(engine) -> None:
    with engine.begin() as baglanti:
        for deyim in (
            "DELETE FROM stock_movements WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM product_lots WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM warehouse_stocks WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM warehouses WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM products WHERE company_id IN "
            "(SELECT id FROM companies WHERE name IN (:a, :b))",
            "DELETE FROM companies WHERE name IN (:a, :b)",
        ):
            baglanti.execute(text(deyim), {"a": FIRMA_ADI, "b": KOMSU_ADI})


def _acilisa_cek() -> None:
    """Admin şifresini AÇILIŞ DURUMUNA (`admin123` + `must_change_password`) yaz.

    1B-A ikizinden DEVRALINDI ve gerekçesi ölçülmüş bir tuzaktır: PostgreSQL
    ikizleri CI'da AYNI veritabanını paylaşıyor ve her biri girişten sonra
    admin şifresini KENDİ sabitine çekiyor. Tek yönlü bir çare (yalnız
    teardown) dosyayı iyi bir komşu yapar ama KENDİSİNİ korumaz — şifreyi
    bozan ÖNCEKİ dosya olabilir. Tablo `app_users`tır, `users` DEĞİL.
    """
    from app.auth import hash_password
    from app.db import SessionLocal

    with SessionLocal() as db:
        if db.execute(text("SELECT to_regclass('public.app_users')")).scalar() is None:
            return
        db.execute(
            text(
                "UPDATE app_users SET password_hash=:h, "
                "must_change_password=true WHERE username='admin'"
            ),
            {"h": hash_password("admin123")},
        )
        db.commit()


@pytest.fixture()
def motor():
    """Şema + temiz kiracı + AÇILIŞ ŞİFRESİ, İKİ UÇTAN (1B-A'nın kalıbı)."""
    config = Config(str(BACKEND / "alembic.ini"))
    engine = create_engine(_url())
    command.upgrade(config, "head")
    _temizle(engine)
    _acilisa_cek()
    try:
        yield engine
    finally:
        _temizle(engine)
        _acilisa_cek()
        engine.dispose()


def _firma_kur(baglanti, firma_adi: str) -> tuple[int, int, int]:
    """Bir firma + BİR depo + BİR ürün kurar; (company_id, warehouse_id, product_id)."""
    simdi = datetime.now(timezone.utc)
    cid = baglanti.execute(
        text(
            "INSERT INTO companies (name, is_active, created_at) "
            "VALUES (:ad, true, :simdi) RETURNING id"
        ),
        {"ad": firma_adi, "simdi": simdi},
    ).scalar_one()
    depo = baglanti.execute(
        text(
            "INSERT INTO warehouses (company_id, name, is_active, is_default) "
            "VALUES (:cid, '1B-C İkiz Deposu', true, true) RETURNING id"
        ),
        {"cid": cid},
    ).scalar_one()
    urun = baglanti.execute(
        text(
            "INSERT INTO products (company_id, name, unit, sale_price, active) "
            "VALUES (:cid, '1B-C İkiz Ürünü', 'Adet', 10, true) RETURNING id"
        ),
        {"cid": cid},
    ).scalar_one()
    return cid, depo, urun


def _parti_yaz(baglanti, cid, urun, depo, kod="LOT-C", skt="2027-01-31", miktar=5):
    return baglanti.execute(
        text(
            "INSERT INTO product_lots "
            "(company_id, product_id, lot_code, expiry_date, quantity, "
            " warehouse_id, created_at) "
            "VALUES (:cid, :pid, :kod, :skt, :miktar, :wid, :simdi) RETURNING id"
        ),
        {
            "cid": cid, "pid": urun, "kod": kod, "skt": skt, "miktar": miktar,
            "wid": depo, "simdi": datetime.now(timezone.utc),
        },
    ).scalar_one()


def _hareket_yaz(baglanti, cid, urun, depo, lot_id, miktar=1):
    return baglanti.execute(
        text(
            "INSERT INTO stock_movements "
            "(company_id, product_id, movement_type, quantity, movement_date, "
            " reference_type, warehouse_id, lot_id) "
            "VALUES (:cid, :pid, 'add', :miktar, '2026-09-08', 'manual', :wid, "
            " :lot) RETURNING id"
        ),
        {"cid": cid, "pid": urun, "miktar": miktar, "wid": depo, "lot": lot_id},
    ).scalar_one()


# ------------------------------------------------------- şema ısırıyor ---


@pytest.mark.postgresql
def test_TAZE_SEMADA_hareket_parti_KISITI_VAR(motor) -> None:
    """Kısıt gerçek şemada DURUYOR — bu dilimin en ciddi sessiz kayıp riski.

    `stock_movements.lot_id` `core_schema`da bildirilince taze veritabanında
    sütunu `create_all` açıyor ve 0067'nin TEK koşulu yanlış olup FK'yi HİÇ
    kurmuyordu. Koşul ikiye ayrıldı; burası sonucun ÜRETİM DİYALEKTİNDE de
    tuttuğunu soruyor. Kardeşi (`test_TAZE_SEMADA_..._REDDEDIYOR`) kısıtın
    yalnız VAR olmadığını, GERÇEKTEN reddettiğini ölçüyor.
    """
    denetci = inspect(motor)
    sutunlar = {s["name"] for s in denetci.get_columns("stock_movements")}
    assert "lot_id" in sutunlar, sorted(sutunlar)
    kisitlar = {
        k["name"]: (
            tuple(k["constrained_columns"]),
            k["referred_table"],
            tuple(k["referred_columns"]),
        )
        for k in denetci.get_foreign_keys("stock_movements")
    }
    assert "fk_stock_movements_lot_same_company" in kisitlar, sorted(kisitlar)
    assert kisitlar["fk_stock_movements_lot_same_company"] == (
        ("company_id", "lot_id"), "product_lots", ("company_id", "id")
    ), kisitlar["fk_stock_movements_lot_same_company"]


@pytest.mark.postgresql
def test_CAPRAZ_KIRACI_PARTI_referansi_REDDEDILIYOR(motor) -> None:
    """Komşunun partisine bağlı hareket YAZILAMAZ.

    ÇIPLAK bir `lot_id` yabancı anahtarı bunu KABUL EDERDİ — parti gerçekten
    vardır, yalnız BAŞKA firmanındır. Reddi üreten şey anahtarın `company_id`yi
    de adlandırmasıdır (0062'nin kuralı).

    SQLite'TA BU TEST YEŞİL KALIRDI ve yeşilliği kusuru DEĞİL, `PRAGMA
    foreign_keys=0`ı gösterirdi. Kusurun kendisi şudur: A firmasının stok
    hareketi B firmasının partisinden düşmüş görünür ve geri çağırma kaydı
    KOMŞUNUN malını suçlar.
    """
    with motor.begin() as baglanti:
        cid, depo, urun = _firma_kur(baglanti, FIRMA_ADI)
        kcid, kdepo, kurun = _firma_kur(baglanti, KOMSU_ADI)
        komsu_parti = _parti_yaz(baglanti, kcid, kurun, kdepo)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _hareket_yaz(baglanti, cid, urun, depo, komsu_parti)


@pytest.mark.postgresql
def test_VAR_OLMAYAN_PARTI_referansi_REDDEDILIYOR(motor) -> None:
    """Silinmiş/hiç açılmamış bir partiye bağlı hareket YAZILAMAZ.

    Kardeşinden AYRI bir iddia: öteki KİRACI ayrımını, bu VARLIĞI ölçüyor.
    İkisi tek testte birleştirilseydi, kısıt yalnız `company_id` sütununu
    kontrol eder hale gelse (bileşikliği bozulsa) burası yine yeşil kalırdı.
    """
    with motor.begin() as baglanti:
        cid, depo, urun = _firma_kur(baglanti, FIRMA_ADI)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _hareket_yaz(baglanti, cid, urun, depo, 2_000_000_001)


@pytest.mark.postgresql
def test_PARTISIZ_hareket_HALA_YAZILABILIYOR(motor) -> None:
    """`lot_id` NULL iken kısıt DENETLENMEZ — ve denetlenmemeli.

    Bu bir gevşeme değil, 0067'nin kararıdır (`MATCH SIMPLE`): parti
    ÖNCESİNDEN kalan ve bugün de partisiz açılan hareketler NULL taşır ve
    "bu hareket bir partiden çıkmadı" cümlesi DOĞRUDUR. Kısıt NOT NULL
    yapılsaydı geçmiş defter YAZILAMAZ hale gelirdi.

    KAPI YÖNÜ ÖNEMLİ: kardeş testler kısıtın ISIRDIĞINI ölçüyor; bu, ISIRMA
    ALANININ FAZLA GENİŞLEMEDİĞİNİ ölçüyor.
    """
    with motor.begin() as baglanti:
        cid, depo, urun = _firma_kur(baglanti, FIRMA_ADI)
        hareket = _hareket_yaz(baglanti, cid, urun, depo, None)
    assert hareket


@pytest.mark.postgresql
def test_PARTI_EKSIYE_DUSEMEZ_semada_da(motor) -> None:
    """`parti_dus`un 409'u tek başına YETMEZ; son savunma ŞEMADADIR.

    Uygulama kapısı "oku, karşılaştır, yaz" yapıyor ve iki EŞZAMANLI düşme
    ikisi de "yeterli" okuyup ikisi de yazabilir. Aradaki farkı yalnız
    `CHECK (quantity >= 0)` kapatır ve SQLite'ta o CHECK dayatılmaz.
    """
    with motor.begin() as baglanti:
        cid, depo, urun = _firma_kur(baglanti, FIRMA_ADI)
        parti = _parti_yaz(baglanti, cid, urun, depo, miktar=5)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            baglanti.execute(
                text(
                    "UPDATE product_lots SET quantity=quantity-:d "
                    "WHERE company_id=:cid AND id=:id"
                ),
                {"d": 6, "cid": cid, "id": parti},
            )


@pytest.mark.postgresql
def test_PARTI_MIKTARI_OLCEGI_NUMERIC_18_4(motor) -> None:
    """Sayım farkı partiye yazılıyor; ölçek ayrışırsa iki defter ayrışır.

    SQLite ölçeği DAYATMAZ: `0.00005` orada yazıldığı gibi durur ve
    `products.stock` ile `product_lots.quantity` sessizce farklı sayılara
    yuvarlanır. PostgreSQL 4 basamağa çeker ve fark BURADA görünür.
    """
    with motor.begin() as baglanti:
        cid, depo, urun = _firma_kur(baglanti, FIRMA_ADI)
        parti = _parti_yaz(baglanti, cid, urun, depo, miktar="1.00005")
        okunan = baglanti.execute(
            text("SELECT quantity FROM product_lots WHERE company_id=:c AND id=:i"),
            {"c": cid, "i": parti},
        ).scalar_one()
    assert Decimal(str(okunan)) == Decimal("1.0001"), okunan


# ------------------------------------------- Core INSERT gerçek şemaya uyuyor ---


@pytest.mark.postgresql
def test_CORE_insert_lot_id_YAZABILIYOR(motor) -> None:
    """Sayım yolunun tek gereksinimi: Core `insert()` `lot_id`i derleyebilmeli.

    Sütun `core_schema`da BİLDİRİLMEMİŞ olsaydı SQLAlchemy `CompileError`
    verirdi ve sayım yolu partiyi HİÇ yazamazdı — bu dilimin `core_schema`ya
    dokunmasının TEK sebebi budur (`text()` yazan yollar bildirimsiz de
    yazabiliyordu).

    BURADA ÖLÇÜLEN ŞEY BİLDİRİM DEĞİL, BİLDİRİMLE ÜRETİM ŞEMASININ
    ÖRTÜŞMESİDİR: bildirim var ama göç sütunu açmamış olsaydı (ya da tersi)
    SQLite tarafında `create_all` farkı örter, burada `UndefinedColumn` olurdu.
    """
    from sqlalchemy import insert

    from app.core_schema import stock_movements

    with motor.begin() as baglanti:
        cid, depo, urun = _firma_kur(baglanti, FIRMA_ADI)
        parti = _parti_yaz(baglanti, cid, urun, depo)
        hareket = baglanti.execute(
            insert(stock_movements)
            .values(
                product_id=urun,
                movement_type="set",
                quantity=Decimal("-3"),
                movement_date="2026-09-08",
                reference_type="inventory_count",
                company_id=cid,
                warehouse_id=depo,
                lot_id=parti,
            )
            .returning(stock_movements.c.id)
        ).scalar_one()
        okunan = baglanti.execute(
            text("SELECT lot_id FROM stock_movements WHERE company_id=:c AND id=:i"),
            {"c": cid, "i": hareket},
        ).scalar_one()
    assert int(okunan) == int(parti)


# ------------------------------------------------- sayısal mutabakat kapısı ---


@pytest.mark.postgresql
def test_lot_id_SAYISAL_ANLIK_GORUNTUYE_GIRMIYOR(motor) -> None:
    """0067'nin gerekçesi GERÇEK ŞEMADA da geçersiz.

    SQLite ikizi manifestonun İÇİNİ okuyor (`iter_numeric_columns`); burası
    mutabakatın KENDİSİNİ gerçek şemaya karşı çalıştırıp `lot_id`in çıktıya
    HİÇ girmediğini ölçüyor. İkisi ayrı çünkü ayrı şeyler kırılır: biri
    manifestonun içeriği, öteki anlık görüntünün ürettiği yüzey.

    TERSİ SESSİZ DEĞİL AMA YANLIŞ: `lot_id` manifestoya girseydi mutabakat
    bir KİMLİK alanını toplayıp `NUMERIC(18,4)`e yuvarlar ve operatöre
    "parti kimlikleri toplamı" gibi anlamsız bir sayıyı DOĞRULANMIŞ bir
    finansal ölçüm olarak sunardı.
    """
    from app.reconciliation import capture_numeric_snapshot

    anlik = capture_numeric_snapshot(motor)
    sutunlar = [
        (kayit["table"], kayit["column"])
        for kayit in anlik["columns"]
    ]
    assert ("stock_movements", "quantity") in sutunlar, sutunlar[:5]
    assert not [ikili for ikili in sutunlar if ikili[1] == "lot_id"], sutunlar
