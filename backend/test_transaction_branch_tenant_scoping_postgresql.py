"""PostgreSQL ikizi: belge şubesinin kiracı kapsamı.

SQLite koşusu mantığı doğruluyor; üretim diyalektinde ayrıca kanıtlanması
gereken şu: ``branches`` ile ``orders``/``purchases`` arasındaki bağ tek kolonlu
yabancı anahtar bile DEĞİL — yani PG'de de veritabanı çapraz kiracı şube bağını
engellemiyor. SQLite'ta ``PRAGMA foreign_keys`` kapalıyken sessizce geçen bir
hata burada da sessizce geçerdi; koruma tamamen uygulama katmanında
(``_branch``) ve bu koşu onu üretim diyalektinde sabitliyor.

Ayrıca 404-vs-500 ayrımı burada anlamlı: doğrulama olmasaydı PG, FK ihlali
yerine satırı KABUL ederdi (kısıt yok), yani hata bir çökme olarak değil sessiz
bir çapraz kiracı referansı olarak kalırdı.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location(
    "transaction_branch_tenant_scoping",
    BACKEND / "tests" / "test_transaction_branch_tenant_scoping.py",
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
run_branch_scope_smoke = _contract.run_branch_scope_smoke


def _pg_url() -> str:
    url = os.getenv("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("The branch scoping test database URL must point to PostgreSQL")
    return url


@pytest.mark.postgresql
def test_document_branch_tenant_scoping_postgresql() -> None:
    run_branch_scope_smoke(_pg_url())
