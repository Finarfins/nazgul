"""PostgreSQL ikizi: Hayvancılık V1 — FAZ 4 aşı takvimi.

İkizin sebebi bu modülde SOMUT ve tek bir satırda toplanıyor:

    WHERE vaccine_code IN :kodlar

Genişleyen (`expanding`) bağlı parametre, kısmi indeks (`ix_animal_vacc_company_code`)
ve `vaccine_code IS NULL OR vaccine_code=''` koşulu diyalekte duyarlı yerler.
Ayrıca takvim TARİH aritmetiğine dayanıyor: SQLite tarihleri metin olarak
saklayıp geri okurken `_gun()` dönüşümüne düşüyor, PostgreSQL gerçek `date`
döndürüyor. İkisinin AYNI pencereyi üretmesi ölçülmeden varsayılamaz —
tarla modülünde tam bu ayrım (boolean ve tarih dönüşümü) iki kez gerçek hata
çıkarmıştı.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location(
    "herd_vaccination_calendar_api",
    BACKEND / "tests" / "test_herd_vaccination_calendar_api.py",
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
run_calendar_smoke = _contract.run_calendar_smoke


def _pg_url() -> str:
    url = os.getenv("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("The vaccination calendar test database URL must point to PostgreSQL")
    return url


@pytest.mark.postgresql
def test_vaccination_calendar_postgresql() -> None:
    run_calendar_smoke(_pg_url())
