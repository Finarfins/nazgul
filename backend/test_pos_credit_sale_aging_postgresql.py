"""Aynı kapının GERÇEK PostgreSQL karşılığı (bkz. eşi olan SQLite dosyası).

Üretim PostgreSQL'dir. Vade türetme tarih aritmetiği yapıyor ve yaşlandırma
süzgeci ham SQL; ikisi de lehçeye göre ayrışabilecek sınıftır, bu yüzden kapı
iki motorda da koşar.
"""
from __future__ import annotations

import os

import pytest

from test_pos_credit_sale_aging import _gate_lines, run_pos_credit_aging_gate


@pytest.mark.postgresql
def test_pos_credit_sale_reaches_the_aging_report_on_postgresql() -> None:
    database_url = os.getenv("POS_CREDIT_AGING_TEST_DATABASE_URL") or os.getenv(
        "APP_TEST_DATABASE_URL"
    )
    if not database_url:
        pytest.skip(
            "POS_CREDIT_AGING_TEST_DATABASE_URL or APP_TEST_DATABASE_URL is required"
        )
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("this gate must run against PostgreSQL")
    lines = _gate_lines(run_pos_credit_aging_gate(database_url))
    for line in lines:
        print(line)
    assert "GATE_OK all writers, both directions" in lines, lines
