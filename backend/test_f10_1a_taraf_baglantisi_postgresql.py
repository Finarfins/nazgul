"""PostgreSQL ikizi: F10-1a taraf bağlantı defterinin GERÇEK kısıtları.

Göç `20260918_0090`. SQLite ikizi `tests/test_f10_1a_taraf_baglantisi.py`
akışın davranışını ölçüyor (kod üret → tüket, rıza, izin matrisi, hız
sınırı); bu dosya yalnız ŞEMANIN GERÇEKTEN ISIRAN kısımlarını ve
GELİŞTİRME DİYALEKTİNDE GÖRÜNMEYEN tuzakları ölçer.

--- BU İKİZ NEDEN VAR — DÖRT GEREKÇE, DÖRDÜ DE YALNIZ BURADA GÖRÜNÜR ----

1. **KISMİ TEKİLİN `WHERE` YÜKLEMİ İKİ DİYALEKTTE AYRI ÜRETİLİYOR.** Göç
   `is_active = 1` (SQLite) ve `is_active = true` (PostgreSQL) yazıyor.
   SQLite hattı yalnız BİRİNCİSİNİ koşturur; ikincisi yanlış yazılmış
   olsaydı indeks PostgreSQL'de HİÇ KURULMAZ ve aynı numara aynı firmada
   iki kez aktif bağlanabilirdi — yani kural ÜRETİMDE hiçbir şeyi
   reddetmezdi. Burada hem RED hem İZİN ölçülüyor.

2. **CHECK'LER GERÇEKTEN REDDEDİYOR.** SQLite CHECK'i YANSITMIYOR
   (0072'de ölçüldü). `party_type` sözlüğü, boş `target_phone` ve durum ×
   zaman damgası matrisi burada ihlal denenip `IntegrityError` bekleniyor.

3. **BİLEŞİK YABANCI ANAHTAR GERÇEKTEN BAĞLIYOR.** SQLite yabancı
   anahtarları varsayılan olarak UYGULAMAZ. Bir firmanın kodunun BAŞKA
   firmanın bağlantısını tükettiğini iddia etmesi burada REDDEDİLİYOR.

4. **AYNI KODU İKİ İŞÇİ AYNI ANDA KULLANIRSA TAM BİRİ KAZANIR.** SQLite'ta
   bu ÜRETİLEMEZ (tek yazar). `kod_kullan` bu değişmezi ÜÇ bağımsız
   katmanla koruyor (satır kilidi, okuma sonrası durum denetimi, CAS) ve
   HERHANGİ BİRİ TEK BAŞINA yetiyor — yani bu adım ÜÇÜNÜN BİRDEN kaybını
   yakalar; katmanların tek tek çivilenmesi AST kapılarının işidir
   (`tests/test_f10_1a_taraf_baglantisi.py`).
"""
from __future__ import annotations

import os
import sys
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
from sqlalchemy.orm import sessionmaker

BACKEND = Path(__file__).resolve().parent

#: DOSYA SEVİYESİNDE İŞARET: bu ikizin TAMAMI gerçek bir PostgreSQL
#: sunucusu ister (`pytest.ini`in `postgresql` işareti).
pytestmark = pytest.mark.postgresql

BAGLANTI = "whatsapp_party_links"
KOD = "whatsapp_party_pairing_codes"
IKISI = (BAGLANTI, KOD)

#: Alembic başı — zincirin ucu bu dilimle 0090 oldu.
BAS = "20260918_0090"

#: HER KOŞU KENDİ ÖNEKİNİ KULLANIR (WA2 ikizinin gerekçesi: PG ikizleri
#: paylaşık bir şemada koşabiliyor ve sabit ad kapıyı koşu sırasına
#: bağımlı yapardı).
KOSU = uuid4().hex[:8]

NUMARA = "905321112233"
IKINCI_NUMARA = "905331112233"


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("F10-1a ikizi APP_TEST_DATABASE_URL ister")
    return url


def _temizle(engine) -> None:
    """KENDİ satırlarını siler — ÖNEKLE, tabloyu SÜPÜRMEDEN (WA2 kuralı).

    Sıra TERS (kod → bağlantı → rıza → cari → üyelik → kullanıcı → firma):
    bileşik yabancı anahtar GERÇEK.
    """
    onek = KOSU + "%"
    firma_alt = "(SELECT id FROM companies WHERE name LIKE :o)"
    with engine.begin() as b:
        for tablo in (
            KOD,
            BAGLANTI,
            "notification_consent_events",
            "notification_consents",
            "customers",
            "suppliers",
            "user_company_memberships",
        ):
            b.execute(
                text(f"DELETE FROM {tablo} WHERE company_id IN {firma_alt}"),
                {"o": onek},
            )
        b.execute(
            text("DELETE FROM whatsapp_pairing_attempts WHERE phone IN (:p,:q)"),
            {"p": NUMARA, "q": IKINCI_NUMARA},
        )
        b.execute(text("DELETE FROM app_users WHERE username LIKE :o"), {"o": onek})
        b.execute(text("DELETE FROM companies WHERE name LIKE :o"), {"o": onek})


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
    """İKİ firma; birincisinde bir müşteri ve bir tedarikçi. Adlar KOŞU önekli."""
    with motor.begin() as b:
        an = datetime.now(timezone.utc)

        def firma(ad: str) -> int:
            return b.execute(
                text(
                    "INSERT INTO companies(name,is_active,created_at)"
                    " VALUES(:n,TRUE,:t) RETURNING id"
                ),
                {"n": ad, "t": an},
            ).scalar_one()

        firma_a = firma(KOSU + "-bir")
        firma_b = firma(KOSU + "-iki")

        def cari(tablo: str, cid: int, ad: str) -> int:
            return b.execute(
                text(
                    f"INSERT INTO {tablo}(name,is_active,company_id,"
                    "opening_balance,risk_limit,payment_term_days)"
                    " VALUES(:a,TRUE,:c,0,0,0) RETURNING id"
                ),
                {"a": ad, "c": cid},
            ).scalar_one()

        musteri = cari("customers", firma_a, KOSU + "-musteri")
        tedarikci = cari("suppliers", firma_a, KOSU + "-tedarikci")
        musteri_b = cari("customers", firma_b, KOSU + "-komsu")

    return {
        "firma_a": int(firma_a),
        "firma_b": int(firma_b),
        "musteri": int(musteri),
        "tedarikci": int(tedarikci),
        "musteri_b": int(musteri_b),
    }


def _baglanti_yaz(
    baglanti, cid: int, party_type: str, party_id: int, telefon: str, aktif: bool
) -> int:
    an = datetime.now(timezone.utc)
    return baglanti.execute(
        text(
            f"INSERT INTO {BAGLANTI}"
            "(company_id,party_type,party_id,phone,is_active,created_at,"
            "updated_at) VALUES(:c,:pt,:pi,:p,:a,:t,:t) RETURNING id"
        ),
        {"c": cid, "pt": party_type, "pi": party_id, "p": telefon, "a": aktif, "t": an},
    ).scalar_one()


def _kod_satiri(**fazla) -> dict:
    an = datetime.now(timezone.utc)
    govde = {
        "party_type": "CUSTOMER",
        "target_phone": NUMARA,
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


def _kod_yaz(baglanti, cid: int, party_id: int, **fazla) -> None:
    govde = _kod_satiri(**fazla)
    govde["company_id"] = cid
    govde["party_id"] = party_id
    sutunlar = ",".join(govde)
    yer = ",".join(":" + ad for ad in govde)
    baglanti.execute(
        text("INSERT INTO %s(%s) VALUES(%s)" % (KOD, sutunlar, yer)), govde
    )


# ------------------------------------------------------------- ŞEMA -------

def test_GOC_BASI_0090(motor) -> None:
    """Zincirin ucu bu dilimle 0090 — GÖÇ EDİLMİŞ veritabanından okunuyor."""
    with motor.connect() as b:
        surumler = [
            s[0] for s in b.execute(text("SELECT version_num FROM alembic_version"))
        ]
    assert surumler == [BAS], surumler


def test_IKI_TABLO_da_company_id_TASIYOR_ve_BILESIK_ANAHTARI_VAR(motor) -> None:
    """Kiracı sütunu ve `(company_id, id)` tekili GÖÇ EDİLMİŞ KATALOGDA.

    Kaynağı grep'lemek, sütunu bir yardımcıdan ekleyen ya da tekili sessizce
    düşüren bir değişikliği kaçırırdı.
    """
    gozlemci = inspect(motor)
    for tablo in IKISI:
        sutunlar = {c["name"] for c in gozlemci.get_columns(tablo)}
        assert "company_id" in sutunlar, tablo
        assert "party_type" in sutunlar and "party_id" in sutunlar, tablo
        tekiller = {
            tuple(u["column_names"]) for u in gozlemci.get_unique_constraints(tablo)
        }
        assert ("company_id", "id") in tekiller, (tablo, tekiller)


def test_ZAMAN_SUTUNLARI_TIMESTAMPTZ(motor) -> None:
    """`expires_at`/`consent_at` saat dilimi TAŞIYOR.

    SQLite'ta zaman bir metindir ve bu soru HİÇ SORULMAZ; TZ'siz bir
    `expires_at`, "kod ne zaman doldu" sorusunu oturumun TZ'sine göre
    yanlış cevaplardı.
    """
    gozlemci = inspect(motor)
    kod_sutun = {c["name"]: c["type"] for c in gozlemci.get_columns(KOD)}
    baglanti_sutun = {c["name"]: c["type"] for c in gozlemci.get_columns(BAGLANTI)}
    assert kod_sutun["expires_at"].timezone is True
    assert baglanti_sutun["consent_at"].timezone is True


def test_AKTIF_NUMARA_TEKILI_gercekten_REDDEDIYOR(motor, dunya) -> None:
    """`(company_id, phone) WHERE is_active` — PG'de KURULU ve ISIRIYOR.

    MUTASYON: `postgresql_where` yüklemini yanlış yazmak indeksi PG'de HİÇ
    kurmaz; SQLite hattı bunu GÖREMEZ.
    """
    with motor.begin() as b:
        _baglanti_yaz(b, dunya["firma_a"], "CUSTOMER", dunya["musteri"], NUMARA, True)
    with pytest.raises(IntegrityError):
        with motor.begin() as b:
            _baglanti_yaz(
                b, dunya["firma_a"], "SUPPLIER", dunya["tedarikci"], NUMARA, True
            )


def test_AKTIF_NUMARA_TEKILI_PASIF_SATIRI_KAPSAMA_ALMIYOR(motor, dunya) -> None:
    """KISMİ tekil: pasif satır anahtarın DIŞINDA — iz silinmeden yeniden bağlanır.

    MUTASYON: `WHERE`i düşürüp düz `UNIQUE` yapmak bunu KIRMIZI yapar.
    """
    with motor.begin() as b:
        _baglanti_yaz(b, dunya["firma_a"], "CUSTOMER", dunya["musteri"], NUMARA, False)
        _baglanti_yaz(b, dunya["firma_a"], "CUSTOMER", dunya["musteri"], NUMARA, False)
        _baglanti_yaz(b, dunya["firma_a"], "CUSTOMER", dunya["musteri"], NUMARA, True)
    with motor.connect() as b:
        sayi = b.execute(
            text(f"SELECT count(*) FROM {BAGLANTI} WHERE company_id=:c"),
            {"c": dunya["firma_a"]},
        ).scalar_one()
    assert int(sayi) == 3


def test_AYNI_NUMARA_BASKA_FIRMADA_SERBEST(motor, dunya) -> None:
    """Anahtar KİRACI KAPSAMLI: bir çiftçi iki alım merkezine ürün verir."""
    with motor.begin() as b:
        _baglanti_yaz(b, dunya["firma_a"], "CUSTOMER", dunya["musteri"], NUMARA, True)
        _baglanti_yaz(b, dunya["firma_b"], "CUSTOMER", dunya["musteri_b"], NUMARA, True)


def test_PARTY_TYPE_CHECKI_REDDEDIYOR(motor, dunya) -> None:
    """Sözlük KAPALI: `consents.PARTY_TYPES` dışı bir değer yazılamaz."""
    with pytest.raises(IntegrityError):
        with motor.begin() as b:
            _baglanti_yaz(b, dunya["firma_a"], "FARMER", dunya["musteri"], NUMARA, True)


def test_BOS_HEDEF_NUMARA_REDDEDILIYOR(motor, dunya) -> None:
    """`ck_wppc_target_phone`: hedefsiz kod bir sözleşme ihlalidir (SEC-1)."""
    with pytest.raises(IntegrityError):
        with motor.begin() as b:
            _kod_yaz(b, dunya["firma_a"], dunya["musteri"], target_phone="")


def test_DURUM_ZAMAN_DAMGASI_MATRISI_ISIRIYOR(motor, dunya) -> None:
    """`CONSUMED` ama `consumed_at` NULL bir satır YAZILAMAZ.

    SQLite CHECK'i yansıtmıyor; bu matris YALNIZ burada gerçekten ölçülüyor.
    """
    with pytest.raises(IntegrityError):
        with motor.begin() as b:
            _kod_yaz(b, dunya["firma_a"], dunya["musteri"], status="CONSUMED")
    with pytest.raises(IntegrityError):
        with motor.begin() as b:
            _kod_yaz(
                b,
                dunya["firma_a"],
                dunya["musteri"],
                status="PENDING",
                cancelled_at=datetime.now(timezone.utc),
            )


def test_BEKLEYEN_KOD_TEKILI_TARAF_UCLUSUNDE(motor, dunya) -> None:
    """`(company_id, party_type, party_id) WHERE status='PENDING'` ısırıyor."""
    with motor.begin() as b:
        _kod_yaz(b, dunya["firma_a"], dunya["musteri"], code_digest=KOSU + "-1")
    with pytest.raises(IntegrityError):
        with motor.begin() as b:
            _kod_yaz(b, dunya["firma_a"], dunya["musteri"], code_digest=KOSU + "-2")
    # AYNI id ama BAŞKA taraf tipi: ayrı bir taraftır, serbest.
    with motor.begin() as b:
        _kod_yaz(
            b,
            dunya["firma_a"],
            dunya["musteri"],
            party_type="SUPPLIER",
            code_digest=KOSU + "-3",
        )


def test_BILESIK_FK_CAPRAZ_KIRACIYI_REDDEDIYOR(motor, dunya) -> None:
    """Bir firmanın kodu BAŞKA firmanın bağlantısını tüketmiş görünemez.

    SQLite yabancı anahtarları varsayılan olarak UYGULAMAZ; bu yüzden bu
    iddia YALNIZ burada gerçek.
    """
    with motor.begin() as b:
        yabanci = _baglanti_yaz(
            b, dunya["firma_b"], "CUSTOMER", dunya["musteri_b"], IKINCI_NUMARA, True
        )
    an = datetime.now(timezone.utc)
    with pytest.raises(IntegrityError):
        with motor.begin() as b:
            _kod_yaz(
                b,
                dunya["firma_a"],
                dunya["musteri"],
                status="CONSUMED",
                consumed_at=an,
                consumed_link_id=int(yabanci),
            )


# ------------------------------------------------------------- YARIŞ ------

def test_AYNI_KODU_YIRMI_ISCI_KULLANIRSA_TEK_BAGLANTI(motor, dunya) -> None:
    """Yirmi eşzamanlı `BAĞLA <KOD>` → TAM BİR bağlantı, kod TAM BİR KEZ tükenir.

    SQLite'ta ÜRETİLEMEZ (tek yazar). Yirmisi de kodun bağlı olduğu AYNI
    numarayı sunar; ayrı numaralar hedef denetiminde elenip CAS'e hiç
    varmazdı (SEC-1'in sonucu).
    """
    os.environ.setdefault("DATABASE_URL", _url())
    sys.path.insert(0, str(BACKEND))
    from app.auth import token_digest
    from app.whatsapp import taraf

    kod = "ABCDEFGHJKMN"
    with motor.begin() as b:
        _kod_yaz(b, dunya["firma_a"], dunya["musteri"], code_digest=token_digest(kod))

    Session = sessionmaker(bind=motor)
    kapi = Barrier(20)

    def isci(_n: int) -> bool:
        with Session() as db:
            kapi.wait(timeout=30)
            sonuc = taraf.kod_kullan(db, NUMARA, kod)
            db.commit()
            return bool(sonuc.basarili)

    with ThreadPoolExecutor(max_workers=20) as havuz:
        sonuclar = list(havuz.map(isci, range(20)))

    assert sum(1 for s in sonuclar if s) == 1, sonuclar
    with motor.connect() as b:
        baglanti_sayisi = b.execute(
            text(f"SELECT count(*) FROM {BAGLANTI} WHERE company_id=:c"),
            {"c": dunya["firma_a"]},
        ).scalar_one()
        durum = b.execute(
            text(f"SELECT status FROM {KOD} WHERE company_id=:c"),
            {"c": dunya["firma_a"]},
        ).scalar_one()
    assert int(baglanti_sayisi) == 1
    assert durum == "CONSUMED"
