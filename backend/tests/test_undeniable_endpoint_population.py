"""Hiçbir rolün REDDEDİLEMEDİĞİ uçlar — sınıf olarak sabitlenir.

Konu: altı rolün altısının da taşıdığı bir izinle korunan uçlar.

--- KUSUR ----------------------------------------------------------------------

Bir izin, hiçbir rolün reddedilemediği bir uçta HİÇBİR BİLGİ TAŞIMAZ. Kapı
çalışır, günlüğe yazar, 403 döndürebilecek gibi durur — ama döndüremez, çünkü
tabloda o izni taşımayan rol yoktur. Ölçüldüğünde (develop `fd71326`):

    yetki kapısından geçen operasyon        : 326
    hiçbir rolün reddedilemediği operasyon  : 117
      read       89
      farm.view  14
      herd.view  14
    yöntem dağılımı                          : 114 GET, 3 POST

--- İKİ AYRI KATEGORİ, AYRI MUAMELE --------------------------------------------

**Tasarım gereği açık (114 GET).** ``read``/``farm.view``/``herd.view``
izinlerini altı rolün altısı da BİLEREK taşır: bu üründe her rol okur. Okuma
uçlarının reddedilemez olması bir kusur değil, ürün kararıdır. Bu dosya o kararı
değiştirmez; DEĞİŞTİĞİNDE ya da SESSİZCE BÜYÜDÜĞÜNDE görünür kılar — dördüncü
bir izin herkese verilirse aşağıdaki çapa kırmızı olur.

**Yazan uçlar (3 POST) — burada tasarım kararı YOKTUR.** Yazan bir operasyonun
middleware izni evrenselse, o operasyonu middleware katmanında hiçbir rolden men
edemezsiniz. Bugün üçü de ``/api/platform/backups`` altındadır ve router kendi
daha güçlü denetimini uygular (``require_platform_operator``: rol ``admin`` VE
kimlik operatör listesinde). Yani middleware izni ATILDIR.

Ama atıllık ÖLÇÜLMEMİŞTİ: bu üç uç yalnız rota envanterinde listeleniyordu,
hiçbir test gerçek bir istekle reddedildiklerini göstermiyordu. ``_authorize``
çağrısı bir gün silinse, uçlar altı rolün altısına da açılırdı ve hiçbir kapı
ateşlemezdi. Bu dosya o boşluğu kapatır.

--- SINIF SABİTLENİR, ÖRNEKLER DEĞİL -------------------------------------------

Kural: **evrensel bir izinle korunan her GET-DIŞI operasyon, tek tek çapalanmak
ve ATIL OLDUĞU KANITLANMAK zorundadır.** Çapalanmamış yeni bir tanesi, kapı onu
hiç tanımasa bile KIRMIZIDIR — varsayılan reddir. Bilinen-kötü uçları sayan bir
kara liste değildir; 117 ucu tek tek yazmak da değildir.

Çapalar ``ROLE_PERMISSIONS``tan ya da rota tablosundan TÜRETİLMEZ, elle
yazılır. Türetseydik, kaynağı bozan bir mutasyon çapayı da kaydırır ve kapı
sessizce yeşil kalırdı — bu depoda üç kez yaşandı.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("DATABASE_URL", "sqlite:///./__undeniable.db")

from fastapi.routing import APIRoute  # noqa: E402

from app.auth import (  # noqa: E402
    ROLE_PERMISSIONS,
    SELF_SERVICE_API,
    has_permission,
    required_permission,
)
from app.main import PUBLIC_API, app  # noqa: E402

#: ELLE YAZILMIŞ ÇAPA — altı rolün ALTISININ da taşıdığı izinler.
#:
#: Her biri bilinçli bir ürün kararıdır:
#:   read       — her rol kendi ekranını okuyabilmeli; okuma yetkisi rol ayrımı
#:                yapmaz, ayrım YAZMA izinlerinde kurulur.
#:   farm.view  — Tarla V1 yetki modeli okumayı ayırmadı (mobil-erp#2).
#:   herd.view  — Hayvancılık V1 aynı deseni izledi (mobil-erp#17).
#:
#: Dördüncü bir izin altı role birden verilirse burası kırmızı olur: yeni bir
#: izin sınıfının sessizce bilgi taşımaz hâle gelmesi görünür olsun diye.
UNIVERSAL_PERMISSIONS = frozenset({"read", "farm.view", "herd.view"})

#: ELLE YAZILMIŞ ÇAPA — evrensel izinle korunan GET-DIŞI operasyonlar.
#:
#: Üçü de ``require_platform_operator`` ile korunur: rol ``admin`` OLMAK ZORUNDA
#: ve kullanıcı kimliği ``SUNGUR_PLATFORM_OPERATORS`` listesinde bulunmak
#: zorunda. Middleware izni (``read``) bu yüzden ATILDIR — ve atıllık aşağıda
#: gerçek isteklerle kanıtlanır, iddia edilmez.
INERT_WRITE_EXEMPTIONS = frozenset({
    ("POST", "/api/platform/backups"),
    ("POST", "/api/platform/backups/{name}/restore"),
    ("POST", "/api/platform/backups/{name}/verify"),
})

ROLES = ("admin", "yonetici", "muhasebe", "satis", "depo", "rapor")


def _walk(routes, prefix: str = ""):
    """``app.routes`` dahil edilen router'ları iç düğümde saklar."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield f"{prefix}{route.path}", route
        else:
            context = getattr(route, "include_context", None)
            original = getattr(route, "original_router", None)
            if context is not None and original is not None:
                yield from _walk(original.routes, f"{prefix}{context.prefix}")


def _concrete(path: str) -> str:
    return "/".join(
        "orders" if part == "{kind}" else ("1" if part.startswith("{") else part)
        for part in path.split("/")
    )


def _gated_operations() -> list[tuple[str, str, str]]:
    """Yetki kapısından GERÇEKTEN geçen (yöntem, yol, somut yol) üçlüleri."""
    operations = sorted({
        (method, path)
        for path, route in _walk(app.routes)
        for method in sorted(route.methods or ())
        if method not in ("HEAD", "OPTIONS")
    })
    gated: list[tuple[str, str, str]] = []
    for method, path in operations:
        concrete = _concrete(path)
        if (method, path) in PUBLIC_API or path in PUBLIC_API or concrete in PUBLIC_API:
            continue
        if (
            (method, path) in SELF_SERVICE_API
            or path in SELF_SERVICE_API
            or concrete in SELF_SERVICE_API
        ):
            continue
        gated.append((method, path, concrete))
    return gated


def _universal_permissions() -> frozenset[str]:
    """Altı rolün de taşıdığı izinler — rota tablosundan değil, ROL tablosundan."""
    every = {permission for granted in ROLE_PERMISSIONS.values() for permission in granted}
    every.discard("*")
    return frozenset(p for p in every if all(has_permission(role, p) for role in ROLES))


def test_role_table_still_has_exactly_the_six_known_roles() -> None:
    assert set(ROLE_PERMISSIONS) == set(ROLES)


def test_universal_permission_set_matches_its_anchor() -> None:
    """Dördüncü bir izin herkese verilirse burası kırmızı olur."""
    assert _universal_permissions() == UNIVERSAL_PERMISSIONS


def test_every_universal_permission_is_truly_undeniable() -> None:
    """Çapanın kendisi doğru mu: altı rol de gerçekten taşıyor mu."""
    for permission in UNIVERSAL_PERMISSIONS:
        for role in ROLES:
            assert has_permission(role, permission) is True, (role, permission)


def test_no_unanchored_write_is_gated_by_a_universal_permission() -> None:
    """SINIF KURALI — varsayılan RED.

    Evrensel izinle korunan yeni bir yazan uç, kapı onu hiç tanımasa bile
    kırmızıdır. Çapaya eklemek görünür bir düzenlemedir.
    """
    offenders = sorted(
        (method, path)
        for method, path, concrete in _gated_operations()
        if method != "GET"
        and required_permission(method, concrete) in UNIVERSAL_PERMISSIONS
        and (method, path) not in INERT_WRITE_EXEMPTIONS
    )
    assert not offenders, (
        "Evrensel izinle korunan ve ÇAPALANMAMIŞ yazan uç(lar) var; middleware "
        "katmanında hiçbir rolden men edilemezler: " + repr(offenders)
    )


def test_every_anchored_exemption_still_exists_and_still_needs_the_anchor() -> None:
    """Çapa BAYATLAMAZ: silinmiş ya da artık evrensel olmayan bir giriş kırmızıdır."""
    gated = {(method, path): concrete for method, path, concrete in _gated_operations()}
    for method, path in sorted(INERT_WRITE_EXEMPTIONS):
        assert (method, path) in gated, (
            f"{method} {path} artık yetki kapısından geçmiyor; çapa bayat"
        )
        assert required_permission(method, gated[(method, path)]) in UNIVERSAL_PERMISSIONS, (
            f"{method} {path} artık evrensel bir izinle korunmuyor; çapa gereksiz"
        )


def test_gets_gated_by_universal_permissions_are_the_declared_design() -> None:
    """Tasarım gereği açık olan GET'ler ölçülür ve BEYAN EDİLİR, gizlenmez."""
    universal_gets = [
        (method, path)
        for method, path, concrete in _gated_operations()
        if method == "GET" and required_permission(method, concrete) in UNIVERSAL_PERMISSIONS
    ]
    assert universal_gets, "ölçüm boşa düştü: evrensel izinli GET bulunamadı"
    for method, path in universal_gets:
        assert method == "GET", (method, path)


# --------------------------------------------------------------------------
# ATILLIK GERÇEK İSTEKLE KANITLANIR — iddia edilmez
# --------------------------------------------------------------------------

#: Kurulumun her adımı ETİKETLİ assert ile doğrulanır: bir mutasyon kurulumu
#: bozduğunda "kapı ateşlendi" ile "kurulum çöktü" ayırt edilebilsin diye.
_SMOKE = r'''
import os, sys
sys.path.insert(0, os.environ["BACKEND"])
from fastapi.testclient import TestClient

import app.main as m

BOOT = os.environ["BOOTSTRAP_ADMIN_PASSWORD"]
# Gövde, ŞEMAYA UYGUN gönderilir. Pydantic doğrulaması router gövdesinden
# ÖNCE koşuyor; eksik alanla 422 alınır ve o 422 bir REDDİN kanıtı değildir.
# Ölçmek istediğimiz şey doğrulama değil, yetki reddi.
YAZAN = (
    ("POST", "/api/platform/backups", {}),
    ("POST", "/api/platform/backups/dosya/restore", {"confirmation": "GERI YUKLE"}),
    ("POST", "/api/platform/backups/dosya/verify", {}),
)

c = TestClient(m.app)
first = c.post("/api/auth/login", json={"username": "admin", "password": BOOT})
assert first.status_code == 200, ("KURULUM admin login", first.status_code, first.text)
rotated = c.post(
    "/api/auth/change-password",
    json={"current_password": BOOT, "new_password": "AtilKapi!2026"},
    headers={"Authorization": "Bearer " + first.json()["access_token"]},
)
assert rotated.status_code == 200, ("KURULUM admin rotation", rotated.status_code, rotated.text)
AH = {"Authorization": "Bearer " + rotated.json()["access_token"]}

# --- YÖN A: ADMIN ama operatör listesinde DEĞİL -> 403 --------------------
# Middleware izni `read`; admin onu taşır. Reddi veren tek şey router'ın
# kendi denetimidir. Bu yüzden bu iki sütun ayrı ayrı ölçülüyor.
for method, path, body in YAZAN:
    answer = c.request(method, path, headers=AH, json=body)
    assert answer.status_code == 403, ("YON-A admin-operator-degil", path, answer.status_code, answer.text)

# --- YÖN B: OKUMA izni taşıyan ama admin OLMAYAN rol -> 403 ---------------
created = c.post("/api/users", headers=AH, json={
    "username": "atil_satis", "display_name": "Atil Satis",
    "password": "AtilKapi!2026x", "role": "satis"})
assert created.status_code == 201, ("KURULUM satis kullanici", created.status_code, created.text)

sc = TestClient(m.app)
login = sc.post("/api/auth/login",
                json={"username": "atil_satis", "password": "AtilKapi!2026x"})
assert login.status_code == 200, ("KURULUM satis login", login.status_code, login.text)
# Yeni hesap zorunlu parola rotasyonuyla doğar; rotasyon yapılmadan HER uç
# PASSWORD_CHANGE_REQUIRED döner ve o 403 yetki reddi DEĞİLDİR. Rotasyon
# self-servis uçtan yapılır (yetki kapısına hiç girmez).
sc_rot = sc.post(
    "/api/auth/change-password",
    json={"current_password": "AtilKapi!2026x", "new_password": "AtilKapi!2026y"},
    headers={"Authorization": "Bearer " + login.json()["access_token"]},
)
assert sc_rot.status_code == 200, ("KURULUM satis rotation", sc_rot.status_code, sc_rot.text)
SH = {"Authorization": "Bearer " + sc_rot.json()["access_token"]}

# Kurulum varsayımı: bu rol middleware izni `read`i GERÇEKTEN taşıyor —
# yani reddin kaynağı yetki kapısı DEĞİL, router olmalı.
okuma = sc.get("/api/products", headers=SH)
assert okuma.status_code == 200, ("KURULUM satis read", okuma.status_code, okuma.text)

for method, path, body in YAZAN:
    answer = sc.request(method, path, headers=SH, json=body)
    assert answer.status_code == 403, ("YON-B satis", path, answer.status_code, answer.text)

print("ATIL-KAPI-TAMAM")
'''


def _run_smoke(database_url: str, workspace: Path) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["SUNGUR_DATA_DIR"] = str(workspace)
    env["AUTO_MIGRATE"] = "true"
    env["ENVIRONMENT"] = "development"
    env["BOOTSTRAP_ADMIN_PASSWORD"] = "AtilBootstrap!2026"
    # Operatör listesi BİLEREK boş: admin bile geçememeli.
    env["SUNGUR_PLATFORM_OPERATORS"] = ""
    env["BACKEND"] = str(BACKEND)
    env["PYTHONPATH"] = str(BACKEND)
    # Alt süreç KATI UTF-8 ile okunur. Windows'ta varsayılan kod sayfası
    # (cp1254) uygulamanın Türkçe günlük satırlarını çözemiyor ve test, kapının
    # sonucuyla ilgisiz bir UnicodeDecodeError ile kırmızıya düşüyordu.
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="strict", timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    assert "ATIL-KAPI-TAMAM" in completed.stdout, completed.stdout


def test_anchored_write_exemptions_are_provably_inert(tmp_path: Path) -> None:
    """Çapalı üç uç GERÇEKTEN reddediyor mu — router denetimi silinirse kırmızı."""
    _run_smoke(f"sqlite:///{(tmp_path / 'atil.db').as_posix()}", tmp_path)
