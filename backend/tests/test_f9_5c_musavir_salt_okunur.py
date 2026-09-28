"""F9-5c-1 — `musavir` (dış mali müşavir) rolü ve rol düzeyinde SALT-OKUNUR kapı.

--- NE ÖLÇÜLÜYOR ---------------------------------------------------------------

Müşavir muhasebenin OKUMA izinlerini taşır (`sales`, `purchases`, `payments`,
`finance`, `reports` ...). İzin modeli okuma ile yazmayı AYIRMAZ:
`/api/payments` GET de POST da `payments` ister. Yazma bu yüzden izin
tablosundan değil ara katmandaki `READ_ONLY_ROLES` kapısından kapanır ve kapı
YOL listesine değil METODA bakar.

Kapı bir liste olmadığı için test de liste değil YÜRÜYÜŞTÜR: uygulamadaki
HER yazan operasyon (`_gated_operations()`, `test_undeniable_endpoint_
population.py` deseni) müşavirle çağrılır ve HER biri 403 `ROLE_READ_ONLY`
almalıdır. Yarın eklenen bir yazma ucu buraya kendiliğinden girer.

Kod ayrıca SIRAYI da çiviler: kapı `has_permission`dan ÖNCE koşmalı. Sonra
koşsaydı müşavirin izni OLMAYAN yazma uçları (`POST /api/users`) yine 403
alırdı ama `PERMISSION_DENIED` ile — ön yüz "salt-okunur" ile "yetkin yok"u
ayıramazdı.

--- PG İKİZİ YOK ---------------------------------------------------------------

Dosya ham SQL taşımaz: kurulum SQLAlchemy Core tablolarıyla, geri kalan her
şey HTTP üstünden. Ölçülen karar ara katmanda, veritabanına inmeden verilir.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
_CALISMA = Path(tempfile.mkdtemp(prefix="f95c1-"))
os.environ["DATABASE_URL"] = "sqlite:///" + (_CALISMA / "f95c1.db").as_posix()
os.environ["SUNGUR_DATA_DIR"] = str(_CALISMA)
os.environ["AUTO_MIGRATE"] = "true"
sys.path.insert(0, str(BACKEND))

from fastapi.routing import APIRoute  # noqa: E402

from app.auth import (  # noqa: E402
    READ_ONLY_ROLES,
    ROLE_PERMISSIONS,
    ROLE_RANK,
    SAFE_METHODS,
    SELF_SERVICE_API,
)
from app.main import PUBLIC_API, app  # noqa: E402

PAROLA = "F95c1Musavir!2026"
HAM_VKN = "1234567890"
DONEM = "2026-07"


# --------------------------------------------------------------------------
# Yürüyüş — `test_undeniable_endpoint_population.py` ile AYNI desen.
# --------------------------------------------------------------------------

def _walk(routes, prefix: str = ""):
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
        if path in SELF_SERVICE_API or concrete in SELF_SERVICE_API:
            continue
        gated.append((method, path, concrete))
    return gated


def _gated_writes() -> list[tuple[str, str, str]]:
    return [op for op in _gated_operations() if op[0] not in SAFE_METHODS]


# --------------------------------------------------------------------------
# Kurulum — Core tablolarıyla, ham SQL YOK.
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def dunya():
    from fastapi.testclient import TestClient
    from sqlalchemy import insert

    from app.auth import hash_password, users
    from app.db import SessionLocal
    from app.tenancy import companies, memberships

    with TestClient(app) as istemci:
        simdi = datetime.now(timezone.utc)
        kimlik: dict[str, int] = {}
        with SessionLocal() as db:
            firma = db.execute(
                insert(companies).values(name="F95c1 Firma", is_active=True, created_at=simdi)
            ).inserted_primary_key[0]
            # Her rol + öz-servis testleri için İKİNCİ bir müşavir (çıkış
            # oturumu düşürür; yürüyüşün oturumunu harcamamalı).
            for ad, rol in [(r, r) for r in sorted(ROLE_PERMISSIONS)] + [("musavir2", "musavir")]:
                uid = db.execute(
                    insert(users).values(
                        username=f"f95c1-{ad}", email=f"f95c1-{ad}@f95c1.invalid",
                        email_verified=True, display_name=ad,
                        password_hash=hash_password(PAROLA), role=rol, is_active=True,
                        must_change_password=False, created_at=simdi,
                    )
                ).inserted_primary_key[0]
                db.execute(
                    insert(memberships).values(
                        user_id=uid, company_id=firma, is_default=True, created_at=simdi
                    )
                )
                kimlik[ad] = int(uid)
            db.commit()

        basliklar: dict[str, dict[str, str]] = {}
        oturumlar: dict[str, dict] = {}
        for ad in kimlik:
            giris = istemci.post(
                "/api/auth/login", json={"username": f"f95c1-{ad}", "password": PAROLA}
            )
            assert giris.status_code == 200, (ad, giris.text)
            oturumlar[ad] = giris.json()
            basliklar[ad] = {
                "Authorization": "Bearer " + giris.json()["access_token"],
                "X-Company-ID": str(firma),
            }

        musteri = istemci.post(
            "/api/customers", headers=basliklar["admin"],
            json={"name": "F95c1 Çiftçi", "tax_number": HAM_VKN},
        )
        assert musteri.status_code == 201, musteri.text
        yield {
            "istemci": istemci,
            "basliklar": basliklar,
            "oturumlar": oturumlar,
            "musteri_id": musteri.json()["id"],
        }


# --------------------------------------------------------------------------
# Rol tablosu
# --------------------------------------------------------------------------

def test_READ_ONLY_ROLES_yalniz_musavir_ve_rutbe_en_altta() -> None:
    assert READ_ONLY_ROLES == frozenset({"musavir"})
    assert ROLE_PERMISSIONS["musavir"] == {
        "read", "reports", "farm.view", "herd.view",
        "sales", "purchases", "payments", "finance",
    }
    assert ROLE_RANK["musavir"] == min(ROLE_RANK.values()) == 10
    assert list(ROLE_RANK.values()).count(10) == 1


def test_admin_musaviri_atayabilir_digerleri_rutbeyle(dunya) -> None:
    from app.auth import can_assign_role

    assert can_assign_role("admin", "musavir") is True
    # Rütbesi müşavirden yüksek her rol atayabilir; müşavir kimseyi atayamaz.
    for rol in ROLE_RANK:
        if rol not in ("admin", "musavir"):
            assert can_assign_role(rol, "musavir") is True, rol
            assert can_assign_role("musavir", rol) is False, rol

    istemci = dunya["istemci"]
    cevap = istemci.post(
        "/api/users", headers=dunya["basliklar"]["admin"],
        json={"username": "f95c1-atanan", "display_name": "Atanan Müşavir",
              "password": "F95c1Atanan!2026x", "role": "musavir"},
    )
    assert cevap.status_code == 201, cevap.text


# --------------------------------------------------------------------------
# Kapı — YÜRÜYÜŞ
# --------------------------------------------------------------------------

def test_MUSAVIR_HER_YAZMA_UCUNDA_403_ROLE_READ_ONLY(dunya) -> None:
    istemci = dunya["istemci"]
    baslik = dunya["basliklar"]["musavir"]
    yazmalar = _gated_writes()
    # Yürüyüş BOŞ olsaydı test sessizce yeşil yanardı.
    assert len(yazmalar) > 150, len(yazmalar)
    kacak = []
    for metot, sablon, somut in yazmalar:
        cevap = istemci.request(metot, somut, headers=baslik, json={})
        kod = cevap.json().get("code") if cevap.headers.get(
            "content-type", "").startswith("application/json") else None
        if cevap.status_code != 403 or kod != "ROLE_READ_ONLY":
            kacak.append(f"{metot} {sablon} -> {cevap.status_code} {kod}")
    assert not kacak, (
        f"{len(kacak)}/{len(yazmalar)} yazma ucu musaviri ROLE_READ_ONLY ile "
        "REDDETMEDI:\n  " + "\n  ".join(kacak)
    )


def test_KAPI_IZINDEN_ONCE_izni_olmayan_yazmada_da_kod_ROLE_READ_ONLY(dunya) -> None:
    """Müşavirin `users` izni YOK; kapı sonra koşsaydı kod PERMISSION_DENIED olurdu."""
    istemci = dunya["istemci"]
    cevap = istemci.post(
        "/api/users", headers=dunya["basliklar"]["musavir"],
        json={"username": "f95c1-sizan", "display_name": "x",
              "password": "F95c1Sizan!2026x", "role": "rapor"},
    )
    assert (cevap.status_code, cevap.json().get("code")) == (403, "ROLE_READ_ONLY"), cevap.text


def test_KAPI_YALNIZ_MUSAVIRE_muhasebe_ayni_ucta_yazar(dunya) -> None:
    """Karşı hücre: aynı yazma ucu izni olan YAZAN rolde açık."""
    istemci = dunya["istemci"]
    cevap = istemci.post(
        "/api/customers", headers=dunya["basliklar"]["muhasebe"],
        json={"name": "F95c1 Muhasebe Yazdı"},
    )
    assert cevap.status_code == 201, cevap.text


# --------------------------------------------------------------------------
# Öz-servis açık
# --------------------------------------------------------------------------

def test_OZ_SERVIS_parola_cikis_hepsi_2xx(dunya) -> None:
    istemci = dunya["istemci"]
    baslik = dict(dunya["basliklar"]["musavir2"])
    parola = istemci.post(
        "/api/auth/change-password", headers=baslik,
        json={"current_password": PAROLA, "new_password": "F95c1Yeni!2026xy"},
    )
    assert parola.status_code == 200, parola.text
    baslik["Authorization"] = "Bearer " + parola.json()["access_token"]
    assert istemci.get("/api/auth/me", headers=baslik).status_code == 200
    cikis_hepsi = istemci.post("/api/auth/logout-all", headers=baslik)
    assert 200 <= cikis_hepsi.status_code < 300, cikis_hepsi.text

    giris = istemci.post(
        "/api/auth/login",
        json={"username": "f95c1-musavir2", "password": "F95c1Yeni!2026xy"},
    )
    assert giris.status_code == 200, giris.text
    baslik["Authorization"] = "Bearer " + giris.json()["access_token"]
    cikis = istemci.post("/api/auth/logout", headers=baslik, json={})
    assert 200 <= cikis.status_code < 300, cikis.text


# --------------------------------------------------------------------------
# Okuma açık
# --------------------------------------------------------------------------

@pytest.mark.parametrize("yol", [
    f"/api/accounting/vouchers?period={DONEM}",
    f"/api/accounting/vat-summary?period={DONEM}",
    f"/api/accounting/export?period={DONEM}&target=luca",
    "/api/invoices",
    "/api/purchases",
    "/api/payments",
])
def test_MUSAVIR_MUHASEBE_OKUMALARI_200(dunya, yol) -> None:
    cevap = dunya["istemci"].get(yol, headers=dunya["basliklar"]["musavir"])
    assert cevap.status_code == 200, (yol, cevap.status_code, cevap.text[:300])


# --------------------------------------------------------------------------
# `/me.read_only`
# --------------------------------------------------------------------------

def test_ME_read_only_yalniz_musavirde_true(dunya) -> None:
    istemci = dunya["istemci"]
    for rol in sorted(ROLE_PERMISSIONS):
        cevap = istemci.get("/api/auth/me", headers=dunya["basliklar"][rol])
        assert cevap.status_code == 200, (rol, cevap.text)
        assert cevap.json()["read_only"] is (rol == "musavir"), rol
        # Giriş cevabı AYNI yardımcıdan gelir; ön yüz iki yoldan da okur.
        assert dunya["oturumlar"][rol]["read_only"] is (rol == "musavir"), rol


# --------------------------------------------------------------------------
# Maskeleme (K13)
# --------------------------------------------------------------------------

def test_MUSAVIR_VKN_MASKESIZ_rapor_maskeli(dunya) -> None:
    istemci = dunya["istemci"]

    def vkn(rol: str) -> str:
        cevap = istemci.get("/api/customers", headers=dunya["basliklar"][rol])
        assert cevap.status_code == 200, (rol, cevap.text)
        return next(s for s in cevap.json() if s["id"] == dunya["musteri_id"])["tax_number"]

    assert vkn("musavir") == HAM_VKN
    # Karşı hücre: maskeli rol aynı ucu MASKELİ görür — test maskeyi ölçüyor.
    assert vkn("rapor") not in (HAM_VKN, None, "")
