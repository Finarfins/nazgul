"""PostgreSQL ikizi: PP2 platform yönetim eylemleri — yedi eylemin denetim
satırı GERÇEK CHECK'e karşı; dondurma yolu ve süzgeçler gerçek lehçede.

SQLite ikizi ``tests/test_pp2_platform_eylemleri.py`` davranışı ölçüyor
(kapılar, idempotensi, oturum süpürgesi, retry sayıları). Bu dosya yalnız
GELİŞTİRME LEHÇESİNDE GÖRÜNMEYEN şeyleri ölçer:

1. **CHECK ``ck_security_audit_logs_untenanted_only_preauth`` ISIRIYOR** ve
   yedi eylemin HER BİRİNİN satırı (``company_id`` NULL, ``user_id`` NULL)
   ona rağmen YAZILIYOR. Önce ihlal DENENİR: kısıt ısırmasaydı ikinci yarı
   anlamsız olurdu.
2. **Yedi kodun gerçek ``VARCHAR(20)``ye sığması** (SQLite uzunluğu zorlamaz).
3. **Dondurma yolu PostgreSQL'de**: ``companies.is_active IS TRUE``
   birleşimi (``tenancy.resolve_company``) gerçek boolean ile.
4. **Retry ve süzgeç SQL'i**: ``:p IS NULL OR ...`` tipli bağlı parametreler
   (PostgreSQL tipsiz NULL bağını ``could not determine data type`` ile
   REDDEDER), ``timestamptz`` karşılaştırması, ``COALESCE`` + ``SUM(CASE)``.

Paylaşılan veritabanı: her koşu kendi önekini (``KOSU``) kullanır. Retry
yalnız bu koşunun KANALINDA çağrılır (başka ikizin FAILED satırına
dokunulmaz); hız sınırı yalnız bu koşunun IP'sinde silinir. Yazılan firma,
kullanıcılar ve kiracı satırları silinir; denetim satırları SİLİNMEZ ama
koşu önekli ``X-Request-ID`` ile ayırt edilir.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
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
PAROLA = "Pp2PlatformPg!2026"
OPERATOR = "pp2op-" + KOSU
UYE = "pp2uye-" + KOSU
HEDEF = "pp2hedef-" + KOSU
KANAL = "PP2" + KOSU
IP = "pp2-" + KOSU


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("PP2 ikizi APP_TEST_DATABASE_URL ister")
    return url


def _temizle(engine) -> None:
    with engine.begin() as c:
        firma = c.execute(text("SELECT id FROM companies WHERE name=:n"),
                          {"n": "PP2 Firma " + KOSU}).scalar()
        uidler = [r[0] for r in c.execute(
            text("SELECT id FROM app_users WHERE username IN (:a,:b,:c)"),
            {"a": OPERATOR, "b": UYE, "c": HEDEF}).all()]
        for uid in uidler:
            for tablo in ("auth_tokens", "auth_refresh_tokens", "email_verification_tokens",
                          "user_company_memberships"):
                c.execute(text(f"DELETE FROM {tablo} WHERE user_id=:u"), {"u": uid})
        if firma is not None:
            c.execute(text("DELETE FROM notifications WHERE company_id=:c"), {"c": firma})
            c.execute(text("DELETE FROM push_devices WHERE company_id=:c"), {"c": firma})
            c.execute(text("DELETE FROM user_company_memberships WHERE company_id=:c"), {"c": firma})
            c.execute(text("DELETE FROM companies WHERE id=:c"), {"c": firma})
        for uid in uidler:
            c.execute(text("DELETE FROM app_users WHERE id=:u"), {"u": uid})
        c.execute(text("DELETE FROM auth_rate_limits WHERE ip_address=:i"), {"i": IP})
        for ad in (OPERATOR, UYE, HEDEF):
            c.execute(text("DELETE FROM login_attempts WHERE username=:u"), {"u": ad})


@pytest.fixture(scope="module")
def ortam():
    url = _url()
    config = Config(str(BACKEND / "alembic.ini"))
    engine = create_engine(url)
    command.upgrade(config, "head")
    _temizle(engine)

    from fastapi.testclient import TestClient

    from app.auth import hash_password
    from app.config import settings
    from app.main import app

    simdi = datetime.now(timezone.utc)
    ph = hash_password(PAROLA)
    with engine.begin() as c:
        firma = int(c.execute(text(
            "INSERT INTO companies(name,is_active,created_at) VALUES (:n,true,:t) RETURNING id"),
            {"n": "PP2 Firma " + KOSU, "t": simdi}).scalar_one())

        def kullanici(ad: str, rol: str, dogrulandi: bool) -> int:
            return int(c.execute(text(
                "INSERT INTO app_users(username,email,email_verified,display_name,password_hash,role,"
                "is_active,created_at,must_change_password) VALUES "
                "(:u,:e,:v,:d,:h,:r,true,:t,false) RETURNING id"),
                {"u": ad, "e": ad + "@platform.example", "v": dogrulandi, "d": ad,
                 "h": ph, "r": rol, "t": simdi}).scalar_one())

        op = kullanici(OPERATOR, "admin", True)
        uye = kullanici(UYE, "yonetici", True)
        hedef = kullanici(HEDEF, "admin", False)
        for uid in (uye, hedef):
            c.execute(text(
                "INSERT INTO user_company_memberships(user_id,company_id,is_default,created_at) "
                "VALUES (:u,:c,true,:t)"), {"u": uid, "c": firma, "t": simdi})
        c.execute(text(
            "INSERT INTO notifications(company_id,type,channel,recipient,template,payload,status,"
            "dispatch_armed,approved_by,approved_at,created_at,updated_at) VALUES "
            "(:c,'MANUAL',:k,'gizli@pp2.example','tmpl','GIZLI-YUK',:s,true,1,:t,:t,:t)"),
            {"c": firma, "k": KANAL, "s": "FAILED", "t": simdi})
        c.execute(text("INSERT INTO auth_rate_limits(action,ip_address,attempted_at) VALUES ('login',:i,:t)"),
                  {"i": IP, "t": simdi})
    onceki = settings.sungur_platform_operators
    settings.sungur_platform_operators = str(op)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            giris = client.post("/api/auth/login", json={"username": OPERATOR, "password": PAROLA})
            assert giris.status_code == 200, giris.text
            uye_giris = client.post("/api/auth/login", json={"username": UYE, "password": PAROLA})
            assert uye_giris.status_code == 200, uye_giris.text
            client.cookies.clear()
            yield {"engine": engine, "client": client, "op": op, "firma": firma, "hedef": hedef,
                   "h": {"Authorization": "Bearer " + giris.json()["access_token"]},
                   "h_uye": {"Authorization": "Bearer " + uye_giris.json()["access_token"]}}
    finally:
        settings.sungur_platform_operators = onceki
        _temizle(engine)
        engine.dispose()


def test_CHECK_kimlikli_firmasiz_satiri_GERCEKTEN_reddediyor(ortam) -> None:
    """Önce ihlal DENENİR: kısıt ısırmıyorsa aşağıdaki yeşiller anlamsızdır."""
    with pytest.raises(IntegrityError):
        with ortam["engine"].begin() as c:
            c.execute(text(
                "INSERT INTO security_audit_logs(user_id,username,action,path,status_code,created_at,"
                "outcome,company_id) VALUES (:u,:k,'platform.co_deact',:p,200,now(),'success',NULL)"),
                {"u": ortam["op"], "k": OPERATOR, "p": "/api/platform/ihlal-" + KOSU})


def _satir(ortam, istek: str, kod: str):
    with ortam["engine"].connect() as c:
        return c.execute(text(
            "SELECT company_id,user_id,username,failure_reason,status_code FROM security_audit_logs "
            "WHERE request_id=:r AND action=:a"), {"r": istek, "a": kod}).all()


def test_YEDI_eylem_ve_DONDURMA_yolu_PG(ortam) -> None:
    from app.main import audit_sink_healthy

    client, h, firma, hedef = ortam["client"], ortam["h"], ortam["firma"], ortam["hedef"]
    assert client.get("/api/customers", headers=ortam["h_uye"]).status_code == 200

    adimlar = (
        ("deactivate", "POST", f"/api/platform/companies/{firma}/deactivate", None, "platform.co_deact"),
        ("activate", "POST", f"/api/platform/companies/{firma}/activate", None, "platform.co_activate"),
        ("lock", "POST", f"/api/platform/users/{hedef}/status", {"locked": True}, "platform.us_status"),
        ("unlock", "POST", f"/api/platform/users/{hedef}/status", {"locked": False}, "platform.us_status"),
        ("resend", "POST", f"/api/platform/users/{hedef}/resend-verification", None, "platform.us_resend"),
        ("pwreset", "POST", f"/api/platform/users/{hedef}/force-password-reset", None,
         "platform.us_pwreset"),
        ("rl_clear", "DELETE", f"/api/platform/rate-limits?ip={IP}", None, "platform.rl_clear"),
        ("ob_retry", "POST", "/api/platform/outbox/retry", {"channel": KANAL}, "platform.ob_retry"),
    )
    for ad, metot, yol, govde, kod in adimlar:
        istek = f"pp2pg{ad}{KOSU}"
        kw = {"headers": {**h, "X-Request-ID": istek}}
        if govde is not None:
            kw["json"] = govde
        cevap = client.request(metot, yol, **kw)
        assert cevap.status_code == 200, (ad, cevap.text)
        assert cevap.json()["changed"] is True, (ad, cevap.json())
        satirlar = _satir(ortam, istek, kod)
        assert len(satirlar) == 1, (ad, satirlar)
        s = satirlar[0]
        assert s[0] is None and s[1] is None and s[2] is None, (ad, s)
        assert str(s[3]).startswith(f"aktor={ortam['op']};"), (ad, s)
        assert s[4] == 200, (ad, s)
        if ad == "deactivate":
            # Donmuş firmanın üyesi: gerçek boolean birleşimiyle 403.
            red = client.get("/api/customers", headers=ortam["h_uye"])
            assert red.status_code == 403, red.text
            assert red.json().get("code") == "COMPANY_ACCESS_DENIED"
        if ad == "activate":
            assert client.get("/api/customers", headers=ortam["h_uye"]).status_code == 200
        if ad == "ob_retry":
            assert cevap.json()["requeued"] == 1, cevap.json()
    with ortam["engine"].connect() as c:
        durum = c.execute(text("SELECT status FROM notifications WHERE channel=:k"), {"k": KANAL}).scalar_one()
    assert durum == "RETRY_SCHEDULED"
    assert audit_sink_healthy()


def test_DENETIM_suzgecleri_PG_tipli_NULL_baglari(ortam) -> None:
    """Her süzgeç hem NULL hem dolu bağlanır; PG tipsiz NULL'u reddederdi."""
    client, h = ortam["client"], ortam["h"]
    assert client.post(f"/api/platform/companies/{ortam['firma']}/deactivate",
                       headers={**h, "X-Request-ID": "pp2pgf" + KOSU}).status_code == 200
    assert client.post(f"/api/platform/companies/{ortam['firma']}/activate", headers=h).status_code == 200
    gecmis = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    gelecek = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    def al(**p):
        cevap = client.get("/api/platform/audit", params=p, headers=h)
        assert cevap.status_code == 200, (p, cevap.text)
        return cevap.json()

    assert al(limit=5)
    satirlar = al(action="platform.co_deact", username=OPERATOR, status_code=200,
                  ip_address="testclient", date_from=gecmis, date_to=gelecek, limit=1000)
    assert any(r["request_id"] == "pp2pgf" + KOSU for r in satirlar), satirlar[:3]
    assert all(r["action"] == "platform.co_deact" and r["company_id"] is None for r in satirlar)
    assert al(action="platform.co_deact", date_from=gelecek) == []
