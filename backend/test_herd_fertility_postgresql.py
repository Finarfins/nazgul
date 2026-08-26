"""PostgreSQL ikizi: Hayvancılık V1 — FAZ 5 döl verimi.

İkizin sebebi bu modülde TARİH ARİTMETİĞİ: göstergelerin tamamı gün farkından
çıkıyor (`(sonraki - onceki).days`). SQLite tarihleri METİN saklayıp geri
okurken `_gun()` dönüşümüne düşüyor, PostgreSQL gerçek `date` döndürüyor. İkisi
aynı aralığı üretmezse buzağılama aralığı sessizce kayar — ve 340 ile 430 gün
arasındaki fark, "sorunsuz" ile "müdahale gerek" arasındaki farktır.

Ayrıca `status='RECORDED'` sabitleri: yanlış sabit yazıldığında sorgu hata
vermiyor, sessizce BOŞ dönüyor ve göstergeler "veri yok" gibi çıkıyor (bu hata
SQLite testinde bir kez gerçekten yakalandı).
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location(
    "herd_fertility_api", BACKEND / "tests" / "test_herd_fertility_api.py"
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
run_fertility_smoke = _contract.run_fertility_smoke


def _pg_url() -> str:
    url = os.getenv("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("The herd fertility test database URL must point to PostgreSQL")
    return url


@pytest.mark.postgresql
def test_herd_fertility_postgresql() -> None:
    run_fertility_smoke(_pg_url())
