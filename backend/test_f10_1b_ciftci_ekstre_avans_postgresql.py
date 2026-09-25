"""PostgreSQL ikizi: F10-1b mesaj sayacının ve avans toplamlarının GERÇEĞİ.

Göç `20260920_0091`. SQLite ikizi `tests/test_f10_1b_ciftci_ekstre_avans.py`
akışın DAVRANIŞINI ölçüyor (dağıtım, rıza kapısı, `DUR`, kapsam mesajı,
kiracı yalıtımı); bu dosya yalnız GELİŞTİRME DİYALEKTİNDE GÖRÜNMEYEN
şeyleri ölçer.

--- BU İKİZ NEDEN VAR — BEŞ GEREKÇE, BEŞİ DE YALNIZ BURADA GÖRÜNÜR ------

1. **UPSERT'İN POSTGRESQL DALI SQLite HATTINDA HİÇ KOŞMUYOR.**
   `ciftci_yurutucu.mesaj_deneme_say` lehçeye göre İKİ AYRI gövdeye
   dallanıyor (`sqlalchemy.dialects.postgresql.insert` ve
   `...sqlite.insert`). SQLite hattı yalnız İKİNCİSİNİ koşturur; birincisi
   yanlış yazılmış olsaydı sayaç ÜRETİMDE hiç artmaz ve hız sınırı HİÇ
   ISIRMAZDI — yani kapatılan açık KAPALI GÖRÜNÜRKEN AÇIK kalırdı.

2. **AYNI PENCEREYE AYNI ANDA YAZAN İKİ İŞÇİ.** SQLite'ta bu
   ÜRETİLEMEZ (tek yazar). Sayacın hakemi uygulama sorgusu DEĞİL
   `uq_whatsapp_message_attempts_pencere` kısıtıdır; kısıt düşerse iki
   eşzamanlı mesaj İKİ SATIR üretir ve sınır HİÇ ısırmazdı.

3. **CHECK GERÇEKTEN REDDEDİYOR.** SQLite CHECK'i YANSITMIYOR (0072'de
   ölçüldü). `attempt_count >= 0` burada ihlal denenip `IntegrityError`
   bekleniyor.

4. **`NUMERIC` ARİTMETİĞİ.** `ciftci_avans` `supplier_advances.amount`
   ve `remaining_amount` değerlerini (düzeltme 1'den beri
   `avans_servis`in ORTAK satırlarından) `Decimal` olarak topluyor. SQLite'ta bu
   sütunlar kayan noktaya çözülebiliyor; PostgreSQL'de `NUMERIC`tir ve
   kuruş farkı YALNIZ burada görünür. Keşif §6.3 aynı gerekçeyi kantar
   dilimi için de yazıyor (`_fis_neti`).

5. **ÇOK FİRMALI RIZA AKIŞI GERÇEK PG'DE (runtime lens düzeltme 2).**
   Lens çok firmalı çiftçinin rıza VEREMEDİĞİNİ SQLite'ta VE PG'de
   ölçtü. Düzeltmenin PG'de de tuttuğu burada, dağıtıcının KENDİSİYLE
   (`service._claim` + `service._mesaj_isle`) ölçülüyor: rıza defterinin
   yazma yolu (`set_consent`), `consent_at` damgası ve denetim satırı
   gerçek PG'de koşuyor.

--- KAPSAM DIŞI, BİLEREK -------------------------------------------------

`service.bekleyenleri_isle` burada KOŞTURULMUYOR: paylaşık bir şemada
kuyruğun TAMAMINI işler, yani komşu ikizin satırlarına dokunurdu. Gerekçe
5'in adımı yalnız KENDİ satırını kiralar ve işler. Akışın geri kalanını
(kapsam mesajı, hız sınırı, kiracı yalıtımı) SQLite ikizi ölçüyor.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
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

SAYAC = "whatsapp_message_attempts"
SAYAC_TEKIL = "uq_whatsapp_message_attempts_pencere"
SAYAC_INDEKS = "ix_whatsapp_message_attempts_pencere"

#: Alembic başı — zincirin ucu bu dilimle 0091 oldu.
BAS = "20260920_0091"

#: HER KOŞU KENDİ ÖNEKİNİ KULLANIR (WA2 ikizinin gerekçesi: PG ikizleri
#: paylaşık bir şemada koşabiliyor ve sabit ad kapıyı koşu sırasına
#: bağımlı yapardı).
KOSU = uuid4().hex[:8]

#: Numara da KOŞUYA ÖZGÜ: sayaç PLATFORM tablosudur, yani kiracı önekiyle
#: daraltılamaz — ayrım NUMARANIN KENDİSİNDE olmak zorunda.
#:
#: YALNIZ RAKAM ve ON İKİ HANE. `KOSU` onaltılık olduğu için doğrudan
#: kullanılamaz: `telefon.normalize_phone` rakam DIŞINDAKİ her karakteri
#: ATAR, yani "9053663111ca" saklanırken "9053663111"e düşer ve sorgunun
#: aradığı anahtarla BİR DAHA eşleşmez (bu önce yazıldı, KIRMIZI görüldü,
#: sonra rakama çevrildi). On iki hane `whatsapp_party_links.phone`un
#: kanonik biçimidir.
_SAYI = f"{uuid4().int % 10**8:08d}"
NUMARA = "9053" + _SAYI
IKINCI_NUMARA = "9054" + _SAYI


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("F10-1b ikizi APP_TEST_DATABASE_URL ister")
    return url


def _temizle(engine) -> None:
    """KENDİ satırlarını siler — ÖNEKLE, tabloyu SÜPÜRMEDEN (WA2 kuralı).

    Sayaç satırları NUMARAYLA, ticari satırlar FİRMA ÖNEKİYLE daralır.
    Tabloyu süpürmek, aynı şemayı paylaşan komşu ikizin satırlarını
    silerdi — ve o ikiz KIRMIZI olurdu, bu dosya yüzünden.
    """
    onek = KOSU + "%"
    firma_alt = "(SELECT id FROM companies WHERE name LIKE :o)"
    with engine.begin() as b:
        b.execute(
            text(f"DELETE FROM {SAYAC} WHERE phone IN (:p,:q)"),
            {"p": NUMARA, "q": IKINCI_NUMARA},
        )
        b.execute(
            text("DELETE FROM whatsapp_inbound WHERE sender_phone IN (:p,:q)"),
            {"p": NUMARA, "q": IKINCI_NUMARA},
        )
        for tablo in (
            "whatsapp_party_links",
            "notification_consent_events",
            "notification_consents",
            "supplier_advances",
            "payments",
            "suppliers",
            "customers",
        ):
            b.execute(
                text(f"DELETE FROM {tablo} WHERE company_id IN {firma_alt}"),
                {"o": onek},
            )
        # `activity_logs` YALNIZ-EKLEMEDIR (BEFORE DELETE tetikleyicisi, göç
        # `20260727_0030`): denetim satırı taşıyan firma SİLİNEMEZ. F10-1a
        # ikizinin kuralı AYNEN: öyle bir firma PASİFE alınır, ötekiler
        # silinir. Rıza akışı adımı (gerekçe 5) `party.whatsapp_consent_*`
        # denetim satırı YAZIYOR; firması bu dal ile pasife çekilir.
        b.execute(
            text(
                "UPDATE companies SET is_active = FALSE WHERE name LIKE :o"
                " AND id IN (SELECT company_id FROM activity_logs)"
            ),
            {"o": onek},
        )
        b.execute(
            text(
                "DELETE FROM companies WHERE name LIKE :o"
                " AND id NOT IN (SELECT company_id FROM activity_logs)"
            ),
            {"o": onek},
        )


@pytest.fixture()
def motor():
    yapilandirma = Config(str(BACKEND / "alembic.ini"))
    engine = create_engine(_url())
    command.upgrade(yapilandirma, "head")
    from tests.pg_ikiz_yardimci import acilisa_cek

    acilisa_cek(engine)
    _temizle(engine)
    try:
        yield engine
    finally:
        _temizle(engine)
        acilisa_cek(engine)
        engine.dispose()


@pytest.fixture()
def dunya(motor):
    """BİR firma, BİR tedarikçi. Adlar KOŞU önekli."""
    with motor.begin() as b:
        an = datetime.now(timezone.utc)
        firma = b.execute(
            text(
                "INSERT INTO companies(name,is_active,created_at)"
                " VALUES(:n,TRUE,:t) RETURNING id"
            ),
            {"n": KOSU + "-bir", "t": an},
        ).scalar_one()
        tedarikci = b.execute(
            text(
                "INSERT INTO suppliers(name,is_active,company_id,"
                "opening_balance,risk_limit,payment_term_days)"
                " VALUES(:a,TRUE,:c,0,0,0) RETURNING id"
            ),
            {"a": KOSU + "-tedarikci", "c": firma},
        ).scalar_one()
    return {"firma": int(firma), "tedarikci": int(tedarikci)}


# ---------------------------------------------------------------- şema ---


def test_ALEMBIC_BASI_ve_SAYAC_TABLOSU_GERCEK_PGde(motor) -> None:
    """Baş `0091`; tablo var, `company_id` YOK, tekil ve indeks GERÇEK.

    `company_id`nin YOKLUĞU burada da ölçülüyor çünkü SQLite ikizinin
    ölçtüğü şey GÖÇÜN KAYNAĞIDIR (AST); buradaki ölçtüğü şey ÇALIŞAN
    ŞEMADIR. Göç doğru yazılıp yanlış koşmuş olabilir.
    """
    with motor.connect() as b:
        surumler = [
            s[0] for s in b.execute(text("SELECT version_num FROM alembic_version"))
        ]
    assert surumler == [BAS], surumler

    gozlemci = inspect(motor)
    assert SAYAC in gozlemci.get_table_names()

    sutunlar = {s["name"]: s for s in gozlemci.get_columns(SAYAC)}
    assert "company_id" not in sutunlar, sutunlar
    assert sutunlar["phone"]["nullable"] is False
    assert sutunlar["window_start"]["nullable"] is False
    assert sutunlar["attempt_count"]["nullable"] is False
    # GENİŞLİK BİR SÜS DEĞİL: `whatsapp_party_links.phone` ile AYNI olmak
    # zorunda — sayaç dağıtıcının elindeki değerle DOĞRUDAN anahtarlanıyor.
    # SQLite uzunluğu ZORLAMAZ, ölçüm yalnız burada anlamlı.
    assert sutunlar["phone"]["type"].length == 20

    tekiller = {
        u["name"]: tuple(u["column_names"])
        for u in gozlemci.get_unique_constraints(SAYAC)
    }
    assert tekiller.get(SAYAC_TEKIL) == ("phone", "window_start"), tekiller
    assert SAYAC_INDEKS in {i["name"] for i in gozlemci.get_indexes(SAYAC)}


def test_CHECK_NEGATIF_SAYACI_GERCEKTEN_REDDEDIYOR(motor) -> None:
    """`attempt_count >= 0` PostgreSQL'de ISIRIYOR (SQLite CHECK'i yansıtmaz)."""
    an = datetime.now(timezone.utc)
    with pytest.raises(IntegrityError):
        with motor.begin() as b:
            b.execute(
                text(
                    f"INSERT INTO {SAYAC}(phone,window_start,attempt_count,"
                    "updated_at) VALUES(:p,:w,-1,:t)"
                ),
                {"p": NUMARA, "w": an, "t": an},
            )


def test_TEKIL_AYNI_PENCEREDE_IKINCI_SATIRI_REDDEDIYOR(motor) -> None:
    """Kısıt sayacın HAKEMİDİR; düşerse sınır HİÇ ısırmaz."""
    an = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)
    with motor.begin() as b:
        b.execute(
            text(
                f"INSERT INTO {SAYAC}(phone,window_start,attempt_count,updated_at)"
                " VALUES(:p,:w,1,:t)"
            ),
            {"p": NUMARA, "w": an, "t": an},
        )
    with pytest.raises(IntegrityError):
        with motor.begin() as b:
            b.execute(
                text(
                    f"INSERT INTO {SAYAC}(phone,window_start,attempt_count,"
                    "updated_at) VALUES(:p,:w,1,:t)"
                ),
                {"p": NUMARA, "w": an, "t": an},
            )


# -------------------------------------------------------------- upsert ---


def test_UPSERTIN_POSTGRESQL_DALI_GERCEKTEN_ARTIRIYOR(motor) -> None:
    """`mesaj_deneme_say`ın PG dalı: aynı pencerede TEK satır, artan sayaç.

    SQLite hattı bu gövdeyi HİÇ KOŞTURMUYOR (başlık, gerekçe 1).
    """
    from app.whatsapp import ciftci_yurutucu

    Oturum = sessionmaker(bind=motor)
    an = datetime(2026, 9, 20, 10, 7, tzinfo=timezone.utc)
    with Oturum() as db:
        assert db.bind.dialect.name == "postgresql"
        for beklenen in (1, 2, 3):
            assert ciftci_yurutucu.mesaj_deneme_say(db, NUMARA, simdi=an) == beklenen
        db.commit()

    with motor.connect() as b:
        satirlar = (
            b.execute(
                text(f"SELECT window_start,attempt_count FROM {SAYAC} WHERE phone=:p"),
                {"p": NUMARA},
            )
            .mappings()
            .all()
        )
    assert len(satirlar) == 1, satirlar
    assert int(satirlar[0]["attempt_count"]) == 3
    # PENCERE YUVARLANDI: 10:07 -> 10:00 (15 dakikalık sabit kova).
    assert satirlar[0]["window_start"].minute == 0


def test_PENCERE_DONUNCE_YENI_SATIR_ACILIYOR(motor) -> None:
    """Sabit pencere PG'de de AYRI kova üretiyor: iki satır, ikisi de 1."""
    from app.whatsapp import ciftci_yurutucu

    Oturum = sessionmaker(bind=motor)
    an = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)
    with Oturum() as db:
        assert ciftci_yurutucu.mesaj_deneme_say(db, NUMARA, simdi=an) == 1
        assert (
            ciftci_yurutucu.mesaj_deneme_say(
                db, NUMARA, simdi=an + timedelta(minutes=15)
            )
            == 1
        )
        db.commit()

    with motor.connect() as b:
        adet = b.execute(
            text(f"SELECT COUNT(*) FROM {SAYAC} WHERE phone=:p"), {"p": NUMARA}
        ).scalar_one()
    assert int(adet) == 2


def test_YIRMI_ISCI_AYNI_PENCEREYE_YAZSA_SAYAC_KAYBOLMUYOR(motor) -> None:
    """Yirmi eşzamanlı UPSERT → TEK satır, `attempt_count == 20`.

    SQLite'ta ÜRETİLEMEZ (tek yazar). Sayaç KAYBOLSAYDI sınır bir masraf
    saldırısı altında tam da ısırması gereken anda ısırmazdı.

    KAYBEDEN YOK: `on_conflict_do_update` çakışmayı bir HATAYA değil bir
    ARTIŞA çeviriyor, yani yirmi işçinin YİRMİSİ de sayılmak zorunda.
    """
    from app.whatsapp import ciftci_yurutucu

    Oturum = sessionmaker(bind=motor)
    an = datetime(2026, 9, 20, 11, 0, tzinfo=timezone.utc)
    kapi = Barrier(20)

    def isci() -> int:
        with Oturum() as db:
            kapi.wait(timeout=30)
            sonuc = ciftci_yurutucu.mesaj_deneme_say(db, NUMARA, simdi=an)
            db.commit()
            return sonuc

    with ThreadPoolExecutor(max_workers=20) as havuz:
        sonuclar = [i.result() for i in [havuz.submit(isci) for _ in range(20)]]

    with motor.connect() as b:
        satirlar = (
            b.execute(
                text(f"SELECT attempt_count FROM {SAYAC} WHERE phone=:p"),
                {"p": NUMARA},
            )
            .mappings()
            .all()
        )
    assert len(satirlar) == 1, satirlar
    assert int(satirlar[0]["attempt_count"]) == 20
    # Dönen değerler 1..20'nin bir PERMÜTASYONU: hiçbir işçi AYNI sayıyı
    # iki kez görmedi, yani `RETURNING` gerçekten atomik okudu.
    assert sorted(sonuclar) == list(range(1, 21))


def test_SAYAC_NUMARA_BASINA_GERCEK_PGde(motor) -> None:
    """İki AYRI numara İKİ AYRI kova; tablo `company_id` taşımıyor."""
    from app.whatsapp import ciftci_yurutucu

    Oturum = sessionmaker(bind=motor)
    an = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    with Oturum() as db:
        ciftci_yurutucu.mesaj_deneme_say(db, NUMARA, simdi=an)
        ciftci_yurutucu.mesaj_deneme_say(db, NUMARA, simdi=an)
        assert ciftci_yurutucu.mesaj_deneme_say(db, IKINCI_NUMARA, simdi=an) == 1
        db.commit()

    with motor.connect() as b:
        sayilar = dict(
            b.execute(
                text(
                    f"SELECT phone,attempt_count FROM {SAYAC} WHERE phone IN (:p,:q)"
                ),
                {"p": NUMARA, "q": IKINCI_NUMARA},
            ).all()
        )
    assert sayilar == {NUMARA: 2, IKINCI_NUMARA: 1}, sayilar


# --------------------------------------------------------------- avans ---


def test_AVANS_TOPLAMLARI_NUMERIC_KURUSU_KORUYOR(motor, dunya) -> None:
    """`SUM(NUMERIC)` kuruşu KAYBETMİYOR ve mahsup tam çıkıyor.

    Kuruşlu üç satır BİLEREK seçildi: kayan noktaya çözülen bir toplam
    `0,30`u `0,29999...`a çevirir ve kullanıcıya YANLIŞ kuruş gösterirdi.
    SQLite'ta bu ayrışma GÖRÜNMEZ (başlık, gerekçe 4).
    """
    from app.whatsapp.ciftci_yurutucu import ciftci_avans
    from app.whatsapp.taraf import TarafKimlik

    an = datetime.now(timezone.utc)
    satirlar = [
        ("10000.10", "5000.05", "2026-09-02"),
        ("20000.10", "0.00", "2026-08-11"),
        ("30000.10", "1234.56", "2026-07-03"),
    ]
    with motor.begin() as b:
        for tutar, kalan, gun in satirlar:
            odeme = b.execute(
                text(
                    "INSERT INTO payments(entity_type,entity_id,amount,"
                    "payment_date,payment_method,company_id)"
                    " VALUES('supplier',:e,:a,:d,'cash',:c) RETURNING id"
                ),
                {"e": dunya["tedarikci"], "a": tutar, "d": gun, "c": dunya["firma"]},
            ).scalar_one()
            b.execute(
                text(
                    "INSERT INTO supplier_advances(company_id,supplier_id,"
                    "payment_id,amount,remaining_amount,created_at,updated_at)"
                    " VALUES(:c,:s,:p,:a,:r,:t,:t)"
                ),
                {
                    "c": dunya["firma"],
                    "s": dunya["tedarikci"],
                    "p": odeme,
                    "a": tutar,
                    "r": kalan,
                    "t": an,
                },
            )

    Oturum = sessionmaker(bind=motor)
    with Oturum() as db:
        veri = ciftci_avans(
            db,
            TarafKimlik(
                company_id=dunya["firma"],
                party_type="SUPPLIER",
                party_id=dunya["tedarikci"],
            ),
            {},
        )

    assert veri["adet"] == 3
    assert veri["alinan"] == Decimal("60000.30")
    assert veri["kalan"] == Decimal("6234.61")
    assert veri["alinan"] - veri["kalan"] == Decimal("53765.69")
    # SON AVANS `payments.payment_date`ten gelir — `supplier_advances`in
    # kendi tarih sütunu YOKTUR (`applied_at` MAHSUP anıdır).
    assert str(veri["son"])[:10] == "2026-09-02"


def test_AVANS_KIRACI_YUKLEMI_GERCEK_PGde(motor, dunya) -> None:
    """KOMŞU firmanın avans satırı toplama GİRMİYOR.

    MUTASYON ADIYLA: `avans_servis`in ortak SQL'inden `a.company_id=:cid`i
    düşürmek bunu KIRMIZI yapar (düzeltme 1'e kadar yüklem
    `ciftci_avans`ın kendi SQL kopyasındaydı).
    """
    from app.whatsapp.ciftci_yurutucu import ciftci_avans
    from app.whatsapp.taraf import TarafKimlik

    an = datetime.now(timezone.utc)
    with motor.begin() as b:
        komsu = b.execute(
            text(
                "INSERT INTO companies(name,is_active,created_at)"
                " VALUES(:n,TRUE,:t) RETURNING id"
            ),
            {"n": KOSU + "-komsu", "t": an},
        ).scalar_one()
        komsu_tedarikci = b.execute(
            text(
                "INSERT INTO suppliers(name,is_active,company_id,"
                "opening_balance,risk_limit,payment_term_days)"
                " VALUES(:a,TRUE,:c,0,0,0) RETURNING id"
            ),
            {"a": KOSU + "-komsu-tedarikci", "c": komsu},
        ).scalar_one()
        odeme = b.execute(
            text(
                "INSERT INTO payments(entity_type,entity_id,amount,payment_date,"
                "payment_method,company_id)"
                " VALUES('supplier',:e,:a,:d,'cash',:c) RETURNING id"
            ),
            {"e": komsu_tedarikci, "a": "999999.99", "d": "2026-09-10", "c": komsu},
        ).scalar_one()
        b.execute(
            text(
                "INSERT INTO supplier_advances(company_id,supplier_id,payment_id,"
                "amount,remaining_amount,created_at,updated_at)"
                " VALUES(:c,:s,:p,:a,:r,:t,:t)"
            ),
            {
                "c": komsu,
                "s": komsu_tedarikci,
                "p": odeme,
                "a": "999999.99",
                "r": "999999.99",
                "t": an,
            },
        )

    Oturum = sessionmaker(bind=motor)
    with Oturum() as db:
        veri = ciftci_avans(
            db,
            TarafKimlik(
                company_id=dunya["firma"],
                party_type="SUPPLIER",
                party_id=dunya["tedarikci"],
            ),
            {},
        )
    assert veri["adet"] == 0
    assert veri["alinan"] == Decimal("0")


def test_MUSTERI_TARAFI_AVANS_SORGUSUNU_HIC_KOSTURMUYOR(motor, dunya) -> None:
    """`CUSTOMER` tarafı için SQL KOŞMAZ — nötr sonuç DOĞRUDAN döner.

    `party_id` BİLEREK tedarikçinin kimliğiyle AYNI sayı: keşif §4b'nin
    gerekçesi tam da budur — `customers.id` ile `suppliers.id` AYRI
    sözlüklerdir ve aynı sayıyı taşıyabilirler. Taraf tipi denetimi
    düşerse bu çağrı BAŞKA BİR CARİNİN avansını döndürürdü.
    """
    from app.whatsapp.ciftci_yurutucu import ciftci_avans
    from app.whatsapp.taraf import TarafKimlik

    an = datetime.now(timezone.utc)
    with motor.begin() as b:
        odeme = b.execute(
            text(
                "INSERT INTO payments(entity_type,entity_id,amount,payment_date,"
                "payment_method,company_id)"
                " VALUES('supplier',:e,:a,:d,'cash',:c) RETURNING id"
            ),
            {
                "e": dunya["tedarikci"],
                "a": "5000.00",
                "d": "2026-09-05",
                "c": dunya["firma"],
            },
        ).scalar_one()
        b.execute(
            text(
                "INSERT INTO supplier_advances(company_id,supplier_id,payment_id,"
                "amount,remaining_amount,created_at,updated_at)"
                " VALUES(:c,:s,:p,:a,:r,:t,:t)"
            ),
            {
                "c": dunya["firma"],
                "s": dunya["tedarikci"],
                "p": odeme,
                "a": "5000.00",
                "r": "5000.00",
                "t": an,
            },
        )

    Oturum = sessionmaker(bind=motor)
    with Oturum() as db:
        veri = ciftci_avans(
            db,
            TarafKimlik(
                company_id=dunya["firma"],
                party_type="CUSTOMER",
                party_id=dunya["tedarikci"],
            ),
            {},
        )
    assert veri["adet"] == 0
    assert veri["alinan"] == Decimal("0")
    assert veri["son"] is None


def test_AVANS_TOPLAMLARI_UCUN_KENDI_JSONUYLA_AYNI_GERCEK_PGde(
    motor, dunya, monkeypatch
) -> None:
    """Çiftçi toplamları = ucun KENDİ döndürdüğü satırların toplamı (düzeltme 1).

    SQLite ikizi aynı eşitliği HTTP üzerinden ölçüyor; burada uç
    fonksiyonu (`list_supplier_advances`) DOĞRUDAN çağrılıyor çünkü bu
    dosyanın firması KOŞU önekli ve açılış yöneticisinin firması DEĞİL —
    `X-Company-ID` onu 403'le reddederdi. Uç fonksiyonun döndürdüğü sözlük
    FastAPI'nin JSON'a çevirdiği nesnenin KENDİSİDİR (`_tutar` sabit
    ölçekli METİN üretir), yani karşılaştırılan şey ucun cevabıdır.

    Sayfa boyu 2'ye indiriliyor ki üç kuruşlu satır İKİ sayfaya düşsün:
    `LIMIT/OFFSET`in gerçek PG'deki birleşimi de ölçülmüş olur.
    """
    from types import SimpleNamespace

    from app import avans_servis
    from app.routers.avans import list_supplier_advances
    from app.whatsapp.ciftci_yurutucu import ciftci_avans
    from app.whatsapp.taraf import TarafKimlik

    an = datetime.now(timezone.utc)
    with motor.begin() as b:
        for tutar, kalan, gun in (
            ("10000.10", "5000.05", "2026-09-02"),
            ("20000.10", "0.00", "2026-08-11"),
            ("30000.10", "1234.56", "2026-07-03"),
        ):
            odeme = b.execute(
                text(
                    "INSERT INTO payments(entity_type,entity_id,amount,"
                    "payment_date,payment_method,company_id)"
                    " VALUES('supplier',:e,:a,:d,'cash',:c) RETURNING id"
                ),
                {"e": dunya["tedarikci"], "a": tutar, "d": gun, "c": dunya["firma"]},
            ).scalar_one()
            b.execute(
                text(
                    "INSERT INTO supplier_advances(company_id,supplier_id,"
                    "payment_id,amount,remaining_amount,created_at,updated_at)"
                    " VALUES(:c,:s,:p,:a,:r,:t,:t)"
                ),
                {
                    "c": dunya["firma"],
                    "s": dunya["tedarikci"],
                    "p": odeme,
                    "a": tutar,
                    "r": kalan,
                    "t": an,
                },
            )

    monkeypatch.setattr(avans_servis, "SAYFA_UST_SINIRI", 2)
    Oturum = sessionmaker(bind=motor)
    with Oturum() as db:
        uc = list_supplier_advances(
            SimpleNamespace(state=SimpleNamespace(company_id=dunya["firma"])),
            dunya["tedarikci"],
            open_only=False,
            limit=200,
            offset=0,
            db=db,
        )
        veri = ciftci_avans(
            db,
            TarafKimlik(
                company_id=dunya["firma"],
                party_type="SUPPLIER",
                party_id=dunya["tedarikci"],
            ),
            {},
        )

    satirlar = uc["items"]
    assert len(satirlar) == 3
    assert veri["adet"] == len(satirlar)
    assert veri["alinan"] == sum(Decimal(s["amount"]) for s in satirlar)
    assert veri["kalan"] == sum(Decimal(s["remaining_amount"]) for s in satirlar)
    assert veri["alinan"] == Decimal("60000.30")
    assert veri["kalan"] == Decimal("6234.61")


# ------------------------------------------------- çok firmalı rıza ---


def test_COK_FIRMALI_RIZA_AKISI_DAGITICIDAN_GERCEK_PGde(motor, monkeypatch) -> None:
    """Lens dizisi dağıtıcıdan, gerçek PG'de: önekli rıza firma BAŞINA açılır/kapanır.

    Dizi: EKSTRE / EVET / 1 EVET / 1 evet / 1. EVET, sonra "2 EVET",
    "1 HAYIR" (AÇIK rızayı kapatır: REVOKED v2, 2. firma GRANTED KALIR;
    runtime lens tur 2), tekrar "1 HAYIR" (v2 kalır), "1 EVET" (v3),
    "2 HAYIR" ve `DUR`. Düzeltme 1'den önce ilk beş adım SIFIR rıza
    satırıyla bitiyordu (runtime lens NO-GO, tur 1 — PG'de de ölçüldü).

    Yönetici listesinin `consent_at`i burada UTC tel biçimiyle ölçülür:
    PG `TIMESTAMPTZ`yi OTURUM diliminde döndürür (`+03:00`); `utc_iso`dan
    geçmeseydi sonek `+00:00` OLMAZDI (H73, `app/zaman.py`). İzin kapısı
    (`_taraf_izni`) bu adımda DEVRE DIŞIDIR — ölçülen şey serileştirmedir;
    kapının kendisini SQLite ikizi gerçek HTTP ile ölçüyor.

    Yalnız KENDİ satırı kiralanır (`_claim`) ve işlenir (`_mesaj_isle`):
    paylaşık şemada komşunun kuyruğuna dokunulmaz (başlık, kapsam dışı).
    """
    from app.config import settings
    from app.whatsapp import service

    class _Sahte:
        def __init__(self) -> None:
            self.gonderilenler: list[str] = []

        def metin_gonder(self, alici: str, metin: str) -> None:
            self.gonderilenler.append(metin)

    an = datetime.now(timezone.utc)
    with motor.begin() as b:

        def firma(ek: str) -> int:
            return int(
                b.execute(
                    text(
                        "INSERT INTO companies(name,is_active,created_at)"
                        " VALUES(:n,TRUE,:t) RETURNING id"
                    ),
                    {"n": KOSU + ek, "t": an},
                ).scalar_one()
            )

        def tedarikci(cid: int, acilis: int) -> int:
            return int(
                b.execute(
                    text(
                        "INSERT INTO suppliers(name,is_active,company_id,"
                        "opening_balance,risk_limit,payment_term_days)"
                        " VALUES(:a,TRUE,:c,:o,0,0) RETURNING id"
                    ),
                    {"a": KOSU + "-ciftci", "c": cid, "o": acilis},
                ).scalar_one()
            )

        def baglanti(cid: int, sid: int) -> int:
            return int(
                b.execute(
                    text(
                        "INSERT INTO whatsapp_party_links(company_id,party_type,"
                        "party_id,phone,is_active,created_at,updated_at)"
                        " VALUES(:c,'SUPPLIER',:s,:p,TRUE,:t,:t) RETURNING id"
                    ),
                    {"c": cid, "s": sid, "p": NUMARA, "t": an},
                ).scalar_one()
            )

        firma_1, firma_2 = firma("-rz1"), firma("-rz2")
        ted_1, ted_2 = tedarikci(firma_1, 10000), tedarikci(firma_2, 77000)
        link_1, link_2 = baglanti(firma_1, ted_1), baglanti(firma_2, ted_2)

    Oturum = sessionmaker(bind=motor)
    saglayici = _Sahte()

    def konus(metin: str) -> str:
        with Oturum() as db:
            satir = db.execute(
                text(
                    "INSERT INTO whatsapp_inbound(wamid,sender_phone,"
                    "phone_number_id,text,status,attempt_count,received_at)"
                    " VALUES(:w,:p,:n,:m,'RECEIVED',0,:t) RETURNING id"
                ),
                {
                    "w": f"{KOSU}-{uuid4().hex}",
                    "p": NUMARA,
                    "n": settings.whatsapp_phone_number_id or "",
                    "m": metin,
                    "t": datetime.now(timezone.utc),
                },
            ).scalar_one()
            db.commit()
            jeton = service._claim(db, int(satir))
            assert jeton is not None
            once = len(saglayici.gonderilenler)
            service._mesaj_isle(db, int(satir), jeton, saglayici)
            assert len(saglayici.gonderilenler) == once + 1, metin
            return saglayici.gonderilenler[-1]

    def durum(cid: int, sid: int):
        with motor.connect() as b:
            return b.execute(
                text(
                    "SELECT status FROM notification_consents WHERE company_id=:c"
                    " AND party_type='SUPPLIER' AND party_id=:s"
                    " AND channel='WHATSAPP'"
                ),
                {"c": cid, "s": sid},
            ).scalar_one_or_none()

    def damga(link: int):
        with motor.connect() as b:
            return b.execute(
                text("SELECT consent_at FROM whatsapp_party_links WHERE id=:i"),
                {"i": link},
            ).scalar_one()

    def surum(cid: int, sid: int) -> int:
        with motor.connect() as b:
            return int(
                b.execute(
                    text(
                        "SELECT version FROM notification_consents WHERE company_id=:c"
                        " AND party_type='SUPPLIER' AND party_id=:s"
                        " AND channel='WHATSAPP'"
                    ),
                    {"c": cid, "s": sid},
                ).scalar_one()
            )

    konus("EKSTRE")
    assert '"1 EVET"' in konus("EVET")
    assert durum(firma_1, ted_1) is None and durum(firma_2, ted_2) is None

    assert "Onayınız alındı" in konus("1 EVET")
    for tekrar in ("1 evet", "1. EVET"):
        konus(tekrar)
    assert durum(firma_1, ted_1) == "GRANTED" and surum(firma_1, ted_1) == 1
    assert durum(firma_2, ted_2) is None
    assert damga(link_1) is not None and damga(link_2) is None

    assert "10.000,00" in konus("1 EKSTRE")
    kvkk = konus("2 EKSTRE")
    assert kvkk.endswith("2 EVET / 2 HAYIR"), kvkk
    assert "77.000,00" not in kvkk

    konus("2 EVET")
    assert durum(firma_2, ted_2) == "GRANTED"

    # "1 HAYIR" AÇIK rızayı KAPATIR — YALNIZ 1. firmada (runtime lens tur 2).
    damga_1 = damga(link_1)
    assert "göndermeyeceğiz" in konus("1 HAYIR")
    assert durum(firma_1, ted_1) == "REVOKED" and surum(firma_1, ted_1) == 2
    assert durum(firma_2, ted_2) == "GRANTED"
    assert damga(link_1) == damga_1, "consent_at iz olarak KALIR"
    assert "10.000,00" not in konus("1 EKSTRE")
    assert "77.000,00" in konus("2 EKSTRE")

    konus("1 HAYIR")
    assert surum(firma_1, ted_1) == 2, "REVOKED deftere ikinci yazim YOK"
    assert "Onayınız alındı" in konus("1 EVET")
    assert durum(firma_1, ted_1) == "GRANTED" and surum(firma_1, ted_1) == 3
    assert "10.000,00" in konus("1 EKSTRE")

    # YÖNETİCİ LİSTESİ: `consent_at` gerçek PG'de UTC `+00:00` tel biçimiyle.
    from types import SimpleNamespace

    from app.routers import whatsapp as wa_router

    monkeypatch.setattr(wa_router, "_taraf_izni", lambda request, party_type: None)
    istek = SimpleNamespace(state=SimpleNamespace(company_id=firma_1))
    with Oturum() as db:
        liste = wa_router.taraf_baglantilarini_listele(
            istek, party_type="SUPPLIER", party_id=ted_1, db=db
        )
    assert [s["id"] for s in liste] == [link_1]
    tel = liste[0]["consent_at"]
    assert isinstance(tel, str) and tel.endswith("+00:00"), tel
    assert datetime.fromisoformat(tel) == damga(link_1)

    # Dizi 15 mesajı doldurdu: 16. mesaj hız sınırıyla İŞLENİR ama
    # CEVAPLANMAZ (`MESAJ_CEVAP_SINIRI`). Kalan adımlar sınırı değil rızayı
    # ölçüyor; sayaç KENDİ numarasıyla sıfırlanır (`_temizle`in kuralı).
    with motor.begin() as b:
        b.execute(text(f"DELETE FROM {SAYAC} WHERE phone=:p"), {"p": NUMARA})

    konus("2 HAYIR")
    assert durum(firma_2, ted_2) == "REVOKED"
    assert durum(firma_1, ted_1) == "GRANTED"

    assert "kapatıldı" in konus("DUR")
    assert durum(firma_1, ted_1) == "REVOKED"
    assert durum(firma_2, ted_2) == "REVOKED"
    with motor.connect() as b:
        acik = b.execute(
            text(
                "SELECT COUNT(*) FROM whatsapp_party_links"
                " WHERE id IN (:a,:b) AND is_active"
            ),
            {"a": link_1, "b": link_2},
        ).scalar_one()
    assert int(acik) == 0
    # DUR damgayı SİLMEZ (tarihçe).
    assert damga(link_1) is not None
