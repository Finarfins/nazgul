"""PostgreSQL ikizi: Uygulama Kayıt Çizelgesi.

Bu ikizin somut sebebi var — çizelge SQLite'ta yeşil olup PostgreSQL'de
farklı davranabilecek üç şeye dokunuyor:

* **Genişleyen bind (`bindparam(expanding=True)`).** Dört sorgunun üçü
  `IN :ids` kullanıyor ve iki sürücü bunun için farklı SQL üretiyor. Boş
  listede sözdizimi hatası vermemesi ölçülmeli; çapraz kiracı senaryosu tam
  olarak boş listeye düşüyor (başka firmanın parsel kimliği hiçbir sezona
  çözülmüyor), yani bu ikiz o yolu GERÇEK sürücüde koşturuyor.
* **NUMERIC ölçeği.** PG `NUMERIC(18,4)`ü `Decimal('25.0000')`, SQLite `25`
  olarak verir. Çizelge değerleri metne çeviriyor; iddialar bu yüzden ölçek
  pinlemiyor, Decimal karşılaştırıyor — ama metne çevirmenin PG tarafında da
  çalıştığı yalnız burada ölçülüyor.
* **Zaman dilimi.** `performed_at` `TIMESTAMPTZ`; PG onu tz-aware döndürür,
  SQLite naive verir. `_yerel_gun` iki durumu da İstanbul gününe çevirmek
  zorunda ve gece yarısı sınırındaki kayıt (UTC 2026-06-01T22:30 ->
  02.06.2026) iddiası bu ikizde asıl yükü taşıyor: naive/aware ayrımı yanlış
  ele alınsaydı burada BİR GÜN kayardı.

ÇAPRAZ KİRACI da bu ikizde GERÇEK PostgreSQL üzerinde ölçülüyor: bileşik
yabancı anahtarlar (`fk_crop_seasons_parcel_same_company` ve akrabaları)
SQLite'ta `batch_alter_table` ile kurulurken PG'de doğrudan kurulur, yani
kısıtın kendisi iki motorda AYNI yoldan gelmiyor.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location(
    "uretici_kayit_defteri_contract",
    BACKEND / "tests" / "test_uretici_kayit_defteri.py",
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
run_logbook_smoke = _contract.run_logbook_smoke


def _pg_url() -> str:
    url = os.getenv("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("The producer logbook test database URL must point to PostgreSQL")
    return url


@pytest.mark.postgresql
def test_producer_logbook_postgresql() -> None:
    run_logbook_smoke(_pg_url())
