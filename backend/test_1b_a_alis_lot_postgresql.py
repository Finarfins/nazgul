"""PostgreSQL ikizi: 1B-A parti/depo ŞEMASININ gerçek kısıtlarla eşi.

Göç `20260908_0073`. SQLite ikizi `tests/test_1b_a_alis_lot.py` DAVRANIŞI
ölçüyor (parti açma, ekleme, ayırma, SKT çatışması, geri alma, 409); bu
dosya yalnız ŞEMANIN GERÇEKTEN ISIRAN kısımlarını ve GELİŞTİRME
DİYALEKTİNDE GÖRÜNMEYEN tuzakları ölçer.

--- BU İKİZ NEDEN VAR ------------------------------------------------------

1. **BİLEŞİK YABANCI ANAHTARLARIN GERÇEKTEN ISIRMASI.** SQLite'ta yabancı
   anahtar uygulaması varsayılan olarak KAPALIDIR (`db.py` PRAGMA'yı
   uygulama bağlantısında açıyor, ama şema seviyesindeki iddia orada
   sınanamaz). `fk_product_lots_warehouse_same_company` orada YEŞİL kalırdı:
   bir kiracının parti satırı BAŞKA kiracının deposunu işaret edebilirdi ve
   o parti, komşunun rafında görünürdü.

2. **`UNIQUE(company_id, product_id, lot_code, warehouse_id)`.** Aynı dörtlü
   için İKİ satır miktarı ikiye böler ve "hangisi gerçek" sorusu SORULAMAZ
   hale gelir. Uygulama kontrolü (`_parti_ac`in SELECT-sonra-INSERT'ü) İKİ
   EŞZAMANLI isteği ayırt EDEMEZ; ayıran YALNIZ şemadır.

3. **ESKİ TEKİLİN GERÇEKTEN DÜŞTÜĞÜ.** 0067'nin
   `uq_product_lots_company_product_code`u (depo TAŞIMAYAN üçlü) hâlâ
   duruyorsa, aynı parti kodunun İKİNCİ BİR DEPODA açılması REDDEDİLİR — bu
   PR'ın getirdiği davranışın TAM TERSİ. 0072'de ÖLÇÜLDÜ ki `batch`
   PostgreSQL'de kısıt DDL'ini SESSİZCE atlayabiliyor; yani "düşürdüm"
   demek YETMEZ, düşmüş OLDUĞU sorulmalıdır.

4. **`warehouse_id` GERÇEKTEN NOT NULL.** SQLite `NUMERIC`/`NOT NULL`
   dayatmasında gevşektir ve tablo yeniden kurulduğu için iddia orada
   başka bir şey ölçerdi.

5. **BOŞ TABLO ÖLÇÜMÜNÜN GERÇEK ŞEMADA TUTMASI.** Göç `upgrade`de de
   `downgrade`de de `SELECT count(*)` yapıyor ve satır varsa gürültülü
   ölüyor. Tur burada GERÇEK PostgreSQL üzerinde kapanıyor.

--- ÖLÇÜLEN KÖK SEBEP, İDDİA DEĞİL -----------------------------------------

Testler kısıtın VARLIĞINI değil GERÇEKTEN REDDETTİĞİNİ ölçüyor: her biri
kısıtı ihlal eden bir yazma deneyip `IntegrityError` bekliyor.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

BACKEND = Path(__file__).resolve().parent

FIRMA_ADI = "1B-A İKİZİ firması"
KOMSU_ADI = "1B-A İKİZİ komşu firması"


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("1B-A ikizi APP_TEST_DATABASE_URL ister")
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

    Tablo yoksa hiçbir şey yapmaz: taze şemada uygulama henüz açılış yapmamış
    olabilir ve orada zaten `admin123` doğacaktır.
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
    """Şema + temiz kiracı + AÇILIŞ ŞİFRESİ, İKİ UÇTAN.

    ŞİFRE YARISI D2'den (`test_d2_avans_tescil_postgresql.py::acilis_sifresi`)
    DEVRALINDI ve BU DOSYA HENÜZ GİRİŞ YAPMASA DA duruyor. Gerekçe ölçülmüş
    bir tuzaktır, üslup değil: PostgreSQL ikizleri CI'da AYNI veritabanını
    paylaşıyor ve her biri girişten sonra admin şifresini KENDİ sabitine
    çeviriyor; tek yönlü bir çare (yalnız teardown) dosyayı iyi bir komşu
    yapar ama KENDİSİNİ korumaz, çünkü şifreyi bozan ÖNCEKİ dosya olabilir.

    İki uçtan çalışması ayrıca bu dosyanın GELECEĞİNİ de kapsıyor: bu ikiz
    bir gün bir HTTP adımı kazanırsa (dilim B'nin tüketim yolu bu şemayı
    kullanacak) giriş SIRAYA BAĞLI olarak 401 almaz. Bugün ölçülebilir
    faydası şudur: dosya veritabanını BULDUĞU GİBİ bırakıyor — aşağıdaki
    `downgrade`/`upgrade` turu şemayı zaten oynatıyor ve o turdan sonra
    açılış durumunun yerinde olduğu GARANTİ.

    Şifre uçtan değil SATIRDAN yazılıyor: `change-password` mevcut şifreyi
    ister (bilmiyoruz) ve açılış durumunun ayırt edici yarısı
    (`must_change_password`) uçtan yazılamaz. Tablo `app_users`tır,
    `users` DEĞİL.
    """
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
            "VALUES (:cid, 'İkiz Deposu', true, true) RETURNING id"
        ),
        {"cid": cid},
    ).scalar_one()
    urun = baglanti.execute(
        text(
            "INSERT INTO products (company_id, name, unit, sale_price, active) "
            "VALUES (:cid, 'İkiz Ürünü', 'Adet', 10, true) RETURNING id"
        ),
        {"cid": cid},
    ).scalar_one()
    return cid, depo, urun


def _parti_yaz(baglanti, cid, urun, depo, kod="LOT-A", skt="2027-01-31", miktar=5):
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


# ------------------------------------------------------- şema ısırıyor ---

@pytest.mark.postgresql
def test_CAPRAZ_KIRACI_DEPO_referansi_REDDEDILIYOR(motor) -> None:
    """Komşunun deposuna bağlı parti satırı YAZILAMAZ.

    ÇIPLAK bir `warehouse_id` yabancı anahtarı bunu KABUL EDERDİ — depo
    gerçekten vardır, yalnız BAŞKA firmanındır. Reddi üreten şey, anahtarın
    `company_id`yi de adlandırmasıdır (0062'nin kuralı, 0067'nin `products`
    için yaptığının aynısı).
    """
    with motor.begin() as baglanti:
        cid, _depo, urun = _firma_kur(baglanti, FIRMA_ADI)
        _kcid, komsu_depo, _kurun = _firma_kur(baglanti, KOMSU_ADI)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _parti_yaz(baglanti, cid, urun, komsu_depo)


@pytest.mark.postgresql
def test_CAPRAZ_KIRACI_URUN_referansi_HALA_REDDEDILIYOR(motor) -> None:
    """0067'nin ürün anahtarı 0073'ün tablo yeniden yazımından SAĞ ÇIKTI.

    Bu kapı yeni bir iddia DEĞİL, bir REGRESYON kapısıdır: 0073 tekil kısıtı
    değiştirdi ve SQLite tarafında tabloyu YENİDEN KURDU. Yeniden kurulan bir
    tablodan bir yabancı anahtarın sessizce düşmesi ÖLÇÜLMÜŞ bir kusur
    sınıfıdır (0071/0072). Burada PostgreSQL'de de duruyor olduğu soruluyor.
    """
    with motor.begin() as baglanti:
        cid, depo, _urun = _firma_kur(baglanti, FIRMA_ADI)
        _kcid, _kdepo, komsu_urun = _firma_kur(baglanti, KOMSU_ADI)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _parti_yaz(baglanti, cid, komsu_urun, depo)


@pytest.mark.postgresql
def test_AYNI_KOD_AYNI_DEPO_IKI_SATIR_REDDEDILIYOR(motor) -> None:
    """Tekilliği ŞEMA kuruyor, `_parti_ac`in SELECT-sonra-INSERT'ü DEĞİL.

    Uygulama kontrolü iki EŞZAMANLI isteği ayırt edemez: ikisi de "yok"
    okuyup ikisi de yazardı ve miktar İKİYE BÖLÜNÜRDÜ. Hangi satırın gerçek
    olduğu o noktada sorulamaz.
    """
    with motor.begin() as baglanti:
        cid, depo, urun = _firma_kur(baglanti, FIRMA_ADI)
        _parti_yaz(baglanti, cid, urun, depo)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _parti_yaz(baglanti, cid, urun, depo)


@pytest.mark.postgresql
def test_AYNI_KOD_BASKA_DEPO_KABUL_EDILIYOR_eski_tekil_DUSTU(motor) -> None:
    """0067'nin (company, product, code) tekili GERÇEKTEN düşmüş olmalı.

    Bu, yukarıdaki kapının TERSİ ve ikisi BİRLİKTE gerekli: yalnız reddi
    ölçmek, kısıt fazla GENİŞ kalsa da (yani eski üçlü hâlâ duruyorsa)
    yeşil kalırdı — ve o durumda üretici bir partiyi iki şubeye
    BÖLEMEZDİ. 0072'de ölçüldü ki `batch` PostgreSQL'de kısıt DDL'ini
    SESSİZCE atlayabiliyor; "düşürdüm" demek YETMEZ.
    """
    with motor.begin() as baglanti:
        cid, depo, urun = _firma_kur(baglanti, FIRMA_ADI)
        ikinci_depo = baglanti.execute(
            text(
                "INSERT INTO warehouses (company_id, name, is_active, is_default) "
                "VALUES (:cid, 'İkiz İkinci Depo', true, false) RETURNING id"
            ),
            {"cid": cid},
        ).scalar_one()
        _parti_yaz(baglanti, cid, urun, depo)
        _parti_yaz(baglanti, cid, urun, ikinci_depo)

    with motor.connect() as baglanti:
        adet = baglanti.execute(
            text(
                "SELECT count(*) FROM product_lots "
                "WHERE company_id=:cid AND product_id=:pid AND lot_code='LOT-A'"
            ),
            {"cid": cid, "pid": urun},
        ).scalar_one()
    assert adet == 2, adet

    # Eski tekilin ADI da GİTMİŞ olmalı — kalıntı bir kısıt bir gün yeniden
    # dayatılabilir hale gelirdi.
    with motor.connect() as baglanti:
        adlar = {
            satir[0]
            for satir in baglanti.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'product_lots'::regclass AND contype='u'"
                )
            )
        }
    assert "uq_product_lots_company_product_code" not in adlar, adlar
    assert "uq_product_lots_company_product_code_warehouse" in adlar, adlar


@pytest.mark.postgresql
def test_DEPOSUZ_PARTI_YAZILAMIYOR(motor) -> None:
    """`warehouse_id` NOT NULL: depo BİLİNMEYEN bir parti satırı yoktur.

    Göçün başlığındaki kararın şema tarafı: hangi depoda durduğu bilinmeyen
    bir parti, "hangi depoda ne kadar var" sorusunu cevaplayamaz ve o soru
    bu tablonun VARLIK SEBEBİDİR.
    """
    with motor.begin() as baglanti:
        cid, _depo, urun = _firma_kur(baglanti, FIRMA_ADI)

    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            baglanti.execute(
                text(
                    "INSERT INTO product_lots "
                    "(company_id, product_id, lot_code, quantity, created_at) "
                    "VALUES (:cid, :pid, 'LOT-X', 1, :simdi)"
                ),
                {"cid": cid, "pid": urun, "simdi": datetime.now(timezone.utc)},
            )


@pytest.mark.postgresql
def test_DEPO_TEKILI_KURULDU_bilesik_anahtarin_HEDEFI(motor) -> None:
    """`warehouses` üzerinde `UNIQUE(company_id, id)` GERÇEKTEN var.

    ÖLÇÜLDÜ: bu tekil 0073'ten ÖNCE HİÇBİR YERDE yoktu — ne
    `app/inventory.py`nin `Table` bildiriminde ne bir göçte. Hedefi tekil
    olmayan bir bileşik yabancı anahtar PostgreSQL'de KURULAMAZ, yani bu
    satır olmasaydı göç `upgrade` sırasında düşerdi. Kapı, birinin onu
    "gereksiz" diye kaldırmasını sessiz bırakmıyor.
    """
    with motor.connect() as baglanti:
        adlar = {
            satir[0]
            for satir in baglanti.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'warehouses'::regclass AND contype='u'"
                )
            )
        }
    assert "uq_warehouses_company_id" in adlar, adlar


@pytest.mark.postgresql
def test_ALIS_KALEMI_PARTI_SUTUNLARI_NULL_KABUL_EDIYOR(motor) -> None:
    """Geriye doldurma YOK: her eski kalem `NULL` taşır ve bu DOĞRUDUR.

    Sütunlar NOT NULL olsaydı göç, hangi partiden geldiği BİLİNMEYEN geçmiş
    kalemlere boş dizgi uydurmak zorunda kalırdı — "parti kodu boş bir parti
    var" cümlesi, "parti taşımıyor" cümlesinden BAŞKA bir şeydir.
    """
    with motor.connect() as baglanti:
        sutunlar = {
            satir[0]: (satir[1], satir[2])
            for satir in baglanti.execute(
                text(
                    "SELECT column_name, is_nullable, data_type "
                    "FROM information_schema.columns "
                    "WHERE table_name='purchase_items' "
                    "AND column_name IN ('lot_code','expiry_date')"
                )
            )
        }
    assert sutunlar["lot_code"] == ("YES", "character varying"), sutunlar
    assert sutunlar["expiry_date"] == ("YES", "date"), sutunlar


@pytest.mark.postgresql
def test_GOC_TURU_BOS_TABLO_OLCUMUYLE_kapaniyor(motor) -> None:
    """`downgrade` -> `upgrade` turu GERÇEK PostgreSQL'de kapanıyor.

    ÖNCE BOŞ OLMAYAN halde `downgrade` DENENİYOR ve GÜRÜLTÜLÜ ÖLMESİ
    bekleniyor — ölçümün kendisi burada sınanıyor. Sonra defter boşaltılıp
    tur kapatılıyor.

    `RuntimeError` yakalanmıyor, `Exception` yakalanıyor: alembic göç
    gövdesindeki istisnayı kendi bağlam yöneticisinden geçiriyor ve
    sarmalayabiliyor; dar bir `pytest.raises(RuntimeError)` kapıyı göçün
    davranışına değil alembic'in sarmalama biçimine bağlardı.
    """
    config = Config(str(BACKEND / "alembic.ini"))
    with motor.begin() as baglanti:
        cid, depo, urun = _firma_kur(baglanti, FIRMA_ADI)
        _parti_yaz(baglanti, cid, urun, depo)

    with pytest.raises(Exception) as bilgi:
        command.downgrade(config, "20260907_0072")
    assert "BOŞ DEĞİL" in str(bilgi.value), str(bilgi.value)

    with motor.begin() as baglanti:
        baglanti.execute(
            text("DELETE FROM product_lots WHERE company_id=:cid"), {"cid": cid}
        )

    command.downgrade(config, "20260907_0072")
    with motor.connect() as baglanti:
        sutunlar = {
            satir[0]
            for satir in baglanti.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='product_lots'"
                )
            )
        }
        kalem = {
            satir[0]
            for satir in baglanti.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='purchase_items'"
                )
            )
        }
    assert "warehouse_id" not in sutunlar, sutunlar
    assert not ({"lot_code", "expiry_date"} & kalem), kalem

    command.upgrade(config, "head")
    with motor.connect() as baglanti:
        adlar = {
            satir[0]
            for satir in baglanti.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'product_lots'::regclass"
                )
            )
        }
    assert "uq_product_lots_company_product_code_warehouse" in adlar, adlar
    assert "fk_product_lots_warehouse_same_company" in adlar, adlar


@pytest.mark.postgresql
def test_BAS_TEK_ve_0073_zincirin_UCUNDA(motor) -> None:
    """Göç 0073 zincire İKİNCİ bir baş EKLEMEDİ.

    Ölçülen şey başın HANGİ göç olduğu değil, TEK olduğudur. Baş ARTIK
    `20260909_0075`tir (E3, hayvan/sürü karantinası) ve 0073 hâlâ zincirin
    İÇİNDE — yukarıdaki `test_GOC_TURU_BOS_TABLO_...` turu onu MUTLAK hedefle
    adıyla sürüyor.
    """
    from alembic.script import ScriptDirectory

    config = Config(str(BACKEND / "alembic.ini"))
    baslar = ScriptDirectory.from_config(config).get_heads()
    assert tuple(baslar) == ("20260909_0075",), baslar
