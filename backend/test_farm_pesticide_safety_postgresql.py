"""PostgreSQL ikizi: Tarla Yönetimi V1 — FAZ 5 ilaç güvenlik aralıkları.

BU İKİZİN GEREKÇESİ BU OTURUMDA ÖĞRENİLDİ. Faz 4'te tekrar koruması SQLite'ta
yeşil, PostgreSQL'de tamamen bozuktu; hata yalnız PG testiyle görüldü.

Buradaki risk aynı sınıftan ve ÖLÇÜLDÜ:

    SQLite     performed_at -> str      '2026-03-20 08:00:00+00:00'
    PostgreSQL performed_at -> datetime  datetime(2026, 6, 1, 9, 0, tzinfo=...)

``_yerel_gun`` ikisini de kabul ediyor. Bu ikizin dekoratif OLMADIĞI şöyle
doğrulandı: fonksiyondaki ``isinstance(deger, str)`` dalı kaldırılıp yalnız
SQLite'ın tipi varsayıldığında **SQLite testi geçti, PostgreSQL testi
``TypeError: fromisoformat: argument must be str`` ile düştü.**

Yani bu dosya, SQLite koşusunun yapısal olarak göremeyeceği bir kırılmayı
yakalıyor. Bekleme süresinde bir günlük kayma gerçek bir kalıntı riskidir;
hesabın üretim diyalektinde doğrulanması şart.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location(
    "farm_pesticide_safety", BACKEND / "tests" / "test_farm_pesticide_safety.py"
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
run_safety_smoke = _contract.run_safety_smoke


def _pg_url() -> str:
    url = os.getenv("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("The farm safety test database URL must point to PostgreSQL")
    return url


@pytest.mark.postgresql
def test_pesticide_safety_postgresql() -> None:
    run_safety_smoke(_pg_url())
