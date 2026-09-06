"""Kimlik/oturum self-servisi yetki kapısına girmez — ve bu muafiyet SIZMAZ.

Konu: rol çözülemediğinde kurtarma yolu.

--- KUSUR ---------------------------------------------------------------------

``/api/auth/change-password``, ``/api/auth/logout`` ve ``/api/auth/me``
``required_permission`` içinde ``read`` olarak ifade ediliyordu. Bu yalnız
tablodaki HER rolün ``read`` taşıması sayesinde çalışıyordu. Fail-open rol
varsayılanı kapatıldığında rolü çözülemeyen hesabın hiçbir izni kalmadı ve bu üç
uç da 403 döndü: hesap ne çıkış yapabiliyor ne parolasını değiştirebiliyor ne de
kendi oturumunu okuyabiliyordu. Arayüz yerel durumu temizler ama sunucudaki
oturumu ve HttpOnly çerezleri düşüremez — yani sessiz bir fazla-yetki, kurtarma
yolu olmayan bir KİLİTLENMEYE dönüşmüştü.

İlke: **kendi kimliği ve oturumu üzerindeki işlemler için kimlik doğrulaması
YETERLİDİR; yetki iş verisini korur.**

--- MUAFİYETİN SINIRI (bu dosyanın asıl işi) -----------------------------------

Muafiyeti eklemek kolay, DAR tutmak zordur. Genişleyen bir muafiyet, kapatılan
fail-open'ı sessizce geri açar. Bu yüzden iki yön de zorunlu:

* **Yön 1 — muafiyet ÇALIŞIYOR.** Rolü çözülemeyen hesap çıkış yapabilir,
  parolasını değiştirebilir, kendi oturumunu okuyabilir.
* **Yön 2 — muafiyet SIZMIYOR.** Aynı hesap hiçbir iş ucuna erişemez. Bu, sessizce
  geri alınabilecek yön olduğu için ayrıca ve açıkça ölçülür.

--- SELF-SERVİS UÇLARIN SAYIMI VE YÖNTEMİ --------------------------------------

Üç uç elle seçilmedi; iki bağımsız adımla türetildi ve ikisi de aşağıda kapı
olarak duruyor:

1. **Yapısal.** Kimlik/oturum yüzeyi ``/api/auth/`` önekidir. Bu önekten
   ``PUBLIC_API`` çıkarıldığında geriye TAM OLARAK bu üç uç kalır; üçünün de
   yol parametresi yoktur ve hiçbiri başka bir aktörü adlandırmaz. Önek altına
   eklenecek yeni bir kimlikli uç bu kapıyı kırmızıya çevirir.

2. **Dışarıda dördüncü yok.** Kimlik/oturum tablolarına (``app_users``,
   ``auth_tokens``, ``auth_refresh_tokens``) ya da kimlik bilgisi yardımcılarına
   dokunan TÜM uçlar tarandı. ``/api/auth/`` dışında kimlik bilgisi
   yardımcılarına dokunan tek uç ``POST /api/users``: BAŞKA bir aktör yaratır,
   kiracıya bağlıdır ve ``users`` iznine tabidir — tanım gereği self-servis
   değildir.
"""
from __future__ import annotations

import ast
import importlib
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from fastapi.routing import APIRoute  # noqa: E402

from app.auth import SELF_SERVICE_API, required_permission  # noqa: E402
from app.main import PASSWORD_CHANGE_ALLOWED_API, PUBLIC_API, app  # noqa: E402
from app.route_security_contracts import registered_api_operations  # noqa: E402


# --------------------------------------------------------------------------
# Sayım yöntemi, kapı olarak
# --------------------------------------------------------------------------

#: ELLE YAZILMIŞ ÇAPA. Bilerek ``SELF_SERVICE_API``dan türetilmiyor: kaynaktan
#: okuyan bir iddia, kaynağı değiştiren mutasyonla birlikte kayar ve yeşil kalır.
EXPECTED_SELF_SERVICE = {
    "/api/auth/change-password",
    "/api/auth/logout",
    # 5.4a: DÖRDÜNCÜ uç bilinçli olarak eklendi. "Tüm oturumlarımı kapat"ın
    # öznesi yalnız çağıranın kendisidir, yol parametresi yoktur ve başka bir
    # aktörü adlandırmaz — yani yukarıdaki iki yöntemin ikisini de geçer.
    # Muafiyet ZORUNLU: uç POST'tur ve ``required_permission`` içinde eşleşen
    # kuralı yoktur, listeye girmezse deny-by-default nöbetçisine düşer ve
    # ``admin`` dışında hiç kimse cihazını kaybettiğinde oturumlarını
    # düşüremezdi.
    "/api/auth/logout-all",
    "/api/auth/me",
}


def test_declared_set_matches_the_hand_written_anchor() -> None:
    assert set(SELF_SERVICE_API) == EXPECTED_SELF_SERVICE


def test_structural_enumeration_auth_prefix_minus_public() -> None:
    """Yöntem 1: ``/api/auth/`` önekinden PUBLIC_API çıkınca tam bu üç uç kalır."""
    authenticated_auth_paths = {
        path
        for _method, path in registered_api_operations(app)
        if path.startswith("/api/auth/") and path not in PUBLIC_API
    }
    assert authenticated_auth_paths == EXPECTED_SELF_SERVICE


def test_self_service_routes_name_no_other_principal() -> None:
    """Self-servis uç yol parametresi ALMAZ: özne yalnız çağıranın kendisidir."""
    for method, path in registered_api_operations(app):
        if path in SELF_SERVICE_API:
            assert "{" not in path, (method, path)


def test_no_fourth_self_service_route_outside_the_auth_prefix() -> None:
    """Yöntem 2: önek dışında kimlik bilgisi yardımcısına dokunan tek uç.

    ``POST /api/users`` BAŞKA bir aktör yaratır; kiracıya bağlı ve ``users``
    iznine tabidir. Dördüncü bir self-servis uç eklenirse bu liste büyür ve kapı
    kırmızıya döner.
    """
    credential_helpers = {
        "hash_password", "verify_password", "issue_token", "issue_refresh_token",
        "rotate_refresh_token", "revoke_token", "revoke_refresh_token",
        "revoke_user_access_tokens", "revoke_user_refresh_tokens",
        "auth_tokens", "auth_refresh_tokens",
    }
    sources: dict[str, dict[str, ast.AST]] = {}

    def functions(module_name: str) -> dict[str, ast.AST]:
        if module_name not in sources:
            # `sys.modules[module_name]` süpürmede KeyError verir:
            # `test_security_audit_visibility.py:43-44` ve `:60-61` her
            # `uygulama` fikstüründe `app.*`yi siler; bu dosyanın `app`
            # referansı koleksiyon-anı nesnesinde kalır, uçların
            # `__module__` adı artık `sys.modules`te yoktur.
            # `import_module` yoksa yeniden yükler, AST yine kaynaktan okunur.
            module = importlib.import_module(module_name)
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            table: dict[str, ast.AST] = {}
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    table.setdefault(node.name, node)
            sources[module_name] = table
        return sources[module_name]

    def touches_credentials(node, module_name, depth, seen) -> bool:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and sub.id in credential_helpers:
                return True
            if isinstance(sub, ast.Call) and depth:
                target = sub.func
                name = (
                    target.id if isinstance(target, ast.Name)
                    else getattr(target, "attr", None)
                )
                if name and name not in seen and name in functions(module_name):
                    seen.add(name)
                    if touches_credentials(
                        functions(module_name)[name], module_name, depth - 1, seen
                    ):
                        return True
        return False

    # ``app.routes`` dahil edilen router'ları iç düğümlerde saklar; düz gezinti
    # neredeyse hiçbir şey bulmaz ve kapı SESSİZCE yeşil kalırdı.
    # ``registered_api_operations`` ile aynı yürüyüş, ama uç fonksiyonu da lazım.
    def walk(routes, prefix=""):
        for route in routes:
            if isinstance(route, APIRoute):
                yield f"{prefix}{route.path}", route.endpoint
                continue
            original_router = getattr(route, "original_router", None)
            include_context = getattr(route, "include_context", None)
            if original_router is not None and include_context is not None:
                yield from walk(
                    original_router.routes, f"{prefix}{include_context.prefix}"
                )

    scanned = 0
    offenders = set()
    for path, endpoint in walk(app.routes):
        if not path.startswith("/api") or path in PUBLIC_API:
            continue
        if path.startswith("/api/auth/"):
            continue
        scanned += 1
        node = functions(endpoint.__module__).get(endpoint.__name__)
        if node is not None and touches_credentials(
            node, endpoint.__module__, 4, set()
        ):
            offenders.add(path)

    # Gezintinin GERÇEKTEN yürüdüğünü kanıtla: sıfır tarama, boş sonuç üretir ve
    # bu kapı hiçbir şey ölçmeden yeşil görünürdü.
    assert scanned > 200, scanned
    assert offenders == {"/api/users"}, offenders


def test_both_middleware_gates_share_one_list() -> None:
    """Zorunlu rotasyon kapısı ile yetki muafiyeti AYNI listeyi kullanır."""
    assert PASSWORD_CHANGE_ALLOWED_API is SELF_SERVICE_API


def test_membership_is_exact_not_prefix() -> None:
    """Önek eşleşmesi olsaydı altına eklenen her uç sessizce muaf olurdu."""
    for path in ("/api/auth/me/tokens", "/api/auth/logout/all", "/api/auth/mex"):
        assert path not in SELF_SERVICE_API


def test_route_inventory_answer_is_unchanged_for_these_paths() -> None:
    """``required_permission`` sözleşmesi DEĞİŞMEDİ; muafiyet middleware'dedir."""
    assert required_permission("GET", "/api/auth/me") == "read"
    assert required_permission("POST", "/api/auth/logout") == "read"
    assert required_permission("POST", "/api/auth/change-password") == "read"
    assert required_permission("POST", "/api/auth/logout-all") == "read"


# --------------------------------------------------------------------------
# İki yön de HTTP üstünde
# --------------------------------------------------------------------------

_SMOKE = r'''
import os, sys
sys.path.insert(0, os.environ["BACKEND"])
from fastapi.testclient import TestClient
from sqlalchemy import select, update

import app.main as m
from app.auth import users
from app.db import SessionLocal

BOOT = os.environ["BOOTSTRAP_ADMIN_PASSWORD"]
c = TestClient(m.app)

first = c.post("/api/auth/login", json={"username": "admin", "password": BOOT})
assert first.status_code == 200, ("KURULUM admin login", first.status_code, first.text)
rotated = c.post(
    "/api/auth/change-password",
    json={"current_password": BOOT, "new_password": "MuafiyetSifresi!2026"},
    headers={"Authorization": "Bearer " + first.json()["access_token"]},
)
assert rotated.status_code == 200, ("KURULUM admin rotation", rotated.status_code, rotated.text)
AH = {"Authorization": "Bearer " + rotated.json()["access_token"]}

created = c.post("/api/users", headers=AH, json={
    "username": "muafiyet", "display_name": "Muafiyet Hesabi",
    "password": "MuafiyetGecici!26", "role": "rapor"})
assert created.status_code == 201, ("KURULUM kullanici", created.status_code, created.text)

with SessionLocal() as db:
    uid = db.execute(select(users.c.id).where(users.c.username == "muafiyet")).scalar_one()
    db.execute(update(users).where(users.c.id == uid).values(must_change_password=False))
    db.commit()

uc = TestClient(m.app)
signed = uc.post("/api/auth/login",
                 json={"username": "muafiyet", "password": "MuafiyetGecici!26"})
assert signed.status_code == 200, ("KURULUM login", signed.status_code, signed.text)
UH = {"Authorization": "Bearer " + signed.json()["access_token"]}
csrf = uc.cookies.get("yhp_csrf_token")
refresh_cookie = uc.cookies.get("yhp_refresh_token")

# Rolü ÇÖZÜLEMEZ hale getir — bu PR'ın yarattığı durum.
with SessionLocal() as db:
    db.execute(update(users).where(users.c.id == uid).values(role="bilinmeyen_rol"))
    db.commit()

# --- YÖN 2: iş uçları HÂLÂ KAPALI (muafiyet sızmadı) --------------------
for path in ("/api/products", "/api/customers", "/api/orders", "/api/users",
             "/api/dashboard", "/api/companies", "/api/invoices", "/api/warehouses"):
    answer = uc.get(path, headers=UH)
    assert answer.status_code == 403, ("YON-2 is ucu acilmis", path, answer.status_code, answer.text)
    assert answer.json().get("code") == "PERMISSION_DENIED", ("YON-2 kod", path, answer.text)

# --- YÖN 1: self-servis ÇALIŞIYOR ---------------------------------------
session_info = uc.get("/api/auth/me", headers=UH)
assert session_info.status_code == 200, ("YON-1 me", session_info.status_code, session_info.text)
assert session_info.json()["permissions"] == [], ("YON-1 izinler", session_info.json()["permissions"])

changed = uc.post("/api/auth/change-password", headers=UH,
                  json={"current_password": "MuafiyetGecici!26",
                        "new_password": "MuafiyetKalici!2026"})
assert changed.status_code == 200, ("YON-1 change-password", changed.status_code, changed.text)

# Parola değişimi tüm oturumları düşürür; çıkış için TAZE bir oturum açılır.
lc = TestClient(m.app)
again = lc.post("/api/auth/login",
                json={"username": "muafiyet", "password": "MuafiyetKalici!2026"})
assert again.status_code == 200, ("YON-1 yeniden login", again.status_code, again.text)
LH = {"Authorization": "Bearer " + again.json()["access_token"]}
live_refresh = lc.cookies.get("yhp_refresh_token")
live_csrf = lc.cookies.get("yhp_csrf_token")

# ÇEREZ yoluyla çıkış: tarayıcının gerçek yolu budur ve refresh AİLESİNİ ancak
# bu yol düşürür. Bearer ile çıkışta ``logout`` yalnız o tokenı iptal eder
# (routers/auth.py:905-914, kasıtlı) — kurtarma iddiası tarayıcı yoluna aittir.
bye = lc.post("/api/auth/logout", headers={"X-CSRF-Token": live_csrf})
assert bye.status_code == 204, ("YON-1 logout", bye.status_code, bye.text)

# Çıkış GERÇEKTEN sunucu tarafında: access tokenı ve refresh ailesi ölmüş olmalı.
assert TestClient(m.app).get("/api/auth/me", headers=LH).status_code == 401, "YON-1 access token yasiyor"
rc = TestClient(m.app)
rc.cookies.set("yhp_refresh_token", live_refresh, path="/api/auth")
rc.cookies.set("yhp_csrf_token", live_csrf)
revoked = rc.post("/api/auth/refresh", headers={"X-CSRF-Token": live_csrf})
assert revoked.status_code == 401, ("YON-1 refresh ailesi yasiyor", revoked.status_code, revoked.text)

print("MUAFIYET-TAMAM")
'''


def _run_smoke(database_url: str, workspace: Path) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["SUNGUR_DATA_DIR"] = str(workspace)
    env["AUTO_MIGRATE"] = "true"
    env["ENVIRONMENT"] = "development"
    env["BOOTSTRAP_ADMIN_PASSWORD"] = "MuafiyetBootstrap!2026"
    env["BACKEND"] = str(BACKEND)
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    assert "MUAFIYET-TAMAM" in completed.stdout, completed.stdout


def test_unresolvable_role_keeps_self_service_and_loses_business_routes(
    tmp_path: Path,
) -> None:
    _run_smoke(f"sqlite:///{(tmp_path / 'self-service.db').as_posix()}", tmp_path)
