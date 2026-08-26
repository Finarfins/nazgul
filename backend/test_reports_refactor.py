from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.routers.reports import _date_conditions, _normalized_date_sql


BACKEND = Path(__file__).resolve().parent


def test_date_conditions_include_only_requested_bounds() -> None:
    where_clause, params = _date_conditions(
        "order_date",
        company_id_value=7,
        date_from="2026-01-01",
        date_to=None,
    )

    normalized = _normalized_date_sql("order_date", "sqlite")
    assert where_clause == f" WHERE company_id=:cid AND {normalized}>=:df"
    assert params == {"cid": 7, "df": "2026-01-01"}


def test_report_summary_preserves_response_contract(tmp_path: Path) -> None:
    database = tmp_path / "reports-refactor.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    smoke = r'''
from fastapi.testclient import TestClient

from app.main import app

with TestClient(app) as client:
    initial_password = 'admin' + '123'
    rotated_password = 'ReportsRot!' + '2026x'
    login = client.post('/api/auth/login', json={'username':'admin','password':initial_password})
    assert login.status_code == 200, login.text
    csrf = client.cookies.get('yhp_csrf_token')
    changed = client.post(
        '/api/auth/change-password',
        json={'current_password':initial_password,'new_password':rotated_password},
        headers={'X-CSRF-Token':csrf},
    )
    assert changed.status_code == 200, changed.text
    response = client.get(
        '/api/reports/summary',
        params={'date_from':'2026-01-01','date_to':'2026-12-31'},
    )
    assert response.status_code == 200, response.text
    assert set(response.json()) == {
        'sales_total', 'sales_count', 'purchases_total', 'purchases_count',
        'collections', 'payments', 'gross_difference', 'top_customers',
        'top_products', 'monthly_sales',
    }
'''
    result = subprocess.run(
        [sys.executable, "-c", smoke],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
