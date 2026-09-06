"""5.4a — MOBİL KİMLİK AKIŞI: çerezsiz istemci, AYNI rotasyon çekirdeği.

Konu: telefondaki kabuk oturumu nasıl taşır ve tarayıcı yolu bundan nasıl
ETKİLENMEZ.

--- SORUN ----------------------------------------------------------------------

Oturum bugün TAMAMEN çereze bağlı: ``/auth/login`` refresh ve CSRF tokenlarını
YALNIZ ``Set-Cookie`` ile verir (``routers/auth.py``), ``/auth/refresh`` refresh
tokenını YALNIZ çerezden okur ve CSRF ister. Capacitor kabuğunun origin'i
cihazın kendisidir (``https://localhost``) ve API başka bir alan adındadır;
oraya yazılan HttpOnly çerez ya hiç yazılmaz ya da üçüncü-taraf çerez
engelleriyle sessizce düşer. Yani mobil istemcinin bugün 15 dakikadan uzun
yaşayan bir oturumu YOKTUR: access token biter ve yenilenemez.

--- ÇÖZÜMÜN SINIRI (bu dosyanın asıl işi) ---------------------------------------

Çerezsiz bir yol açmak kolay, onu tarayıcı yolundan SIZDIRMADAN açmak zordur.
Üç yön de zorunlu ve üçü de ayrı ayrı ölçülür:

* **Yön 1 — tarayıcı yolu BİT BİT AYNI.** Başlıksız login bugünkü ÜÇ çerezi
  aynı adlarla verir ve gövdeye ham refresh token KOYMAZ; çerezli yenileme
  hâlâ CSRF ister.
* **Yön 2 — mobil yol ÇALIŞIYOR.** ``X-Client-Kind: mobile`` ile login gövdede
  refresh token verir ve HİÇ çerez yazmaz; gövde yenilemesi hem yeni access
  hem yeni refresh döndürür.
* **Yön 3 — güvenlik çekirdeği PAYLAŞILIYOR.** Gövde yolu
  ``rotate_refresh_token`` fonksiyonunun TA KENDİSİNİ çağırır; bu yüzden aile
  rotasyonu ve REPLAY TESPİTİ mobilde de aynen işler. İkinci bir rotasyon yolu
  yazılsaydı, o yol bir gün sessizce ayrışır ve replay tespiti mobilde ölürdü.

--- YOL SEÇİMİ NEDEN ÇEREZE BAKAR ----------------------------------------------

``/auth/refresh`` gövde yolunu YALNIZ çerez YOKKEN seçer. Tersi olsaydı —
"gövde varsa gövdeyi kullan" — tarayıcıda çalışan bir saldırgan gövdeye kendi
seçtiği bir token koyarak CSRF kapısını ATLATABİLİRDİ; kapı, saldırganın
yazabildiği bir alanla açılıp kapanır hale gelirdi.

Gövde yolunun CSRF İSTEMEMESİ bir gevşetme değildir: CSRF, tarayıcının isteği
OTOMATİK kimliklendirmesine karşıdır (çerez, saldırganın sayfasından yapılan
isteğe de iliştirilir). Gövdedeki token otomatik iliştirilmez; saldırgan onu
yazabiliyorsa tokenı zaten BİLİYOR demektir ve CSRF'in koruduğu şey çoktan
gitmiştir.

--- GÖÇ YOK: İSTEMCİ TÜRÜ VAR OLAN SÜTUNA YAZILIR ------------------------------

``auth_refresh_tokens.user_agent`` zaten var ve zaten serbest metin. Mobil
oturumu ayırt etmek için yeni sütun açmak bir göç demekti; bunun yerine tür,
metnin önüne ``mobile:`` öneki olarak yazılır. Web yolunda metin DEĞİŞMEZ.

--- MUTASYONLAR (uygulandı, kırmızı GÖRÜLDÜ) -----------------------------------

Her biri aşağıda ADIYLA anılan testi düşürür:

1. ``_refresh_from_body`` rotasyonu ATLAR (eski tokenı geri döndürür)
   -> ``test_govde_yenilemesi_ROTASYON_yapar_eski_token_olur``
2. ``logout_all`` içinde ``revoke_user_access_tokens`` satırı SİLİNİR
   -> ``test_logout_all_ACCESS_tokenlari_da_dusurur``
3. ``login`` mobil dalında ``_set_session_cookies`` DA çağrılır
   -> ``test_mobil_login_HIC_cerez_yazmaz``
4. Gövde yolu ``rotate_refresh_token`` yerine tüketilmiş tokenı sessizce
   kabul eden bir kopya kullanır (replay tespiti ATLANIR)
   -> ``test_govde_yolunda_YENIDEN_KULLANIM_aileyi_oldurur``
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.auth import SELF_SERVICE_API  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import PASSWORD_CHANGE_ALLOWED_API, PUBLIC_API  # noqa: E402
from app.routers.auth import (  # noqa: E402
    MOBILE_CLIENT_KIND,
    _is_mobile_client,
    _session_user_agent,
)


# ===========================================================================
# STATİK KAPILAR — HTTP'siz ölçülebilen sözleşme
# ===========================================================================

def test_istemci_turu_basligi_YALNIZ_mobile_degerini_tanir() -> None:
    """Başlık kapalı bir sözlüktür: tanınmayan her değer TARAYICI yoludur.

    Fail-closed olan yön budur. "mobile değilse mobil say" gibi bir açılım,
    başlığı hiç göndermeyen SPA'yı bir gün çerezsiz bırakabilirdi.
    """
    assert _is_mobile_client("mobile") is True
    assert _is_mobile_client("Mobile") is True
    assert _is_mobile_client("  MOBILE  ") is True
    for yabanci in (None, "", "web", "mobil", "mobile-app", "ios", "android"):
        assert _is_mobile_client(yabanci) is False, yabanci


def test_istemci_turu_user_agent_ONEKI_olarak_yazilir_gocsuz() -> None:
    """Tür var olan sütuna yazılır; web metni DEĞİŞMEZ."""
    assert _session_user_agent("CapacitorApp/1.0", mobile=True) == (
        "mobile:CapacitorApp/1.0"
    )
    assert _session_user_agent(None, mobile=True) == "mobile:"
    # Web yolunda önek YOK — olsaydı bugünkü satırlar sessizce değişirdi.
    assert _session_user_agent("Mozilla/5.0", mobile=False) == "Mozilla/5.0"
    assert _session_user_agent(None, mobile=False) is None
    assert MOBILE_CLIENT_KIND == "mobile"


def test_logout_all_SELF_SERVIS_muafiyetindedir() -> None:
    """Muafiyet olmasaydı uç ``admin`` dışında hiç kimseye açık olmazdı.

    ``required_permission`` içinde POST için eşleşen kural yoktur; uç dosyanın
    sonundaki deny-by-default nöbetçisine düşer. Bu iddia o DOLAYLI yolu
    çapalar.
    """
    assert "/api/auth/logout-all" in SELF_SERVICE_API
    # Zorunlu parola rotasyonu kapısı AYNI listeyi kullanır: parolasını
    # değiştirmesi gereken hesap da çalınmış oturumlarını kapatabilmeli.
    assert "/api/auth/logout-all" in PASSWORD_CHANGE_ALLOWED_API


def test_logout_all_KIMLIK_ISTER_public_degil() -> None:
    """Kimliksiz bir "herkesin oturumunu kapat" ucu düşünülemez."""
    assert "/api/auth/logout-all" not in PUBLIC_API


# ===========================================================================
# DAVRANIŞ — gerçek şema, gerçek uçlar (alt süreç, kendi veritabanı).
#
# NEDEN ALT SÜREÇ: ``app.config.Settings`` modül düzeyinde TEK KOPYADIR ve
# ``app.main`` import anında göçleri koşar. Bu dosyanın senaryoları TAZE bir
# şema ve kendi ``DATABASE_URL``ı ister (deponun mustahsil makbuzu / kantar
# ikizleriyle AYNI gerekçe).
# ===========================================================================

_SMOKE = r'''
import os, sys
sys.path.insert(0, os.environ["BACKEND"])
from fastapi.testclient import TestClient
from sqlalchemy import select, update

import app.main as m
from app.auth import auth_refresh_tokens, auth_tokens, users
from app.db import SessionLocal

BOOT = os.environ["BOOTSTRAP_ADMIN_PASSWORD"]
MOBIL = {"X-Client-Kind": "mobile"}
UA = {"User-Agent": "CapacitorApp/1.0"}


def cookie_names(response):
    """Yanitin YAZDIGI cerez adlari — yalniz bu istekte set edilenler."""
    return sorted(raw.split("=", 1)[0].strip()
                  for raw in response.headers.get_list("set-cookie"))


def kur(kullanici, parola, rol="rapor"):
    created = c.post("/api/users", headers=AH, json={
        "username": kullanici, "display_name": kullanici.title(),
        "password": parola, "role": rol})
    assert created.status_code == 201, ("KURULUM kullanici", created.status_code, created.text)
    with SessionLocal() as db:
        uid = db.execute(
            select(users.c.id).where(users.c.username == kullanici)
        ).scalar_one()
        db.execute(update(users).where(users.c.id == uid).values(must_change_password=False))
        db.commit()
    return uid


# --- KURULUM ---------------------------------------------------------------
c = TestClient(m.app)
first = c.post("/api/auth/login", json={"username": "admin", "password": BOOT})
assert first.status_code == 200, ("KURULUM admin login", first.status_code, first.text)
rotated = c.post(
    "/api/auth/change-password",
    json={"current_password": BOOT, "new_password": "MobilAkisSifresi!2026"},
    headers={"Authorization": "Bearer " + first.json()["access_token"]},
)
assert rotated.status_code == 200, ("KURULUM admin rotation", rotated.status_code, rotated.text)
AH = {"Authorization": "Bearer " + rotated.json()["access_token"]}

UID = kur("mobilci", "MobilGecici!26")
OTEKI_UID = kur("oteki", "OtekiGecici!26")

# ===========================================================================
# YON 1 — TARAYICI YOLU BIT BIT AYNI
# ===========================================================================

wc = TestClient(m.app)
web = wc.post("/api/auth/login", json={"username": "mobilci", "password": "MobilGecici!26"})
assert web.status_code == 200, ("YON-1 web login", web.status_code, web.text)

# CEREZ KUMESI TAM OLARAK BU UCU. Eksigi kadar fazlasi da kirmizidir: mobil
# dal yanlislikla web daline sizsaydi ya da bir cerez dusseydi burasi duser.
assert cookie_names(web) == ["yhp_access_token", "yhp_csrf_token", "yhp_refresh_token"], \
    ("YON-1 cerez kumesi", cookie_names(web))

# HAM REFRESH TOKEN GOVDEDE YOK. Bu tam olarak mobil dalin ekledigi alandir;
# web yoluna sizmasi, HttpOnly korumasini JavaScript'e acmak demekti.
assert "refresh_token" not in web.json(), ("YON-1 govdede refresh sizmis", sorted(web.json()))
assert "refresh_expires_at" not in web.json(), sorted(web.json())
assert web.json()["token_type"] == "bearer"

# user_agent'a mobil oneki YAZILMAMIS olmali.
with SessionLocal() as db:
    ua = db.execute(
        select(auth_refresh_tokens.c.user_agent)
        .where(auth_refresh_tokens.c.user_id == UID)
        .order_by(auth_refresh_tokens.c.id.desc())
    ).scalars().first()
assert ua is None or not ua.startswith("mobile:"), ("YON-1 web satirina mobil oneki", ua)

# CEREZLI YENILEME HALA CSRF ISTER — govde yolu bu kapiyi acmadi.
web_csrf = wc.cookies.get("yhp_csrf_token")
csrfsiz = wc.post("/api/auth/refresh")
assert csrfsiz.status_code == 403, ("YON-1 CSRF kapisi acilmis", csrfsiz.status_code, csrfsiz.text)

# Cerez VARKEN govdeye token koymak CSRF'i ATLATAMAZ: yol secimi cereze bakar.
kacak = wc.post("/api/auth/refresh", json={"refresh_token": "uydurma-token"})
assert kacak.status_code == 403, ("YON-1 govde ile CSRF atlatildi", kacak.status_code, kacak.text)

# SPA'nin gercek cagrisi: bos govde + CSRF basligi. 422'ye dusmemeli.
yenilendi = wc.post("/api/auth/refresh", json={}, headers={"X-CSRF-Token": web_csrf})
assert yenilendi.status_code == 200, ("YON-1 cerezli yenileme", yenilendi.status_code, yenilendi.text)
assert cookie_names(yenilendi) == ["yhp_access_token", "yhp_csrf_token", "yhp_refresh_token"], \
    ("YON-1 yenileme cerezleri", cookie_names(yenilendi))
assert "refresh_token" not in yenilendi.json(), \
    ("YON-1 yenilemede govdeye sizmis", sorted(yenilendi.json()))

# ===========================================================================
# YON 2 — MOBIL YOL CALISIYOR
# ===========================================================================

mc = TestClient(m.app)
mob = mc.post("/api/auth/login", json={"username": "mobilci", "password": "MobilGecici!26"},
              headers={**MOBIL, **UA})
assert mob.status_code == 200, ("YON-2 mobil login", mob.status_code, mob.text)
assert cookie_names(mob) == [], ("YON-2 mobil login CEREZ yazmis", cookie_names(mob))
assert mc.cookies.get("yhp_refresh_token") is None, "YON-2 istemcide refresh cerezi olusmus"
assert mc.cookies.get("yhp_csrf_token") is None, "YON-2 istemcide csrf cerezi olusmus"
M_REFRESH = mob.json().get("refresh_token")
assert M_REFRESH, ("YON-2 govdede refresh yok", sorted(mob.json()))
assert mob.json().get("refresh_expires_at"), sorted(mob.json())
M_ACCESS = mob.json()["access_token"]

# GOCSUZ ISTEMCI TURU: satir var olan sutuna onekle yazildi.
with SessionLocal() as db:
    mua = db.execute(
        select(auth_refresh_tokens.c.user_agent)
        .where(auth_refresh_tokens.c.user_id == UID)
        .order_by(auth_refresh_tokens.c.id.desc())
    ).scalars().first()
assert mua == "mobile:CapacitorApp/1.0", ("YON-2 istemci turu yazilmamis", mua)

# GOVDE YENILEMESI: CSRF'siz, cerezsiz, hem access hem refresh doner.
nc = TestClient(m.app)
yeni = nc.post("/api/auth/refresh", json={"refresh_token": M_REFRESH})
assert yeni.status_code == 200, ("YON-2 govde yenileme", yeni.status_code, yeni.text)
assert cookie_names(yeni) == [], ("YON-2 govde yenilemesi CEREZ yazmis", cookie_names(yeni))
M_REFRESH_2 = yeni.json().get("refresh_token")
assert M_REFRESH_2, ("YON-2 yenilemede refresh donmemis", sorted(yeni.json()))
assert M_REFRESH_2 != M_REFRESH, "YON-2 ROTASYON YOK — ayni refresh token geri dondu"
M_ACCESS_2 = yeni.json()["access_token"]
assert M_ACCESS_2 != M_ACCESS, "YON-2 yeni access token uretilmemis"

# Yeni access token GERCEKTEN calisiyor.
me = TestClient(m.app).get("/api/auth/me", headers={"Authorization": "Bearer " + M_ACCESS_2})
assert me.status_code == 200, ("YON-2 yeni access calismiyor", me.status_code, me.text)

# ===========================================================================
# YON 3 — GUVENLIK CEKIRDEGI PAYLASILIYOR (replay tespiti)
# ===========================================================================

# TUKETILMIS tokenin yeniden sunulmasi: reddedilir VE aile olur.
replay = TestClient(m.app).post("/api/auth/refresh", json={"refresh_token": M_REFRESH})
assert replay.status_code == 401, ("YON-3 replay kabul edilmis", replay.status_code, replay.text)

# AILE OLDU: replay'den SONRA, hala gecerli olan YENI token da artik calismaz.
olu = TestClient(m.app).post("/api/auth/refresh", json={"refresh_token": M_REFRESH_2})
assert olu.status_code == 401, ("YON-3 aile yasiyor — replay tespiti govde yolunda yok",
                                olu.status_code, olu.text)

with SessionLocal() as db:
    canli = db.execute(
        select(auth_refresh_tokens.c.id)
        .where(auth_refresh_tokens.c.user_id == UID,
               auth_refresh_tokens.c.revoked_at.is_(None))
    ).scalars().all()
# Web oturumunun ailesi AYAKTA kalmali: mobil ailenin imhasi web'e SIZMAZ.
assert len(canli) >= 1, "YON-3 web ailesi de dusmus — imha aileye degil kullaniciya gitmis"

# --- AYNI TESPIT CEREZ YOLUNDA DA (cekirdek tek) ---------------------------
cc = TestClient(m.app)
cl = cc.post("/api/auth/login", json={"username": "oteki", "password": "OtekiGecici!26"})
assert cl.status_code == 200, ("YON-3 cerez login", cl.status_code, cl.text)
eski_refresh = cc.cookies.get("yhp_refresh_token")
eski_csrf = cc.cookies.get("yhp_csrf_token")
d1 = cc.post("/api/auth/refresh", json={}, headers={"X-CSRF-Token": eski_csrf})
assert d1.status_code == 200, ("YON-3 cerez yenileme", d1.status_code, d1.text)
yeni_refresh = cc.cookies.get("yhp_refresh_token")
yeni_csrf = cc.cookies.get("yhp_csrf_token")

rc = TestClient(m.app)
rc.cookies.set("yhp_refresh_token", eski_refresh, path="/api/auth")
rc.cookies.set("yhp_csrf_token", eski_csrf)
tekrar = rc.post("/api/auth/refresh", headers={"X-CSRF-Token": eski_csrf})
assert tekrar.status_code == 401, ("YON-3 cerez replay kabul", tekrar.status_code, tekrar.text)

lc = TestClient(m.app)
lc.cookies.set("yhp_refresh_token", yeni_refresh, path="/api/auth")
lc.cookies.set("yhp_csrf_token", yeni_csrf)
oldu = lc.post("/api/auth/refresh", headers={"X-CSRF-Token": yeni_csrf})
assert oldu.status_code == 401, ("YON-3 cerez ailesi yasiyor", oldu.status_code, oldu.text)

# ===========================================================================
# ZORUNLU PAROLA ROTASYONU — govde yolu da 403
# ===========================================================================

zc = TestClient(m.app)
zorunlu = zc.post("/api/auth/login", json={"username": "oteki", "password": "OtekiGecici!26"},
                  headers=MOBIL)
assert zorunlu.status_code == 200, ("ZORUNLU login", zorunlu.status_code, zorunlu.text)
Z_REFRESH = zorunlu.json()["refresh_token"]
with SessionLocal() as db:
    db.execute(update(users).where(users.c.id == OTEKI_UID).values(must_change_password=True))
    db.commit()
engel = TestClient(m.app).post("/api/auth/refresh", json={"refresh_token": Z_REFRESH})
assert engel.status_code == 403, ("ZORUNLU govde yenileme 403 degil", engel.status_code, engel.text)
assert engel.json()["detail"]["code"] == "PASSWORD_CHANGE_REQUIRED", engel.text
with SessionLocal() as db:
    db.execute(update(users).where(users.c.id == OTEKI_UID).values(must_change_password=False))
    db.commit()

# ===========================================================================
# GOVDE ILE CIKIS — kendi ailesini dusurur, BASKASININKINI dusurmez
# ===========================================================================

ac = TestClient(m.app)
a = ac.post("/api/auth/login", json={"username": "mobilci", "password": "MobilGecici!26"},
            headers=MOBIL)
A_ACCESS = a.json()["access_token"]

bc = TestClient(m.app)
b = bc.post("/api/auth/login", json={"username": "oteki", "password": "OtekiGecici!26"},
            headers=MOBIL)
B_REFRESH = b.json()["refresh_token"]

# SAHIPLIK YUKLEMI: A, B'nin refresh tokenini govdeye koyup cikis yapamaz.
kacak_cikis = TestClient(m.app).post(
    "/api/auth/logout", headers={"Authorization": "Bearer " + A_ACCESS},
    json={"refresh_token": B_REFRESH})
assert kacak_cikis.status_code == 204, (kacak_cikis.status_code, kacak_cikis.text)
b_hala = TestClient(m.app).post("/api/auth/refresh", json={"refresh_token": B_REFRESH})
assert b_hala.status_code == 200, ("CAPRAZ IMHA — baskasinin ailesi dusurulmus",
                                   b_hala.status_code, b_hala.text)
B_REFRESH = b_hala.json()["refresh_token"]

# KENDI tokeni ile cikis: aile duser.
a2 = TestClient(m.app).post("/api/auth/login",
                            json={"username": "mobilci", "password": "MobilGecici!26"},
                            headers=MOBIL)
A2_REFRESH, A2_ACCESS = a2.json()["refresh_token"], a2.json()["access_token"]
kendi = TestClient(m.app).post(
    "/api/auth/logout", headers={"Authorization": "Bearer " + A2_ACCESS},
    json={"refresh_token": A2_REFRESH})
assert kendi.status_code == 204, (kendi.status_code, kendi.text)
dusuk = TestClient(m.app).post("/api/auth/refresh", json={"refresh_token": A2_REFRESH})
assert dusuk.status_code == 401, ("KENDI ailesi dusmemis", dusuk.status_code, dusuk.text)

# ===========================================================================
# LOGOUT-ALL — IKI BAGIMSIZ OTURUMU BIRDEN OLDURUR
# ===========================================================================

s1 = TestClient(m.app).post("/api/auth/login",
                            json={"username": "mobilci", "password": "MobilGecici!26"},
                            headers=MOBIL).json()
s2 = TestClient(m.app).post("/api/auth/login",
                            json={"username": "mobilci", "password": "MobilGecici!26"},
                            headers=MOBIL).json()
assert s1["refresh_token"] != s2["refresh_token"], "KURULUM: iki oturum ayni aileden"

# Her iki access token da GERCEKTEN canli (kapi bir sey olcuyor).
for etiket, s in (("s1", s1), ("s2", s2)):
    ok = TestClient(m.app).get("/api/auth/me",
                               headers={"Authorization": "Bearer " + s["access_token"]})
    assert ok.status_code == 200, ("KURULUM canli degil", etiket, ok.status_code)

kapat = TestClient(m.app).post("/api/auth/logout-all",
                               headers={"Authorization": "Bearer " + s1["access_token"]})
assert kapat.status_code == 204, ("LOGOUT-ALL", kapat.status_code, kapat.text)

# ACCESS tokenlar da oldu — yalniz refresh iptali 15 dakikalik pencere birakirdi.
for etiket, s in (("s1", s1), ("s2", s2)):
    olu_a = TestClient(m.app).get("/api/auth/me",
                                  headers={"Authorization": "Bearer " + s["access_token"]})
    assert olu_a.status_code == 401, ("LOGOUT-ALL access unutuldu", etiket,
                                      olu_a.status_code, olu_a.text)
    olu_r = TestClient(m.app).post("/api/auth/refresh",
                                   json={"refresh_token": s["refresh_token"]})
    assert olu_r.status_code == 401, ("LOGOUT-ALL refresh unutuldu", etiket,
                                      olu_r.status_code, olu_r.text)

# BASKA KULLANICIYA DOKUNMADI.
oteki_canli = TestClient(m.app).post("/api/auth/refresh", json={"refresh_token": B_REFRESH})
assert oteki_canli.status_code == 200, ("LOGOUT-ALL baska kullaniciyi dusurmus",
                                        oteki_canli.status_code, oteki_canli.text)

# KIMLIKSIZ cagri 401.
acik = TestClient(m.app).post("/api/auth/logout-all")
assert acik.status_code == 401, ("LOGOUT-ALL kimliksiz acik", acik.status_code, acik.text)

with SessionLocal() as db:
    kalan = db.execute(
        select(auth_tokens.c.id).where(auth_tokens.c.user_id == UID)
    ).scalars().all()
assert kalan == [], ("LOGOUT-ALL access satirlari kalmis", kalan)

print("MOBIL-AUTH-TAMAM")
'''


def _run_smoke(database_url: str, workspace: Path) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["SUNGUR_DATA_DIR"] = str(workspace)
    env["AUTO_MIGRATE"] = "true"
    env["ENVIRONMENT"] = "development"
    env["BOOTSTRAP_ADMIN_PASSWORD"] = "MobilBootstrap!2026"
    env["BACKEND"] = str(BACKEND)
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=900,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    assert "MOBIL-AUTH-TAMAM" in completed.stdout, completed.stdout


def test_web_login_DEGISMEDI_mobil_login_cerezsiz_calisir(tmp_path: Path) -> None:
    """Kapsayıcı davranış kapısı.

    Aşağıdaki adlandırma testleri bu smoke'un içindeki iddiaları işaret eder;
    dört mutasyonun her biri tek tek uygulanıp kırmızı görüldü.
    """
    _run_smoke(f"sqlite:///{(tmp_path / 'mobil-auth.db').as_posix()}", tmp_path)


# --- MUTASYONLARIN ADLARI ---------------------------------------------------
#
# Aşağıdaki testler ayrı senaryo koşmaz; smoke içindeki İDDİAYI ADLANDIRIR ve
# hangi mutasyonun onu düşürdüğünü YAZILI tutar. Adsız bir smoke, kırıldığında
# "hangi güvence gitti" sorusunu yanıtlayamazdı.

def test_govde_yenilemesi_ROTASYON_yapar_eski_token_olur() -> None:
    """Mutasyon 1: ``_refresh_from_body`` rotasyonu atlar -> smoke'ta
    "YON-2 ROTASYON YOK" düşer."""
    assert "YON-2 ROTASYON YOK" in _SMOKE


def test_logout_all_ACCESS_tokenlari_da_dusurur() -> None:
    """Mutasyon 2: ``revoke_user_access_tokens`` silinir -> smoke'ta
    "LOGOUT-ALL access unutuldu" düşer."""
    assert "LOGOUT-ALL access unutuldu" in _SMOKE
    assert settings.access_token_minutes == 15, (
        "access ömrü değişti: logout-all'un access tokenlarını da düşürme "
        "gerekçesi bu pencereye dayanıyor"
    )


def test_mobil_login_HIC_cerez_yazmaz() -> None:
    """Mutasyon 3: mobil dal da ``_set_session_cookies`` çağırır -> smoke'ta
    "YON-2 mobil login CEREZ yazmis" düşer."""
    assert "YON-2 mobil login CEREZ yazmis" in _SMOKE


def test_govde_yolunda_YENIDEN_KULLANIM_aileyi_oldurur() -> None:
    """Mutasyon 4: gövde yolu tüketilmiş tokenı kabul eder -> smoke'ta
    "YON-3 replay kabul edilmis" / "YON-3 aile yasiyor" düşer."""
    assert "YON-3 replay kabul edilmis" in _SMOKE
    assert "YON-3 aile yasiyor" in _SMOKE
