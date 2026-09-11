"""PostgreSQL ikizi: PP1 platform paneli — firmasız denetim satırı GERÇEK
CHECK'e ve GERÇEK sütun genişliğine karşı; yedi GET gerçek lehçede.

SQLite ikizi ``tests/test_pp1_platform_paneli.py`` davranışı ölçüyor (muafiyet,
403'ler, sayılar, yanıt anahtar kümesi). Bu dosya yalnız GELİŞTİRME
LEHÇESİNDE GÖRÜNMEYEN üç şeyi ölçer:

1. **CHECK ``ck_security_audit_logs_untenanted_only_preauth`` GERÇEKTEN
   ISIRIYOR** (PostgreSQL'de NOT VALID eklenir: eski satırlar taranmaz, YENİ
   yazımlar tam zorlanır). Kimliği olan firmasız satır burada REDDEDİLİR;
   platform yazıcısının ve ara katmanın firmasız satırları ise KABUL EDİLİR.
   Kısıt ısırmasaydı ikinci yarının yeşili anlamsız olurdu — bu yüzden önce
   ihlal DENENİR.
2. **``action`` ``VARCHAR(20)``**: SQLite uzunluğu zorlamaz. Platform olay
   kataloğunun HER kodu gerçek sütuna yazılır; 21 karakterlik bir kod burada
   ``StringDataRightTruncation`` verirdi.
3. **Yedi GET'in SQL'i PostgreSQL'de koşuyor**: ``SUM(CASE ...)`` boolean
   karşılaştırmaları, ``COALESCE(boolean, false) IN (...)``, ``LOWER(...)
   LIKE ... ESCAPE``, birleşimli ``GROUP BY`` (e-belge dağılımı adı da
   grupluyor — PostgreSQL seçilen her gruplanmamış sütunu REDDEDER).

Paylaşılan veritabanı: CI'da PostgreSQL ikizleri AYNI veritabanını paylaşır.
Her koşu kendi önekini (``KOSU``) kullanır ve yazdığı kullanıcıyı/firmayı
siler; denetim satırları SİLİNMEZ (denetim kaydı silinmek için yazılmıyor)
ama koşu önekiyle ayırt edilir.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

BACKEND = Path(__file__).resolve().parent

pytestmark = pytest.mark.postgresql

KOSU = uuid4().hex[:8]
PAROLA = "Pp1PlatformPg!2026"
OPERATOR = "pp1op-" + KOSU

YENI_UCLAR = (
    "/api/platform/overview",
    "/api/platform/companies",
    "/api/platform/users",
    "/api/platform/verifications",
    "/api/platform/outbox/health",
    "/api/platform/rate-limits",
    "/api/platform/edocuments/health",
)


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("PP1 ikizi APP_TEST_DATABASE_URL ister")
    return url


def _temizle(engine) -> None:
    with engine.begin() as c:
        uid = c.execute(text("SELECT id FROM app_users WHERE username=:u"), {"u": OPERATOR}).scalar()
        if uid is not None:
            c.execute(text("DELETE FROM auth_tokens WHERE user_id=:u"), {"u": uid})
            c.execute(text("DELETE FROM auth_refresh_tokens WHERE user_id=:u"), {"u": uid})
            c.execute(text("DELETE FROM app_users WHERE id=:u"), {"u": uid})


@pytest.fixture(scope="module")
def ortam(tmp_path_factory):
    url = _url()
    config = Config(str(BACKEND / "alembic.ini"))
    engine = create_engine(url)
    command.upgrade(config, "head")
    _temizle(engine)

    from fastapi.testclient import TestClient

    from app.auth import hash_password
    from app.config import settings
    from app.main import app

    with engine.begin() as c:
        op = int(c.execute(text(
            "INSERT INTO app_users(username,email,email_verified,display_name,password_hash,role,"
            "is_active,created_at,must_change_password) VALUES "
            "(:u,:e,true,'PP1 Operatör',:h,'admin',true,:t,false) RETURNING id"),
            {"u": OPERATOR, "e": OPERATOR + "@platform.example", "h": hash_password(PAROLA),
             "t": datetime.now(timezone.utc)}).scalar_one())
    onceki = settings.sungur_platform_operators
    settings.sungur_platform_operators = str(op)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            giris = client.post("/api/auth/login", json={"username": OPERATOR, "password": PAROLA})
            assert giris.status_code == 200, giris.text
            client.cookies.clear()
            yield {"engine": engine, "client": client, "op": op,
                   "h": {"Authorization": "Bearer " + giris.json()["access_token"]}}
    finally:
        settings.sungur_platform_operators = onceki
        _temizle(engine)
        engine.dispose()


@pytest.mark.parametrize("yol", YENI_UCLAR + ("/api/platform/backups", "/api/platform/audit"))
def test_SIFIR_UYELIKLI_operator_pg_200(ortam, yol: str) -> None:
    cevap = ortam["client"].get(yol, headers=ortam["h"])
    assert cevap.status_code == 200, (yol, cevap.status_code, cevap.text[:400])


def test_CHECK_kimlikli_firmasiz_satiri_GERCEKTEN_reddediyor(ortam) -> None:
    """Önce ihlal DENENİR: kısıt ısırmıyorsa aşağıdaki yeşiller anlamsızdır."""
    with pytest.raises(IntegrityError):
        with ortam["engine"].begin() as c:
            c.execute(text(
                "INSERT INTO security_audit_logs(user_id,username,action,path,status_code,created_at,"
                "outcome,company_id) VALUES (:u,:k,'POST',:p,200,now(),'success',NULL)"),
                {"u": ortam["op"], "k": OPERATOR, "p": "/api/platform/ihlal-" + KOSU})


def test_ARA_KATMAN_platform_satiri_kisiti_TUTAR(ortam) -> None:
    """Operatörün platform POST'u (yönlendirici 4xx döner) firmasız satır yazar."""
    from app.main import audit_sink_healthy

    istek = "pp1pg" + KOSU
    cevap = ortam["client"].post(
        f"/api/platform/backups/yok-{KOSU}.dump/verify",
        headers={**ortam["h"], "X-Request-ID": istek, "X-Company-Id": "1"},
    )
    assert cevap.status_code in (400, 404), cevap.text
    with ortam["engine"].connect() as c:
        satir = c.execute(text(
            "SELECT company_id,user_id,username,failure_reason FROM security_audit_logs "
            "WHERE request_id=:r"), {"r": istek}).one()
    assert satir[0] is None and satir[1] is None and satir[2] is None, satir
    assert f"aktor={ortam['op']}" in satir[3], satir
    assert audit_sink_healthy()


def test_OLAY_KATALOGU_gercek_VARCHAR20_ve_CHECKe_sigar(ortam) -> None:
    from app.platform_denetim import PLATFORM_OLAYLARI, platform_olayi_yaz

    istek = "pp1kat" + KOSU
    sahte = SimpleNamespace(
        state=SimpleNamespace(user={"id": ortam["op"], "username": OPERATOR},
                              request_id=istek, auth_source="bearer"),
        url=SimpleNamespace(path="/api/platform/backups"),
        client=SimpleNamespace(host="203.0.113.9"),
        headers={"user-agent": "pp1-ikiz"},
    )
    for olay in PLATFORM_OLAYLARI:
        platform_olayi_yaz(sahte, olay, f"PP1 ikiz {KOSU}")
    with ortam["engine"].connect() as c:
        satirlar = c.execute(text(
            "SELECT action,company_id,user_id,outcome,status_code FROM security_audit_logs "
            "WHERE request_id=:r ORDER BY id"), {"r": istek}).all()
        tenant = c.execute(text(
            "SELECT COUNT(*) FROM activity_logs WHERE correlation_id=:r"), {"r": istek}).scalar_one()
    assert [s[0] for s in satirlar] == list(PLATFORM_OLAYLARI.values())
    assert all(s[1] is None and s[2] is None for s in satirlar), satirlar
    assert {s[3] for s in satirlar if s[0].endswith("_fail")} == {"error"}
    assert {s[4] for s in satirlar if s[0].endswith("_fail")} == {409}
    assert tenant == 0
