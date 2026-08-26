"""Yetki tablosunun çözemediği rol HİÇBİR ŞEY almaz (fail-closed).

Konu: yetki kapısının ``{"read"}`` varsayılanı.

--- KUSUR ---------------------------------------------------------------------

``has_permission`` tanımadığı bir rolü ``{"read"}`` ile karşılıyordu. Sonuç:
gerekli izni ``read`` olan HER uç — ölçüldüğünde 326 kimlikli ucun 89'u —
``app_users.role`` sütununun alabileceği HİÇBİR değerle reddedilemiyordu. Boş
dize, sonraki bir sürümde tablodan düşürülen bir rol, doğrudan veritabanına
yazılmış bir değer: hepsi okuma yetkisi alıyordu. Kapı çalışıyordu ama HAYIR
diyemiyordu.

--- BU DOSYA İKİ YÖNÜ DE ÖLÇER -------------------------------------------------

Yalnız kapının ateşlendiği yönü ölçen bir test kümesi, kapının ateşlendiğini
kanıtlar; FAZLA GÜVENMEDİĞİNİ kanıtlamaz. Bu yüzden iki yön de zorunlu:

* **Yön A — kapı ateşleniyor.** Çözülemeyen rol ``read`` isteyen bir uçta
  reddedilir. Düzeltme geri alınırsa bu testler KIRMIZI olmalıdır.

* **Yön B — kapı fazla reddetmiyor.** ``read`` iznini gerçekten taşıyan bilinen
  bir rol geçmeye devam eder. Bu yön BAŞTAN SONA yeşil kalmalı, ve o izni
  kaldıran bir mutasyon KIRMIZI vermelidir.

Yön B'nin çapaları ``ROLE_PERMISSIONS``tan OKUNMAZ, elle yazılır. Tablodan
okusaydı tabloyu değiştiren bir mutasyonu takip eder ve sessizce yeşil kalırdı —
yani hiçbir şey ölçmezdi.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.auth import (  # noqa: E402
    ROLE_PERMISSIONS,
    has_permission,
    permissions_for,
    required_permission,
)


# --------------------------------------------------------------------------
# Yön A — kapı çözülemeyen rolde ateşlenir
# --------------------------------------------------------------------------

#: Sütun ``VARCHAR(40) NOT NULL``; CHECK kısıtı YOK. Tablo dışındaki her değer
#: buraya düşebilir — büyük/küçük harf ve boşluk farkları dahil, çünkü arama
#: birebir sözlük araması.
UNRESOLVABLE_ROLES = (
    "bilinmeyen_rol",
    "",
    "   ",
    "Admin",
    "ADMIN",
    "admin ",
    " admin",
    "rapor.",
    "service",  # app/auth.py'de "ileride eklenecek" diye anılan, HENÜZ OLMAYAN rol
    None,
)

#: Tabloda geçen izinlerin tamamı. Bunların hiçbiri çözülemeyen bir role
#: verilmemeli — ``read`` özellikle, kusurun tam olarak orada olması yüzünden.
EVERY_PERMISSION = (
    "read", "sales", "field_service", "purchases", "payments", "finance",
    "stock", "reports", "users", "machines", "notifications",
    "notifications_approve", "notifications_dispatch", "notifications_admin",
    "supplier_prices.view", "supplier_prices.import", "supplier_prices.apply",
    "supplier_prices.override_block", "farm.view", "farm.manage", "farm.inputs",
    "herd.view", "herd.manage", "herd.health", "__admin_only__",
)


@pytest.mark.parametrize("role", UNRESOLVABLE_ROLES)
def test_unresolvable_role_holds_no_permission_at_all(role) -> None:
    assert permissions_for(role) == frozenset()
    for permission in EVERY_PERMISSION:
        assert has_permission(role, permission) is False, (role, permission)


def test_read_is_not_a_free_pass_for_an_unresolvable_role() -> None:
    """Kusurun tam merkezi: ``read`` isteyen bir uç + tanınmayan rol."""
    assert required_permission("GET", "/api/products") == "read"
    assert required_permission("GET", "/api/customers") == "read"
    assert has_permission("bilinmeyen_rol", "read") is False
    assert has_permission("", "read") is False


def test_wildcard_is_not_reachable_through_an_unresolvable_role() -> None:
    assert "*" not in permissions_for("bilinmeyen_rol")
    assert has_permission("bilinmeyen_rol", "__admin_only__") is False


# --------------------------------------------------------------------------
# Yön B — bilinen roller taşıdıkları izni TAŞIMAYA DEVAM EDER
# --------------------------------------------------------------------------

#: ELLE YAZILMIŞ ÇAPALAR. Bilerek ``ROLE_PERMISSIONS``tan türetilmiyor: tablodan
#: okuyan bir iddia, tabloyu bozan mutasyonla birlikte kayar ve yeşil kalır.
KNOWN_ROLE_ANCHORS = (
    ("admin", "read", True),
    ("admin", "users", True),
    ("admin", "finance", True),
    ("admin", "__admin_only__", True),
    ("yonetici", "read", True),
    ("yonetici", "users", True),
    ("muhasebe", "read", True),
    ("muhasebe", "payments", True),
    ("satis", "read", True),
    ("satis", "sales", True),
    ("depo", "read", True),
    ("depo", "stock", True),
    ("rapor", "read", True),
    ("rapor", "reports", True),
    # Fazla reddetmediğini ölçmek yetmez; fazla GÜVENMEDİĞİNİ de ölçmek gerek.
    ("rapor", "users", False),
    ("rapor", "sales", False),
    ("satis", "stock", False),
    ("satis", "finance", False),
    ("depo", "finance", False),
    ("muhasebe", "stock", False),
    ("yonetici", "__admin_only__", False),
)


@pytest.mark.parametrize("role,permission,expected", KNOWN_ROLE_ANCHORS)
def test_known_role_permission_anchor(role, permission, expected) -> None:
    assert has_permission(role, permission) is expected


def test_every_known_role_still_holds_baseline_read() -> None:
    """Altı rolün altısı da ``read`` taşır; düzeltme bunu değiştirmemeli."""
    for role in ("admin", "yonetici", "muhasebe", "satis", "depo", "rapor"):
        assert has_permission(role, "read") is True, role
    assert set(ROLE_PERMISSIONS) == {
        "admin", "yonetici", "muhasebe", "satis", "depo", "rapor"
    }


# --------------------------------------------------------------------------
# İki yön de HTTP üstünde: kapı gerçekten istek yolunda mı?
# --------------------------------------------------------------------------

#: KURULUM HİÇBİR YERDE ÇÖKMEZ. Her adım kendi durum kodunu ETİKETLİ bir
#: assert ile doğrular; hiçbir yanıt gövdesi önce kontrol edilmeden
#: indekslenmez. Aksi hâlde bir mutasyon kurulumu bozduğunda test ``KeyError``
#: ile kırmızıya döner ve "kapı ateşlendi" ile "kurulum çöktü" ayırt edilemez.
#: Bu gerçekten yaşandı: rapor rolünden ``read`` düşürüldüğünde zorunlu parola
#: değişimi ucu (``/api/auth/change-password``, gerekli izin ``read``) 403
#: dönüyor ve gövdede ``access_token`` bulunmuyordu. O yüzden ``rapor`` hesabının
#: zorunlu rotasyonu kurulumda VERİTABANINDAN temizleniyor: ölçülmek istenen şey
#: rotasyon değil, kapının kendisi.
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
    json={"current_password": BOOT, "new_password": "KapiSifresi!2026"},
    headers={"Authorization": "Bearer " + first.json()["access_token"]},
)
assert rotated.status_code == 200, ("KURULUM admin rotation", rotated.status_code, rotated.text)
AH = {"Authorization": "Bearer " + rotated.json()["access_token"]}

created = c.post("/api/users", headers=AH, json={
    "username": "kapi_rapor", "display_name": "Kapi Rapor",
    "password": "KapiSifresi!2026x", "role": "rapor"})
assert created.status_code == 201, ("KURULUM rapor kullanici", created.status_code, created.text)

# Tanınmayan rol API'den YAZILAMAZ; kapı bu yüzden ayrıca ölçülüyor.
refused = c.post("/api/users", headers=AH, json={
    "username": "kapi_bilinmeyen", "display_name": "Kapi Bilinmeyen",
    "password": "KapiSifresi!2026x", "role": "bilinmeyen_rol"})
assert refused.status_code == 400, ("KURULUM bilinmeyen rol reddi", refused.status_code, refused.text)

# Zorunlu rotasyon kurulumda veritabanından kaldırılır — bkz. yukarıdaki not.
with SessionLocal() as db:
    uid = db.execute(
        select(users.c.id).where(users.c.username == "kapi_rapor")
    ).scalar_one()
    db.execute(update(users).where(users.c.id == uid)
               .values(must_change_password=False))
    db.commit()

rc = TestClient(m.app)
signed_in = rc.post("/api/auth/login",
                    json={"username": "kapi_rapor", "password": "KapiSifresi!2026x"})
assert signed_in.status_code == 200, ("KURULUM rapor login", signed_in.status_code, signed_in.text)
RH = {"Authorization": "Bearer " + signed_in.json()["access_token"]}

# --- YÖN B: bilinen, ``read`` taşıyan rol GEÇMEYE DEVAM EDER -------------
assert "read" in signed_in.json()["permissions"], ("YON-B izin listesi", signed_in.json()["permissions"])
for path in ("/api/products", "/api/customers", "/api/orders"):
    answer = rc.get(path, headers=RH)
    assert answer.status_code == 200, ("YON-B", path, answer.status_code, answer.text)

# --- YÖN A: aynı hesabın rolü çözülemez olunca REDDEDİLİR ---------------
with SessionLocal() as db:
    db.execute(update(users).where(users.c.id == uid).values(role="bilinmeyen_rol"))
    db.commit()

for path in ("/api/products", "/api/customers", "/api/orders"):
    answer = rc.get(path, headers=RH)
    assert answer.status_code == 403, ("YON-A", path, answer.status_code, answer.text)
    # 401 ya da 500 DEĞİL: reddi kapının kendisi vermiş olmalı.
    assert answer.json().get("code") == "PERMISSION_DENIED", ("YON-A kod", path, answer.text)

bc = TestClient(m.app)
again = bc.post("/api/auth/login",
                json={"username": "kapi_rapor", "password": "KapiSifresi!2026x"})
assert again.status_code == 200, ("YON-A yeniden login", again.status_code, again.text)
assert again.json()["permissions"] == [], ("YON-A oturum izinleri", again.json()["permissions"])

print("KAPI-TAMAM")
'''


def _run_smoke(database_url: str, workspace: Path) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["SUNGUR_DATA_DIR"] = str(workspace)
    env["AUTO_MIGRATE"] = "true"
    env["ENVIRONMENT"] = "development"
    env["BOOTSTRAP_ADMIN_PASSWORD"] = "KapiBootstrap!2026"
    env["BACKEND"] = str(BACKEND)
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    assert "KAPI-TAMAM" in completed.stdout, completed.stdout


def test_gate_denies_unresolvable_role_and_admits_known_role_over_http(
    tmp_path: Path,
) -> None:
    _run_smoke(f"sqlite:///{(tmp_path / 'role-gate.db').as_posix()}", tmp_path)
