"""PostgreSQL ikizi: Tarla Yönetimi V1 — FAZ 6 ilaçlama değişmezleri.

Bu ikiz, Faz 5'inki kadar "diyalekte duyarlı bir hesap" içermiyor; buradaki
doğrulamalar saf Python. Yine de üretim diyalektinde koşuluyor çünkü smoke
uçtan uca gidiyor: reddedilen isteğin KAYIT BIRAKMADIĞI iddiası (`total == 0`)
işlem sınırına bağlıdır ve PG'de başarısız bir statement işlemin tamamını
iptal eder — bu davranışın SQLite'takiyle aynı sonucu verdiği ölçülmeli.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location(
    "farm_spraying_invariants", BACKEND / "tests" / "test_farm_spraying_invariants.py"
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
run_spray_smoke = _contract.run_spray_smoke


def _pg_url() -> str:
    url = os.getenv("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("The spraying invariants test database URL must point to PostgreSQL")
    return url


@pytest.mark.postgresql
def test_spraying_invariants_postgresql() -> None:
    run_spray_smoke(_pg_url())
