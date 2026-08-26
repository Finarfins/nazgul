"""PostgreSQL ikizi: Tarla Yönetimi V1 — FAZ 9 kural ayarları.

İkizin somut sebebi BOOLEAN. `farm_spraying_dose_required` sütunu:

* SQLite'ta 0/1 tamsayı olarak geri gelir, PostgreSQL'de gerçek `bool`.
  `_firma_kurallari` ikisini de `bool()` ile okuyor; bunu yalnız SQLite'ta
  doğrulamak kanıt değil.
* Sütunun varsayılanı da diyalekte duyarlı: `server_default` PG'de `true`,
  SQLite'ta `1` olarak render edilmeli. (İlk yazımda `text('1')` kullanılmıştı
  ve PG'de "boolean sütuna integer varsayılan" hatası verirdi — ölçülüp
  `true()` ile düzeltildi.)

Bir ayarın diyalekte göre yanlış okunması, kuralın SESSİZCE gevşemesi
demektir; bu yüzden üretim diyalektinde koşulmalı.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location(
    "farm_rule_policies", BACKEND / "tests" / "test_farm_rule_policies.py"
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
run_policy_smoke = _contract.run_policy_smoke


def _pg_url() -> str:
    url = os.getenv("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("The farm rule policy test database URL must point to PostgreSQL")
    return url


@pytest.mark.postgresql
def test_rule_policies_postgresql() -> None:
    run_policy_smoke(_pg_url())
