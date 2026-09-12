"""PostgreSQL ikizi: göç `20260911_0081`in GERÇEK PG 16'da up->down->up turu.

SQLite ikizi (`test_e1_efatura_sertlestirme.py`) göç DOSYASININ sözleşmesini
ölçüyor (üç sütun, nullable, geri alınabilir). Bu dosya o sözleşmenin GERÇEK
diyalekte İNDİĞİNİ ölçüyor ve üç şey YALNIZ burada görünür:

1. **`VARCHAR(n)` PostgreSQL'de GERÇEK BİR KISITTIR.** SQLite uzunluğu
   ZORLAMAZ: `VARCHAR(10)` sütununa 500 karakter yazılabilir ve test yeşil
   yanar. PG'de aynı yazma `StringDataRightTruncation` ile düşer. Sınırların
   kasıtlı olduğu (`einvoice_gib_status_code` bir KOD taşır, bir hata GÖVDESİ
   değil) ancak burada kanıtlanabilir.

2. **`DROP COLUMN` + yeniden `ADD COLUMN` sütunu İLK HÂLİYLE geri getiriyor.**
   `downgrade()`in gerçekten geri alınabilir olduğu, dosyayı okuyarak
   söylenemez; turu KOŞMAK gerekir. Üç sütunun hiçbirinde varsayılan, indeks
   ya da kısıt YOKTUR, yani tur bilgi KAYBETMEDEN kapanmalıdır.

3. **ŞEMA BAŞI GERÇEKTEN 0081.** Zincir testi dosyaları okuyor; bu, göçün
   PG'de UYGULANDIĞINI ölçüyor.

TEMİZLİK: bu dosya KENDİ satırını siler, tablo SÜPÜRMEZ. Ölçülmüş tuzak —
PG ikizleri paylaşık bir şemada koşabiliyor ve arkada bırakılan satır KOMŞU
dosyayı kırıyor (`product_lots` artıklarıyla bir kez ölçülmüş hata sınıfı).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DataError

BACKEND = Path(__file__).resolve().parent

pytestmark = pytest.mark.postgresql

KOSU = uuid4().hex[:8]
GOC = "20260911_0081"
ONCEKI = "20260910_0080"
#: ZINCIRIN BASI — `GOC`tan AYRI bir sabit ve bu ayrim ZORUNLU.
#: `GOC` bu dosyanin KONUSU olan gocun kimligidir ve up->down->up
#: turunun hedefidir; `BAS` ise semanin o an bulundugu yerdir ve HER
#: yeni gocle KIMILDAR. Ikisi 0082ye kadar TESADUFEN ayni degerdi;
#: tek sabitle yazili kalsaydi, basi guncelleyen biri bu dosyanin
#: goc turunu da farkinda olmadan baska bir goce cevirirdi.
BAS = "20260914_0086"

#: Göçün açtığı üç sütun ve ilan edilen uzunlukları.
SUTUNLAR = {
    "einvoice_web_key": 255,
    "einvoice_gib_status_code": 10,
    "einvoice_pk_alias": 120,
}


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("E1 ikizi APP_TEST_DATABASE_URL ister")
    return url


def _temizle(engine) -> None:
    """KENDİ faturasını ve firmasını siler — ÖNEKLE, tabloyu SÜPÜRMEDEN."""
    with engine.begin() as baglanti:
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
            text("DELETE FROM app_users WHERE username LIKE :onek"), {"onek": f"{KOSU}%"}
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


def _firma_ve_kullanici(motor, ad: str) -> tuple[int, int]:
    """Bir firma + bir kullanıcı. `invoices.created_by` NOT NULL — ölçüldü.

    Satırı EKSİK kurup `IntegrityError` almak, uzunluk testini YANLIŞ
    SEBEPTEN yeşil yapardı: `NotNullViolation` da bir istisnadır ama ölçmek
    istediğimiz şey DEĞİLDİR.
    """
    an = datetime.now(timezone.utc)
    with motor.begin() as baglanti:
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
                " VALUES(:k,:e,true,:k,'x','admin',true,false,:t) RETURNING id"
            ),
            {"k": f"{ad}-kul", "e": f"{ad}@ornek.test", "t": an},
        ).scalar_one()
    return firma, kullanici


_INSERT = (
    "INSERT INTO invoices(company_id,created_by,invoice_number,invoice_type,status,"
    "currency,exchange_rate,customer_snapshot,machine_snapshot,"
    "work_order_snapshot,company_snapshot,technician_snapshot,"
    "warranty_snapshot,totals_snapshot,tax_snapshot,created_at,updated_at,"
    "einvoice_web_key,einvoice_gib_status_code,einvoice_pk_alias)"
    " VALUES(:cid,:uid,:no,'SALES','ISSUED','TRY',1,'{}','{}','{}','{}','{}',"
    "'{}','{}','{}',:t,:t,:wk,:kod,:pk) RETURNING id"
)


def _sutunlar(engine) -> dict[str, object]:
    return {c["name"]: c for c in inspect(engine).get_columns("invoices")}


# --- 1. Şema başı ---------------------------------------------------------
def test_SEMA_BASI_GERCEK_PostgreSQLde(motor) -> None:
    """`alembic upgrade head` semayi ZINCIRIN BASINA getiriyor.

    Iddia `GOC` degil `BAS` uzerinedir: bu dosyanin konusu 0081 ama sema
    bugun 0082'dedir (SEC-1). Ikisini tek sabitte tutmak, yeni bir goc
    indigi gun bu testi "0081'e kadar goc et" diye YANLIS okutuyordu.
    """
    with motor.connect() as baglanti:
        surumler = baglanti.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalars().all()
    assert surumler == [BAS], surumler


# --- 2. Üç sütun gerçekten VAR ve NULLABLE --------------------------------
def test_UC_SUTUN_PGde_VAR_ve_NULLABLE(motor) -> None:
    """Nullable olmaları KASITLI: eski faturalar bu değerleri TAŞIYAMAZ ve
    "boş" ile "bilinmiyor" ayırt edilebilir kalmalı."""
    sutunlar = _sutunlar(motor)
    for ad, uzunluk in SUTUNLAR.items():
        assert ad in sutunlar, f"{ad} PG'de açılmamış"
        assert sutunlar[ad]["nullable"] is True, ad
        assert getattr(sutunlar[ad]["type"], "length", None) == uzunluk, (
            ad,
            sutunlar[ad]["type"],
        )


# --- 3. VARCHAR(n) PG'de GERÇEKTEN ISIRIYOR -------------------------------
def test_GIB_KODU_SINIRI_PGde_GERCEKTEN_ISIRIYOR(motor) -> None:
    """`einvoice_gib_status_code` 10 hane: bir KOD taşır, bir hata GÖVDESİ değil.

    SQLite'ta bu test ÜRETİLEMEZ — orada `VARCHAR(10)` bir NİYET beyanıdır,
    kısıt değil, ve 500 karakter sessizce yazılır. Sınırın anlamı yalnız
    burada vardır.
    """
    an = datetime.now(timezone.utc)
    firma, kullanici = _firma_ve_kullanici(motor, f"{KOSU} E1 Firma")

    # ÖNCE aynı satır GEÇERLİ bir kodla yazılıyor: yol açık, satır kurulabilir.
    with motor.begin() as baglanti:
        baglanti.execute(
            text(_INSERT),
            {"cid": firma, "uid": kullanici, "no": f"{KOSU}-0", "t": an,
             "wk": None, "kod": "0123456789", "pk": None},
        )

    # SONRA yalnız kod BİR karakter uzatılıyor. Tek fark budur; düşerse sebebi
    # UZUNLUKTUR, eksik bir sütun değil.
    with pytest.raises(DataError):
        with motor.begin() as baglanti:
            baglanti.execute(
                text(_INSERT),
                {"cid": firma, "uid": kullanici, "no": f"{KOSU}-1", "t": an,
                 "wk": None, "kod": "0123456789A", "pk": None},
            )


# --- 4. up -> down -> up ---------------------------------------------------
def test_GOC_TURU_up_down_up_GERCEK_PostgreSQLde(motor) -> None:
    """Tur bilgi KAYBETMEDEN kapanmalı: üç sütun gider ve AYNI hâlle döner.

    `downgrade()`in gerçekten geri alınabilir olduğu dosyayı okuyarak
    söylenemez — turu KOŞMAK gerekir.
    """
    yapilandirma = Config(str(BACKEND / "alembic.ini"))

    onceki = _sutunlar(motor)
    assert set(SUTUNLAR) <= set(onceki)

    command.downgrade(yapilandirma, ONCEKI)
    inen = _sutunlar(motor)
    for ad in SUTUNLAR:
        assert ad not in inen, f"{ad} downgrade sonrası HÂLÂ duruyor"
    with motor.connect() as baglanti:
        assert baglanti.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalars().all() == [ONCEKI]

    command.upgrade(yapilandirma, GOC)
    donen = _sutunlar(motor)
    for ad, uzunluk in SUTUNLAR.items():
        assert ad in donen, f"{ad} ikinci upgrade'de geri gelmedi"
        assert donen[ad]["nullable"] is True, ad
        assert getattr(donen[ad]["type"], "length", None) == uzunluk, ad
    with motor.connect() as baglanti:
        assert baglanti.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalars().all() == [GOC]

    # TUR SONUNDA SEMA `head`TE BIRAKILIYOR. 0082'ye kadar bu satira gerek
    # YOKTU cunku 0081 zaten BASTI; artik degil ve bu dosya semayi 0081'de
    # birakirsa KOMSU dosyalar (CI'da ayni konteyneri paylasan kosularda)
    # eksik bir sema bulur. WA2 ikizinin ayni sozlesmesiyle BIREBIR.
    command.upgrade(yapilandirma, "head")
    with motor.connect() as baglanti:
        assert baglanti.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalars().all() == [BAS]


# --- 5. Değerler gerçekten yazılıp okunuyor -------------------------------
def test_WEB_KEY_PGde_TAM_URL_OLARAK_YAZILIP_OKUNUYOR(motor) -> None:
    """255 hane gerçek bir portal URL'ini alır — ölçüldü, varsayılmadı."""
    an = datetime.now(timezone.utc)
    web_key = (
        "https://portaltest.izibiz.com.tr/earchive/view-earchive/"
        "view-pdf-earchive.xhtml?webValidationKey=" + uuid4().hex + "&viewType=PDF"
    )
    assert len(web_key) <= SUTUNLAR["einvoice_web_key"]

    firma, kullanici = _firma_ve_kullanici(motor, f"{KOSU} E1 Firma2")
    with motor.begin() as baglanti:
        fatura = baglanti.execute(
            text(_INSERT),
            {
                "cid": firma,
                "uid": kullanici,
                "no": f"{KOSU}-2",
                "t": an,
                "wk": web_key,
                "kod": "130",
                "pk": "urn:mail:defaultpk@izibiz.com.tr",
            },
        ).scalar_one()

    with motor.connect() as baglanti:
        satir = baglanti.execute(
            text(
                "SELECT einvoice_web_key,einvoice_gib_status_code,einvoice_pk_alias"
                " FROM invoices WHERE id=:id"
            ),
            {"id": fatura},
        ).one()
    assert satir[0] == web_key, "URL kırpıldı"
    assert satir[1] == "130"
    assert satir[2] == "urn:mail:defaultpk@izibiz.com.tr"
