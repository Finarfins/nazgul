"""PostgreSQL ikizi: sipariş/irsaliye satırının kiracı bağı.

İkizin sebebi ölçülmüş farklarla:

1. **Yabancı anahtar zorlaması diyalekte bağlı.** SQLite ``PRAGMA
   foreign_keys=ON`` olmadan hiç zorlamaz ve pragma BAĞLANTI başınadır;
   PostgreSQL her zaman zorlar. Üretim PostgreSQL olduğu için asıl cevap
   burada.
2. **``batch_alter_table`` yalnız SQLite'ta tabloyu YENİDEN KURAR.**
   PostgreSQL ``ALTER TABLE ... ADD CONSTRAINT``a düşer. İki diyalekt
   migration'ı FARKLI yollarla uyguluyor; ikisinin de aynı son şemaya
   vardığı ayrıca kanıtlanmalı.
3. **UNIQUE kısıtının arkasındaki indeks yalnız PostgreSQL'de görünür.**
   Yapısal iz bu farkı ölçtü; beyan diyalekte bağlı yazıldı.
4. **Kimlik üretimi yalnız PostgreSQL'de YAPISAL olarak görünür**
   (SQLite inspect() autoincrement=None döndürüyor). Dizi bağının yeniden
   kurulumdan sağ çıktığı yapısal olarak ancak burada ölçülebilir.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location(
    "wf_line_contract", BACKEND / "tests" / "test_workflow_line_item_tenancy.py"
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
run_workflow_line_item_smoke = _contract.run_workflow_line_item_smoke
run_workflow_line_item_migration = _contract.run_workflow_line_item_migration


def _pg_url() -> str:
    url = os.getenv("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("The workflow line item test database URL must point to PostgreSQL")
    return url


@pytest.mark.postgresql
def test_workflow_line_item_tenancy_postgresql() -> None:
    run_workflow_line_item_smoke(_pg_url())


@pytest.mark.postgresql
def test_workflow_line_item_migration_postgresql() -> None:
    run_workflow_line_item_migration(_pg_url())
