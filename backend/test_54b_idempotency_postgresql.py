"""PostgreSQL ikizi: 5.4b idempotensi defterinin GERÇEK kısıtları ve YARIŞI.

Göç `20260909_0076`. SQLite ikizi `tests/test_54b_idempotency.py` davranışı
ölçüyor (tekrar oynatma, 422, atlanan uçlar, süresi dolan anahtar, 5xx);
bu dosya yalnız ŞEMANIN GERÇEKTEN ISIRAN kısımlarını ve GELİŞTİRME
DİYALEKTİNDE GÖRÜNMEYEN tuzakları ölçer.

--- BU İKİZ NEDEN VAR — DÖRT GEREKÇE, DÖRDÜ DE YALNIZ BURADA GÖRÜNÜR -----

1. **YARIŞ, KUSURUN VE ÇARENİN TEK GÖRÜNDÜĞÜ YER.** Bu modülün TAMAMI bir
   eşzamanlılık savunmasıdır: iddia bir INSERT'tür ve yarışı
   `uq_idempotency_keys_company_user_key` çözer. SQLite'ta bu ÜRETİLEMEZ
   (tek yazar), yani hem kusur hem de korumasının kaldırılması geliştirme
   diyalektinde GÖRÜNMEZ. Yirmi eşzamanlı istek burada koşuyor ve ölçülen
   şey durum kodları DEĞİL, DEFTERİN KENDİSİDİR: tam BİR satır ve tam BİR
   yan etki.

2. **ÜÇ SÜTUNLU TEKİLİN GERÇEKTEN ISIRMASI.** `(company_id, user_id, key)`
   kısıtı anahtarın kapsamıdır; iki sütuna indirilseydi aynı firmadaki iki
   kullanıcının çakışan anahtarı birbirinin isteğini yutardı. Kısıtın VARLIĞI
   şemadan okunabilir; REDDETTİĞİ ancak gerçek katalogda sorulabilir.

3. **`ck_idempotency_keys_status` KAPALI KÜMESİ.** SQLite CHECK'i
   YANSITMIYOR (0072'de ölçüldü) ve 0072'de ölçülen kusur — açılış DDL'i
   nesneyi kurar, göç dalı atlar, CHECK HİÇ kurulmaz — tam olarak burada
   görünür. Küme açık olsaydı `failed` gibi üçüncü bir durum SESSİZCE
   yazılabilir ve tekrar oynatma yolu onu `completed` sanmazdı: satır
   `processing` da olmadığı için anahtar SONSUZA KADAR 409 verirdi.

4. **`TIMESTAMPTZ` KARŞILAŞTIRMASI.** `expires_at <= :now` süzgeci iki
   diyalektte İKİ FARKLI şey yapar: SQLite'ta metin karşılaştırmasıdır,
   PostgreSQL'de gerçek bir zaman karşılaştırması. Bağlama tipi (`DateTime`)
   düşerse SQLite yine yeşil kalır (dizgeler sıralanabilir) ama PostgreSQL
   TİP HATASI verir ya da daha kötüsü, oturumun saat dilimine göre YANLIŞ
   satırı süresi dolmuş sayardı.

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
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

BACKEND = Path(__file__).resolve().parent

#: DOSYA SEVİYESİNDE İŞARET: bu ikizin TAMAMI gerçek bir PostgreSQL sunucusu
#: ister (`pytest.ini`in `postgresql` işareti).
pytestmark = pytest.mark.postgresql

DEFTER = "idempotency_keys"

#: HER KOŞU KENDİ ÖNEKİNİ KULLANIR. CI'da PostgreSQL ikizleri AYNI
#: veritabanını paylaşıyor; sabit bir anahtar kapıyı ilk koşuda yeşil,
#: ikincisinde kırmızı yapardı ve kırmızılığı kusuru DEĞİL koşu sırasını
#: gösterirdi.
KOSU = uuid4().hex[:8]

ADMIN_PW = "Idem54bPg!123"


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("5.4b ikizi APP_TEST_DATABASE_URL ister")
    return url


def _acilisa_cek() -> None:
    """Admin şifresini AÇILIŞ DURUMUNA (`admin123` + `must_change_password`) yaz.

    D2/1B-A/1B-B ikizlerinden DEVRALINDI ve gerekçesi ölçülmüş bir tuzaktır:
    PostgreSQL ikizleri CI'da AYNI veritabanını paylaşıyor ve her biri
    girişten sonra admin şifresini KENDİ sabitine çeviriyor. Tek yönlü bir
    çare (yalnız teardown) dosyayı iyi bir komşu yapar ama KENDİSİNİ korumaz,
    çünkü şifreyi bozan ÖNCEKİ dosya olabilir.
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
            text("DELETE FROM idempotency_keys WHERE key LIKE :onek"),
            {"onek": KOSU + "%"},
        )
        baglanti.execute(
            text("DELETE FROM customers WHERE phone LIKE :onek"),
            {"onek": "54B" + KOSU + "%"},
        )
        # SIRA ÖNEMLİ: defter satırları `app_users`a yabancı anahtarla bağlı,
        # yani kullanıcı ÖNCE silinemez. Silme yukarıdaki defter temizliğinden
        # SONRA geliyor ve bu sıra ölçüldü — tersi ForeignKeyViolation verdi.
        baglanti.execute(
            text("DELETE FROM app_users WHERE username=:k"),
            {"k": "idem54b-" + KOSU},
        )


def _acilisi_kostur() -> None:
    """Uygulamanın AÇILIŞINI bir kez koştur: açılış verisi (admin + firma) doğsun.

    ÖLÇÜLDÜ, VARSAYILMADI: `alembic upgrade head` ŞEMAYI kurar ama AÇILIŞ
    VERİSİNİ kurmaz. CI her `*_postgresql*.py` dosyasını TAZE bir şemaya karşı
    koşturuyor ve bu dosyanın `_kimlik()`i `app_users`ta `admin` satırını
    arıyor — açılış koşturulmasaydı dosya TAZE şemada `NoResultFound` ile
    ölürdü ve PAYLAŞIK bir şemada (önceki dosyanın açılışını devralarak)
    YEŞİL kalırdı. Kusur tam olarak öyle bulundu: 5432'deki paylaşık sunucuda
    15 test yeşildi, 5433'teki taze şemada 13'ü kırmızı oldu.
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
        "key": KOSU + "-taban",
        "method": "POST",
        "route": "/api/x",
        "request_hash": "a" * 64,
        "status": "processing",
        "response_status": None,
        "response_body": None,
        "created_at": simdi,
        "completed_at": None,
        "expires_at": simdi + timedelta(hours=24),
    }
    govde.update(fazla)
    sutunlar = ",".join(govde)
    yer = ",".join(":" + ad for ad in govde)
    baglanti.execute(
        text("INSERT INTO %s(%s) VALUES(%s)" % (DEFTER, sutunlar, yer)), govde
    )


# ------------------------------------------------------------- ŞEMA -------

def test_UC_SUTUNLU_TEKIL_gercekten_REDDEDIYOR(motor) -> None:
    """Aynı (firma, kullanıcı, anahtar) İKİNCİ KEZ yazılamıyor.

    MUTASYON: göçün tekilini `(company_id, key)`ye indirmek ya da tamamen
    kaldırmak bunu KIRMIZI yapar — ve ara katmanın EŞZAMANLILIK SINIRI yok
    olurdu: yarışı çözen şey tam olarak bu kısıttır.
    """
    with motor.begin() as baglanti:
        _satir_yaz(baglanti)
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _satir_yaz(baglanti, request_hash="b" * 64)


def test_AYNI_ANAHTAR_BASKA_KULLANICI_kabul_ediliyor(motor) -> None:
    """Kullanıcı kapsamı GERÇEK: aynı anahtar başka kullanıcıda serbest.

    MUTASYON: tekile `user_id`yi eklememek bunu KIRMIZI yapar (ikinci satır
    reddedilirdi) — ve o hâlde aynı firmadaki iki kullanıcının çakışan
    anahtarı birbirinin isteğini yutardı.
    """
    with motor.begin() as baglanti:
        _satir_yaz(baglanti)
        ikinci = baglanti.execute(
            text(
                "INSERT INTO app_users(username,email,display_name,password_hash,"
                "role,is_active,must_change_password,created_at)"
                " VALUES(:k,:e,:d,'x','depo',true,false,:t) RETURNING id"
            ),
            {
                "k": "idem54b-" + KOSU,
                "e": "idem54b-" + KOSU + "@ornek.test",
                "d": "Idem PG",
                "t": datetime.now(timezone.utc),
            },
        ).scalar_one()
        _satir_yaz(baglanti, user_id=int(ikinci))
        sayi = baglanti.execute(
            text("SELECT count(*) FROM idempotency_keys WHERE key=:k"),
            {"k": KOSU + "-taban"},
        ).scalar_one()
    assert int(sayi) == 2, sayi


def test_DURUM_CHECKI_gercekten_REDDEDIYOR(motor) -> None:
    """`status` kapalı kümesi ısırıyor; SQLite bunu YANSITMIYOR.

    MUTASYON: göçten `ck_idempotency_keys_status`u kaldırmak bunu KIRMIZI
    yapar. Küme açık olsaydı üçüncü bir durum SESSİZCE yazılabilir ve o
    anahtar SONSUZA KADAR 409 dönerdi (ne `completed` ne `processing`).
    """
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _satir_yaz(baglanti, status="failed")


@pytest.mark.parametrize(
    "sutun", ["company_id", "user_id", "key", "method", "route",
              "request_hash", "status", "created_at", "expires_at"]
)
def test_ZORUNLU_SUTUNLAR_NULL_KABUL_ETMIYOR(motor, sutun: str) -> None:
    """Dokuz sütunun dokuzu da NOT NULL ve GERÇEKTEN reddediyor.

    MUTASYON: herhangi birini `nullable=True` yapmak bunu KIRMIZI yapar.
    `expires_at` NULL olsaydı o anahtar HİÇ süresi dolmayan bir satır olurdu;
    `request_hash` NULL olsaydı "aynı istek mi" sorusu cevapsız kalırdı.
    """
    with pytest.raises(IntegrityError):
        with motor.begin() as baglanti:
            _satir_yaz(baglanti, **{sutun: None})


def test_SURE_INDEKSI_ve_FIRMA_KIMLIK_TEKILI_semada(motor) -> None:
    """Süpürgenin tarama yolu ve bileşik anahtar hedefi GERÇEKTEN var."""
    from sqlalchemy import inspect

    gozlemci = inspect(motor)
    indeksler = {i["name"] for i in gozlemci.get_indexes(DEFTER)}
    assert "ix_idempotency_keys_expires_at" in indeksler, indeksler
    tekiller = {
        u["name"]: tuple(u["column_names"])
        for u in gozlemci.get_unique_constraints(DEFTER)
    }
    assert tekiller.get("uq_idempotency_keys_company_user_key") == (
        "company_id", "user_id", "key"
    ), tekiller
    assert tekiller.get("uq_idempotency_keys_company_id") == (
        "company_id", "id"
    ), tekiller
    tipler = {c["name"]: c for c in gozlemci.get_columns(DEFTER)}
    # TIMESTAMPTZ: `expires_at <= :now` karşılaştırması gerçek bir ZAMAN
    # karşılaştırmasıdır ve saat dilimi taşımayan bir sütun oturumun TZ'sine
    # göre YANLIŞ satırı süresi dolmuş sayardı.
    assert tipler["expires_at"]["type"].timezone is True, tipler["expires_at"]
    assert tipler["created_at"]["type"].timezone is True, tipler["created_at"]


def test_SURESI_DOLMUS_SATIR_gercek_ZAMAN_karsilastirmasiyla_bulunuyor(motor) -> None:
    """`expires_at <= :now` PostgreSQL'de TİPLİ karşılaştırma.

    MUTASYON: `app/idempotency.py`de `bindparams(... DateTime ...)`ı kaldırmak
    bunu KIRMIZI yapar; SQLite'ta aynı mutasyon YEŞİL kalırdı çünkü orada iki
    taraf da dizgedir.
    """
    from app.idempotency import _SURESI_DOLANI_SIL

    simdi = datetime.now(timezone.utc)
    with motor.begin() as baglanti:
        _satir_yaz(baglanti, expires_at=simdi - timedelta(seconds=1))
        cid, uid = _kimlik(baglanti)
        silinen = baglanti.execute(
            _SURESI_DOLANI_SIL,
            {"cid": cid, "uid": uid, "key": KOSU + "-taban", "now": simdi},
        ).rowcount
    assert silinen == 1, silinen

    # SÜRESİ DOLMAMIŞ SATIR DOKUNULMADAN KALIYOR — süzgecin gerçekten
    # süzdüğü ancak bu ikinci yarıda görünür.
    with motor.begin() as baglanti:
        _satir_yaz(baglanti, expires_at=simdi + timedelta(hours=1))
        cid, uid = _kimlik(baglanti)
        silinen = baglanti.execute(
            _SURESI_DOLANI_SIL,
            {"cid": cid, "uid": uid, "key": KOSU + "-taban", "now": simdi},
        ).rowcount
    assert silinen == 0, silinen


# ------------------------------------------------------------- YARIŞ ------

YARIS_ISTEK = 20


def test_YIRMI_ESZAMANLI_ISTEK_TEK_SATIR_TEK_YAN_ETKI(motor) -> None:
    """Yirmi istek, aynı anahtar: TEK 201 taze, TEK satır, TEK müşteri.

    Bu, modülün VAR OLMA SEBEBİNİN ölçümüdür ve SQLite'ta ÜRETİLEMEZ.

    MUTASYON: iddiayı INSERT yerine "önce SELECT sonra INSERT" hâline
    getirmek (yani yarışı veritabanına bırakmamak) bunu KIRMIZI yapar —
    yirmi istekten birden fazlası 201 alır ve müşteri İKİ KEZ yazılırdı.
    Aynı şekilde göçün üç sütunlu tekilini düşürmek de KIRMIZI yapar.

    ÖLÇÜLEN ŞEY DURUM KODLARI DEĞİL, DEFTERİN KENDİSİDİR: tek turluk bir
    kod dizisi yarışın hiç tetiklenmediği anlamına da gelebilirdi, o yüzden
    hem satır sayısı hem yan etki sayısı ayrıca soruluyor.
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

    anahtar = KOSU + "-yaris"
    telefon = "54B" + KOSU + "01"
    istek = {"name": "Idempotensi Yarisi " + KOSU, "phone": telefon}
    engel = Barrier(YARIS_ISTEK)

    def bir_istek() -> tuple[int, str]:
        with TestClient(app) as esli:
            engel.wait(timeout=60)
            cevap = esli.post(
                "/api/customers",
                headers={**baslik, "Idempotency-Key": anahtar},
                json=istek,
            )
            return cevap.status_code, cevap.headers.get("Idempotent-Replayed", "")

    with ThreadPoolExecutor(max_workers=YARIS_ISTEK) as havuz:
        sonuclar = [
            is_.result(timeout=180)
            for is_ in [havuz.submit(bir_istek) for _ in range(YARIS_ISTEK)]
        ]

    taze = [s for s in sonuclar if s == (201, "")]
    assert len(taze) == 1, sonuclar
    # Kalan on dokuz ya "hâlâ işleniyor" (409) ya da tekrar oynatma (201 +
    # başlık). ÜÇÜNCÜ bir sonuç YOK.
    for durum, tekrar in sonuclar:
        assert (durum, tekrar) in {(201, ""), (201, "true"), (409, "")}, sonuclar

    with motor.begin() as baglanti:
        satir = baglanti.execute(
            text(
                "SELECT count(*) FROM idempotency_keys WHERE key=:k"
            ),
            {"k": anahtar},
        ).scalar_one()
        musteri = baglanti.execute(
            text("SELECT count(*) FROM customers WHERE phone=:t"),
            {"t": telefon},
        ).scalar_one()
    assert int(satir) == 1, satir
    assert int(musteri) == 1, musteri
