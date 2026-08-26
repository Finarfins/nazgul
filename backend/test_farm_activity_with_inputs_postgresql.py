"""PostgreSQL ikizi: Tarla Yönetimi V1 — FAZ 10 tek istekte faaliyet + girdiler.

İkizin sebebi doğrudan İŞLEM SINIRI. Testin ana iddiası "ya hepsi ya hiçbiri"
ve bu, veritabanının hata sonrası davranışına bağlı:

* PostgreSQL'de başarısız bir statement işlemin TAMAMINI iptal eder; SQLite
  daha hoşgörülüdür. Bu modülde daha önce tam bu fark yüzünden SQLite'ta yeşil,
  PG'de bozuk bir davranış yaşandı (FAZ 4 tekrar koruması).
* Girdi doğrulaması INSERT'ten sonra hata fırlatıyorsa, faaliyetin de geri
  alındığı üretim diyalektinde kanıtlanmalı — yarım kayıt, hiç kayıt
  olmamasından kötüdür.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location(
    "farm_activity_with_inputs", BACKEND / "tests" / "test_farm_activity_with_inputs.py"
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
run_nested_smoke = _contract.run_nested_smoke


def _pg_url() -> str:
    url = os.getenv("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("The nested activity test database URL must point to PostgreSQL")
    return url


@pytest.mark.postgresql
def test_activity_with_inputs_postgresql() -> None:
    run_nested_smoke(_pg_url())
