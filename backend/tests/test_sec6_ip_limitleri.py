"""SEC-6: IP BAŞINA saatlik tavanlar — giriş ve WhatsApp webhook'u.

ÖLÇÜLEN AÇIK (develop `8cfa7a0`)
--------------------------------
`app/routers/auth.py` zaten bir `_consume_ip_limit` taşıyordu ve onu
`register` / `forgot` / `reset` yollarında kullanıyordu. GİRİŞ'te ise YALNIZ
`login_lock_status(db, username, ip_address)` vardı ve o kilit
**(kullanıcı adı, IP) İKİLİSİNE** bağlıdır. Sonuç ölçülebilir bir açıktır:
tek bir IP, N farklı kullanıcı adına, her biri için kilit eşiğinin (5) BİR
ALTINA kadar deneme yapabilir ve HİÇBİR kilidi tetiklemeden ilerler. Bu
dosyanın (a) testi tam olarak o senaryoyu koşar — 201 farklı kullanıcı adı,
TEK IP — ve `develop`te 201 denemenin 201'i de 401 döner, yani küresel bir
fren YOKTUR. Kimlik-doldurmanın (credential stuffing) tanımı budur.

`POST /api/whatsapp/webhook` ise Meta'nın imzasını doğruluyordu ama imzası
DÜŞEN isteklere hiçbir tavan uygulamıyordu: uç oturumsuzdur, gövde üretmek
bedavadır ve HMAC doğrulaması saldırganın elinde ücretsiz bir CPU pompasına
dönüşür.

BU DOSYANIN ÖLÇTÜĞÜ ŞEY BİR NİYET DEĞİL DAVRANIŞTIR: her kapı gerçek şemalı
bir uygulamaya gerçek HTTP istekleri atar.

--- MUTASYON TABLOSU ------------------------------------------------------

  * `login` içindeki `_consume_ip_limit` çağrısını silmek
        -> `test_TEK_IP_201_FARKLI_KULLANICI_ADI_201inci_429` ve
           `test_TAVAN_ASILDIGINDA_govde_DIGER_UCLARLA_AYNI` KIRMIZI
  * Webhook tavanını imza doğrulamasının ÖNÜNE almak (yani imzası TUTAN
    istekleri de saymak)
        -> `test_IMZASI_TUTAN_ISTEK_TAVANDAN_SONRA_DA_200` ve
           `test_IMZASI_TUTAN_ISTEK_SAYACA_HIC_DOKUNMUYOR` KIRMIZI
  * `login`in `kaydet=False`ini `True` yapmak (başarılı girişin bütçe yemesi)
        -> `test_BASARILI_GIRIS_BUTCE_HARCAMIYOR` KIRMIZI
  * Pencere süpürmesini (`delete ... attempted_at < cutoff`) kaldırmak
        -> `test_BIR_SAATTEN_ESKI_SATIRLAR_SUPURULUYOR` KIRMIZI
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]

#: SIR/JETON/PNID testin KENDİSİNDEDİR; WA1 kapısıyla (`test_wa1_ingress.py`)
#: aynı değerler kullanılıyor ki imzalama yardımcısı iki dosyada AYNI şeyi
#: söylesin.
SIR = "wa1-test-app-secret"
JETON = "wa1-test-verify-token"
PNID = "111222333444555"

#: Tavanlar `Settings` varsayılanlarıdır ve test onları ORTAMDAN ZORLAMAZ:
#: aşağıdaki iki sabit beklentiyi ADIYLA çiviler — varsayılanı sessizce
#: düşürmek (ör. 200 -> 5) kapıyı KIRMIZI yapar.
BEKLENEN_GIRIS_TAVANI = 200
BEKLENEN_WEBHOOK_TAVANI = 60

# UYGULAMA İÇE AKTARILMADAN ÖNCE ORTAM KURULUR (gerekçe `test_wa1_ingress.py`
# modül başında: `app.config.Settings` modül düzeyinde TEK KOPYADIR).
_CALISMA = Path(tempfile.mkdtemp(prefix="sec6-"))
os.environ["DATABASE_URL"] = "sqlite:///" + (_CALISMA / "sec6.db").as_posix()
os.environ["SUNGUR_DATA_DIR"] = str(_CALISMA)
os.environ["AUTO_MIGRATE"] = "true"
os.environ["WHATSAPP_APP_SECRET"] = SIR
os.environ["WHATSAPP_VERIFY_TOKEN"] = JETON
os.environ["WHATSAPP_PHONE_NUMBER_ID"] = PNID

sys.path.insert(0, str(BACKEND))

#: Her testin KENDİ IP'si var. Ortak bir IP kullanılsaydı testler birbirinin
#: bütçesini yer ve sıraya bağlı, açıklanamayan kırmızılar üretirdi.
IP_SPREY = "203.0.113.11"
IP_GOVDE = "203.0.113.15"
IP_BASARILI = "203.0.113.12"
IP_PENCERE = "203.0.113.13"
IP_WEBHOOK = "203.0.113.14"


def _istemci(ip: str):
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app, client=(ip, 40000))


@pytest.fixture(scope="module")
def uygulama():
    """Gerçek şemalı uygulama; `TestClient` bağlamı göçleri koşturur."""
    with _istemci("203.0.113.1") as c:
        yield c


@pytest.fixture()
def defter(uygulama):
    """`auth_rate_limits` ve `login_attempts` okuma/temizleme yardımcısı."""
    from sqlalchemy import delete, select

    from app.auth import auth_rate_limits, login_attempts
    from app.db import SessionLocal

    def temizle():
        with SessionLocal() as db:
            db.execute(delete(auth_rate_limits))
            db.execute(delete(login_attempts))
            db.commit()

    class Defter:
        @staticmethod
        def sayac(action: str, ip: str) -> int:
            with SessionLocal() as db:
                return len(db.execute(
                    select(auth_rate_limits).where(
                        auth_rate_limits.c.action == action,
                        auth_rate_limits.c.ip_address == ip,
                    )
                ).mappings().all())

        @staticmethod
        def giris_denemeleri(ip: str):
            with SessionLocal() as db:
                return db.execute(
                    select(login_attempts).where(login_attempts.c.ip_address == ip)
                ).mappings().all()

    temizle()
    yield Defter()
    temizle()


def _giris(istemci, kullanici: str, parola: str = "yanlis-parola-123"):
    return istemci.post("/api/auth/login", json={
        "username": kullanici, "password": parola,
    })


# ------------------------------------------------------- (a) GİRİŞ TAVANI ---

def test_TAVAN_VARSAYILANLARI_beklenen_degerlerde() -> None:
    """Varsayılanı sessizce değiştirmek bu kapıyı KIRMIZI yapar."""
    from app.config import settings

    assert settings.login_ip_limit_per_hour == BEKLENEN_GIRIS_TAVANI
    assert (
        settings.whatsapp_webhook_bad_signature_limit_per_hour
        == BEKLENEN_WEBHOOK_TAVANI
    )


def test_TEK_IP_201_FARKLI_KULLANICI_ADI_201inci_429(defter) -> None:
    """AÇIĞIN TA KENDİSİ: 201 farklı kullanıcı adı, TEK IP.

    Kullanıcı adları FARKLI olduğu için kullanıcı başına kilit (5 deneme /
    15 dakika) HİÇ tetiklenmez — bu ayrıca ölçülüyor, varsayılmıyor. Freni
    çeken tek şey IP başına tavandır.
    """
    with _istemci(IP_SPREY) as c:
        for sira in range(BEKLENEN_GIRIS_TAVANI):
            cevap = _giris(c, f"kurban{sira:04d}")
            assert cevap.status_code == 401, (sira, cevap.status_code, cevap.text)

        tasma = _giris(c, f"kurban{BEKLENEN_GIRIS_TAVANI:04d}")
        assert tasma.status_code == 429, tasma.text

    # KİLİT HİÇ ATEŞLEMEDİ ve bu iddia AYRI ölçülüyor: 429'u kilidin
    # getirdiği bir dünyada test yine yeşil olurdu ve YANLIŞ şeyi kanıtlardı.
    satirlar = defter.giris_denemeleri(IP_SPREY)
    assert len(satirlar) == BEKLENEN_GIRIS_TAVANI
    assert max(int(r["fail_count"]) for r in satirlar) == 1
    assert all(r["locked_until"] is None for r in satirlar)

    # Bütçe tam olarak tavan kadar yendi; taşan istek satır EKLEMEDİ.
    assert defter.sayac("login", IP_SPREY) == BEKLENEN_GIRIS_TAVANI


def test_TAVAN_ASILDIGINDA_govde_DIGER_UCLARLA_AYNI(defter) -> None:
    """429 gövdesi `register`/`forgot`/`reset` ile HARFİ HARFİNE aynı.

    Metin `_consume_ip_limit`in TEK kopyasından gelir; giriş yolu kendi
    metnini yazsaydı istemci aynı sınıf hata için iki farklı 429 görürdü.
    """
    with _istemci(IP_GOVDE) as c:
        for sira in range(BEKLENEN_GIRIS_TAVANI):
            assert _giris(c, f"govde{sira:04d}").status_code == 401
        tasma = _giris(c, "govde-tasma")

    assert tasma.status_code == 429
    assert tasma.json()["detail"] == "Çok fazla deneme. Bir saat sonra tekrar deneyin."


# --------------------------------------------- (b) BAŞARILI GİRİŞ BÜTÇESİ ---

@pytest.fixture()
def dogrulanmis_kullanici(uygulama):
    """Parolası BİLİNEN, e-postası doğrulanmış, etkin bir kullanıcı."""
    from sqlalchemy import delete, insert

    from app.auth import hash_password, users, utcnow
    from app.db import SessionLocal

    ad = "sec6-gercek-kullanici"
    parola = "Sec6-Dogru-Parola!42"
    with SessionLocal() as db:
        db.execute(delete(users).where(users.c.username == ad))
        db.execute(insert(users).values(
            username=ad,
            email="sec6@example.invalid",
            email_verified=True,
            display_name="SEC-6 Kullanıcı",
            password_hash=hash_password(parola),
            role="admin",
            is_active=True,
            created_at=utcnow(),
            must_change_password=False,
        ))
        db.commit()
    yield ad, parola
    with SessionLocal() as db:
        db.execute(delete(users).where(users.c.username == ad))
        db.commit()


def test_BASARILI_GIRIS_BUTCE_HARCAMIYOR(defter, dogrulanmis_kullanici) -> None:
    """Doğru parolayla giren kullanıcı kendi tavanını YEMEZ.

    Ortak NAT arkasındaki bir dükkânda gün boyu giren 10-15 kişi, sayım ve
    kayıt tek çağrıda birleşmiş olsaydı kendi tavanlarını kendileri
    tüketirdi. Bu yüzden `login` sayımı `kaydet=False` ile yapar ve kaydı
    YALNIZ başarısız yollara bırakır.
    """
    ad, parola = dogrulanmis_kullanici
    with _istemci(IP_BASARILI) as c:
        for _ in range(5):
            cevap = c.post("/api/auth/login", json={
                "username": ad, "password": parola,
            })
            assert cevap.status_code == 200, cevap.text

        assert defter.sayac("login", IP_BASARILI) == 0

        # BAŞARISIZ deneme AYNI IP'den bütçeyi HARCIYOR — testin "sayaç hiç
        # çalışmıyor" diye vacuous yeşil dönmediğini bu adım ölçüyor.
        assert _giris(c, ad, "kesinlikle-yanlis").status_code == 401

    assert defter.sayac("login", IP_BASARILI) == 1


# ---------------------------------------------------------- (d) PENCERE ----

def test_BIR_SAATTEN_ESKI_SATIRLAR_SUPURULUYOR(defter) -> None:
    """Saat DONDURULUYOR: satırlar 2 saat GERİYE yazılıp süpürülmeleri ölçülüyor.

    Zamanı ileri sarmak yerine satırların `attempted_at`i geriye yazılıyor —
    sonuç aynıdır ve sürecin saatine dokunulmaz.
    """
    from sqlalchemy import insert

    from app.auth import auth_rate_limits, utcnow
    from app.db import SessionLocal

    eski = utcnow() - timedelta(hours=2)
    with SessionLocal() as db:
        for _ in range(BEKLENEN_GIRIS_TAVANI):
            db.execute(insert(auth_rate_limits).values(
                action="login", ip_address=IP_PENCERE, attempted_at=eski,
            ))
        db.commit()
    assert defter.sayac("login", IP_PENCERE) == BEKLENEN_GIRIS_TAVANI

    # Tavan DOLU görünüyor ama satırların hepsi pencerenin DIŞINDA: istek
    # 429 DEĞİL 401 almalı.
    with _istemci(IP_PENCERE) as c:
        assert _giris(c, "pencere-kurbani").status_code == 401

    # Ve eski satırlar SİLİNMİŞ olmalı — yalnız sayılmamış değil. Kalan tek
    # satır az önceki başarısız denemedir.
    assert defter.sayac("login", IP_PENCERE) == 1


# --------------------------------------------------- (c) WEBHOOK TAVANI ----

def _imzala(govde: bytes, sir: str = SIR) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": "sha256=" + hmac.new(
            sir.encode("utf-8"), govde, hashlib.sha256
        ).hexdigest(),
    }


BOZUK_IMZA = {"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=00"}


def _webhook_govdesi() -> bytes:
    return json.dumps({
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": PNID},
            "messages": [],
        }}]}],
    }).encode("utf-8")


def test_IMZASI_TUTAN_ISTEK_TAVANDAN_SONRA_DA_200(defter) -> None:
    """61. bozuk imza 429; ARDINDAN gelen DOĞRU imzalı istek yine 200.

    İkinci yarı testin can alıcı yarısıdır: tavan `verify_signature`ın ÖNÜNE
    konsaydı ya da imzası TUTAN istekleri de sayıyor olsaydı, Meta'nın meşru
    teslimatı da 429 alırdı ve bu test KIRMIZI olurdu.
    """
    govde = _webhook_govdesi()
    with _istemci(IP_WEBHOOK) as c:
        for sira in range(BEKLENEN_WEBHOOK_TAVANI):
            cevap = c.post("/api/whatsapp/webhook", content=govde, headers=BOZUK_IMZA)
            assert cevap.status_code == 403, (sira, cevap.status_code, cevap.text)

        tasma = c.post("/api/whatsapp/webhook", content=govde, headers=BOZUK_IMZA)
        assert tasma.status_code == 429, tasma.text
        assert tasma.json()["detail"] == (
            "Çok fazla deneme. Bir saat sonra tekrar deneyin."
        )

        # TAVAN DOLUYKEN doğru imzalı istek YİNE 200.
        gecerli = c.post(
            "/api/whatsapp/webhook", content=govde, headers=_imzala(govde)
        )
        assert gecerli.status_code == 200, gecerli.text
        assert gecerli.json() == {"status": "ok"}

    assert defter.sayac("wa_webhook_bad_sig", IP_WEBHOOK) == BEKLENEN_WEBHOOK_TAVANI


def test_IMZASI_TUTAN_ISTEK_SAYACA_HIC_DOKUNMUYOR(defter) -> None:
    """Doğru imzalı 5 teslimat sayacı 0'da bırakır."""
    govde = _webhook_govdesi()
    with _istemci(IP_WEBHOOK) as c:
        for _ in range(5):
            assert c.post(
                "/api/whatsapp/webhook", content=govde, headers=_imzala(govde)
            ).status_code == 200
    assert defter.sayac("wa_webhook_bad_sig", IP_WEBHOOK) == 0


def test_WEBHOOK_ve_GIRIS_BUTCELERI_AYRI(defter) -> None:
    """İki tavan AYRI `action` taşır; biri diğerini tüketemez."""
    govde = _webhook_govdesi()
    with _istemci(IP_WEBHOOK) as c:
        for _ in range(10):
            assert c.post(
                "/api/whatsapp/webhook", content=govde, headers=BOZUK_IMZA
            ).status_code == 403

    assert defter.sayac("wa_webhook_bad_sig", IP_WEBHOOK) == 10
    assert defter.sayac("login", IP_WEBHOOK) == 0


# ----------------------------------------------------------- STATİK KAPI ---

def test_WEBHOOK_IP_ADRESINI_X_FORWARDED_FOR_DAN_OKUMUYOR() -> None:
    """Webhook `request.client`e bakar; başlığa ELLE bakmaz.

    `X-Forwarded-For`u doğrudan okumak, istemcinin kendi IP'sini uydurup
    tavanı her istekte tazelemesi demekti. Güvenilen-vekil çözümlemesi süreç
    genelinde `app/client_ip.py` tarafından kurulur.

    ARAMA METİNDE DEĞİL AST'te YAPILIYOR ve bu bir ayrıntı değil: düz metin
    grep'i bu dosyanın kendi YORUMLARINI da yakalar, yani başlığı NEDEN
    okumadığımızı yazan cümle kapıyı kırmızı yapardı. Kapı ancak `X-Forwarded-For`
    bir KOD sabiti olarak geçtiğinde kırmızı olmalı.
    """
    import ast

    kaynak = (BACKEND / "app" / "routers" / "whatsapp.py").read_text(encoding="utf-8")
    assert "request.client.host" in kaynak

    agac = ast.parse(kaynak)
    sabitler = {
        dugum.value.lower()
        for dugum in ast.walk(agac)
        if isinstance(dugum, ast.Constant) and isinstance(dugum.value, str)
    }
    assert "x-forwarded-for" not in sabitler
    # Tarayıcı KENDİNİ DOĞRULUYOR: sabit toplayıcı gerçekten çalışıyor mu?
    # Bu olmadan boş bir küme her zaman yeşil dönerdi.
    assert "/webhook" in sabitler


def test_TENANT_TABLES_auth_rate_limits_ICERMIYOR() -> None:
    """`auth_rate_limits` PLATFORM tablosudur; kiracı envanterine GİRMEZ.

    Bu dilim TABLO EKLEMİYOR — mevcut `auth_rate_limits`i yeniden kullanıyor
    (göç YOK). Kapı yine de burada duruyor çünkü sayaç artık İKİ yeni
    `action` taşıyor ve birileri onu "kiracıya ait" sanıp envantere
    ekleyebilir: tablo `company_id` TAŞIMAZ, taşısaydı bir IP'nin bütçesi
    kiracı başına sıfırlanır ve tavan HİÇBİR ŞEY frenlemezdi.
    """
    from tests.test_tenant_scoping_guard import TENANT_TABLES

    assert "auth_rate_limits" not in TENANT_TABLES
    assert "login_attempts" not in TENANT_TABLES
    # Envanterin BOYU da çivili: bu dilim ona DOKUNMADI.
# 120 -> 121: E4a e-IRSALIYE DEFTERI (goc 20260913_0083). Tek yeni kiraci
    # tablosu `despatch_notes`; `company_id` tasir, yani envantere OTOMATIK
    # girer ve her sorgusundan `company_id=:cid` yuklemi istenir.
    # 121 -> 122: CS1 CEK/SENET PORTFOYU (goc 20260914_0085). Tek yeni kiraci
    # tablosu `cek_senetler`; `company_id` tasir, envantere OTOMATIK girer.
    assert len(TENANT_TABLES) == 122
