from __future__ import annotations

import os

import pytest


@pytest.mark.postgresql
def test_machine_workorder_numeric_bounds_postgresql(monkeypatch: pytest.MonkeyPatch) -> None:
    """PG twin of test_v2_9_machine_workorder_numeric_bounds.

    Proves parity: the column-maximum value is accepted and stored on real
    PostgreSQL, while over-max input is rejected with the same HTTP 422 as on
    SQLite -- i.e. the schema bound stops oversized values before they can
    overflow the NUMERIC columns (which would otherwise be a PG DataError/500).
    """
    database_url = os.environ.get("NUMERIC_BOUNDS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("NUMERIC_BOUNDS_TEST_DATABASE_URL is not configured")
    monkeypatch.setenv("DATABASE_URL", database_url)

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).json()
        cid = login["companies"][0]["id"]
        headers = {"Authorization": "Bearer " + login["access_token"], "X-Company-ID": str(cid)}
        changed = client.post("/api/auth/change-password", headers=headers, json={
            "current_password": "admin123", "new_password": "NumericBoundsPg123!"
        }).json()
        headers["Authorization"] = "Bearer " + changed["access_token"]

        def machine(**over):
            body = {"brand": "John Deere", "model": "6155R"}
            body.update(over)
            return client.post("/api/machines", headers=headers, json=body)

        # NUMERIC(12,2) column maximum is accepted and persists on real PG.
        ok = machine(working_hours="9999999999.99")
        assert ok.status_code == 201, ok.text
        fetched = client.get(f"/api/machines/{ok.json()['id']}", headers=headers).json()
        assert str(fetched["working_hours"]) in ("9999999999.99", "9999999999.9900"), fetched["working_hours"]

        # Over-max is rejected identically to SQLite (no NUMERIC overflow / 500).
        assert machine(working_hours="10000000000.00").status_code == 422

        def work_order(**over):
            body = {"machine_id": 1, "technician_id": 1}
            body.update(over)
            return client.post("/api/work-orders", headers=headers, json=body)

        assert work_order(labor_rate="100000001").status_code == 422
        assert work_order(actual_hours="1000001").status_code == 422
        assert work_order(estimated_hours="1000001").status_code == 422
