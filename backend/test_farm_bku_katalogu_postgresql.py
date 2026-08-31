"""PostgreSQL ikizi: BKÜ kataloğu ve PHI kökeni (göç 20260901_0063).

BU İKİZ DEKORATİF DEĞİL. Katalog yolu, SQLite koşusunun YAPISAL olarak
göremeyeceği üç ayrı diyalekt farkına dokunuyor:

1. **`crop` BOŞ DİZE.** Bitkiden bağımsız katalog satırı `crop=''` ile
   yazılıyor ve çözüm bu satırı yedek olarak kullanıyor. `server_default=""`
   iki diyalektte farklı üretiliyor; boş dize bir gün NULL'a düşerse
   `UNIQUE(company_id, product_id, crop)` bütün "bütün bitkiler" satırlarını
   BİRBİRİNDEN FARKLI sayar ve göç başlığının kapattığını iddia ettiği
   belirsizlik geri gelir. İki diyalektte de ölçülmeli.

2. **`performed_at` tipi.** `test_farm_pesticide_safety_postgresql` bunu zaten
   ölçtü: SQLite `str`, PostgreSQL `datetime` döndürüyor. Katalogdan çözülen
   süre AYNI `_yerel_gun` hesabına giriyor, yani buradaki İstanbul-günü
   iddiası da üretim diyalektinde doğrulanmalı. Bekleme süresinde bir günlük
   kayma gerçek bir kalıntı riskidir.

3. **BİLEŞİK YABANCI ANAHTAR.** `(company_id, product_id) -> products` kısıtı
   SQLite'ta `PRAGMA foreign_keys`e bağlıdır ve sessizce uygulanmayabilir;
   PostgreSQL'de her zaman uygulanır. Çapraz kiracı iddiasının veritabanı
   tarafı (B firması A'nın ürününe katalog satırı AÇAMAZ) asıl burada
   ölçülüyor — SQLite tarafında onu tutan yalnız uygulama katmanındaki
   `_urun_dogrula`.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location(
    "farm_bku_katalogu", BACKEND / "tests" / "test_farm_bku_katalogu.py"
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
run_katalog_smoke = _contract.run_katalog_smoke


def _pg_url() -> str:
    url = os.getenv("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("The BKU catalogue test database URL must point to PostgreSQL")
    return url


@pytest.mark.postgresql
def test_bku_katalogu_postgresql() -> None:
    run_katalog_smoke(_pg_url())
