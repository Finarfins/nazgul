"""PostgreSQL ikizi: kantar fişi uçlarının sözleşmesi.

--- BU İKİZİN SOMUT SEBEBİ ---------------------------------------------------

* **TÜRETİLEN NET NUMERIC ARİTMETİĞİDİR.** `brüt × oran / 100` toplamı
  Python tarafında Decimal ile yapılıyor ama girdiler VERİTABANINDAN geliyor:
  SQLite `gross_quantity`yi str/float, PostgreSQL `Decimal` verir.
  `Decimal(str(...))` sarmalamasının iki teslimde de AYNI sayıyı ürettiği
  ölçülmeli — aksi hâlde `950.0000` bir arka uçta `950.6` çıkabilir ve bu
  hata vermez, CEVAP verir.
* **KISITLARIN GERÇEKTEN KISITLAMASI.** `uq_field_harvest_tickets_paper`
  (409 yolu) ve `ck_field_harvest_tickets_gross_positive` PostgreSQL'de
  kurulur; SQLite'ta aynı kısıtlar tablo yeniden kurulurken yazılır. NULL
  `ticket_no`nun tekillikte ÇAKIŞMAMASI — "numarasız fiş iki kez girilebilir"
  bedelinin dayanağı — iki diyalektte de doğrulanmalı, çünkü bu bedel
  BİLEREK kabul edildi ve davranışının varsayılmaması gerekiyor.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location(
    "kantar_fisi_sozlesme", BACKEND / "tests" / "test_kantar_fisi_sozlesme.py"
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
run_sozlesme_smoke = _contract.run_sozlesme_smoke


def _pg_url() -> str:
    url = os.getenv("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("The weighbridge contract test database URL must point to PostgreSQL")
    return url


@pytest.mark.postgresql
def test_kantar_fisi_sozlesme_postgresql() -> None:
    run_sozlesme_smoke(_pg_url())
