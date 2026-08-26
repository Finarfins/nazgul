from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_dashboard_uses_seeded_data_and_preserves_contract(tmp_path: Path) -> None:
    database = tmp_path / "dashboard-aggregation.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["DEMO_MODE"] = "true"
    smoke = r'''
from fastapi.testclient import TestClient
from app.main import app
from seed_demo_data import build_demo
import os

build_demo(os.environ['DATABASE_URL'])
with TestClient(app) as client:
    login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
    assert login.status_code == 200, login.text
    csrf = client.cookies.get('yhp_csrf_token')
    changed = client.post('/api/auth/change-password', json={'current_password':'admin123','new_password':'DemoRot!2026x'}, headers={'X-CSRF-Token':csrf})
    assert changed.status_code == 200, changed.text
    response = client.get('/api/dashboard')
    assert response.status_code == 200, response.text
    data = response.json()
    required = {
      'today_sales','today_collections','month_sales','month_profit','stock_value','cash_bank_total',
      'customer_receivables','supplier_payables','overdue_total','recent_sales','critical_products',
      'overdue_receivables','finance_accounts','sales_trend','top_products','recent_activity',
      'customer_count','product_count','critical_stock_count','overdue_count'
    }
    assert required.issubset(data), required - set(data)
    assert data['customer_count'] == 25, data['customer_count']
    assert data['product_count'] == 75, data['product_count']
    assert len(data['sales_trend']) == 14
    assert len(data['recent_sales']) <= 8
    assert len(data['critical_products']) <= 8
    assert len(data['finance_accounts']) <= 8
    assert len(data['top_products']) <= 5
    assert len(data['recent_activity']) <= 10
    assert data['month_sales'] >= 0
    assert data['stock_value'] >= 0
'''
    result = subprocess.run(
        [sys.executable, "-c", smoke],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
