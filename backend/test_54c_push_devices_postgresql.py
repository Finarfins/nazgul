"""PostgreSQL ikizi: 5.4c push cihaz defterinin GERÇEK kısıtları ve YARIŞI.

Göç `20260909_0077`. SQLite ikizi `tests/test_54c_push_devices.py` davranışı
ölçüyor (kayıt, upsert, çapraz firma, sahiplik, logout-all, kanal, pasif
cihaz); bu dosya yalnız ŞEMANIN GERÇEKTEN ISIRAN kısımlarını ve GELİŞTİRME
DİYALEKTİNDE GÖRÜNMEYEN tuzakları ölçer.

--- BU İKİZ NEDEN VAR — DÖRT GEREKÇE, DÖRDÜ DE YALNIZ BURADA GÖRÜNÜR -----

1. **UPSERT YARIŞI, KUSURUN VE ÇARENİN TEK GÖRÜNDÜĞÜ YER.** `push_devices.
   kaydet` ÖNCE `UPDATE`, sonra `INSERT`, `IntegrityError`de YENİDEN `UPDATE`
   yapıyor. Üçüncü adım YALNIZ eşzamanlı iki kayıt isteği varken koşar ve
   SQLite'ta ÜRETİLEMEZ (tek yazar). Adım silinseydi geliştirme diyalektinde
   HİÇBİR ŞEY kırmızı olmaz, üretimde ise aynı anda açılan iki uygulama
   penceresinden biri 500 alırdı. Yirmi eşzamanlı kayıt burada koşuyor ve
   ölçülen şey durum kodları DEĞİL, DEFTERİN KENDİSİDİR: tam BİR satır.

2. **`uq_push_devices_company_token` GERÇEKTEN ISIRIYOR.** Kısıtın VARLIĞI
   şemadan okunabilir; REDDETTİĞİ ancak gerçek katalogda sorulabilir.
   `company_id` tekilin İÇİNDE olduğu için AYNI jeton BAŞKA firmada serbest
   olmalı — o da burada ölçülüyor.

3. **`ck_push_devices_platform` KAPALI KÜMESİ.** SQLite CHECK'i YANSITMIYOR
   (0072'de ölçüldü) ve 0072'de ölçülen kusur — açılış DDL'i nesneyi kurar,
   göç dalı atlar, CHECK HİÇ kurulmaz — tam olarak burada görünür. Küme açık
   olsaydı tanınmayan bir platform SESSİZCE yazılabilir ve o satır HİÇBİR
   adaptörün almadığı ölü bir hedef olurdu.

4. **`is_active` GERÇEK BİR `BOOLEAN`.** SQLite'ta bu sütun tamsayıdır ve
   `is_active=:etkin` karşılaştırması 1/0 ile çalışır; PostgreSQL'de tip
   `boolean`dır ve bağlanan değer tamsayıya düşerse sorgu TİP HATASI verir.
   `_ETKIN_HEDEFLER`in gerçekten süzdüğü ancak burada sorulabilir. Aynı
   şekilde `last_seen_at`/`created_at` `TIMESTAMPTZ` olmalı: saat dilimi
   taşımayan bir sütun, "en son ne zaman görüldü" sorusunu oturumun TZ'sine
   göre yanlış cevaplardı.

--- ÖLÇÜLEN KÖK SEBEP, İDDİA DEĞİL ---------------------------------------

Kısıt testleri kısıtın VARLIĞINI değil GERÇEKTEN REDDETTİĞİNİ ölçüyor: her
biri kısıtı ihlal eden bir yazma deneyip `IntegrityError` bekliyor.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

BACKEND = Path(__file__).resolve().parent

#: DOSYA SEVİYESİNDE İŞARET: bu ikizin TAMAMI gerçek bir PostgreSQL sunucusu
#: ister (`pytest.ini`in `postgresql` işareti).
pytestmark = pytest.mark.postgresql

DEFTER = "push_devices"

#: HER KOŞU KENDİ ÖNEKİNİ KULLANIR. CI'da PostgreSQL ikizleri AYNI
#: veritabanını paylaşıyor; sabit bir jeton kapıyı ilk koşuda yeşil,
#: ikincisinde kırmızı yapardı ve kırmızılığı kusuru DEĞİL koşu sırasını
#: gösterirdi.
KOSU = uuid4().hex[:8]

ADMIN_PW = "Push54cPg!123"


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("5.4c ikizi APP_TEST_DATABASE_URL ister")
    return url


def _acilisa_cek() -> None:
    """Admin şifresini AÇILIŞ DURUMUNA (`admin123` + `must_change_password`) yaz.

    D2/1B-A/1B-B/5.4b ikizlerinden DEVRALINDI ve gerekçesi ölçülmüş bir
    tuzaktır: PostgreSQL ikizleri CI'da AYNI veritabanını paylaşıyor ve her
    biri girişten sonra admin şifresini KENDİ sabitine çeviriyor. Tek yönlü
    bir çare (yalnız teardown) dosyayı iyi bir komşu yapar ama KENDİSİNİ
    korumaz, çünkü şifreyi bozan ÖNCEKİ dosya olabilir.
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


def _temizle(engine) -> None:
    with engine.begin() as baglanti:
        baglanti.execute(
            text("DELETE FROM push_devices WHERE token LIKE :onek"),
            {"onek": KOSU + "%"},
        )
        # SIRA ÖNEMLİ: cihaz satırları `app_users`a yabancı anahtarla bağlı,
        # yani kullanıcı ÖNCE silinemez (5.4b ikizinde ölçüldü: tersi
        # ForeignKeyViolation verdi).
        baglanti.execute(
            text("DELETE FROM app_users WHERE username=:k"),
            {"k": "push54c-" + KOSU},
        )
        baglanti.execute(
            text("DELETE FROM companies WHERE name=:a"),
            {"a": "Push Komsu " + KOSU},
        )


def _acilisi_kostur() -> None:
    """Uygulamanın AÇILIŞINI bir kez koştur: açılış verisi (admin + firma) doğsun.

    ÖLÇÜLDÜ, VARSAYILMADI: `alembic upgrade head` ŞEMAYI kurar ama AÇILIŞ
    VERİSİNİ kurmaz. CI her `*_postgresql*.py` dosyasını TAZE bir şemaya karşı
    koşturuyor ve bu dosyanın `_kimlik()`i `app_users`ta `admin` satırını
    arıyor — açılış koşturulmasaydı dosya TAZE şemada `NoResultFound` ile
    ölürdü ve PAYLAŞIK bir şemada YEŞİL kalırdı.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app):
        pass


@pytest.fixture()
def motor():
    config = Config(str(BACKEND / "alembic.ini"))
    engine = create_engine(_url())
    command.upgrade(config, "head")
    _acilisi_kostur()
    _acilisa_cek()
    _temizle(engine)
    try:
        yield engine
    finally:
        _temizle(engine)
        _acilisa_cek()
        engine.dispose()


def _kimlik(baglanti) -> tuple[int, int]:
    """(company_id, user_id) — açılış admini ve onun varsayılan firması."""
    uid = baglanti.execute(
        text("SELECT id FROM app_users WHERE username='admin'")
    ).scalar_one()
    cid = baglanti.execute(
        text(
            "SELECT company_id FROM user_company_memberships "
            "WHERE user_id=:u ORDER BY is_default DESC, company_id LIMIT 1"
        ),
        {"u": uid},
    ).scalar_one()
    return int(cid), int(uid)


def _satir_yaz(baglanti, **fazla) -> None:
    simdi = datetime.now(timezone.utc)
    cid, uid = _kimlik(baglanti)
    govde = {
        "company_id": cid,
        "user_id": uid,
        "platform": "android",
        "token": KOSU + "-taban",
        "session_family_id": None,
        "last_seen_at": simdi,
        "is_active": True,
        "created_at": simdi,
    }
    govde.update(fazla)
    sutunlar = ",".join(govde)
    yer = ",".join(":" + ad for ad in govde)
    baglanti.execute(
        text("INSERT INTO %s(%s) VALUES(%s)" % (DEFTER, sutunlar, yer)), govde
    )


# ------------------------------------------------------------- ŞEMA -------

def test_JETON_TEKILI_gercekten_REDDEDIYOR(motor) -> None:
    """Aynı (firma, jeton) İKİNCİ KEZ yazılamıyor.

    MUTASYON: göçün `(company_id, token)` tekilini kaldırmak bunu KIRMIZI
    yapar — ve `kaydet`in upsert'ü YENİ SATIR üretmeye başlardı: aynı cihaza
    aynı bildirim İKİ KEZ giderdi.
    """
    with motor.begin() as baglanti:
        _satir_yaz(baglanti)
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _satir_yaz(baglanti, platform="ios")


def test_AYNI_JETON_BASKA_FIRMA_kabul_ediliyor(motor) -> None:
    """Kiracı kapsamı GERÇEK: aynı jeton başka firmada serbest.

    MUTASYON: tekilden `company_id`yi çıkarmak bunu KIRMIZI yapar (ikinci
    satır reddedilirdi) — ve o hâlde aynı telefonu iki firmada kullanan bir
    kişinin ikinci kaydı BİRİNCİYİ ezerdi.
    """
    with motor.begin() as baglanti:
        _satir_yaz(baglanti)
        komsu = baglanti.execute(
            text(
                "INSERT INTO companies (name, is_active, created_at)"
                " VALUES (:a, true, :t) RETURNING id"
            ),
            {"a": "Push Komsu " + KOSU, "t": datetime.now(timezone.utc)},
        ).scalar_one()
        _satir_yaz(baglanti, company_id=int(komsu))
        sayi = baglanti.execute(
            text("SELECT count(*) FROM push_devices WHERE token=:k"),
            {"k": KOSU + "-taban"},
        ).scalar_one()
    assert int(sayi) == 2, sayi


def test_PLATFORM_CHECKI_gercekten_REDDEDIYOR(motor) -> None:
    """`platform` kapalı kümesi ısırıyor; SQLite bunu YANSITMIYOR.

    MUTASYON: göçten `ck_push_devices_platform`u kaldırmak bunu KIRMIZI yapar.
    Küme açık olsaydı tanınmayan bir platform SESSİZCE yazılabilir ve o satır
    HİÇBİR adaptörün almadığı ölü bir hedef olurdu.
    """
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _satir_yaz(baglanti, platform="symbian")


@pytest.mark.parametrize(
    "sutun",
    ["company_id", "user_id", "platform", "token", "last_seen_at",
     "is_active", "created_at"],
)
def test_ZORUNLU_SUTUNLAR_NULL_KABUL_ETMIYOR(motor, sutun: str) -> None:
    """Yedi sütunun yedisi de NOT NULL ve GERÇEKTEN reddediyor.

    MUTASYON: herhangi birini `nullable=True` yapmak bunu KIRMIZI yapar.
    `token` NULL olsaydı hedefi olmayan bir cihaz satırı doğardı; `is_active`
    NULL olsaydı `is_active=:etkin` süzgeci onu NE etkin NE pasif sayardı ve
    satır gönderim yolundan SESSİZCE düşerdi.
    """
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _satir_yaz(baglanti, **{sutun: None})


def test_SESSION_FAMILY_ID_NULL_KABUL_EDIYOR(motor) -> None:
    """Web istemcisinin refresh ailesi OLMAYABİLİR — sütun bunu söylüyor.

    MUTASYON: sütunu `nullable=False` yapmak bunu KIRMIZI yapar ve tarayıcı
    push'u kaydedilemez hâle gelirdi.
    """
    with motor.begin() as baglanti:
        _satir_yaz(baglanti, platform="web", session_family_id=None)
        deger = baglanti.execute(
            text(
                "SELECT session_family_id FROM push_devices WHERE token=:k"
            ),
            {"k": KOSU + "-taban"},
        ).scalar_one_or_none()
    assert deger is None, deger


def test_TIPLER_ve_INDEKS_gercek_katalogda(motor) -> None:
    """`TIMESTAMPTZ`, gerçek `BOOLEAN` ve tarama indeksi GERÇEKTEN var."""
    gozlemci = inspect(motor)
    indeksler = {i["name"] for i in gozlemci.get_indexes(DEFTER)}
    assert "ix_push_devices_company_user" in indeksler, indeksler
    tekiller = {
        u["name"]: tuple(u["column_names"])
        for u in gozlemci.get_unique_constraints(DEFTER)
    }
    assert tekiller.get("uq_push_devices_company_token") == (
        "company_id", "token"
    ), tekiller
    assert tekiller.get("uq_push_devices_company_id") == (
        "company_id", "id"
    ), tekiller
    tipler = {c["name"]: c for c in gozlemci.get_columns(DEFTER)}
    assert tipler["last_seen_at"]["type"].timezone is True, tipler["last_seen_at"]
    assert tipler["created_at"]["type"].timezone is True, tipler["created_at"]
    assert tipler["is_active"]["type"].python_type is bool, tipler["is_active"]
    assert tipler["token"]["type"].length == 512, tipler["token"]


def test_ETKIN_SUZGECI_GERCEK_BOOLEAN_uzerinde_kosuyor(motor) -> None:
    """`is_active=:etkin` PostgreSQL'de TİPLİ bir karşılaştırma.

    MUTASYON: süzgeci `_ETKIN_HEDEFLER`den düşürmek bunu KIRMIZI yapar;
    süzgeci `is_active=1` gibi tamsayı literaliyle yazmak ise SQLite'ta YEŞİL
    kalır ama burada TİP HATASI verirdi.
    """
    from app.db import SessionLocal
    from app.push_devices import etkin_hedefler

    with motor.begin() as baglanti:
        _satir_yaz(baglanti, is_active=True)
        _satir_yaz(baglanti, token=KOSU + "-pasif", is_active=False)
        cid, uid = _kimlik(baglanti)

    with SessionLocal() as db:
        jetonlar = {
            h["token"]
            for h in etkin_hedefler(db, company_id=cid, user_id=uid)
            if str(h["token"]).startswith(KOSU)
        }
    assert jetonlar == {KOSU + "-taban"}, jetonlar


# ------------------------------------------------------------- YARIŞ ------

YARIS_ISTEK = 20


def test_YIRMI_ESZAMANLI_KAYIT_TEK_SATIR(motor) -> None:
    """Yirmi kayıt isteği, aynı jeton: TEK satır ve HİÇ 5xx yok.

    Bu, `kaydet`in ÜÇÜNCÜ adımının (`IntegrityError` -> yeniden `UPDATE`)
    ölçümüdür ve SQLite'ta ÜRETİLEMEZ.

    MUTASYON: o `except IntegrityError` dalını kaldırmak bunu KIRMIZI yapar —
    yirmi istekten en az biri 500 alırdı. Göçün `(company_id, token)` tekilini
    düşürmek de KIRMIZI yapar: bu kez satır sayısı BİRDEN büyük olurdu.

    ÖLÇÜLEN ŞEY YALNIZ DURUM KODLARI DEĞİL, DEFTERİN KENDİSİDİR: tek turluk
    bir kod dizisi yarışın hiç tetiklenmediği anlamına da gelebilirdi.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as istemci:
        for aday in ("admin123", ADMIN_PW):
            giris = istemci.post(
                "/api/auth/login",
                json={"username": "admin", "password": aday},
            )
            if giris.status_code == 200:
                break
        assert giris.status_code == 200, giris.text
        govde = giris.json()
        baslik = {
            "Authorization": "Bearer " + govde["access_token"],
            "X-Company-ID": str(govde["companies"][0]["id"]),
        }
        if aday != ADMIN_PW:
            degis = istemci.post(
                "/api/auth/change-password",
                headers=baslik,
                json={"current_password": aday, "new_password": ADMIN_PW},
            )
            assert degis.status_code == 200, degis.text
            baslik["Authorization"] = "Bearer " + degis.json()["access_token"]

    jeton = KOSU + "-yaris"
    istek = {"platform": "android", "token": jeton}
    engel = Barrier(YARIS_ISTEK)

    def bir_istek() -> int:
        with TestClient(app) as esli:
            engel.wait(timeout=60)
            return esli.post(
                "/api/push/devices", headers=baslik, json=istek
            ).status_code

    with ThreadPoolExecutor(max_workers=YARIS_ISTEK) as havuz:
        sonuclar = [
            is_.result(timeout=180)
            for is_ in [havuz.submit(bir_istek) for _ in range(YARIS_ISTEK)]
        ]

    # HİÇBİRİ 5xx DEĞİL: yarışın kaybedeni de doğru cevabı alıyor.
    assert set(sonuclar) == {201}, sonuclar

    with motor.begin() as baglanti:
        satir = baglanti.execute(
            text("SELECT count(*) FROM push_devices WHERE token=:k"),
            {"k": jeton},
        ).scalar_one()
    assert int(satir) == 1, satir


def test_YARIS_SONRASI_SON_GORULME_ILERLEMIS(motor) -> None:
    """Kaybeden istek de satırı GÜNCELLİYOR — sessizce düşmüyor.

    MUTASYON: `except IntegrityError` dalını `pass` yapmak (yani hatayı yutup
    hiçbir şey yazmamak) bunu KIRMIZI yapar: satır var olurdu ama `last_seen_at`
    ilk yazandan sonra HİÇ ilerlemezdi ve "bu cihaz hâlâ canlı mı" sorusu
    cevapsız kalırdı.
    """
    from app.db import SessionLocal
    from app.push_devices import kaydet

    simdi = datetime.now(timezone.utc)
    with motor.begin() as baglanti:
        _satir_yaz(baglanti, last_seen_at=simdi - timedelta(days=2))
        cid, uid = _kimlik(baglanti)

    with SessionLocal() as db:
        kaydet(
            db,
            company_id=cid,
            user_id=uid,
            platform="ios",
            token=KOSU + "-taban",
        )
        db.commit()

    with motor.begin() as baglanti:
        satir = baglanti.execute(
            text(
                "SELECT platform,last_seen_at,is_active FROM push_devices"
                " WHERE company_id=:c AND token=:k"
            ),
            {"c": cid, "k": KOSU + "-taban"},
        ).mappings().one()
    assert satir["platform"] == "ios", satir
    assert satir["is_active"] is True, satir
    assert satir["last_seen_at"] > simdi - timedelta(days=1), satir
