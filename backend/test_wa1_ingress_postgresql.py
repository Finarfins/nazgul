"""PostgreSQL ikizi: WA1 giriş kuyruğunun GERÇEK kısıtları ve YARIŞI.

Göç `20260910_0078`. SQLite ikizi `tests/test_wa1_ingress.py` uçların
davranışını ölçüyor (el sıkışması, HMAC, gövde sınırı, yabancı numara,
kopya, yapılandırılmamış kanal); bu dosya yalnız ŞEMANIN GERÇEKTEN
ISIRAN kısımlarını ve GELİŞTİRME DİYALEKTİNDE GÖRÜNMEYEN tuzakları ölçer.

--- BU İKİZ NEDEN VAR — DÖRT GEREKÇE, DÖRDÜ DE YALNIZ BURADA GÖRÜNÜR -----

1. **KOPYA `wamid` YARIŞI — KUSURUN VE ÇARENİN TEK GÖRÜNDÜĞÜ YER.** Meta
   aynı teslimatı PARALEL gönderebilir ve bizim iki konteynerimiz onu aynı
   anda karşılayabilir. `giris.gelen_kaydet` yarışı UYGULAMADA çözmeye
   ÇALIŞMIYOR; hakem `uq_whatsapp_inbound_wamid` kısıtıdır ve kaybeden
   taraf `IntegrityError`ı yutar. SQLite'ta bu ÜRETİLEMEZ (tek yazar):
   kısıt düşürülseydi geliştirme diyalektinde HİÇBİR ŞEY kırmızı olmaz,
   üretimde ise aynı mesaj İKİ SATIR olurdu — yani iki kez işlenirdi.
   Yirmi eşzamanlı yazma burada koşuyor ve ölçülen şey durum kodları
   DEĞİL, DEFTERİN KENDİSİDİR: tam BİR satır.

2. **`ck_whatsapp_inbound_status` KAPALI KÜMESİ GERÇEKTEN REDDEDİYOR.**
   SQLite CHECK'i YANSITMIYOR (0072'de ölçüldü) ve 0072'de ölçülen kusur
   — açılış DDL'i nesneyi kurar, göç dalı atlar, CHECK HİÇ kurulmaz — tam
   olarak burada görünür. Küme açık olsaydı tanınmayan bir durum sessizce
   yazılabilir ve o satırı HİÇBİR işçi seçmezdi.

3. **`company_id` GERÇEKTEN YOK.** Kaynak metnini grep'lemek, sütunu bir
   yardımcıdan ekleyen bir değişikliği kaçırırdı; burada GÖÇ EDİLMİŞ
   KATALOĞA soruluyor. Sütun bir gün geri gelirse `TENANT_TABLES`
   envanteri de kırmızı olur — iki kapı aynı olguyu iki yerden tutuyor.

4. **ZAMAN SÜTUNLARI `TIMESTAMPTZ`.** SQLite'ta zaman bir metindir ve
   saat dilimi sorusu HİÇ SORULMAZ. PostgreSQL'de saat dilimi taşımayan
   bir `received_at`, "bu mesaj ne zaman geldi" sorusunu oturumun TZ'sine
   göre yanlış cevaplardı ve lease penceresi (`locked_until`) yanlış
   yerde açılıp kapanırdı.

--- ÖLÇÜLEN KÖK SEBEP, İDDİA DEĞİL ---------------------------------------

Kısıt testleri kısıtın VARLIĞINI değil GERÇEKTEN REDDETTİĞİNİ ölçüyor: her
biri kısıtı ihlal eden bir yazma deneyip `IntegrityError` bekliyor.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

BACKEND = Path(__file__).resolve().parent

#: DOSYA SEVİYESİNDE İŞARET: bu ikizin TAMAMI gerçek bir PostgreSQL
#: sunucusu ister (`pytest.ini`in `postgresql` işareti).
pytestmark = pytest.mark.postgresql

KUYRUK = "whatsapp_inbound"
SAYAC = "whatsapp_pairing_attempts"

#: HER KOŞU KENDİ ÖNEKİNİ KULLANIR. CI'da PostgreSQL ikizleri AYNI
#: veritabanını paylaşabiliyor; sabit bir `wamid` kapıyı ilk koşuda yeşil,
#: ikincisinde kırmızı yapardı ve kırmızılığı kusuru DEĞİL koşu sırasını
#: gösterirdi.
KOSU = uuid4().hex[:8]

#: Kanonik numara (`normalize_phone` çıktısı biçiminde).
NUMARA = "905405995959"
PNID = "111222333444555"


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("WA1 ikizi APP_TEST_DATABASE_URL ister")
    return url


def _temizle(engine) -> None:
    """KENDİ satırlarını siler — ÖNEKLE, tabloyu SÜPÜRMEDEN.

    ÖLÇÜLMÜŞ TUZAK: PG ikizleri paylaşık bir şemada koşabiliyor ve arkada
    bıraktığı satır KOMŞU dosyayı kırıyor. `DELETE FROM whatsapp_inbound`
    yazmak da yanlış olurdu: bu dosya kendi çöpünü toplamalı, başkasınınkini
    değil.
    """
    with engine.begin() as baglanti:
        baglanti.execute(
            text("DELETE FROM whatsapp_inbound WHERE wamid LIKE :onek"),
            {"onek": KOSU + "%"},
        )
        baglanti.execute(
            text("DELETE FROM whatsapp_pairing_attempts WHERE phone LIKE :onek"),
            {"onek": KOSU + "%"},
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
        engine.dispose()


def _satir(**fazla) -> dict:
    govde = {
        "wamid": KOSU + "-taban",
        "sender_phone": NUMARA,
        "phone_number_id": PNID,
        "text": "merhaba",
        "media_id": None,
        "media_mime": None,
        "status": "RECEIVED",
        "attempt_count": 0,
        "locked_until": None,
        "lock_token": None,
        "last_error": None,
        "received_at": datetime.now(timezone.utc),
        "processed_at": None,
    }
    govde.update(fazla)
    return govde


def _yaz(baglanti, **fazla) -> None:
    govde = _satir(**fazla)
    sutunlar = ",".join(govde)
    yer = ",".join(":" + ad for ad in govde)
    baglanti.execute(
        text("INSERT INTO %s(%s) VALUES(%s)" % (KUYRUK, sutunlar, yer)), govde
    )


# ------------------------------------------------------------- ŞEMA -------

def test_company_id_GOC_EDILMIS_SEMADA_da_YOK(motor) -> None:
    """İki tabloda da kiracı sütunu YOK — kaynakta değil, KATALOGDA ölçüldü.

    MUTASYON: sütunu eklemek bunu KIRMIZI yapar; `TENANT_TABLES` envanteri
    de aynı anda kırmızı olur (o envanter göç edilmiş şemadan türetiliyor).
    """
    gozlemci = inspect(motor)
    for tablo in (KUYRUK, SAYAC):
        sutunlar = {c["name"] for c in gozlemci.get_columns(tablo)}
        assert "company_id" not in sutunlar, (tablo, sorted(sutunlar))
        assert "user_id" not in sutunlar, (tablo, sorted(sutunlar))


def test_ZAMAN_SUTUNLARI_TIMESTAMPTZ(motor) -> None:
    """Dört zaman sütununun dördü de saat dilimi TAŞIYOR.

    MUTASYON: `sa.DateTime(timezone=True)`ı `timezone=False` yapmak bunu
    KIRMIZI yapar. SQLite'ta hiçbir şey değişmez — bu iddia YALNIZ burada
    sorulabilir.
    """
    tipler = {c["name"]: c["type"] for c in inspect(motor).get_columns(KUYRUK)}
    for ad in ("received_at", "processed_at", "locked_until"):
        assert getattr(tipler[ad], "timezone", False) is True, (ad, tipler[ad])
    sayac_tipler = {c["name"]: c["type"] for c in inspect(motor).get_columns(SAYAC)}
    for ad in ("window_start", "updated_at"):
        assert getattr(sayac_tipler[ad], "timezone", False) is True, ad


def test_wamid_TEKILI_gercekten_REDDEDIYOR(motor) -> None:
    """Aynı `wamid` İKİNCİ KEZ yazılamıyor — kısıt ISIRIYOR.

    MUTASYON: göçün `UniqueConstraint("wamid")`ini kaldırmak bunu KIRMIZI
    yapar ve aşağıdaki YARIŞ adımını da düşürür.
    """
    with motor.begin() as baglanti:
        _yaz(baglanti, wamid=KOSU + "-tek")
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _yaz(baglanti, wamid=KOSU + "-tek")


def test_DURUM_kumesi_gercekten_KAPALI(motor) -> None:
    """`ck_whatsapp_inbound_status` tanınmayan durumu REDDEDİYOR.

    MUTASYON: CHECK'i kaldırmak bunu KIRMIZI yapar; o hâlde bir işçi
    hatası "SENT" gibi bir değer yazabilir ve o satırı hiçbir süpürücü
    seçmezdi.
    """
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _yaz(baglanti, wamid=KOSU + "-durum", status="SENT")
    # Beş değerin BEŞİ de kabul ediliyor: kapı kümeyi DARALTMIYOR.
    for i, durum in enumerate(
        ("RECEIVED", "PROCESSING", "ANSWERED", "IGNORED", "DEAD")
    ):
        with motor.begin() as baglanti:
            _yaz(baglanti, wamid="%s-d%d" % (KOSU, i), status=durum)


def test_DENEME_SAYACI_NEGATIF_OLAMAZ(motor) -> None:
    """`attempt_count >= 0` iki tabloda da ISIRIYOR.

    MUTASYON: CHECK'i kaldırmak bunu KIRMIZI yapar; negatif bir sayaç,
    "en fazla N kez dene" kuralını SONSUZ döngüye çevirirdi.
    """
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _yaz(baglanti, wamid=KOSU + "-neg", attempt_count=-1)
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            baglanti.execute(
                text(
                    "INSERT INTO whatsapp_pairing_attempts"
                    "(phone,window_start,attempt_count,updated_at) "
                    "VALUES(:p,:w,:c,:u)"
                ),
                {"p": KOSU + "-1", "w": datetime.now(timezone.utc),
                 "c": -1, "u": datetime.now(timezone.utc)},
            )


def test_PENCERE_TEKILI_gercekten_REDDEDIYOR(motor) -> None:
    """`(phone, window_start)` ikinci kez yazılamıyor.

    MUTASYON: tekili kaldırmak bunu KIRMIZI yapar — ve o hâlde iki
    eşzamanlı deneme İKİ satır üretir, ikisi de "1" sayılır ve pencere
    sınırı HİÇ ısırmazdı.
    """
    pencere = datetime.now(timezone.utc)
    with motor.begin() as baglanti:
        baglanti.execute(
            text(
                "INSERT INTO whatsapp_pairing_attempts"
                "(phone,window_start,attempt_count,updated_at) "
                "VALUES(:p,:w,:c,:u)"
            ),
            {"p": KOSU + "-p", "w": pencere, "c": 1, "u": pencere},
        )
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            baglanti.execute(
                text(
                    "INSERT INTO whatsapp_pairing_attempts"
                    "(phone,window_start,attempt_count,updated_at) "
                    "VALUES(:p,:w,:c,:u)"
                ),
                {"p": KOSU + "-p", "w": pencere, "c": 9, "u": pencere},
            )


# ------------------------------------------------------- GÖÇ TURU --------

def test_GOC_TURU_up_down_up_GERCEK_PostgreSQLde(motor) -> None:
    """0078 PostgreSQL'de de GERİ ALINABİLİYOR — SQLite turu bunu SÖYLEYEMEZ.

    İki diyalektin `DROP TABLE` semantiği AYNI DEĞİL: PostgreSQL indeks ve
    kısıt bağımlılıklarını GERÇEKTEN uygular, SQLite ise çoğunu görmezden
    gelir. `downgrade` SQLite'ta yeşil kalıp burada `DependentObjectsStill
    Exist` ile ölebilirdi.

    SIRA DA ÖLÇÜLÜYOR: `downgrade` ÖNCE sayacı, SONRA kuyruğu düşürüyor ve
    ikisinin de gerçekten düşmesi burada sorulabiliyor.

    MUTASYON: `downgrade`den `op.drop_index` ya da `op.drop_table`
    çağrılarından birini kaldırmak bunu KIRMIZI yapar.

    TUR SONUNDA ŞEMA `head`TE BIRAKILIYOR: KOMŞU dosyalar (CI'da aynı
    konteyneri paylaşan koşularda) şemayı VAR bulmak zorunda.
    """
    yapilandirma = Config(str(BACKEND / "alembic.ini"))
    yapilandirma.set_main_option("sqlalchemy.url", _url())

    def gorunen() -> set[str]:
        return set(inspect(motor).get_table_names())

    assert {KUYRUK, SAYAC} <= gorunen(), "başlangıç: tablolar yok"
    command.downgrade(yapilandirma, "20260909_0077")
    dusen = gorunen()
    assert KUYRUK not in dusen and SAYAC not in dusen, "aşağı: tablolar düşmedi"
    command.upgrade(yapilandirma, "head")
    assert {KUYRUK, SAYAC} <= gorunen(), "ikinci yukarı: tur kapanmadı"
    # İNDEKSLER DE GERİ GELDİ: `drop_table` onları düşürdü, `upgrade` yeniden
    # kurdu.
    assert "ix_whatsapp_inbound_status" in {
        i["name"] for i in inspect(motor).get_indexes(KUYRUK)
    }
    assert "ix_whatsapp_pairing_attempts_pencere" in {
        i["name"] for i in inspect(motor).get_indexes(SAYAC)
    }


# ------------------------------------------------------------- YARIŞ ------

def test_YIRMI_ESZAMANLI_ayni_wamid_TEK_satir_ve_HIC_HATA_YOK(motor) -> None:
    """Yirmi yazıcı AYNI mesajı yazmaya çalışıyor; defterde TEK satır kalıyor.

    Bu dosyanın var oluş sebebi. SQLite'ta üretilemez (tek yazar).

    ÖLÇÜLEN İKİ ŞEY AYRI: (a) satır sayısı TAM BİR — kısıt hakemlik etti;
    (b) HİÇBİR yazıcı istisna SIZDIRMADI — `gelen_kaydet` kaybeden tarafı
    yutuyor. (b) olmasaydı webhook 500 döner, Meta 2xx alamaz ve teslimatı
    TEKRARLARDI, yani yarış kendini besleyen bir döngüye dönerdi.

    MUTASYON: `IntegrityError` yakalamasını kaldırmak (b)'yi, `wamid`
    tekilini düşürmek (a)'yı KIRMIZI yapar.
    """
    import sys

    sys.path.insert(0, str(BACKEND))
    from app.whatsapp.cloud_api import GelenMesaj
    from app.whatsapp.giris import gelen_kaydet

    wamid = KOSU + "-yaris"
    mesaj = GelenMesaj(
        wamid=wamid,
        sender_phone=NUMARA,
        phone_number_id=PNID,
        text="ayni mesaj",
        media_id=None,
        media_mime=None,
    )
    Oturum = sessionmaker(bind=motor)
    kapi = Barrier(20)

    def yaz() -> int:
        kapi.wait(timeout=30)
        with Oturum() as db:
            return gelen_kaydet(db, [mesaj])

    with ThreadPoolExecutor(max_workers=20) as havuz:
        sonuclar = [i.result(timeout=60) for i in [havuz.submit(yaz) for _ in range(20)]]

    # Tam BİR yazıcı satırı yazdığını bildirdi; on dokuzu "0 yeni" dedi.
    assert sum(sonuclar) == 1, sonuclar

    with motor.connect() as baglanti:
        adet = baglanti.execute(
            text("SELECT COUNT(*) FROM whatsapp_inbound WHERE wamid=:w"),
            {"w": wamid},
        ).scalar_one()
    assert adet == 1, adet
