from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_migration_up_down_up_and_legacy_user_backfill(tmp_path: Path) -> None:
    database = tmp_path / "migration.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    scenario = r'''
from sqlalchemy import create_engine, inspect, text
from app.auth import hash_password

engine = create_engine(__import__("os").environ["DATABASE_URL"])
with engine.begin() as connection:
    connection.execute(text(
        "CREATE TABLE app_users ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, username VARCHAR(80) NOT NULL UNIQUE, "
        "display_name VARCHAR(160) NOT NULL, password_hash TEXT NOT NULL, "
        "role VARCHAR(40) NOT NULL, is_active BOOLEAN NOT NULL, "
        "created_at DATETIME NOT NULL, last_login_at DATETIME, "
        "must_change_password BOOLEAN NOT NULL DEFAULT 0)"
    ))
    connection.execute(text(
        "CREATE TABLE companies (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "name VARCHAR(200) NOT NULL, is_active BOOLEAN NOT NULL, "
        "created_at DATETIME NOT NULL)"
    ))
    connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
    connection.execute(text(
        "INSERT INTO alembic_version(version_num) VALUES ('20260729_0038')"
    ))
    connection.execute(text(
        "INSERT INTO app_users "
        "(username,display_name,password_hash,role,is_active,created_at,must_change_password) "
        "VALUES ('legacy','Legacy User',:password,'admin',1,CURRENT_TIMESTAMP,0)"
    ), {"password": hash_password("LegacyPassword!2026")})

from alembic import command
from alembic.config import Config
config = Config("alembic.ini")
command.upgrade(config, "20260729_0040")
columns = {item["name"]: item for item in inspect(engine).get_columns("app_users")}
assert columns["email"]["nullable"] is False
assert columns["email_verified"]["nullable"] is False
assert inspect(engine).has_table("email_verification_tokens")
assert inspect(engine).has_table("auth_rate_limits")
assert {"phone", "terms_accepted_at"} <= set(columns)
with engine.connect() as connection:
    legacy = connection.execute(text(
        "SELECT email,email_verified FROM app_users WHERE username='legacy'"
    )).mappings().one()
assert legacy["email"] == "legacy-user-1@legacy.invalid"
assert bool(legacy["email_verified"]) is True

command.downgrade(config, "20260729_0038")
columns = {item["name"] for item in inspect(engine).get_columns("app_users")}
assert "email" not in columns
assert "email_verified" not in columns
assert not inspect(engine).has_table("email_verification_tokens")
assert not inspect(engine).has_table("auth_rate_limits")

command.upgrade(config, "20260729_0040")
columns = {item["name"] for item in inspect(engine).get_columns("app_users")}
assert {"email", "email_verified", "phone", "terms_accepted_at"} <= columns
assert inspect(engine).has_table("email_verification_tokens")
assert inspect(engine).has_table("auth_rate_limits")
engine.dispose()
'''
    result = subprocess.run(
        [sys.executable, "-c", scenario],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
