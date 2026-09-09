"""PostgreSQL ikizi: WA2 eşleştirme defterinin GERÇEK kısıtları ve YARIŞI.

Göç `20260910_0079`. SQLite ikizi `tests/test_wa2_eslestirme.py` akışın
davranışını ölçüyor (kod üret → tüket, kilitli pencere, süre, FİRMA SEÇ,
kiracı yalıtımı); bu dosya yalnız ŞEMANIN GERÇEKTEN ISIRAN kısımlarını ve
GELİŞTİRME DİYALEKTİNDE GÖRÜNMEYEN tuzakları ölçer.

--- BU İKİZ NEDEN VAR — BEŞ GEREKÇE, BEŞİ DE YALNIZ BURADA GÖRÜNÜR ------

1. **TEK KULLANIMLIK KOD GERÇEKTEN TEK KULLANIMLIK — YALNIZ BURADA
   ÖLÇÜLEBİLİR.** Kod bir sırdır ve sızabilir (ekran görüntüsü, iletilmiş
   mesaj); YİRMİ AYRI numaradan aynı anda gelen `BAĞLA <KOD>` denemesinden
   yalnız BİRİ geçmelidir. SQLite'ta bu ÜRETİLEMEZ (tek yazar).

   YİRMİ AYRI NUMARA ve bu seçim ÖLÇÜMLE DÜZELTİLDİ: ilk kurgu yirmi işçiye
   AYNI numarayı veriyordu ve o kurgu kodun iki kez tüketilmiş olmasını HİÇ
   GÖREMİYORDU — ikinci bağlantı zaten `uq_whatsapp_links_aktif_numara`
   kısmi tekilinde ölüyordu. Ayrı numaralarla bağlantı tekili yardım EDEMEZ.

   NEYİ ÖLDÜRDÜĞÜ ve NEYİ ÖLDÜRMEDİĞİ ÖLÇÜLDÜ — testin kendi başlığında
   tablo hâlinde. Kısaca: `kod_kullan` bu değişmezi ÜÇ BAĞIMSIZ katmanla
   koruyor ve HERHANGİ BİRİ TEK BAŞINA yetiyor, yani tek bir katmanı düşüren
   mutant bu testten SAĞ ÇIKAR. Katmanların tek tek çivilenmesi bu yüzden
   AST kapılarının işidir (`tests/test_wa2_eslestirme.py::
   test_CAS_KOSULU_STATUS_PENDING_ve_KIRACI_YUKLEMLI`), bu testin değil.
   Bu test ÜÇÜNÜN BİRDEN kaybını yakalıyor — ve yakaladığı ölçüldü.

2. **KISMİ TEKİL POSTGRESQL'DE DE KURULUYOR ve ISIRIYOR.** Göç yüklemi
   diyalekte göre AYRI yazıyor (`is_active = 1` / `is_active = true`).
   SQLite ikizi yalnız BİRİNCİSİNİ ölçebilir; ikincisi yanlış yazılmış
   olsaydı indeks PostgreSQL'de HİÇ kurulmaz ve aynı numara aynı firmada
   iki kez aktif bağlanabilirdi. Burada hem RED hem İZİN ölçülüyor.

3. **YEDİ `ck_wpc_*` CHECK'İ GERÇEKTEN REDDEDİYOR.** SQLite CHECK'i
   YANSITMIYOR (0072'de ölçüldü). Durum × zaman damgası matrisi açık
   olsaydı `CONSUMED` ama `consumed_at` NULL bir satır yazılabilirdi ve
   "bu kod ne zaman kullanıldı" sorusu cevapsız kalırdı.

4. **BİLEŞİK YABANCI ANAHTAR GERÇEKTEN BAĞLIYOR.** SQLite yabancı
   anahtarları varsayılan olarak UYGULAMAZ. Burada bir firmanın kodunun
   BAŞKA firmanın bağlantısını işaret etmesi denenip REDDEDİLDİĞİ
   görülüyor — kaynağı grep'lemek bunu SÖYLEYEMEZ.

5. **ZAMAN SÜTUNLARI `TIMESTAMPTZ`.** SQLite'ta zaman bir metindir ve saat
   dilimi sorusu HİÇ SORULMAZ. Saat dilimi taşımayan bir `expires_at`,
   "kod ne zaman doldu" sorusunu oturumun TZ'sine göre yanlış cevaplardı ve
   süresi dolmuş bir kod TÜKETİLEBİLİR hâle gelirdi.

--- PAYLAŞILAN YARDIMCI: `acilisa_cek` İKİ UÇTAN ------------------------

#81/#82 ile gelen `tests/pg_ikiz_yardimci.py` benimsendi. BU DOSYA İÇİN
BUGÜN NO-OP (bu ikiz `admin` olarak hiç giriş yapmıyor; ölçüldü) —
gerekçesi dolaylı yol ve `_sync_sequences`tir; ayrıntı fixture'ın kendi
başlığında.

--- ÖLÇÜLEN KÖK SEBEP, İDDİA DEĞİL ---------------------------------------

Kısıt testleri kısıtın VARLIĞINI değil GERÇEKTEN REDDETTİĞİNİ ölçüyor: her
biri kısıtı ihlal eden bir yazma deneyip `IntegrityError` bekliyor.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

BACKEND = Path(__file__).resolve().parent

#: DOSYA SEVİYESİNDE İŞARET: bu ikizin TAMAMI gerçek bir PostgreSQL
#: sunucusu ister (`pytest.ini`in `postgresql` işareti).
pytestmark = pytest.mark.postgresql

BAGLANTI = "whatsapp_links"
KOD = "whatsapp_pairing_codes"
BAGLAM = "whatsapp_context"
UCU = (BAGLANTI, KOD, BAGLAM)

#: HER KOŞU KENDİ ÖNEKİNİ KULLANIR. CI'da PostgreSQL ikizleri AYNI
#: veritabanını paylaşabiliyor; sabit bir ad kapıyı ilk koşuda yeşil,
#: ikincisinde kırmızı yapardı ve kırmızılığı kusuru DEĞİL koşu sırasını
#: gösterirdi.
KOSU = uuid4().hex[:8]

#: Kanonik numara (`normalize_phone` çıktısı biçiminde).
NUMARA = "905405995959"


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("WA2 ikizi APP_TEST_DATABASE_URL ister")
    return url


def _temizle(engine) -> None:
    """KENDİ satırlarını siler — ÖNEKLE, tabloyu SÜPÜRMEDEN.

    ÖLÇÜLMÜŞ TUZAK: PG ikizleri paylaşık bir şemada koşabiliyor ve arkada
    bıraktığı satır KOMŞU dosyayı kırıyor. `DELETE FROM whatsapp_links`
    yazmak da yanlış olurdu: bu dosya kendi çöpünü toplamalı, başkasınınkini
    değil. Sıra TERS (bağlam → kod → bağlantı → üyelik → kullanıcı → firma):
    bileşik yabancı anahtar gerçek.
    """
    onek = KOSU + "%"
    with engine.begin() as b:
        b.execute(
            text(
                "DELETE FROM whatsapp_context WHERE company_id IN"
                " (SELECT id FROM companies WHERE name LIKE :o)"
            ),
            {"o": onek},
        )
        b.execute(
            text(
                "DELETE FROM whatsapp_pairing_codes WHERE company_id IN"
                " (SELECT id FROM companies WHERE name LIKE :o)"
            ),
            {"o": onek},
        )
        b.execute(
            text(
                "DELETE FROM whatsapp_links WHERE company_id IN"
                " (SELECT id FROM companies WHERE name LIKE :o)"
            ),
            {"o": onek},
        )
        b.execute(
            text("DELETE FROM whatsapp_pairing_attempts WHERE phone = :p"
                 " OR phone LIKE '9053%'"),
            {"p": NUMARA},
        )
        b.execute(
            text(
                "DELETE FROM user_company_memberships WHERE company_id IN"
                " (SELECT id FROM companies WHERE name LIKE :o)"
            ),
            {"o": onek},
        )
        b.execute(text("DELETE FROM app_users WHERE username LIKE :o"), {"o": onek})
        b.execute(text("DELETE FROM companies WHERE name LIKE :o"), {"o": onek})


def _acilisa_cek(engine) -> None:
    """Admin şifresini AÇILIŞ DURUMUNA (`admin123` + `must_change_password`) yaz.

    Ortak yardımcıya (#81/#82) devredildi; gövde `tests/pg_ikiz_yardimci.py`de.

    BU DOSYA İÇİN BUGÜN BİR NO-OP ve bu ÖLÇÜLDÜ, varsayılmadı: bu ikiz
    HİÇBİR ZAMAN `admin` olarak giriş YAPMIYOR — kendi firmalarını ve
    kullanıcılarını ham SQL ile açıyor (`dunya` fixture'ı) ve `_temizle`
    yalnız KOŞU ÖNEKLİ kullanıcıları siliyor, yani `admin` satırına ne okur
    ne yazar. Yani çağrı bugün ne bizi korur ne komşuyu.

    YİNE DE İKİ UÇTAN ÇAĞRILIYOR ve gerekçe DOLAYLI YOLDUR: bu ikiz WA3'ün
    işçisiyle birlikte büyüyecek ve o turda bir uç/oturum smoke'u eklendiği
    an dosya SESSİZCE paylaşılan şifreye bağımlı hâle gelir — o bağımlılık
    doğduğunda kimse bu satırı eklemeyi hatırlamak zorunda kalmasın.
    (`app/auth.py`deki "kural bugün sonucu değiştirmiyor ama dolaylı yola
    bağlıdır" kaydıyla AYNI sınıftan bir karar.)

    TEK GÖRÜNÜR ETKİSİ yardımcının `_sync_sequences` adımıdır: bu ikiz
    `companies` ve `app_users`a satır ekliyor, sonra siliyor; paylaşılan bir
    veritabanında dizilerin `max(id)`ye çekilmesi komşu dosyaların açık
    kimlik yazan yollarını korur.
    """
    from tests.pg_ikiz_yardimci import acilisa_cek

    acilisa_cek(engine)


@pytest.fixture()
def motor():
    yapilandirma = Config(str(BACKEND / "alembic.ini"))
    engine = create_engine(_url())
    command.upgrade(yapilandirma, "head")
    _acilisa_cek(engine)
    _temizle(engine)
    try:
        yield engine
    finally:
        _temizle(engine)
        _acilisa_cek(engine)
        engine.dispose()


@pytest.fixture()
def dunya(motor):
    """İKİ firma, BİR kullanıcı, İKİ üyelik. Adlar KOŞU önekli."""
    an = datetime.now(timezone.utc)
    with motor.begin() as b:
        firma_a = b.execute(
            text("INSERT INTO companies(name,is_active,created_at)"
                 " VALUES(:n,TRUE,:t) RETURNING id"),
            {"n": KOSU + "-bir", "t": an},
        ).scalar_one()
        firma_b = b.execute(
            text("INSERT INTO companies(name,is_active,created_at)"
                 " VALUES(:n,TRUE,:t) RETURNING id"),
            {"n": KOSU + "-iki", "t": an},
        ).scalar_one()
        kullanici = b.execute(
            text("INSERT INTO app_users(username,email,email_verified,"
                 "display_name,password_hash,role,is_active,"
                 "must_change_password,created_at)"
                 " VALUES(:u,:e,TRUE,:u,'x','admin',TRUE,FALSE,:t)"
                 " RETURNING id"),
            {"u": KOSU + "-kul", "e": KOSU + "@wa2.invalid", "t": an},
        ).scalar_one()
        for cid in (firma_a, firma_b):
            b.execute(
                text("INSERT INTO user_company_memberships"
                     "(user_id,company_id,is_default,created_at)"
                     " VALUES(:u,:c,FALSE,:t)"),
                {"u": kullanici, "c": cid, "t": an},
            )
    return {"firma_a": int(firma_a), "firma_b": int(firma_b),
            "kullanici": int(kullanici)}


def _baglanti_yaz(baglanti, cid: int, uid: int, telefon: str, aktif: bool) -> int:
    an = datetime.now(timezone.utc)
    return baglanti.execute(
        text("INSERT INTO whatsapp_links"
             "(company_id,user_id,phone,is_active,created_at,updated_at)"
             " VALUES(:c,:u,:p,:a,:t,:t) RETURNING id"),
        {"c": cid, "u": uid, "p": telefon, "a": aktif, "t": an},
    ).scalar_one()


def _kod_satiri(**fazla) -> dict:
    an = datetime.now(timezone.utc)
    govde = {
        "code_digest": KOSU + "-ozet",
        "status": "PENDING",
        "expires_at": an + timedelta(minutes=10),
        "attempt_count": 0,
        "max_attempts": 5,
        "consumed_at": None,
        "cancelled_at": None,
        "consumed_link_id": None,
        "created_at": an,
    }
    govde.update(fazla)
    return govde


def _kod_yaz(baglanti, cid: int, uid: int, **fazla) -> None:
    govde = _kod_satiri(**fazla)
    govde["company_id"] = cid
    govde["user_id"] = uid
    sutunlar = ",".join(govde)
    yer = ",".join(":" + ad for ad in govde)
    baglanti.execute(
        text("INSERT INTO %s(%s) VALUES(%s)" % (KOD, sutunlar, yer)), govde
    )


# ------------------------------------------------------------- ŞEMA -------

def test_UC_TABLO_da_company_id_TASIYOR_ve_BILESIK_ANAHTARI_VAR(motor) -> None:
    """Kiracı sütunu ve `(company_id, id)` tekili GÖÇ EDİLMİŞ KATALOGDA.

    Kaynak metnini grep'lemek, sütunu bir yardımcıdan ekleyen ya da bir
    tekili sessizce düşüren bir değişikliği kaçırırdı.

    MUTASYON: sütunu düşürmek bunu ve `TENANT_TABLES` envanterini AYNI ANDA
    kırmızı yapar — iki kapı aynı olguyu iki yerden tutuyor.
    """
    gozlemci = inspect(motor)
    for tablo in UCU:
        sutunlar = {c["name"] for c in gozlemci.get_columns(tablo)}
        assert "company_id" in sutunlar, tablo
        tekiller = {
            u["name"]: tuple(u["column_names"])
            for u in gozlemci.get_unique_constraints(tablo)
        }
        assert ("company_id", "id") in tekiller.values(), (tablo, tekiller)


def test_ZAMAN_SUTUNLARI_TIMESTAMPTZ(motor) -> None:
    """Sekiz zaman sütununun sekizi de saat dilimi TAŞIYOR.

    MUTASYON: `sa.DateTime(timezone=True)`ı `timezone=False` yapmak bunu
    KIRMIZI yapar. SQLite'ta hiçbir şey değişmez — bu iddia YALNIZ burada
    sorulabilir. `expires_at` özellikle önemli: saat dilimsiz bir değer,
    süresi dolmuş bir kodu oturumun TZ'sine göre HALA GEÇERLİ gösterirdi.
    """
    gozlemci = inspect(motor)
    beklenen = {
        BAGLANTI: ("created_at", "updated_at"),
        KOD: ("expires_at", "created_at", "consumed_at", "cancelled_at"),
        BAGLAM: ("created_at", "expires_at"),
    }
    for tablo, adlar in beklenen.items():
        tipler = {c["name"]: c["type"] for c in gozlemci.get_columns(tablo)}
        for ad in adlar:
            assert getattr(tipler[ad], "timezone", False) is True, (tablo, ad)


def test_AKTIF_NUMARA_TEKILI_gercekten_REDDEDIYOR(motor, dunya) -> None:
    """`(company_id, phone) WHERE is_active` PostgreSQL'de de ISIRIYOR.

    ÜÇ İDDİA, üçü de göçün `postgresql_where` yükleminin GERÇEKTEN
    kurulduğunun kanıtı:

      * aynı firmada İKİNCİ aktif satır -> RED
      * BAŞKA firmada aynı numara       -> KABUL
      * aynı firmada PASİF satır        -> KABUL (kısmi indeks kapsamı dışı)

    MUTASYON: `postgresql_where`i kaldırmak İKİNCİ ve ÜÇÜNCÜ iddiayı,
    anahtardan `company_id`yi düşürmek İKİNCİYİ, tekili büsbütün düşürmek
    BİRİNCİYİ KIRMIZI yapar.
    """
    with motor.begin() as b:
        _baglanti_yaz(b, dunya["firma_a"], dunya["kullanici"], NUMARA, True)

    with pytest.raises(IntegrityError):
        with motor.begin() as b:
            _baglanti_yaz(b, dunya["firma_a"], dunya["kullanici"], NUMARA, True)

    with motor.begin() as b:
        _baglanti_yaz(b, dunya["firma_b"], dunya["kullanici"], NUMARA, True)
    with motor.begin() as b:
        _baglanti_yaz(b, dunya["firma_a"], dunya["kullanici"], NUMARA, False)
        _baglanti_yaz(b, dunya["firma_a"], dunya["kullanici"], NUMARA, False)

    with motor.connect() as b:
        adet = b.execute(
            text("SELECT COUNT(*) FROM whatsapp_links WHERE company_id=:c"),
            {"c": dunya["firma_a"]},
        ).scalar_one()
    assert adet == 3, adet


def test_BEKLEYEN_KOD_TEKILI_gercekten_REDDEDIYOR(motor, dunya) -> None:
    """`uq_wpc_aktif_kod`: firma+kullanıcı başına EN FAZLA BİR PENDING kod.

    MUTASYON: kısmi tekili düşürmek bunu KIRMIZI yapar — ve o hâlde iki
    yönetici aynı anda kod üretse İKİ bekleyen kod kalır, yani "hangi kod
    geçerli" sorusunun iki cevabı olurdu.
    """
    with motor.begin() as b:
        _kod_yaz(b, dunya["firma_a"], dunya["kullanici"])

    with pytest.raises(IntegrityError):
        with motor.begin() as b:
            _kod_yaz(b, dunya["firma_a"], dunya["kullanici"],
                     code_digest=KOSU + "-ozet2")

    # İPTAL EDİLMİŞ satır kapsam DIŞINDA: yeni kod açılabiliyor.
    an = datetime.now(timezone.utc)
    with motor.begin() as b:
        b.execute(
            text("UPDATE whatsapp_pairing_codes SET status='CANCELLED',"
                 " cancelled_at=:t WHERE company_id=:c"),
            {"t": an, "c": dunya["firma_a"]},
        )
        _kod_yaz(b, dunya["firma_a"], dunya["kullanici"],
                 code_digest=KOSU + "-ozet3")


def test_YEDI_CHECK_gercekten_REDDEDIYOR(motor, dunya) -> None:
    """Durum × zaman damgası matrisi veritabanı seviyesinde ISIRIYOR.

    SQLite CHECK'i YANSITMIYOR (0072'de ölçüldü); bu iddia YALNIZ burada
    sorulabilir.

    MUTASYON: yedi CHECK'ten birini kaldırmak ilgili adımı KIRMIZI yapar.
    """
    an = datetime.now(timezone.utc)

    def reddedildi(**fazla) -> bool:
        try:
            with motor.begin() as b:
                _kod_yaz(b, dunya["firma_a"], dunya["kullanici"], **fazla)
            return False
        except IntegrityError:
            return True

    # ck_wpc_status — kapalı küme.
    assert reddedildi(status="SENT", code_digest=KOSU + "-a")
    # ck_wpc_attempt_count / ck_wpc_max_attempts.
    assert reddedildi(attempt_count=-1, code_digest=KOSU + "-b")
    assert reddedildi(max_attempts=0, code_digest=KOSU + "-c")
    # ck_wpc_pending_temiz — PENDING satırda zaman damgası OLAMAZ.
    assert reddedildi(consumed_at=an, code_digest=KOSU + "-d")
    # ck_wpc_consumed_alanlari — CONSUMED satırda damgalar ZORUNLU.
    assert reddedildi(status="CONSUMED", code_digest=KOSU + "-e")
    # ck_wpc_cancelled_alani — CANCELLED satırda `cancelled_at` ZORUNLU.
    assert reddedildi(status="CANCELLED", code_digest=KOSU + "-f")
    # ck_wpc_expired_temiz — EXPIRED satırda üç damga da NULL olmalı.
    assert reddedildi(status="EXPIRED", cancelled_at=an, code_digest=KOSU + "-g")

    # DÖRT DURUMUN DÖRDÜ DE geçerli biçimde YAZILABİLİYOR: kapı kümeyi
    # DARALTMIYOR.
    with motor.begin() as b:
        link_id = _baglanti_yaz(
            b, dunya["firma_a"], dunya["kullanici"], NUMARA, False
        )
    with motor.begin() as b:
        _kod_yaz(b, dunya["firma_a"], dunya["kullanici"],
                 code_digest=KOSU + "-h", status="CONSUMED",
                 consumed_at=an, consumed_link_id=link_id)
        _kod_yaz(b, dunya["firma_a"], dunya["kullanici"],
                 code_digest=KOSU + "-i", status="CANCELLED", cancelled_at=an)
        _kod_yaz(b, dunya["firma_a"], dunya["kullanici"],
                 code_digest=KOSU + "-j", status="EXPIRED")
        _kod_yaz(b, dunya["firma_a"], dunya["kullanici"],
                 code_digest=KOSU + "-k", status="PENDING")


def test_CAPRAZ_KIRACI_BILESIK_ANAHTAR_REDDEDIYOR(motor, dunya) -> None:
    """Bir firmanın kodu BAŞKA firmanın bağlantısını tüketmiş GÖRÜNEMEZ.

    SQLite yabancı anahtarları varsayılan olarak UYGULAMAZ, yani bu iddia
    YALNIZ burada sorulabilir.

    MUTASYON: `(company_id, consumed_link_id) -> whatsapp_links(company_id,
    id)` bileşik anahtarını `consumed_link_id -> whatsapp_links(id)` yapmak
    bunu KIRMIZI yapar — ve o hâlde çapraz kiracı bir tüketim kaydı
    veritabanı seviyesinde MÜMKÜN olurdu.
    """
    an = datetime.now(timezone.utc)
    with motor.begin() as b:
        b_link = _baglanti_yaz(
            b, dunya["firma_b"], dunya["kullanici"], NUMARA, True
        )

    with pytest.raises(IntegrityError):
        with motor.begin() as b:
            _kod_yaz(b, dunya["firma_a"], dunya["kullanici"],
                     code_digest=KOSU + "-capraz", status="CONSUMED",
                     consumed_at=an, consumed_link_id=b_link)

    # AYNI firmanın bağlantısı KABUL: kısıt DOĞRU yeri kesiyor.
    with motor.begin() as b:
        a_link = _baglanti_yaz(
            b, dunya["firma_a"], dunya["kullanici"], NUMARA, False
        )
    with motor.begin() as b:
        _kod_yaz(b, dunya["firma_a"], dunya["kullanici"],
                 code_digest=KOSU + "-ayni", status="CONSUMED",
                 consumed_at=an, consumed_link_id=a_link)


def test_BAGLAM_KAPSAM_TEKILI_gercekten_REDDEDIYOR(motor, dunya) -> None:
    """`(company_id, user_id, phone)` başına TEK bağlam satırı.

    MUTASYON: tekili düşürmek bunu KIRMIZI yapar — ve o hâlde "aktif firma"
    sorusunun İKİ cevabı olurdu.
    """
    an = datetime.now(timezone.utc)

    def yaz(baglanti, yuk: str) -> None:
        baglanti.execute(
            text("INSERT INTO whatsapp_context"
                 "(company_id,user_id,phone,payload,created_at,expires_at)"
                 " VALUES(:c,:u,:p,:y,:t,:s)"),
            {"c": dunya["firma_a"], "u": dunya["kullanici"], "p": NUMARA,
             "y": yuk, "t": an, "s": an + timedelta(minutes=30)},
        )

    with motor.begin() as b:
        yaz(b, '{"aktif_firma": 1}')
    with pytest.raises(IntegrityError):
        with motor.begin() as b:
            yaz(b, '{"aktif_firma": 2}')


# ------------------------------------------------------- GÖÇ TURU --------

def test_GOC_TURU_up_down_up_GERCEK_PostgreSQLde(motor) -> None:
    """0079 PostgreSQL'de de GERİ ALINABİLİYOR — SQLite turu bunu SÖYLEYEMEZ.

    İki diyalektin `DROP TABLE` semantiği AYNI DEĞİL: PostgreSQL yabancı
    anahtar bağımlılıklarını GERÇEKTEN uygular, SQLite çoğunu görmezden
    gelir. `downgrade` bağlantıyı KOD DEFTERİNDEN ÖNCE düşürseydi SQLite'ta
    yeşil kalır, burada `DependentObjectsStillExist` ile ölürdü — bu yüzden
    sıra TERSTİR ve o sıra burada sorgulanıyor.

    MUTASYON: `downgrade`deki sırayı ters çevirmek ya da `op.drop_table`
    çağrılarından birini kaldırmak bunu KIRMIZI yapar.

    TUR SONUNDA ŞEMA `head`TE BIRAKILIYOR: KOMŞU dosyalar (CI'da aynı
    konteyneri paylaşan koşularda) şemayı VAR bulmak zorunda.
    """
    yapilandirma = Config(str(BACKEND / "alembic.ini"))
    yapilandirma.set_main_option("sqlalchemy.url", _url())

    def gorunen() -> set[str]:
        return set(inspect(motor).get_table_names())

    assert set(UCU) <= gorunen(), "başlangıç: tablolar yok"
    command.downgrade(yapilandirma, "20260910_0078")
    dusen = gorunen()
    assert not (set(UCU) & dusen), "aşağı: tablolar düşmedi"
    # WA1'in tabloları YERİNDE: 0079 onlara DOKUNMUYOR.
    assert "whatsapp_inbound" in dusen
    assert "whatsapp_pairing_attempts" in dusen

    command.upgrade(yapilandirma, "head")
    assert set(UCU) <= gorunen(), "ikinci yukarı: tur kapanmadı"
    # KISMİ TEKİLLER DE GERİ GELDİ: `drop_table` onları düşürdü.
    indeksler = {i["name"] for i in inspect(motor).get_indexes(BAGLANTI)}
    assert "uq_whatsapp_links_aktif_numara" in indeksler, indeksler
    assert "ix_whatsapp_links_phone" in indeksler, indeksler
    kod_indeks = {i["name"] for i in inspect(motor).get_indexes(KOD)}
    assert "uq_wpc_aktif_kod" in kod_indeks, kod_indeks


# ------------------------------------------------------------- YARIŞ ------

def test_YIRMI_ESZAMANLI_ayni_kod_TEK_KEZ_tukeniyor(motor, dunya) -> None:
    """Yirmi işçi AYNI kodu AYRI numaralarla tüketiyor; TEK'i geçiyor.

    Bu dosyanın var oluş sebebi: değişmez ("tek kullanımlık kod GERÇEKTEN
    tek kullanımlıktır") yalnız gerçek eşzamanlılıkta sınanabilir ve SQLite
    tek yazarlı olduğu için orada ÜRETİLEMEZ.

    --- NEDEN YİRMİ AYRI NUMARA, AYNI NUMARA DEĞİL --------------------------

    İlk kurgu yirmi işçiye AYNI numarayı veriyordu ve o kurgu ölçtüğünü
    sandığı şeyi ÖLÇMÜYORDU: hakem `uq_whatsapp_links_aktif_numara` kısmi
    tekiliydi — ikinci bağlantı zaten orada ölüyor ve kodun iki kez
    tüketilmiş olması hiç görünmüyordu. Ayrı numaralarla bağlantı tekili
    yardım EDEMEZ; geriye kodun kendi korumaları kalır.

    --- ÜÇ KATMAN VE HANGİSİNİN TAŞIDIĞI — ÖLÇÜLDÜ, VARSAYILMADI -----------

    `kod_kullan` bu değişmezi ÜÇ BAĞIMSIZ katmanla koruyor:

      A. `SELECT ... FOR UPDATE` — kod satırı kilitleniyor (yalnız
         PostgreSQL; SQLite'ta bu satır HİÇ çalışmaz).
      B. Okuma sonrası `status != PENDING` denetimi.
      C. CAS: `UPDATE ... WHERE status='PENDING'` + `rowcount == 1` +
         SAVEPOINT geri alması.

    Bu test üzerinde ölçülen tablo (PG 16, yirmi işçi):

      | düşürülen                  | sonuç   |
      |----------------------------|---------|
      | hiçbiri (taban)            | YEŞİL   |
      | yalnız A                   | YEŞİL   |
      | yalnız B                   | YEŞİL   |
      | yalnız C                   | YEŞİL   |
      | A + B (C tek başına)       | YEŞİL   |
      | A + B + C                  | KIRMIZI (20 bağlantı, 20 "başarılı") |

    Yani HER KATMAN TEK BAŞINA YETİYOR ve tek bir katmanı düşüren mutant bu
    testten SAĞ ÇIKAR. Bu bir zayıflık DEĞİL, katmanlı savunmanın tanımıdır
    — ama testin ne ölçtüğü konusunda YANILMAMAK için buraya yazılıyor:
    katmanların TEK TEK varlığı AST kapılarının işidir
    (`tests/test_wa2_eslestirme.py::test_CAS_KOSULU_STATUS_PENDING_ve_
    KIRACI_YUKLEMLI`), bu testin değil. Bu test ÜÇÜNÜN BİRDEN kaybını
    yakalıyor ve o hâlde kod yirmi numarayı birden bağlıyor.

    C KATMANI NEDEN VAZGEÇİLMEZ: A yalnız PostgreSQL'de çalışıyor. SQLite'ta
    (geliştirme ve testlerin çoğu) geriye B ve C kalıyor ve B tek başına bir
    TOCTOU'dur — okuma ile yazma arasında satır değişebilir. Yazmayı koşula
    bağlayan tek katman C'dir.

    ÖLÇÜLEN ÜÇ ŞEY AYRI:
      (a) tam BİR işçi "başarılı" dedi;
      (b) `whatsapp_links`te tam BİR satır var — kaybedenlerin INSERT'i
          SAVEPOINT ile geri alındı;
      (c) kod satırı TAM BİR KEZ `CONSUMED` ve `consumed_link_id` o tek
          bağlantıyı gösteriyor.
    """
    import sys

    sys.path.insert(0, str(BACKEND))
    from app.whatsapp import eslestirme

    Oturum = sessionmaker(bind=motor)
    with Oturum() as db:
        uretilen = eslestirme.kod_uret(
            db, dunya["firma_a"], dunya["kullanici"], hedef_telefon=telefon
        )
        db.commit()
    kod = uretilen.kod

    # YİRMİ AYRI KANONİK NUMARA. Hız sınırı TELEFON BAŞINADIR, yani hiçbiri
    # sınıra takılmıyor ve sınırı bu test için gevşetmeye GEREK YOK —
    # gevşetmek, ölçtüğümüz şeyi ölçmeyi bırakmak olurdu.
    numaralar = ["9053%08d" % i for i in range(20)]
    kapi = Barrier(len(numaralar))

    # --- YARIŞIN KENDİSİ ÖLÇÜLÜYOR, VARSAYILMIYOR (WA3-full, H9) ----------
    #
    # ESKİ KURGU: bariyerden SONRA oturum açılıyordu, yani bariyer
    # "yirmi thread AYNI ANDA yarışıyor" değil "yirmi thread aynı anda
    # BAĞLANTI AÇMAYA BAŞLIYOR" anlamına geliyordu. Bağlantı kurma
    # maliyeti (TCP + kimlik doğrulama) yarışın İÇİNDEYDİ.
    #
    # YENİ KURGU: her thread bariyerden ÖNCE bir GİDİŞ-DÖNÜŞ yapıyor
    # (`SELECT 1`), yani bariyer açıldığında yirmisi de KURULMUŞ ve canlı
    # bir bağlantı TUTUYOR.
    #
    # ISINMA KENDİ MOTORUNU ZORUNLU KILIYOR — ÖLÇÜLDÜ. `motor`
    # VARSAYILAN havuzla kuruluyor (pool_size=5 + overflow=10 = ON BEŞ) ve
    # yirmi thread bariyerden önce bağlantı TUTMAYA çalışınca on beşi
    # bağlantıyı alıp bariyerde bekliyor, kalan beşi havuzdan bağlantı
    # bekliyor: KİMSE ilerlemiyor ve bariyer `BrokenBarrierError` ile
    # düşüyor (üç koşuda üçü de). Bu yüzden yarışın KENDİ motoru var.
    #
    # DÜRÜST NOT — ISINMA SONUCU DEĞİŞTİRMEDİ. Aşağıdaki iki ölçüm ESKİ
    # kurguda da AYNI çıkıyor (üçer koşu, PG 16): `for_update`=20,
    # `cas_denemesi`=1. Yani bu değişiklik bir kusuru KAPATMIYOR, testin
    # ÖLÇTÜĞÜ ŞEYİ ÖLÇÜLEBİLİR KILIYOR: sayılar artık bariyerin ne söz
    # verdiğini VARSAYMADAN doğrulanıyor.
    yaris_motoru = create_engine(_url(), pool_size=len(numaralar) + 5,
                                 max_overflow=5)
    YarisOturumu = sessionmaker(bind=yaris_motoru)

    # ÖLÇÜM SÜRÜCÜ SEVİYESİNDEN OKUNUYOR, uygulamanın kendi raporundan
    # DEĞİL: hangi katmanın kaç thread'i durdurduğu ancak GÖNDERİLEN
    # DEYİMLERDEN görülebilir.
    olcum = {"for_update": 0, "cas_denemesi": 0}
    olcum_kilidi = threading.Lock()

    @event.listens_for(yaris_motoru, "before_cursor_execute")
    def _deyimleri_say(conn, imlec, deyim, parametreler, baglam, cok):
        buyuk = " ".join(deyim.split()).upper()
        with olcum_kilidi:
            if "FOR UPDATE" in buyuk:
                olcum["for_update"] += 1
            if "UPDATE WHATSAPP_PAIRING_CODES" in buyuk and (
                "CONSUMED" in str(parametreler).upper()
            ):
                olcum["cas_denemesi"] += 1

    def tuket(telefon: str) -> bool:
        with YarisOturumu() as db:
            db.execute(text("SELECT 1")).scalar()  # ISINMA: bariyerden ÖNCE
            kapi.wait(timeout=30)
            sonuc = eslestirme.kod_kullan(db, telefon, kod)
            db.commit()
            return sonuc.basarili

    try:
        with ThreadPoolExecutor(max_workers=len(numaralar)) as havuz:
            isler = [havuz.submit(tuket, t) for t in numaralar]
            sonuclar = [i.result(timeout=120) for i in isler]
    finally:
        yaris_motoru.dispose()

    assert sum(1 for s in sonuclar if s) == 1, sonuclar

    # --- YİRMİSİ DE YARIŞ NOKTASINA GERÇEKTEN GİRDİ ----------------------
    #
    # `SELECT ... FOR UPDATE` kod satırının KİLİT NOKTASIDIR; oraya
    # ulaşmayan bir thread yarışa hiç girmemiş demektir. Yirmi deyim, yirmi
    # thread. Bir mutant `kod_kullan`ı erken döndürürse (ör. hız sınırını
    # numara başına değil KÜRESEL sayarsa) bu sayı düşer ve testin geri
    # kalanı YİNE DE yeşil kalırdı — bu yüzden ayrıca ölçülüyor.
    assert olcum["for_update"] == len(numaralar), olcum

    # --- CAS'e YALNIZ BİR THREAD ULAŞIYOR — ÖLÇÜLDÜ, VARSAYILMADI --------
    #
    # Bu sayı SEZGİYE AYKIRIDIR ve tam da bu yüzden yazılı: "yirmi işçi CAS
    # yarışına giriyor, on dokuzu CAS'te kaybediyor" DOĞRU DEĞİLDİR.
    # `SELECT ... FOR UPDATE` yirmi thread'i SIRAYA sokar; kazanan commit
    # ettikten sonra sıradaki thread kilidi aldığında satırı YENİDEN okur ve
    # `status='CONSUMED'` görür, yani B KATMANINDA (okuma sonrası durum
    # denetimi) döner — CAS deyimini HİÇ GÖNDERMEZ.
    #
    # ÖLÇÜM (PG 16, dört koşu, ısınmalı ve ısınmasız): `for_update`=20,
    # `cas_denemesi`=1, CAS kaybı=0 — HER KOŞUDA. Yani C katmanının
    # kaybedeni bu kurguda ÜRETİLEMEZ ve "CAS kaybı ≥ N" biçiminde bir
    # eşik yazmak, hiçbir zaman doğrulanamayacak bir iddia olurdu. C
    # katmanının varlığı AST kapısının işidir
    # (`tests/test_wa2_eslestirme.py::test_CAS_KOSULU_STATUS_PENDING_ve_
    # KIRACI_YUKLEMLI`); burada ölçülen şey SIRALAMANIN GERÇEKTEN
    # ÇALIŞTIĞIDIR — tek bir tüketim deyimi.
    assert olcum["cas_denemesi"] == 1, olcum

    with motor.connect() as b:
        baglantilar = b.execute(
            text("SELECT id FROM whatsapp_links WHERE company_id=:c"),
            {"c": dunya["firma_a"]},
        ).scalars().all()
        kodlar = b.execute(
            text("SELECT status, consumed_link_id FROM whatsapp_pairing_codes"
                 " WHERE company_id=:c"),
            {"c": dunya["firma_a"]},
        ).mappings().all()
    assert len(baglantilar) == 1, baglantilar
    assert len(kodlar) == 1, kodlar
    assert kodlar[0]["status"] == "CONSUMED", kodlar
    assert kodlar[0]["consumed_link_id"] == baglantilar[0], (kodlar, baglantilar)
