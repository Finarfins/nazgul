from __future__ import annotations

import os
from pathlib import Path


def test_demo_summary_reports_seeded_counts(tmp_path: Path):
    db_path = tmp_path / "demo.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    os.environ["DEMO_MODE"] = "true"

    from fastapi.testclient import TestClient
    from app.main import app
    from seed_demo_data import build_demo

    build_demo(os.environ["DATABASE_URL"])
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        assert login.status_code == 200
        csrf = client.cookies.get("yhp_csrf_token")
        changed = client.post("/api/auth/change-password", json={"current_password": "admin123", "new_password": "DemoRot!2026x"}, headers={"X-CSRF-Token": csrf})
        assert changed.status_code == 200
        response = client.get("/api/demo/summary")
        assert response.status_code == 200
        payload = response.json()
        assert payload["enabled"] is True
        assert payload["counts"]["customers"] == 25
        assert payload["counts"]["products"] == 75
        assert payload["counts"]["sales"] == 60
