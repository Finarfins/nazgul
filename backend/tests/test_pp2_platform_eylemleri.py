"""PP2 — platform yönetim eylemleri + denetim (göç YOK).

Konu: ``app/routers/platform_management.py`` (yedi yazma ucu),
``app/routers/platform_audit.py`` (süzgeçler), ``app/platform_denetim.py``
(yedi yeni katalog kodu).

--- ÖLÇÜLEN, VARSAYILMAYAN -------------------------------------------------

* Ara katman izni yazma yollarında ``__admin_only__`` (PP1'in ``read`` kuralı
  YALNIZ güvenli metotları kapsar) ve operatör bu kapıyı geçer: operatör
  tanım gereği ``admin``dir.
* Firma dondurma YENİ kod istemedi: ``tenancy.resolve_company`` üyeliği
  ``companies.is_active IS TRUE`` ile birleştiriyor; pasif firmanın üyesi
  kiracı ucunda 403 COMPANY_ACCESS_DENIED alıyor.
* Kuyruk retry'ı ``FAILED -> RETRY_SCHEDULED``dır (kapalı geçiş tablosu
  ``FAILED -> PENDING``e izin VERMEZ).

--- MUTASYON TABLOSU ---------------------------------------------------------

  * herhangi bir uçtan ``require_platform_operator``ı silmek
                         -> ``test_operator_olmayan_admin_403`` KIRMIZI
  * ``platform_olayi_yaz`` çağrısını silmek
                         -> eylem testlerinin satır sayısı KIRMIZI
  * idempotensi dalını (``changed: False``) silmek
                         -> ikinci çağrıda ikinci satır -> KIRMIZI
  * kilitte ``revoke_user_*`` çağrılarını silmek
                         -> ``test_kilit_*`` (jeton sayısı) KIRMIZI
  * retry yüklemlerinden birini (onay/silah/rıza) silmek
                         -> ``test_kuyruk_retry_*`` sayıları KIRMIZI
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
PAROLA = "PlatformEylem!2026x"
ANAHTAR = "pp2-platform-eylemleri-anahtar-pp2-platform-eylemleri-anahtar"
ISTEMCI_IP = "testclient"  # TestClient'in ``request.client.host``u

#: Yeni katalog kodları — ``security_audit_logs.action`` ``String(20)``.
KODLAR = {
    "activate": "platform.co_activate",
    "deactivate": "platform.co_deact",
    "status": "platform.us_status",
    "resend": "platform.us_resend",
    "pwreset": "platform.us_pwreset",
    "rl_clear": "platform.rl_clear",
    "ob_retry": "platform.ob_retry",
}


def _app_anahtarlari() -> set[str]:
    return {k for k in sys.modules if k == "app" or k.startswith("app.")}


@contextmanager
def _uygulama(tmp_path: Path):
    """``app`` paketini bu dosyanın veritabanıyla TAZE içe aktarır, sonra geri koyar."""
    onceki = {k: sys.modules[k] for k in _app_anahtarlari()}
    mp = pytest.MonkeyPatch()
    for ad in list(_app_anahtarlari()):
        del sys.modules[ad]
    mp.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'pp2.db').as_posix()}")
    mp.setenv("SUNGUR_DATA_DIR", str(tmp_path))
    mp.setenv("SECRET_KEY", ANAHTAR)
    mp.setenv("BOOTSTRAP_ADMIN_PASSWORD", PAROLA)
    mp.setenv("AUTO_MIGRATE", "true")
    mp.setenv("SUNGUR_PLATFORM_OPERATORS", "")
    mp.syspath_prepend(str(BACKEND))
    try:
        from fastapi.testclient import TestClient

        import app.main as main
        from app.db import engine

        with TestClient(main.app, raise_server_exceptions=False) as client:
            yield main, engine, client
    finally:
        mp.undo()
        for ad in list(_app_anahtarlari()):
            del sys.modules[ad]
        sys.modules.update(onceki)


def _sql(engine, sorgu: str, **p):
    from sqlalchemy import text

    with engine.connect() as c:
        return c.execute(text(sorgu), p).all()


def _yaz(engine, sorgu: str, **p):
    from sqlalchemy import text

    with engine.begin() as c:
        sonuc = c.execute(text(sorgu), p)
        return sonuc.scalar_one() if sonuc.returns_rows else None


def _giris_ham(client, kullanici: str):
    cevap = client.post("/api/auth/login", json={"username": kullanici, "password": PAROLA})
    client.cookies.clear()  # Bearer ile devam; CSRF bu dosyanın konusu değil
    return cevap


def _giris(client, kullanici: str) -> dict:
    cevap = _giris_ham(client, kullanici)
    assert cevap.status_code == 200, cevap.text
    return {"Authorization": "Bearer " + cevap.json()["access_token"]}


def _tohumla(engine) -> dict:
    from sqlalchemy import text

    from app.auth import hash_password

    simdi = datetime.now(timezone.utc)
    ph = hash_password(PAROLA)
    with engine.begin() as c:
        c.execute(text("UPDATE app_users SET must_change_password=0, email_verified=1"))
        a = int(c.execute(text("SELECT MIN(id) FROM companies")).scalar_one())

        def firma(ad: str) -> int:
            return int(c.execute(
                text("INSERT INTO companies(name,is_active,created_at) VALUES (:n,1,:t) RETURNING id"),
                {"n": ad, "t": simdi}).scalar_one())

        b = firma("Bravo Tarim")
        dondur = firma("Dondurulacak Firma")

        def kullanici(ad: str, rol: str, *, dogrulandi: bool = True, eposta: str | None = None) -> int:
            return int(c.execute(text(
                "INSERT INTO app_users(username,email,email_verified,display_name,password_hash,"
                "role,is_active,created_at,must_change_password) VALUES "
                "(:u,:e,:v,:d,:h,:r,1,:t,0) RETURNING id"),
                {"u": ad, "e": eposta or f"{ad}@platform.example", "v": dogrulandi,
                 "d": ad.title(), "h": ph, "r": rol, "t": simdi}).scalar_one())

        op = kullanici("operator", "admin")                 # SIFIR üyelik
        admin_b = kullanici("bravoadmin", "admin")          # B admini, operatör DEĞİL
        yon = kullanici("yonetici1", "yonetici")
        uye_d = kullanici("donukuye", "yonetici")           # YALNIZ dondurulacak firmada
        kilit = kullanici("kilitlenecek", "yonetici")
        sifirla = kullanici("sifirlanacak", "yonetici")
        yeni = kullanici("dogrulanmamis", "admin", dogrulandi=False)
        eski = kullanici("eskiadres", "admin", dogrulandi=False,
                         eposta="legacy-user-99@legacy.invalid")
        for uid, cid in ((admin_b, b), (yon, a), (uye_d, dondur), (kilit, a),
                         (sifirla, a), (yeni, b), (eski, b)):
            c.execute(text(
                "INSERT INTO user_company_memberships(user_id,company_id,is_default,created_at) "
                "VALUES (:u,:c,1,:t)"), {"u": uid, "c": cid, "t": simdi})
    return {"a": a, "b": b, "dondur": dondur, "op": op, "admin_b": admin_b, "yon": yon,
            "uye_d": uye_d, "kilit": kilit, "sifirla": sifirla, "yeni": yeni, "eski": eski}


@pytest.fixture(scope="module")
def ortam(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pp2")
    with _uygulama(tmp) as (main, engine, client):
        kimlik = _tohumla(engine)
        from app.config import settings

        settings.sungur_platform_operators = str(kimlik["op"])
        try:
            yield {"main": main, "engine": engine, "client": client, **kimlik,
                   "h_op": _giris(client, "operator"),
                   "h_admin_b": _giris(client, "bravoadmin"),
                   "h_yon": _giris(client, "yonetici1")}
        finally:
            settings.sungur_platform_operators = ""


@pytest.fixture(autouse=True)
def _hiz_sayaclari_temiz(request):
    """Her test temiz hız sınırıyla başlar: sıra (ileri/ters/karışık) sonucu değiştirmez."""
    if "ortam" in request.fixturenames:
        e = request.getfixturevalue("ortam")["engine"]
        _yaz(e, "DELETE FROM auth_rate_limits")
        _yaz(e, "DELETE FROM login_attempts")
    yield


def _satir_sayisi(engine, kod: str) -> int:
    return int(_sql(engine, "SELECT COUNT(*) FROM security_audit_logs WHERE action=:a", a=kod)[0][0])


def _son_satir(engine, kod: str):
    return _sql(engine,
                "SELECT company_id,user_id,username,path,status_code,outcome,failure_reason "
                "FROM security_audit_logs WHERE action=:a ORDER BY id DESC LIMIT 1", a=kod)[0]


def _denetim_satiri_dogru(ortam, kod: str, yol_parcasi: str) -> None:
    satir = _son_satir(ortam["engine"], kod)
    assert satir[0] is None and satir[1] is None and satir[2] is None, satir  # firmasız, kimliksiz
    assert yol_parcasi in satir[3], satir
    assert satir[4] == 200 and satir[5] == "success", satir
    assert str(satir[6]).startswith(f"aktor={ortam['op']};"), satir


# ------------------------------------------------------------- kapı ve izin ---

#: (ad, metot, yol şablonu, gövde). Yol şablonu ``ortam`` anahtarlarıyla dolar.
EYLEMLER = (
    ("activate", "POST", "/api/platform/companies/{b}/activate", None),
    ("deactivate", "POST", "/api/platform/companies/{b}/deactivate", None),
    ("status", "POST", "/api/platform/users/{yon}/status", {"locked": True}),
    ("resend", "POST", "/api/platform/users/{yeni}/resend-verification", None),
    ("pwreset", "POST", "/api/platform/users/{yon}/force-password-reset", None),
    ("rl_clear", "DELETE", "/api/platform/rate-limits?ip=198.51.100.9", None),
    ("ob_retry", "POST", "/api/platform/outbox/retry", {}),
)


def _istek(client, metot: str, yol: str, govde, basliklar: dict | None):
    kw = {"headers": basliklar or {}}
    if govde is not None:
        kw["json"] = govde
    return client.request(metot, yol, **kw)


def test_ara_katman_izni_yazmada_admin_only_ve_operator_gecer(ortam) -> None:
    from app.auth import has_permission, required_permission

    for _, metot, sablon, _ in EYLEMLER:
        yol = sablon.split("?")[0].format(b=1, yon=1, yeni=1)
        assert required_permission(metot, yol) == "__admin_only__", (metot, yol)
    assert required_permission("POST", "/api/platform/companies/1/deactivate") == "__admin_only__"
    # PP1 kuralı yalnız güvenli metotları kapsıyor: GET hâlâ ``read``.
    assert required_permission("GET", "/api/platform/rate-limits") == "read"
    assert has_permission("admin", "__admin_only__")
    assert not has_permission("yonetici", "__admin_only__")


def _durum_fotografi(ortam) -> tuple:
    e = ortam["engine"]
    return (
        _sql(e, "SELECT id,is_active FROM companies ORDER BY id"),
        _sql(e, "SELECT id,is_active,must_change_password FROM app_users ORDER BY id"),
        _sql(e, "SELECT id,status FROM notifications ORDER BY id"),
        _sql(e, "SELECT COUNT(*) FROM security_audit_logs WHERE action LIKE 'platform.%'"),
    )


@pytest.mark.parametrize("ad,metot,sablon,govde", EYLEMLER, ids=[e[0] for e in EYLEMLER])
def test_operator_olmayan_admin_403(ortam, ad, metot, sablon, govde) -> None:
    """Üyeliği olan ama listede OLMAYAN admin: yönlendiricinin 403'ü, HİÇBİR yan etki yok."""
    once = _durum_fotografi(ortam)
    cevap = _istek(ortam["client"], metot, sablon.format(**ortam), govde, ortam["h_admin_b"])
    assert cevap.status_code == 403, (ad, cevap.text)
    assert cevap.json()["detail"] == "Platform operatörü yetkisi gerekli"
    assert _durum_fotografi(ortam) == once


@pytest.mark.parametrize("ad,metot,sablon,govde", EYLEMLER, ids=[e[0] for e in EYLEMLER])
def test_normal_kullanici_403_ara_katmanda(ortam, ad, metot, sablon, govde) -> None:
    once = _durum_fotografi(ortam)
    cevap = _istek(ortam["client"], metot, sablon.format(**ortam), govde, ortam["h_yon"])
    assert cevap.status_code == 403, (ad, cevap.text)
    assert cevap.json().get("code") == "PERMISSION_DENIED"
    assert _durum_fotografi(ortam) == once


@pytest.mark.parametrize("ad,metot,sablon,govde", EYLEMLER, ids=[e[0] for e in EYLEMLER])
def test_anonim_401(ortam, ad, metot, sablon, govde) -> None:
    once = _durum_fotografi(ortam)
    cevap = _istek(ortam["client"], metot, sablon.format(**ortam), govde, None)
    assert cevap.status_code == 401, (ad, cevap.text)
    assert _durum_fotografi(ortam) == once


def test_katalog_kodlari_sutuna_sigar_ve_katalogda(ortam) -> None:
    from app.platform_denetim import EYLEM_GENISLIGI, PLATFORM_OLAYLARI

    assert EYLEM_GENISLIGI == 20
    for kod in KODLAR.values():
        assert len(kod) <= EYLEM_GENISLIGI, kod
        assert kod in PLATFORM_OLAYLARI.values(), kod


# ------------------------------------------------------- firma dondur / aç ---

def test_firma_dondur_uye_403_ac_geri_200_tek_denetim_satiri(ortam) -> None:
    client, e, fid = ortam["client"], ortam["engine"], ortam["dondur"]
    h_uye = _giris(client, "donukuye")
    assert client.get("/api/customers", headers=h_uye).status_code == 200

    once_d, once_a = _satir_sayisi(e, KODLAR["deactivate"]), _satir_sayisi(e, KODLAR["activate"])
    cevap = client.post(f"/api/platform/companies/{fid}/deactivate", headers=ortam["h_op"])
    assert cevap.status_code == 200, cevap.text
    assert cevap.json() == {"id": fid, "is_active": False, "changed": True}
    assert _satir_sayisi(e, KODLAR["deactivate"]) == once_d + 1
    _denetim_satiri_dogru(ortam, KODLAR["deactivate"], f"/companies/{fid}/deactivate")

    # Donmuş firmanın üyesi: kiracı ucunda 403 COMPANY_ACCESS_DENIED.
    red = client.get("/api/customers", headers=h_uye)
    assert red.status_code == 403, red.text
    assert red.json().get("code") == "COMPANY_ACCESS_DENIED"
    # Üyelik SİLİNMEDİ.
    assert _sql(e, "SELECT COUNT(*) FROM user_company_memberships WHERE company_id=:c", c=fid)[0][0] == 1

    # İdempotent: ikinci dondurma değişiklik değil, ikinci satır YOK.
    tekrar = client.post(f"/api/platform/companies/{fid}/deactivate", headers=ortam["h_op"])
    assert tekrar.status_code == 200 and tekrar.json()["changed"] is False
    assert _satir_sayisi(e, KODLAR["deactivate"]) == once_d + 1

    acildi = client.post(f"/api/platform/companies/{fid}/activate", headers=ortam["h_op"])
    assert acildi.status_code == 200
    assert acildi.json() == {"id": fid, "is_active": True, "changed": True}
    assert _satir_sayisi(e, KODLAR["activate"]) == once_a + 1
    _denetim_satiri_dogru(ortam, KODLAR["activate"], f"/companies/{fid}/activate")
    assert client.get("/api/customers", headers=h_uye).status_code == 200

    tekrar = client.post(f"/api/platform/companies/{fid}/activate", headers=ortam["h_op"])
    assert tekrar.status_code == 200 and tekrar.json()["changed"] is False
    assert _satir_sayisi(e, KODLAR["activate"]) == once_a + 1


def test_olmayan_firma_404(ortam) -> None:
    for eylem in ("activate", "deactivate"):
        cevap = ortam["client"].post(f"/api/platform/companies/999999/{eylem}", headers=ortam["h_op"])
        assert cevap.status_code == 404, cevap.text


# --------------------------------------------------------------- hesap kilidi ---

def test_kilit_oturumu_dusurur_girisi_reddeder_ac_geri_girer(ortam) -> None:
    client, e, uid = ortam["client"], ortam["engine"], ortam["kilit"]
    h = _giris(client, "kilitlenecek")
    assert client.get("/api/auth/me", headers=h).status_code == 200
    once = _satir_sayisi(e, KODLAR["status"])

    cevap = client.post(f"/api/platform/users/{uid}/status", json={"locked": True}, headers=ortam["h_op"])
    assert cevap.status_code == 200, cevap.text
    assert cevap.json() == {"id": uid, "locked": True, "changed": True}
    assert _satir_sayisi(e, KODLAR["status"]) == once + 1
    _denetim_satiri_dogru(ortam, KODLAR["status"], f"/users/{uid}/status")

    assert client.get("/api/auth/me", headers=h).status_code == 401       # açık oturum düştü
    assert _giris_ham(client, "kilitlenecek").status_code == 401         # giriş reddedildi
    # Jetonlar SÜPÜRÜLDÜ: kilit açılınca eski oturum dirilmez.
    assert _sql(e, "SELECT COUNT(*) FROM auth_tokens WHERE user_id=:u", u=uid)[0][0] == 0
    assert _sql(e, "SELECT COUNT(*) FROM auth_refresh_tokens WHERE user_id=:u AND revoked_at IS NULL",
                u=uid)[0][0] == 0

    tekrar = client.post(f"/api/platform/users/{uid}/status", json={"locked": True}, headers=ortam["h_op"])
    assert tekrar.status_code == 200 and tekrar.json()["changed"] is False
    assert _satir_sayisi(e, KODLAR["status"]) == once + 1

    acik = client.post(f"/api/platform/users/{uid}/status", json={"locked": False}, headers=ortam["h_op"])
    assert acik.status_code == 200 and acik.json() == {"id": uid, "locked": False, "changed": True}
    assert _satir_sayisi(e, KODLAR["status"]) == once + 2
    assert client.get("/api/auth/me", headers=h).status_code == 401       # eski jeton hâlâ ölü
    assert _giris_ham(client, "kilitlenecek").status_code == 200


def test_operator_kendini_kilitleyemez_409(ortam) -> None:
    e = ortam["engine"]
    once = _satir_sayisi(e, KODLAR["status"])
    cevap = ortam["client"].post(f"/api/platform/users/{ortam['op']}/status",
                                 json={"locked": True}, headers=ortam["h_op"])
    assert cevap.status_code == 409, cevap.text
    assert bool(_sql(e, "SELECT is_active FROM app_users WHERE id=:u", u=ortam["op"])[0][0])
    assert _satir_sayisi(e, KODLAR["status"]) == once


def test_son_aktif_yoneticiyi_kilitlemek_serbest_notta_gorunur(ortam) -> None:
    """Şef PP2 kararı 4: platform kiracı kuralını ezer; denetim notu bunu söyler."""
    client, e = ortam["client"], ortam["engine"]
    simdi = datetime.now(timezone.utc)
    fid = _yaz(e, "INSERT INTO companies(name,is_active,created_at) VALUES ('Tek Adminli',1,:t) "
                  "RETURNING id", t=simdi)
    uid = _yaz(e, "INSERT INTO app_users(username,email,email_verified,display_name,password_hash,"
                  "role,is_active,created_at,must_change_password) VALUES "
                  "('tekadmin','tekadmin@platform.example',1,'Tek','x','admin',1,:t,0) RETURNING id", t=simdi)
    _yaz(e, "INSERT INTO user_company_memberships(user_id,company_id,is_default,created_at) "
            "VALUES (:u,:c,1,:t)", u=uid, c=fid, t=simdi)

    cevap = client.post(f"/api/platform/users/{uid}/status", json={"locked": True}, headers=ortam["h_op"])
    assert cevap.status_code == 200 and cevap.json()["changed"] is True, cevap.text
    assert f"son aktif yönetici firma={fid}" in str(_son_satir(e, KODLAR["status"])[6])


def test_olmayan_kullanici_404(ortam) -> None:
    client, h = ortam["client"], ortam["h_op"]
    assert client.post("/api/platform/users/999999/status", json={"locked": True},
                       headers=h).status_code == 404
    assert client.post("/api/platform/users/999999/force-password-reset", headers=h).status_code == 404
    assert client.post("/api/platform/users/999999/resend-verification", headers=h).status_code == 404


# ---------------------------------------------------- doğrulama yeniden gönder ---

def test_dogrulama_gonder_kuyruga_alir_tek_satir(ortam) -> None:
    client, e, uid = ortam["client"], ortam["engine"], ortam["yeni"]
    once = _satir_sayisi(e, KODLAR["resend"])
    cevap = client.post(f"/api/platform/users/{uid}/resend-verification", headers=ortam["h_op"])
    assert cevap.status_code == 200, cevap.text
    assert cevap.json() == {"user_id": uid, "queued": True, "changed": True}
    assert _satir_sayisi(e, KODLAR["resend"]) == once + 1
    _denetim_satiri_dogru(ortam, KODLAR["resend"], f"/users/{uid}/resend-verification")
    # Kuyruk satırı kullanıcının ÜYELİĞİNDEKİ firmaya yazıldı (B), operatörün değil.
    satirlar = _sql(e, "SELECT company_id FROM notifications WHERE type='EMAIL_VERIFICATION' "
                       "AND recipient='dogrulanmamis@platform.example'")
    assert satirlar and {r[0] for r in satirlar} == {ortam["b"]}, satirlar
    # Kullanılmamış TEK belirteç kalır (eskisi öldürülür).
    assert _sql(e, "SELECT COUNT(*) FROM email_verification_tokens WHERE user_id=:u AND used_at IS NULL",
                u=uid)[0][0] == 1


def test_dogrulanmis_hesaba_gonderim_409_bir_kez_dogrula(ortam) -> None:
    e = ortam["engine"]
    once = _satir_sayisi(e, KODLAR["resend"])
    cevap = ortam["client"].post(f"/api/platform/users/{ortam['yon']}/resend-verification",
                                 headers=ortam["h_op"])
    assert cevap.status_code == 409, cevap.text
    assert _satir_sayisi(e, KODLAR["resend"]) == once


def test_eski_yer_tutucu_adrese_gonderim_409(ortam) -> None:
    cevap = ortam["client"].post(f"/api/platform/users/{ortam['eski']}/resend-verification",
                                 headers=ortam["h_op"])
    assert cevap.status_code == 409, cevap.text


def test_dogrulama_gonder_hiz_sinirli_kamu_ucu_gibi(ortam) -> None:
    """IP başına saatte 5 — kamu ucuyla aynı yardımcı, AYRI eylem adı."""
    client, h = ortam["client"], ortam["h_op"]
    yol = f"/api/platform/users/{ortam['yon']}/resend-verification"
    for _ in range(5):
        assert client.post(yol, headers=h).status_code == 409
    assert client.post(yol, headers=h).status_code == 429
    eylemler = {r[0] for r in _sql(ortam["engine"], "SELECT DISTINCT action FROM auth_rate_limits")}
    assert eylemler == {"platform_resend_verification"}, eylemler


# ------------------------------------------------------- zorunlu parola sıfırlama ---

def test_zorunlu_sifirlama_oturumlari_dusurur_sonraki_istek_rotasyon_kapisinda(ortam) -> None:
    client, e, uid = ortam["client"], ortam["engine"], ortam["sifirla"]
    h = _giris(client, "sifirlanacak")
    assert client.get("/api/customers", headers=h).status_code == 200
    once = _satir_sayisi(e, KODLAR["pwreset"])

    cevap = client.post(f"/api/platform/users/{uid}/force-password-reset", headers=ortam["h_op"])
    assert cevap.status_code == 200, cevap.text
    assert cevap.json() == {"user_id": uid, "must_change_password": True, "changed": True}
    assert _satir_sayisi(e, KODLAR["pwreset"]) == once + 1
    _denetim_satiri_dogru(ortam, KODLAR["pwreset"], f"/users/{uid}/force-password-reset")

    # logout-all ile aynı süpürge: eski access jetonu ÖLÜ, refresh aileleri iptal.
    assert client.get("/api/customers", headers=h).status_code == 401
    assert _sql(e, "SELECT COUNT(*) FROM auth_tokens WHERE user_id=:u", u=uid)[0][0] == 0
    assert _sql(e, "SELECT COUNT(*) FROM auth_refresh_tokens WHERE user_id=:u AND revoked_at IS NULL",
                u=uid)[0][0] == 0
    # Yeniden giriş mümkün, ama sonraki korumalı istek rotasyon kapısında.
    h2 = _giris(client, "sifirlanacak")
    red = client.get("/api/customers", headers=h2)
    assert red.status_code == 403, red.text
    assert red.json().get("code") == "PASSWORD_CHANGE_REQUIRED"

    tekrar = client.post(f"/api/platform/users/{uid}/force-password-reset", headers=ortam["h_op"])
    assert tekrar.status_code == 200 and tekrar.json()["changed"] is False
    assert _satir_sayisi(e, KODLAR["pwreset"]) == once + 1


# ------------------------------------------------------------ hız sınırı temizle ---

def test_hiz_siniri_temizle_ip_yeniden_girebilir(ortam) -> None:
    from sqlalchemy import text

    from app.config import settings

    client, e = ortam["client"], ortam["engine"]
    tavan = settings.login_ip_limit_per_hour
    simdi = datetime.now(timezone.utc)
    with e.begin() as c:
        c.execute(text("INSERT INTO auth_rate_limits(action,ip_address,attempted_at) VALUES ('login',:i,:t)"),
                  [{"i": ISTEMCI_IP, "t": simdi}] * tavan)
        c.execute(text("INSERT INTO auth_rate_limits(action,ip_address,attempted_at) VALUES ('login',:i,:t)"),
                  {"i": "198.51.100.77", "t": simdi})
    assert _giris_ham(client, "yonetici1").status_code == 429

    once = _satir_sayisi(e, KODLAR["rl_clear"])
    cevap = client.delete(f"/api/platform/rate-limits?ip={ISTEMCI_IP}", headers=ortam["h_op"])
    assert cevap.status_code == 200, cevap.text
    govde = cevap.json()
    assert set(govde) == {"ip_address", "rate_limit_rows", "login_attempt_rows", "changed"}, govde
    assert govde["ip_address"] == ISTEMCI_IP and govde["rate_limit_rows"] == tavan, govde
    assert _satir_sayisi(e, KODLAR["rl_clear"]) == once + 1
    _denetim_satiri_dogru(ortam, KODLAR["rl_clear"], "/api/platform/rate-limits")
    # Başka IP'nin satırı KALDI.
    assert _sql(e, "SELECT COUNT(*) FROM auth_rate_limits WHERE ip_address='198.51.100.77'")[0][0] == 1
    assert _giris_ham(client, "yonetici1").status_code == 200

    yok = client.delete(f"/api/platform/rate-limits?ip={ISTEMCI_IP}", headers=ortam["h_op"])
    assert yok.status_code == 404, yok.text
    assert _satir_sayisi(e, KODLAR["rl_clear"]) == once + 1


def test_hiz_siniri_temizle_giris_kilidini_de_kaldirir_404_yalniz_ikisi_bossa(ortam) -> None:
    """Şef PP2 kararı 2: ``login_attempts`` kilidi AYNI çağrıda; sayılar ayrı döner."""
    from sqlalchemy import text

    client, e = ortam["client"], ortam["engine"]
    simdi = datetime.now(timezone.utc)
    with e.begin() as c:
        c.execute(text("INSERT INTO login_attempts(username,ip_address,fail_count,locked_until,updated_at) "
                       "VALUES (:u,:i,5,:k,:t)"),
                  [{"u": "yonetici1", "i": ISTEMCI_IP, "k": simdi + timedelta(minutes=15), "t": simdi},
                   {"u": "yonetici1", "i": "198.51.100.78", "k": simdi + timedelta(minutes=15), "t": simdi}])
    assert _giris_ham(client, "yonetici1").status_code != 200            # kilitli
    _yaz(e, "DELETE FROM auth_rate_limits")   # o giriş denemesi IP sayacına da yazdı

    # ``auth_rate_limits`` BOŞ, yalnız giriş kilidi var: 404 DEĞİL, 200.
    cevap = client.delete(f"/api/platform/rate-limits?ip={ISTEMCI_IP}", headers=ortam["h_op"])
    assert cevap.status_code == 200, cevap.text
    assert cevap.json() == {"ip_address": ISTEMCI_IP, "rate_limit_rows": 0,
                            "login_attempt_rows": 1, "changed": True}
    assert "login_attempt_rows=1" in str(_son_satir(e, KODLAR["rl_clear"])[6])
    # Başka IP'nin kilidi KALDI.
    assert _sql(e, "SELECT COUNT(*) FROM login_attempts WHERE ip_address='198.51.100.78'")[0][0] == 1
    assert _giris_ham(client, "yonetici1").status_code == 200

    _yaz(e, "DELETE FROM auth_rate_limits")
    _yaz(e, "DELETE FROM login_attempts WHERE ip_address=:i", i=ISTEMCI_IP)
    assert client.delete(f"/api/platform/rate-limits?ip={ISTEMCI_IP}",
                         headers=ortam["h_op"]).status_code == 404


def test_hiz_siniri_temizle_ip_zorunlu(ortam) -> None:
    assert ortam["client"].delete("/api/platform/rate-limits", headers=ortam["h_op"]).status_code == 422


# ------------------------------------------------------------ kuyruk yeniden dene ---

def _bildirim(engine, cid: int, kanal: str, durum: str, *, onayli=True, silahli=True, riza=None) -> int:
    simdi = datetime.now(timezone.utc)
    return int(_yaz(
        engine,
        "INSERT INTO notifications(company_id,type,channel,recipient,template,payload,status,"
        "dispatch_armed,approved_by,approved_at,consent_decision,created_at,updated_at) VALUES "
        "(:c,'MANUAL',:k,'gizli-alici@musteri.example','tmpl','GIZLI-YUK-PP2',:s,:a,:ob,:oa,:r,:t,:t) "
        "RETURNING id",
        c=cid, k=kanal, s=durum, a=silahli, ob=1 if onayli else None, oa=simdi if onayli else None,
        r=riza, t=simdi))


def test_kuyruk_retry_yalniz_uygun_FAILED_RETRY_SCHEDULED_olur(ortam) -> None:
    client, e, a, b = ortam["client"], ortam["engine"], ortam["a"], ortam["b"]
    _yaz(e, "DELETE FROM notifications")
    uygun_e1 = _bildirim(e, a, "EMAIL", "FAILED")
    uygun_e2 = _bildirim(e, b, "EMAIL", "FAILED")
    uygun_s = _bildirim(e, b, "SMS", "FAILED")
    engelli = _bildirim(e, a, "EMAIL", "FAILED", riza="BLOCKED")
    onaysiz = _bildirim(e, a, "EMAIL", "FAILED", onayli=False)
    silahsiz = _bildirim(e, b, "EMAIL", "FAILED", silahli=False)
    bekleyen = _bildirim(e, a, "EMAIL", "PENDING")
    once = _satir_sayisi(e, KODLAR["ob_retry"])

    # Kanal + tavan: EMAIL'de 2 uygun, 3 uygunsuz; tavan 1.
    cevap = client.post("/api/platform/outbox/retry", json={"channel": "EMAIL", "max": 1},
                        headers=ortam["h_op"])
    assert cevap.status_code == 200, cevap.text
    assert cevap.json() == {"requeued": 1, "remaining": 1, "not_retryable": 3, "changed": True}
    assert _satir_sayisi(e, KODLAR["ob_retry"]) == once + 1
    _denetim_satiri_dogru(ortam, KODLAR["ob_retry"], "/api/platform/outbox/retry")

    # Kanalsız: kalan iki uygun (EMAIL + SMS).
    cevap = client.post("/api/platform/outbox/retry", json={}, headers=ortam["h_op"])
    assert cevap.json() == {"requeued": 2, "remaining": 0, "not_retryable": 3, "changed": True}
    assert _satir_sayisi(e, KODLAR["ob_retry"]) == once + 2

    durum = dict(_sql(e, "SELECT id,status FROM notifications"))
    for nid in (uygun_e1, uygun_e2, uygun_s):
        assert durum[nid] == "RETRY_SCHEDULED", (nid, durum)
    for nid in (engelli, onaysiz, silahsiz):
        assert durum[nid] == "FAILED", (nid, durum)
    assert durum[bekleyen] == "PENDING"
    assert all(r[0] is not None for r in _sql(
        e, "SELECT next_attempt_at FROM notifications WHERE status='RETRY_SCHEDULED'"))

    # İdempotent: yapılacak iş yok -> changed false, satır YOK.
    cevap = client.post("/api/platform/outbox/retry", json={}, headers=ortam["h_op"])
    assert cevap.json() == {"requeued": 0, "remaining": 0, "not_retryable": 3, "changed": False}
    assert _satir_sayisi(e, KODLAR["ob_retry"]) == once + 2


def test_kuyruk_retry_tavan_500(ortam) -> None:
    h = ortam["h_op"]
    assert ortam["client"].post("/api/platform/outbox/retry", json={"max": 501}, headers=h).status_code == 422
    assert ortam["client"].post("/api/platform/outbox/retry", json={"max": 0}, headers=h).status_code == 422


def test_kapali_gecis_tablosu_FAILED_PENDING_e_izin_vermez(ortam) -> None:
    """Retry'ın ``PENDING``i değil ``RETRY_SCHEDULED``ı seçmesinin ölçüsü."""
    from app.notifications.service import assert_transition

    assert_transition("FAILED", "RETRY_SCHEDULED")
    with pytest.raises(Exception):
        assert_transition("FAILED", "PENDING")


# -------------------------------------------------------------- yanıt içeriği ---

YASAK_ANAHTARLAR = frozenset({
    "phone", "email", "address", "tax_number", "recipient", "payload", "last_error",
    "token", "token_hash", "password_hash", "customer_id", "supplier_id", "link",
})


def test_yanitlarda_cari_ya_da_hassas_alan_yok(ortam) -> None:
    from app.routers import platform_management as pm

    for model in (pm.SirketDurumu, pm.KullaniciDurumu, pm.DogrulamaGonderimi, pm.ParolaSifirlama,
                  pm.HizSiniriTemizligi, pm.KuyrukYenidenSonucu):
        assert not (set(model.model_fields) & YASAK_ANAHTARLAR), model


# ------------------------------------------------------------ denetim süzgeçleri ---

def test_denetim_suzgecleri(ortam) -> None:
    client, e, h = ortam["client"], ortam["engine"], ortam["h_op"]
    # Kendi satırlarını üret: bir dondur + aç.
    assert client.post(f"/api/platform/companies/{ortam['b']}/deactivate", headers=h).status_code == 200
    assert client.post(f"/api/platform/companies/{ortam['b']}/activate", headers=h).status_code == 200

    def al(**p):
        cevap = client.get("/api/platform/audit", params=p, headers=h)
        assert cevap.status_code == 200, cevap.text
        return cevap.json()

    tum = al(limit=1000)
    assert tum and all(r["company_id"] is None for r in tum)

    deact = al(action=KODLAR["deactivate"], limit=1000)
    assert deact and {r["action"] for r in deact} == {KODLAR["deactivate"]}
    assert len(deact) == _satir_sayisi(e, KODLAR["deactivate"])

    # username: operatörün adı -> ``aktor=<id>;`` notuyla eşlenen platform satırları.
    op_satirlari = al(username="operator", limit=1000)
    assert op_satirlari and all(str(r["failure_reason"]).startswith(f"aktor={ortam['op']};")
                                for r in op_satirlari)
    assert al(username="hic-yok-boyle-biri") == []

    assert {r["status_code"] for r in al(status_code=200, limit=1000)} == {200}
    assert {r["ip_address"] for r in al(ip_address=ISTEMCI_IP, limit=1000)} == {ISTEMCI_IP}
    gelecek = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    gecmis = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert al(date_from=gelecek) == []
    assert len(al(date_from=gecmis, limit=1000)) == len(al(limit=1000))
    assert al(date_to=gecmis) == []
    # Süzgeçler birleşir (AND).
    birlesik = al(action=KODLAR["activate"], username="operator", status_code=200, date_from=gecmis)
    assert birlesik and {r["action"] for r in birlesik} == {KODLAR["activate"]}
    assert al(action=KODLAR["activate"], status_code=429) == []


def test_denetim_actor_id_suzgeci(ortam) -> None:
    """Şef PP2 kararı 5: ``actor_id`` yalnız ``aktor=<id>;`` notunu eşler."""
    client, h, op = ortam["client"], ortam["h_op"], ortam["op"]
    assert client.post(f"/api/platform/companies/{ortam['b']}/deactivate", headers=h).status_code == 200
    assert client.post(f"/api/platform/companies/{ortam['b']}/activate", headers=h).status_code == 200

    def al(**p):
        return client.get("/api/platform/audit", params={"limit": 1000, **p}, headers=h)

    satirlar = al(actor_id=op).json()
    assert satirlar and all(str(r["failure_reason"]).startswith(f"aktor={op};") for r in satirlar)
    beklenen = _sql(ortam["engine"], "SELECT COUNT(*) FROM security_audit_logs WHERE company_id IS NULL "
                                     "AND failure_reason LIKE :d", d=f"aktor={op};%")[0][0]
    assert len(satirlar) == beklenen
    assert al(actor_id=op * 10 + 7).json() == []        # ``aktor=1``, ``aktor=17``yi yakalamaz
    assert al(actor_id=0).status_code == 422
    assert {r["id"] for r in al(actor_id=op, action=KODLAR["activate"]).json()} <= {r["id"] for r in satirlar}
