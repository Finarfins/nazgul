"""H54 — beş liste ucunda `offset` tavanı `INT4_UST` (SQLite kapısı).

KUSUR: beş uç `offset: int = Query(0, ge=0)` taşıyordu — alt sınır var, üst
yok. ÖLÇÜLDÜ (PG 16, taze veritabanı, `60f737b`): `offset=2^31` beşinde de
200; `offset=2^63` dördünde 500 (`psycopg.errors.NumericValueOutOfRange:
bigint out of range`), `/api/products/lots/mutabakat`ta 200 (sayfayı
Python'da dilimliyor). Tavan artık her yerde `INT4_UST` (H51 kararı, gerekçe
`app/sinirlar.py`): dışı 422, sınır 200.

Bu dosya 422/200 SÖZLEŞMESİNİ ölçer; sürücü taşmasının 500'ü yalnız PG'de
görünür ve `test_h54_offset_tavani_postgresql.py`de ölçülür.

MUTASYON: bir uçtan `le=INT4_UST`yi silmek o ucun `INT4_UST+1` dalını 200'e
döndürür (KIRMIZI).
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
PAROLA = "H54OffsetTavani!2026x"
ANAHTAR = "h54-offset-tavani-anahtar-h54-offset-tavani-anahtar-h54"

UCLAR = (
    "/api/activity-logs",
    "/api/products/lots/mutabakat",
    "/api/platform/companies",
    "/api/platform/users",
    "/api/platform/verifications",
)


def _app_anahtarlari() -> set[str]:
    return {k for k in sys.modules if k == "app" or k.startswith("app.")}


@contextmanager
def _uygulama(tmp_path: Path):
    """``app`` paketini bu dosyanın veritabanıyla TAZE içe aktarır, sonra geri
    koyar (``test_pp1_platform_paneli`` ile aynı desen)."""
    onceki = {k: sys.modules[k] for k in _app_anahtarlari()}
    mp = pytest.MonkeyPatch()
    for ad in list(_app_anahtarlari()):
        del sys.modules[ad]
    mp.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'h54.db').as_posix()}")
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
            yield engine, client
    finally:
        mp.undo()
        for ad in list(_app_anahtarlari()):
            del sys.modules[ad]
        sys.modules.update(onceki)


@pytest.fixture(scope="module")
def ortam(tmp_path_factory):
    """Açılış admini: firma üyesi VE platform operatörü — tek istemci beş uca da
    ulaşır (platform uçları `X-Company-ID`yi okumaz)."""
    with _uygulama(tmp_path_factory.mktemp("h54")) as (engine, client):
        from sqlalchemy import text

        from app.config import settings
        from app.sinirlar import INT4_UST

        with engine.begin() as c:
            c.execute(text("UPDATE app_users SET must_change_password=0, email_verified=1"))
            admin = int(c.execute(text("SELECT id FROM app_users WHERE username='admin'")).scalar_one())
            firma = int(c.execute(text(
                "SELECT company_id FROM user_company_memberships WHERE user_id=:u AND is_default=1"),
                {"u": admin}).scalar_one())
        onceki = settings.sungur_platform_operators
        settings.sungur_platform_operators = str(admin)
        try:
            giris = client.post("/api/auth/login", json={"username": "admin", "password": PAROLA})
            assert giris.status_code == 200, giris.text
            client.cookies.clear()  # Bearer ile devam; CSRF bu dosyanın konusu değil
            client.headers.update({
                "Authorization": "Bearer " + giris.json()["access_token"],
                "X-Company-ID": str(firma),
            })
            yield client, INT4_UST
        finally:
            settings.sungur_platform_operators = onceki


@pytest.mark.parametrize("yol", UCLAR)
def test_OFFSET_INT4_UST_ustu_422_sinirda_200(ortam, yol: str) -> None:
    client, int4_ust = ortam
    taban = client.get(f"{yol}?offset=0")
    assert taban.status_code == 200, (yol, taban.status_code, taban.text[:300])

    tasan = client.get(f"{yol}?offset={int4_ust + 1}")
    assert tasan.status_code == 422, (yol, tasan.status_code, tasan.text[:300])
    assert tasan.json()["detail"][0]["loc"] == ["query", "offset"]

    sinir = client.get(f"{yol}?offset={int4_ust}")
    assert sinir.status_code == 200, (yol, sinir.status_code, sinir.text[:300])
    assert sinir.json()["items"] == []


def test_INT4_UST_tek_evde_ve_int4_tavani() -> None:
    """H69: sabit `app/sinirlar.py`de yaşar; yönlendiriciler onu tanımlamaz,
    yalnız içe aktarır. İkinci bir `INT4_UST = ...` ataması KIRMIZI."""
    import ast

    tanimlar = []
    deger = None
    for dosya in sorted((BACKEND / "app").rglob("*.py")):
        agac = ast.parse(dosya.read_text(encoding="utf-8"))
        for dugum in ast.walk(agac):
            hedefler = (dugum.targets if isinstance(dugum, ast.Assign)
                        else [dugum.target] if isinstance(dugum, ast.AnnAssign) else [])
            if any(isinstance(h, ast.Name) and h.id == "INT4_UST" for h in hedefler):
                tanimlar.append(dosya.relative_to(BACKEND).as_posix())
                deger = ast.literal_eval(dugum.value)
    assert tanimlar == ["app/sinirlar.py"]
    assert deger == 2**31 - 1
