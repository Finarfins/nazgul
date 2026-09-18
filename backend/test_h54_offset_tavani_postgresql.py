"""PostgreSQL ikizi: H54 — beş liste ucunda `offset` tavanı `INT4_UST`.

KUSUR: beş uç `offset: int = Query(0, ge=0)` taşıyordu — ALT sınır var, ÜST
yok. Tavansız `offset` sürücüye olduğu gibi gider; PostgreSQL `OFFSET` bigint
alır ve 2^63 ve üstü `NumericValueOutOfRange` ile 500 olur. H51 aynı kusuru
`/api/despatch-notes`ta kapattı ve tavanı `INT4_UST` seçti (sürücünün değil
veritabanının tavanı — gerekçe `app/sinirlar.py`de); bu dosya kalan beş ucu
ölçer:

* `GET /api/activity-logs`
* `GET /api/products/lots/mutabakat`
* `GET /api/platform/companies`
* `GET /api/platform/users`
* `GET /api/platform/verifications`

Beklenen: `INT4_UST+1` ve 2^63 -> 422 (`loc == ["query", "offset"]`, 500
DEĞİL); `INT4_UST` -> 200 ve boş sayfa. SQLite tarafındaki kapı
(`tests/test_h54_offset_tavani.py`) 422 sözleşmesini ölçer; sürücü taşmasının
500'ü YALNIZ burada görünür.

TEK KULLANICI: firma üyesi bir admin, aynı zamanda platform operatörü
(`settings.sungur_platform_operators`). Platform uçları kiracı çözümünden muaf
olduğu için `X-Company-ID` onlarda okunmaz; kiracı uçları ise onu kullanır.

TEMİZLİK: KENDİ satırlarını önekle siler, tablo SÜPÜRMEZ. `activity_logs`
yalnız-eklemedir; API girişi bir iz bırakırsa firma ya da kullanıcı silinemez
ve pasife alınır (H51/CS1 ikizlerinin gerekçesi).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

BACKEND = Path(__file__).resolve().parent

pytestmark = pytest.mark.postgresql

KOSU = uuid4().hex[:8]
ONEK = f"H54-{KOSU}"
KULLANICI = f"h54-{KOSU}"
PAROLA = "H54Tavan!2026xyz"

UCLAR = (
    "/api/activity-logs",
    "/api/products/lots/mutabakat",
    "/api/platform/companies",
    "/api/platform/users",
    "/api/platform/verifications",
)


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("H54 ikizi APP_TEST_DATABASE_URL ister")
    return url


def _config(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _acilisi_kostur() -> None:
    """Açılış verisi firma yazılmadan ÖNCE (CS1 ikizinin gerekçesi)."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app):
        pass


def _temizle(engine) -> None:
    """SIRA ÖNEMLİ: belirteçler -> üyelik -> kullanıcı -> firma."""
    with engine.begin() as c:
        firmalar = [r[0] for r in c.execute(
            text("SELECT id FROM companies WHERE name LIKE :o"), {"o": ONEK + "%"})]
        kullanicilar = [r[0] for r in c.execute(
            text("SELECT id FROM app_users WHERE username=:k"), {"k": KULLANICI})]
    for uid in kullanicilar:
        try:
            with engine.begin() as c:
                c.execute(text("DELETE FROM auth_tokens WHERE user_id=:u"), {"u": uid})
                c.execute(text("DELETE FROM auth_refresh_tokens WHERE user_id=:u"), {"u": uid})
                c.execute(text("DELETE FROM user_company_memberships WHERE user_id=:u"), {"u": uid})
                c.execute(text("DELETE FROM app_users WHERE id=:u"), {"u": uid})
        except IntegrityError:
            with engine.begin() as c:
                c.execute(text("UPDATE app_users SET is_active=false WHERE id=:u"), {"u": uid})
    for cid in firmalar:
        try:
            with engine.begin() as c:
                c.execute(text("DELETE FROM user_company_memberships WHERE company_id=:c"), {"c": cid})
                c.execute(text("DELETE FROM companies WHERE id=:c"), {"c": cid})
        except IntegrityError:
            with engine.begin() as c:
                c.execute(text("UPDATE companies SET is_active=false WHERE id=:c"), {"c": cid})


@pytest.fixture(scope="module")
def istemci():
    """Firma üyesi admin + platform operatörü ile GERÇEK giriş. 500'ler istisna
    değil yanıt olarak görünür — iddia durum koduna bakar."""
    url = _url()
    command.upgrade(_config(url), "head")
    _acilisi_kostur()
    engine = create_engine(url)
    _temizle(engine)

    from fastapi.testclient import TestClient

    from app.auth import hash_password
    from app.config import settings
    from app.main import app

    an = datetime.now(timezone.utc)
    with engine.begin() as c:
        cid = c.execute(text(
            "INSERT INTO companies(name,is_active,created_at) VALUES(:n,true,:t) RETURNING id"),
            {"n": f"{ONEK}-A", "t": an}).scalar_one()
        uid = c.execute(text(
            "INSERT INTO app_users(username,email,email_verified,display_name,password_hash,role,"
            "is_active,must_change_password,created_at) VALUES(:k,:e,true,'H54',:p,'admin',true,false,:t)"
            " RETURNING id"),
            {"k": KULLANICI, "e": f"{KULLANICI}@ornek.test", "p": hash_password(PAROLA), "t": an}).scalar_one()
        c.execute(text("INSERT INTO user_company_memberships(user_id,company_id,is_default,created_at) "
                       "VALUES(:u,:c,true,:t)"), {"u": uid, "c": cid, "t": an})
    onceki = settings.sungur_platform_operators
    settings.sungur_platform_operators = str(uid)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            giris = client.post("/api/auth/login", json={"username": KULLANICI, "password": PAROLA})
            assert giris.status_code == 200, giris.text
            client.cookies.clear()
            client.headers.update({
                "Authorization": "Bearer " + giris.json()["access_token"],
                "X-Company-ID": str(cid),
            })
            yield client
    finally:
        settings.sungur_platform_operators = onceki
        _temizle(engine)
        engine.dispose()


@pytest.mark.parametrize("yol", UCLAR)
def test_TABAN_offset_sifir_200(istemci, yol: str) -> None:
    """Kapı doğru kuruldu mu: aynı istemci her uca 200 ile ulaşıyor. Bu yeşil
    olmadan aşağıdaki 422'ler bir yetki 4xx'i de olabilirdi."""
    yanit = istemci.get(f"{yol}?offset=0")
    assert yanit.status_code == 200, (yol, yanit.status_code, yanit.text[:400])


@pytest.mark.parametrize("tasan", [2**31, 2**63])
@pytest.mark.parametrize("yol", UCLAR)
def test_OFFSET_int4_USTU_422_500_DEGIL(istemci, yol: str, tasan: int) -> None:
    """Tabanda (tavansız `offset`) 2^63 PG'de 500 (`bigint out of range`).
    500 OLMADIĞI ayrıca iddia ediliyor: yalnız "422 değilse kırmızı" diyen bir
    iddia, tavan kaldırılınca 500'ü de 200'ü de aynı mesajla boğardı.
    MUTASYON: bir uçtan `le=INT4_UST`yi silmek o ucun 2^63 dalını 500'e, 2^31
    dalını 200'e döndürür."""
    yanit = istemci.get(f"{yol}?offset={tasan}")
    assert yanit.status_code != 500, f"sürücü taşması uca sızdı: {yol} {yanit.text[:400]}"
    assert yanit.status_code == 422, (yol, yanit.status_code, yanit.text[:400])
    assert yanit.json()["detail"][0]["loc"] == ["query", "offset"]


@pytest.mark.parametrize("yol", UCLAR)
def test_OFFSET_sinirda_INT4_UST_200_bos_sayfa(istemci, yol: str) -> None:
    """Sınırın kendisi geçerli ve gerçek PG'de koşuyor: boş sayfa, 500 değil."""
    from app.sinirlar import INT4_UST

    yanit = istemci.get(f"{yol}?offset={INT4_UST}")
    assert yanit.status_code == 200, (yol, yanit.status_code, yanit.text[:400])
    assert yanit.json()["items"] == []
