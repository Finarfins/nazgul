"""PostgreSQL ikizi: kantar fişi yazımı DEFTERİ DEĞİŞTİRMİYOR.

--- BU İKİZİN SOMUT SEBEBİ ---------------------------------------------------

SQLite yeşili burada TEK BAŞINA kanıt değil, çünkü ölçülen şeyin tamamı
diyalekte duyarlı iki mekanizmadan geçiyor:

* **NUMERIC teslimi.** Defter satırının `quantity`si SQLite'ta str/float,
  PostgreSQL'de `Decimal` olarak geri gelir. "Fişli ve fişsiz satır ALAN ALAN
  AYNI" karşılaştırması bu yüzden temsile değil DEĞERE bakmak zorunda ve
  bunun gerçekten öyle olduğu, iki temsilin de görüldüğü yerde ölçülmeli.
* **`INSERT ... RETURNING` ve tüketicinin rowcount kararı.** `field_stok_tuketici`
  başlığında ölçüldü: `RETURNING` sqlite3'te 0, psycopg'de 1 rowcount
  döndürüyor. Tüketici bu koşumda GERÇEKTEN hareket yazıyor; yazmadığı bir
  arka uçta "hareket değişmedi" iddiası vakumda geçerdi.

Ayrıca fiş tablolarının BİLEŞİK yabancı anahtarları PostgreSQL'de yerinde
kurulur, SQLite'ta tablo yeniden kurularak — göç yordamının iki dalı da
koşturulmuş olur.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location(
    "kantar_fisi_defter", BACKEND / "tests" / "test_kantar_fisi_defter.py"
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
run_defter_smoke = _contract.run_defter_smoke


def _pg_url() -> str:
    url = os.getenv("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("The weighbridge ledger test database URL must point to PostgreSQL")
    return url


@pytest.mark.postgresql
def test_kantar_fisi_defter_postgresql() -> None:
    run_defter_smoke(_pg_url())
