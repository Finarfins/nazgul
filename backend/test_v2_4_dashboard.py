from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_dashboard_contract_on_clean_seeded_database(tmp_path: Path) -> None:
    database = tmp_path / "dashboard-legacy-contract.db"
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
    changed = client.post('/api/auth/change-password', json={'current_password':'admin123','new_password':'Demo12345!xy'}, headers={'X-CSRF-Token':csrf})
    assert changed.status_code == 200, changed.text
    response = client.get('/api/dashboard')
    assert response.status_code == 200, response.text
    data = response.json()
    required = (
      'today_sales','today_collections','month_sales','month_profit','stock_value','cash_bank_total',
      'customer_receivables','supplier_payables','overdue_total','recent_sales','critical_products',
      'overdue_receivables','finance_accounts','sales_trend','top_products','recent_activity'
    )
    for key in required:
        assert key in data, key
    assert len(data['sales_trend']) == 14
    assert data['customer_count'] == 25
    assert data['product_count'] == 75
    for item in data.get('recent_activity', []):
        if item.get('type') == 'sale':
            assert 'entity_id' in item and item.get('entity_type') == 'customer'
        elif item.get('type') in ('collection','payment'):
            assert 'entity_id' in item and item.get('entity_type') in ('customer','supplier')
        elif item.get('type') == 'finance':
            assert 'account_id' in item
'''
    result = subprocess.run(
        [sys.executable, "-c", smoke], cwd=BACKEND, env=env,
        text=True, capture_output=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
