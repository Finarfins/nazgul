from __future__ import annotations

import os

import pytest


def _acilisa_cek() -> None:
    """Admin şifresini AÇILIŞ DURUMUNA (`admin123` + `must_change_password`) yaz.

    D2/1B-A ikizlerinden DEVRALINDI. CI dosya başına `reset_schema` çalıştırdığı
    için (`ci.yml:609`) CI ortamında DB durumu paylaşılmaz; bu dikiş yerel/pglens
    paylaşılan veritabanı koşularını korur ve gelecekte reset_schema adımının
    kaldırılmasına karşı savunma sağlar. Tek yönlü bir çare (yalnız teardown)
    dosyayı iyi bir komşu yapar ama KENDİSİNİ korumaz, çünkü şifreyi bozan
    ÖNCEKİ dosya olabilir. Bu yüzden İKİ UÇTAN çağrılır.
    """
    try:
        from tests.pg_ikiz_yardimci import acilisa_cek
        acilisa_cek()
    except ImportError:
        from sqlalchemy import text as _text
        from app.auth import hash_password
        from app.db import SessionLocal, engine as _eng

        if _eng.dialect.name != "postgresql":
            return
        with SessionLocal() as db:
            if db.execute(_text("SELECT to_regclass('public.app_users')")).scalar() is None:
                return
            db.execute(
                _text(
                    "UPDATE app_users SET password_hash=:h, "
                    "must_change_password=true WHERE username='admin'"
                ),
                {"h": hash_password("admin123")},
            )
            db.commit()


@pytest.fixture(autouse=True)
def _acilis_sifresi():
    _acilisa_cek()
    try:
        yield
    finally:
        _acilisa_cek()


def _get_database_url() -> str:
    database_url = os.environ.get("NUMERIC_BOUNDS_TEST_DATABASE_URL")
    if not database_url and os.environ.get("REQUIRE_PG") == "1":
        database_url = os.environ.get("APP_TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.skip("NUMERIC_BOUNDS_TEST_DATABASE_URL is not configured")
    return database_url


def _login_client(client, new_pw: str = "NumericBoundsPg123!") -> dict[str, str]:
    login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).json()
    cid = login["companies"][0]["id"]
    headers = {"Authorization": "Bearer " + login["access_token"], "X-Company-ID": str(cid)}
    changed = client.post("/api/auth/change-password", headers=headers, json={
        "current_password": "admin123", "new_password": new_pw
    }).json()
    headers["Authorization"] = "Bearer " + changed["access_token"]
    return headers


@pytest.mark.postgresql
def test_machine_workorder_numeric_bounds_postgresql(monkeypatch: pytest.MonkeyPatch) -> None:
    """PG twin of test_v2_9_machine_workorder_numeric_bounds.

    Proves parity: the column-maximum value is accepted and stored on real
    PostgreSQL, while over-max input is rejected with the same HTTP 422 as on
    SQLite -- i.e. the schema bound stops oversized values before they can
    overflow the NUMERIC columns (which would otherwise be a PG DataError/500).
    """
    database_url = _get_database_url()
    monkeypatch.setenv("DATABASE_URL", database_url)

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        headers = _login_client(client)

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


@pytest.mark.postgresql
def test_machine_string_identifier_bounds_postgresql(monkeypatch: pytest.MonkeyPatch) -> None:
    """PG twin of SEC-11 machine identifier string bounds.

    Proves parity: each of the 6 bounded identifier fields accepts up to column
    length N on PostgreSQL (persisting exactly N chars), while N+1 chars is
    rejected at the schema boundary with HTTP 422 (loc ['body', <field>])
    before overflowing VARCHAR(N) on PostgreSQL (which would otherwise be
    StringDataRightTruncation / DataError / 500).
    Also pins the PUT update path (serial_number 121 -> 422).
    """
    database_url = _get_database_url()
    monkeypatch.setenv("DATABASE_URL", database_url)

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text

    from app.main import app

    bounded_fields = (
        ("serial_number", 120),
        ("chassis_number", 120),
        ("registration_number", 120),
        ("engine_number", 120),
        ("manufacturer", 160),
        ("variant", 160),
    )

    import uuid

    pg_engine = create_engine(database_url)
    created_machines: dict[str, int] = {}
    try:
        with TestClient(app) as client:
            headers = _login_client(client)

            for idx, (field, n) in enumerate(bounded_fields):
                prefix = f"t{idx}{uuid.uuid4().hex[:6]}"

                # 1) N+1 chars -> 422 with loc ['body', <field>]
                bad_val = prefix + "X" * (n + 1 - len(prefix))
                assert len(bad_val) == n + 1
                bad_body = {
                    "brand": "John Deere",
                    "model": f"6155R-{idx}",
                    field: bad_val,
                }
                bad_res = client.post("/api/machines", headers=headers, json=bad_body)
                assert bad_res.status_code == 422, (
                    f"{field} N+1: expected 422, got {bad_res.status_code}: {bad_res.text}"
                )
                bad_errors = bad_res.json().get("detail", [])
                assert any(list(err.get("loc", [])) == ["body", field] for err in bad_errors), (
                    f"{field} N+1: loc ['body', '{field}'] not found in {bad_errors}"
                )

                # 2) N chars -> 201 and SELECT length(<col>) on PG == N
                char = chr(ord("a") + idx)
                good_val = prefix + char * (n - len(prefix))
                assert len(good_val) == n
                good_body = {
                    "brand": "John Deere",
                    "model": f"6155R-{idx}",
                    field: good_val,
                }
                ok_res = client.post("/api/machines", headers=headers, json=good_body)
                assert ok_res.status_code == 201, (
                    f"{field} N: expected 201, got {ok_res.status_code}: {ok_res.text}"
                )
                mid = ok_res.json()["id"]
                created_machines[field] = mid

                with pg_engine.connect() as conn:
                    stored_len = conn.execute(
                        text(f"SELECT length({field}) FROM machines WHERE id = :mid"),
                        {"mid": mid},
                    ).scalar()
                    assert stored_len == n, f"{field} on PG: expected length {n}, got {stored_len}"

            # 3) One PUT case: serial 121 -> 422 with loc ['body', 'serial_number']
            serial_mid = created_machines["serial_number"]
            put_bad_val = f"u{uuid.uuid4().hex[:6]}" + "Z" * (121 - 7)
            assert len(put_bad_val) == 121
            put_bad_body = {
                "brand": "John Deere",
                "model": "6155R-PUT",
                "serial_number": put_bad_val,
            }
            put_res = client.put(f"/api/machines/{serial_mid}", headers=headers, json=put_bad_body)
            assert put_res.status_code == 422, (
                f"PUT serial 121: expected 422, got {put_res.status_code}: {put_res.text}"
            )
            put_errors = put_res.json().get("detail", [])
            assert any(list(err.get("loc", [])) == ["body", "serial_number"] for err in put_errors), (
                f"PUT serial 121: loc ['body', 'serial_number'] not found in {put_errors}"
            )

            # Confirm original value was preserved on PG
            with pg_engine.connect() as conn:
                current_len = conn.execute(
                    text("SELECT length(serial_number) FROM machines WHERE id = :mid"),
                    {"mid": serial_mid},
                ).scalar()
                assert current_len == 120, f"serial_number on PG expected 120, got {current_len}"
    finally:
        if created_machines:
            try:
                with pg_engine.begin() as conn:
                    mids_csv = ",".join(str(m) for m in created_machines.values())
                    conn.execute(text(f"DELETE FROM machines WHERE id IN ({mids_csv})"))
            except Exception:
                pass
        pg_engine.dispose()

