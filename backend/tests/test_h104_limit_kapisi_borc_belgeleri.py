"""H104 — kredi limiti kapısı borç belgelerini kartla AYNI sayar.

Konu: ``app/routers/transactions._credit_exposure`` (satış kaydı ve POS,
``_save`` üzerinden).

Develop ``9a6d7e3``te kapı borç belgelerinden yalnız ``service_fee``
okuyordu (``service_receivable_net_total``). Cari kartı ise ``late_fee`` +
``service_fee`` + ``bounced_check`` topluyordu. Karşılıksız çeki ya da vade
farkı olan müşteri limitten o borç yokmuş gibi geçiyordu (F10-9 keşfi §1.6
P2, K6).

Çare: tek tanım ``receivables_engine.customer_charge_scope_sql`` /
``customer_charge_total``; kart yüklemi, kapı toplamı okur. As-of = bugün
(kartla aynı).

İade KAPSAM DIŞI (K6: ayrı karar). Kart da kapı da iadeyi düşmez, yani
birbirleriyle HÂLÂ aynıdır; yalnız yaşlandırmadan ayrılırlar.

--- MUTASYON TABLOSU --------------------------------------------------------

  * yardımcının tür listesinden ``bounced_check``i düşürmek
        -> ``test_P2_karsiliksiz_cek_limiti_asar_409`` KIRMIZI
  * kapıyı eski ``service_receivable_net_total``a döndürmek
        -> ``test_kapi_bakiyesi_kart_bakiyesine_ESIT`` KIRMIZI
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ANAHTAR = "h104-limit-kapisi-anahtar-h104-limit-kapisi-anahtar-h104-limit"


def _app_anahtarlari() -> set[str]:
    return {k for k in sys.modules if k == "app" or k.startswith("app.")}


@contextmanager
def _uygulama(tmp_path: Path):
    onceki = {k: sys.modules[k] for k in _app_anahtarlari()}
    mp = pytest.MonkeyPatch()
    for ad in list(_app_anahtarlari()):
        del sys.modules[ad]
    mp.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'h104.db').as_posix()}")
    mp.setenv("SUNGUR_DATA_DIR", str(tmp_path))
    mp.setenv("SECRET_KEY", ANAHTAR)
    mp.setenv("BOOTSTRAP_ADMIN_PASSWORD", "H104Acilis!2026x")
    mp.setenv("AUTO_MIGRATE", "true")
    mp.setenv("SUNGUR_PLATFORM_OPERATORS", "")
    mp.setenv("PAYMENT_ALLOCATION_ENGINE_ENABLED", "true")
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
def kurgu(tmp_path_factory):
    from tests.h104_limit_senaryo import Kurgu

    with _uygulama(tmp_path_factory.mktemp("h104")) as (engine, client):
        assert engine.dialect.name == "sqlite"
        yield Kurgu(engine, client, uuid4().hex[:8])


def test_P2_karsiliksiz_cek_limiti_asar_409(kurgu) -> None:
    from tests.h104_limit_senaryo import p2_karsiliksiz_cek

    p2_karsiliksiz_cek(kurgu)


def test_TERSLENMIS_karsiliksiz_cek_limiti_asmaz_201(kurgu) -> None:
    from tests.h104_limit_senaryo import terslenmis_karsiliksiz_cek

    terslenmis_karsiliksiz_cek(kurgu)


def test_vade_farki_limiti_asar_409(kurgu) -> None:
    from tests.h104_limit_senaryo import vade_farki

    vade_farki(kurgu)


def test_yonetici_onayi_satis_403_admin_201_ve_kayit(kurgu) -> None:
    from tests.h104_limit_senaryo import yonetici_onayi

    yonetici_onayi(kurgu)


def test_kapi_bakiyesi_kart_bakiyesine_ESIT(kurgu) -> None:
    from tests.h104_limit_senaryo import kapi_kart_esitligi

    kapi_kart_esitligi(kurgu)


def test_kart_ve_kapi_TEK_yardimciyi_kullanir() -> None:
    """Kaynak düzeyi: iki yüzey de paylaşılan tanımı çağırır, eski yardımcıyı değil."""
    import ast

    def cagrilar(yol: str) -> set[str]:
        agac = ast.parse((BACKEND / yol).read_text(encoding="utf-8"))
        return {d.func.id for d in ast.walk(agac)
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Name)}

    kapi = cagrilar("app/routers/transactions.py")
    assert "customer_charge_total" in kapi
    assert "service_receivable_net_total" not in kapi
    assert "customer_charge_scope_sql" in cagrilar("app/entity_detail.py")
    assert "customer_charge_scope_sql" in cagrilar("app/receivables_engine.py")
