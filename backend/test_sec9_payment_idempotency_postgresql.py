"""PostgreSQL ikizi: SEC-9 kullanıcı kapsamı ve göç 0084'ün GERÇEK PG 16 turu.

SQLite ikizi (`test_sec9_payment_idempotency_user.py`) ile AYNI koşucuları
çağırır — iddia gövdesi TEK yerde durur, iki lehçe için ayrışamaz.

PG'de AYRICA ölçülen, SQLite'ın ölçEMEDİĞİ iki şey var:

1. `ALTER COLUMN ... SET NOT NULL` ve `DROP CONSTRAINT`/`ADD CONSTRAINT`
   YERİNDE yürüyor; SQLite yolu ise tabloyu YENİDEN KURUYOR. İki yol AYNI
   şemaya varmak zorunda ve bunu yalnız GERÇEK PG gösterebilir.
2. `NULL = NULL` yanlış olduğu için, `user_id` NULLABLE bırakılsaydı PG'de
   tekillik eski satırlar arasında ÇALIŞMAZDI. Sütunun NOT NULL'lığı bu
   yüzden PG'de ölçülmesi gereken bir sözleşmedir.

Şema sıfırlaması `test_payment_allocation_api_postgresql.py`nin kalıbıdır.
Göç turu `0083`e iner ve `0084`e geri çıkar, yani dosya şemayı BAŞ SÜRÜMDE
bırakır; sıfırlama koşuların ARASINDA yapılır ki göç turu, motor turunun
bıraktığı satırları görmesin ve komşu dosyalara satır devretmesin.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine

from test_sec9_payment_idempotency_user import run_sec9_goc_turu, run_sec9_smoke


def _pg_url() -> str:
    database_url = os.getenv("APP_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("APP_TEST_DATABASE_URL must point to PostgreSQL")
    return database_url


def _semayi_sifirla(database_url: str) -> None:
    motor = create_engine(database_url, isolation_level="AUTOCOMMIT")
    try:
        with motor.connect() as baglanti:
            baglanti.exec_driver_sql("DROP SCHEMA IF EXISTS public CASCADE")
            baglanti.exec_driver_sql("CREATE SCHEMA public")
    finally:
        motor.dispose()


@pytest.mark.postgresql
def test_sec9_payment_idempotency_user_postgresql() -> None:
    database_url = _pg_url()
    _semayi_sifirla(database_url)
    run_sec9_smoke(database_url)


@pytest.mark.postgresql
def test_sec9_goc_up_down_up_postgresql() -> None:
    database_url = _pg_url()
    _semayi_sifirla(database_url)
    run_sec9_goc_turu(database_url)
    # Defter satirlari bu dosyanindir; komsu dosyalari kirletmemek icin
    # temizlenir. `run_sec9_goc_turu` en son 0084'e cikar, yani sema BASTADIR.
    _semayi_sifirla(database_url)
