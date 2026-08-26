"""PostgreSQL ikizi: Tarla Yönetimi V1 — FAZ 1 veri modeli.

SQLite koşusu mantığı doğruluyor ama iki iddia ancak ÜRETİM diyalektinde
gerçek anlam kazanıyor:

1. **Çapraz kiracı bağın imkânsızlığı.** SQLite'ta yabancı anahtarlar
   varsayılan olarak KAPALI; testte `PRAGMA foreign_keys=ON` ile açılıyor ve
   bu, üretimin davranışını taklit ediyor — taklit değil, gerçeğini görmek
   gerekiyor. PostgreSQL'de bileşik yabancı anahtar her zaman zorunludur.

2. **NUMERIC → Decimal.** SQLite NUMERIC'i REAL olarak saklar ve float
   döndürür; dekar başına verim/maliyet bölmelerine giren değerin üretimde
   Decimal geldiği yalnız burada kanıtlanabilir.

Ayrıca CHECK kısıtlarının PostgreSQL'de de fiilen uygulandığı doğrulanıyor —
SQLite bazı kısıtları daha gevşek yorumlayabiliyor.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent

# backend/tests bir paket değil; ortak smoke dosya yolundan yükleniyor.
_spec = importlib.util.spec_from_file_location(
    "farm_management_foundation",
    BACKEND / "tests" / "test_farm_management_foundation.py",
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
run_farm_foundation_smoke = _contract.run_farm_foundation_smoke


def _pg_url() -> str:
    database_url = os.getenv("APP_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("The farm test database URL must point to PostgreSQL")
    return database_url


@pytest.mark.postgresql
def test_farm_foundation_postgresql() -> None:
    run_farm_foundation_smoke(_pg_url())
