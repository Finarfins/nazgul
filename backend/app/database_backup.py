from __future__ import annotations

import hashlib
import hmac
import errno
import json
import os
import shutil
import sqlite3
import subprocess
import time
import re
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable
from urllib.parse import unquote

from sqlalchemy.engine import URL, make_url
from sqlalchemy import create_engine, inspect, text
from alembic.config import Config
from alembic.script import ScriptDirectory
from .backup_errors import BackupError


@dataclass(frozen=True)
class BackupArtifact:
    backup_path: Path
    manifest_path: Path
    sha256: str
    size_bytes: int


BACKUP_NAME_RE = re.compile(
    r"^backup-(?P<stamp>\d{8}-\d{6})-(?P<revision>[A-Za-z0-9_]+)(?:-\d+)?\.dump$"
)
REQUIRED_TABLES = (
    "alembic_version",
    "companies",
    "app_users",
    "products",
    "orders",
    "activity_logs",
    "platform_maintenance",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_path(database_url: str) -> Path:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        raise BackupError("Bu işlem yalnızca SQLite veritabanı için kullanılabilir")
    database = url.database
    if not database or database == ":memory:":
        raise BackupError("Bellek içi SQLite veritabanı yedeklenemez")
    return Path(unquote(database)).expanduser().resolve()


def _integrity_check(path: Path) -> None:
    if not path.is_file():
        raise BackupError(f"Veritabanı dosyası bulunamadı: {path}")
    try:
        with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as connection:
            result = connection.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.DatabaseError as exc:
        raise BackupError(f"SQLite bütünlük kontrolü çalıştırılamadı: {exc}") from exc
    if not result or str(result[0]).lower() != "ok":
        raise BackupError(f"SQLite bütünlük kontrolü başarısız: {result!r}")


def _replace_with_retry(source: Path, destination: Path) -> None:
    """Atomically replace a file, tolerating short-lived Windows file locks.

    SQLite, antivirus software and synced folders can briefly retain a handle after
    the database connection closes. Retrying only PermissionError preserves the
    atomic replace contract while allowing that handle to be released.
    """

    delays = (0.02, 0.05, 0.1, 0.2, 0.4)
    for attempt, delay in enumerate(delays, start=1):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == len(delays):
                raise
            time.sleep(delay)


def create_sqlite_backup(database_url: str, destination_dir: Path) -> BackupArtifact:
    source = _sqlite_path(database_url)
    if not source.is_file():
        raise BackupError(f"Kaynak veritabanı bulunamadı: {source}")
    destination_dir = destination_dir.expanduser().resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final_path = destination_dir / f"yerel_hesap_{timestamp}.sqlite3"
    counter = 1
    while final_path.exists():
        final_path = destination_dir / f"yerel_hesap_{timestamp}_{counter}.sqlite3"
        counter += 1

    temporary = final_path.with_suffix(final_path.suffix + ".tmp")
    try:
        with closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)) as source_db:
            with closing(sqlite3.connect(temporary)) as destination_db:
                source_db.backup(destination_db)
        _integrity_check(temporary)
        _replace_with_retry(temporary, final_path)
    finally:
        temporary.unlink(missing_ok=True)

    checksum = sha256_file(final_path)
    manifest = {
        "format_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database_type": "sqlite",
        "source_name": source.name,
        "backup_file": final_path.name,
        "size_bytes": final_path.stat().st_size,
        "sha256": checksum,
    }
    manifest_path = final_path.with_suffix(final_path.suffix + ".json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return BackupArtifact(final_path, manifest_path, checksum, final_path.stat().st_size)


def verify_sqlite_backup(backup_path: Path, expected_sha256: str | None = None) -> BackupArtifact:
    backup_path = backup_path.expanduser().resolve()
    _integrity_check(backup_path)
    actual = sha256_file(backup_path)
    if expected_sha256 and actual.lower() != expected_sha256.lower():
        raise BackupError("Yedek checksum doğrulaması başarısız")
    manifest_path = backup_path.with_suffix(backup_path.suffix + ".json")
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_checksum = manifest.get("sha256")
        if manifest_checksum and actual.lower() != str(manifest_checksum).lower():
            raise BackupError("Yedek manifest checksum doğrulaması başarısız")
    return BackupArtifact(backup_path, manifest_path, actual, backup_path.stat().st_size)


def restore_sqlite_backup(
    backup_path: Path,
    target_path: Path,
    *,
    expected_sha256: str | None = None,
    overwrite: bool = False,
) -> Path | None:
    artifact = verify_sqlite_backup(backup_path, expected_sha256)
    target_path = target_path.expanduser().resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists() and not overwrite:
        raise BackupError("Hedef veritabanı mevcut; --overwrite olmadan üzerine yazılmaz")

    safety_backup: Path | None = None
    if target_path.exists():
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safety_backup = target_path.with_name(f"{target_path.name}.pre_restore_{timestamp}.bak")
        counter = 1
        while safety_backup.exists():
            safety_backup = target_path.with_name(
                f"{target_path.name}.pre_restore_{timestamp}_{counter}.bak"
            )
            counter += 1
        shutil.copy2(target_path, safety_backup)

    temporary = target_path.with_suffix(target_path.suffix + ".restore.tmp")
    try:
        shutil.copy2(artifact.backup_path, temporary)
        _integrity_check(temporary)
        _replace_with_retry(temporary, target_path)
    finally:
        temporary.unlink(missing_ok=True)
    return safety_backup


def _postgres_env(url: URL) -> dict[str, str]:
    env = os.environ.copy()
    if url.password:
        env["PGPASSWORD"] = url.password
    return env


def create_postgresql_backup(database_url: str, output_path: Path) -> Path:
    """Create a custom-format PostgreSQL backup using the official pg_dump tool."""
    url = make_url(database_url)
    if url.get_backend_name() not in {"postgresql", "postgres"}:
        raise BackupError("PostgreSQL yedeği için PostgreSQL DATABASE_URL gerekir")
    executable = shutil.which("pg_dump")
    if not executable:
        raise BackupError("pg_dump bulunamadı; PostgreSQL istemci araçlarını kurun")
    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        executable,
        "--format=custom",
        "--no-owner",
        "--no-acl",
        "--file",
        str(output_path),
        "--host",
        url.host or "localhost",
        "--port",
        str(url.port or 5432),
        "--username",
        url.username or "postgres",
        url.database or "postgres",
    ]
    result = subprocess.run(command, env=_postgres_env(url), capture_output=True, text=True)
    if result.returncode != 0:
        output_path.unlink(missing_ok=True)
        raise BackupError(f"pg_dump başarısız: {result.stderr.strip()}")
    return output_path


def current_schema_revision(database_url: str) -> str:
    target = create_engine(database_url)
    try:
        with target.connect() as connection:
            if not inspect(connection).has_table("alembic_version"):
                raise BackupError("Veritabanında alembic_version tablosu yok")
            revisions = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    finally:
        target.dispose()
    if len(revisions) != 1:
        raise BackupError(f"Tek Alembic revision bekleniyordu, bulunan: {revisions!r}")
    return str(revisions[0])


def application_head() -> str:
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise BackupError(f"Uygulama migration grafiğinde çift head var: {heads!r}")
    return heads[0]


def backup_directory(data_dir: str | Path) -> Path:
    path = Path(data_dir).expanduser().resolve() / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ensure_capacity(directory: Path, required_bytes: int) -> None:
    free = shutil.disk_usage(directory).free
    if free < required_bytes:
        raise BackupError(
            f"Yedek için disk alanı yetersiz: gerekli={required_bytes}, boş={free}"
        )


def _estimated_backup_bytes(database_url: str, directory: Path) -> int:
    existing = sorted(
        directory.glob("backup-*.dump"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    if existing:
        baseline = existing[0].stat().st_size
    elif make_url(database_url).get_backend_name() == "sqlite":
        baseline = _sqlite_path(database_url).stat().st_size
    else:
        baseline = 1
    return max(int(baseline * 1.5), 1)


def _next_backup_path(directory: Path, revision: str, now: datetime | None = None) -> Path:
    moment = now or datetime.now(timezone.utc)
    stem = f"backup-{moment.strftime('%Y%m%d-%H%M%S')}-{revision}"
    path = directory / f"{stem}.dump"
    counter = 1
    while path.exists():
        path = directory / f"{stem}-{counter}.dump"
        counter += 1
    return path


def _write_checksum(path: Path) -> str:
    checksum = sha256_file(path)
    try:
        path.with_suffix(path.suffix + ".sha256").write_text(
            f"{checksum}  {path.name}\n", encoding="ascii"
        )
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            raise BackupError("Yedek yazılırken disk alanı tükendi") from exc
        raise
    return checksum


def manifest_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".manifest.json")


def _database_inventory(database_url: str) -> tuple[list[str], dict[str, int]]:
    target = create_engine(database_url)
    try:
        with target.connect() as connection:
            inspector = inspect(connection)
            present = set(inspector.get_table_names())
            missing = [name for name in REQUIRED_TABLES if name not in present]
            if missing:
                raise BackupError(f"Zorunlu tablolar eksik: {', '.join(missing)}")
            counts = {
                name: int(
                    connection.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar_one()
                )
                for name in REQUIRED_TABLES
            }
            return list(REQUIRED_TABLES), counts
    finally:
        target.dispose()


def _write_manifest(path: Path, database_url: str, revision: str, checksum: str) -> dict:
    tables, counts = _database_inventory(database_url)
    payload = {
        "format_version": 1,
        "backup_file": path.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "alembic_revision": revision,
        "required_tables": tables,
        "table_counts": counts,
        "sha256": checksum,
    }
    try:
        manifest_path(path).write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            raise BackupError("Yedek yazılırken disk alanı tükendi") from exc
        raise
    return payload


def read_manifest(path: Path) -> dict:
    target = manifest_path(path)
    if not target.is_file():
        raise BackupError("Yedek manifest dosyası bulunamadı")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError("Yedek manifest dosyası okunamadı") from exc
    if payload.get("format_version") != 1 or payload.get("backup_file") != path.name:
        raise BackupError("Yedek manifest formatı uyumsuz")
    if not isinstance(payload.get("alembic_revision"), str):
        raise BackupError("Yedek manifest Alembic revision alanı eksik")
    if not isinstance(payload.get("sha256"), str):
        raise BackupError("Yedek manifest SHA-256 alanı eksik")
    required = set(payload.get("required_tables") or [])
    if required != set(REQUIRED_TABLES):
        raise BackupError("Yedek manifest zorunlu tablo listesi uyumsuz")
    if set((payload.get("table_counts") or {}).keys()) != set(REQUIRED_TABLES):
        raise BackupError("Yedek manifest tablo sayımları eksik")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in payload["table_counts"].values()
    ):
        raise BackupError("Yedek manifest tablo sayımları geçersiz")
    return payload


def read_checksum(path: Path) -> str:
    sidecar = path.with_suffix(path.suffix + ".sha256")
    if not sidecar.is_file():
        raise BackupError("SHA-256 yan dosyası bulunamadı")
    expected = sidecar.read_text(encoding="ascii").split()[0].lower()
    actual = sha256_file(path).lower()
    if not hmac.compare_digest(expected, actual):
        raise BackupError("Yedek SHA-256 doğrulaması başarısız")
    return actual


def create_platform_backup(
    database_url: str,
    data_dir: str | Path,
    *,
    retention_count: int = 14,
) -> dict[str, object]:
    revision = current_schema_revision(database_url)
    directory = backup_directory(data_dir)
    url = make_url(database_url)
    _ensure_capacity(directory, _estimated_backup_bytes(database_url, directory))
    output = _next_backup_path(directory, revision)
    try:
        if url.get_backend_name() == "sqlite":
            temporary = output.with_suffix(".tmp")
            with closing(sqlite3.connect(f"file:{_sqlite_path(database_url).as_posix()}?mode=ro", uri=True)) as source_db:
                with closing(sqlite3.connect(temporary)) as destination_db:
                    source_db.backup(destination_db)
            _integrity_check(temporary)
            _replace_with_retry(temporary, output)
            temporary.unlink(missing_ok=True)
        else:
            create_postgresql_backup(database_url, output)
    except OSError as exc:
        output.unlink(missing_ok=True)
        if exc.errno == errno.ENOSPC:
            raise BackupError("Yedek yazılırken disk alanı tükendi") from exc
        raise
    checksum = _write_checksum(output)
    _write_manifest(output, database_url, revision, checksum)
    enforce_retention(directory, retention_count)
    return backup_info(output, revision=revision, checksum=checksum)


def enforce_retention(directory: Path, retention_count: int) -> None:
    files = sorted(directory.glob("backup-*.dump"), key=lambda item: item.stat().st_mtime, reverse=True)
    for old in files[retention_count:]:
        old.unlink(missing_ok=True)
        old.with_suffix(old.suffix + ".sha256").unlink(missing_ok=True)
        manifest_path(old).unlink(missing_ok=True)


def _postgres_revision(database_url: str, path: Path) -> str:
    executable = shutil.which("pg_restore")
    if not executable:
        raise BackupError("pg_restore bulunamadı; PostgreSQL istemci araçlarını kurun")
    result = subprocess.run(
        [executable, "--data-only", "--table=alembic_version", "--file=-", str(path)],
        env=_postgres_env(make_url(database_url)), capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise BackupError(f"Yedek revision okunamadı: {result.stderr.strip()}")
    match = re.search(
        r"COPY\s+(?:\w+\.)?alembic_version\b[^\n]*FROM stdin;\s*\n"
        r"([A-Za-z0-9_]+)\s*\n\\\.",
        result.stdout,
        re.IGNORECASE,
    )
    if not match:
        raise BackupError("Yedek içinden Alembic revision okunamadı")
    return match.group(1)


def backup_revision(database_url: str, path: Path) -> str:
    if make_url(database_url).get_backend_name() == "sqlite":
        with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as connection:
            row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        if not row:
            raise BackupError("Yedek içinden Alembic revision okunamadı")
        return str(row[0])
    return _postgres_revision(database_url, path)


def verify_platform_backup(
    database_url: str, path: Path, *, validate_manifest: bool = True
) -> dict[str, object]:
    checksum = read_checksum(path)
    if make_url(database_url).get_backend_name() == "sqlite":
        _integrity_check(path)
    revision = backup_revision(database_url, path)
    if validate_manifest:
        manifest = read_manifest(path)
        if not hmac.compare_digest(str(manifest.get("sha256", "")).lower(), checksum):
            raise BackupError("Manifest SHA-256 değeri yedekle eşleşmiyor")
        if manifest.get("alembic_revision") != revision:
            raise BackupError("Manifest Alembic revision değeri yedekle eşleşmiyor")
    current = current_schema_revision(database_url)
    if revision != current or revision != application_head():
        raise BackupError(
            f"Yedek şeması uyumsuz: yedek={revision}, mevcut={current}, uygulama={application_head()}"
        )
    return backup_info(path, revision=revision, checksum=checksum)


def backup_info(path: Path, *, revision: str | None = None, checksum: str | None = None) -> dict[str, object]:
    match = BACKUP_NAME_RE.match(path.name)
    parsed_revision = revision or (match.group("revision") if match else "")
    return {
        "name": path.name,
        "created_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
        "size_bytes": path.stat().st_size,
        "revision": parsed_revision,
        "sha256": checksum or read_checksum(path),
        "manifest": manifest_path(path).name,
    }


def list_platform_backups(data_dir: str | Path) -> list[dict[str, object]]:
    directory = backup_directory(data_dir)
    return [
        backup_info(path)
        for path in sorted(directory.glob("backup-*.dump"), key=lambda item: item.stat().st_mtime, reverse=True)
    ]


def resolve_backup(data_dir: str | Path, name: str) -> Path:
    if not BACKUP_NAME_RE.fullmatch(name):
        raise BackupError("Geçersiz yedek adı")
    directory = backup_directory(data_dir)
    path = (directory / name).resolve()
    if path.parent != directory or not path.is_file():
        raise BackupError("Yedek bulunamadı")
    return path


def validate_restored_database(database_url: str, manifest: dict) -> dict[str, int]:
    revision = current_schema_revision(database_url)
    if revision != manifest["alembic_revision"] or revision != application_head():
        raise BackupError("Restore sonrası Alembic revision doğrulanamadı")
    _, counts = _database_inventory(database_url)
    expected = {key: int(value) for key, value in manifest["table_counts"].items()}
    if counts != expected:
        raise BackupError(
            f"Restore sonrası tablo sayımları manifest ile eşleşmiyor: expected={expected}, actual={counts}"
        )
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite":
        path = _sqlite_path(database_url)
        with closing(sqlite3.connect(path)) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if not integrity or str(integrity[0]).lower() != "ok" or foreign_keys:
            raise BackupError("SQLite restore sonrası bütünlük doğrulaması başarısız")
    else:
        target = create_engine(database_url)
        try:
            with target.connect() as connection:
                connection.execute(text("SELECT 1")).scalar_one()
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                connection.execute(text("SELECT id FROM platform_maintenance WHERE id=1")).scalar_one()
        finally:
            target.dispose()
    return counts


def apply_backup(database_url: str, path: Path) -> None:
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite":
        target = _sqlite_path(database_url)
        temporary = target.with_suffix(target.suffix + ".restore.tmp")
        try:
            shutil.copy2(path, temporary)
            _integrity_check(temporary)
            _replace_with_retry(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return
    executable = shutil.which("pg_restore")
    if not executable:
        raise BackupError("pg_restore bulunamadı; PostgreSQL istemci araçlarını kurun")
    command = [
        executable, "--clean", "--if-exists", "--single-transaction",
        "--no-owner", "--no-acl", "--host", url.host or "localhost",
        "--port", str(url.port or 5432), "--username", url.username or "postgres",
        "--dbname", url.database or "postgres", str(path),
    ]
    result = subprocess.run(command, env=_postgres_env(url), capture_output=True, text=True)
    if result.returncode != 0:
        raise BackupError(f"pg_restore başarısız: {result.stderr.strip()}")


class RestoreExecutionError(BackupError):
    def __init__(
        self,
        message: str,
        *,
        database_state: str,
        rollback_error: str | None = None,
    ) -> None:
        super().__init__(message)
        self.database_state = database_state
        self.rollback_error = rollback_error


def apply_backup_with_rollback(
    database_url: str,
    selected_path: Path,
    pre_restore_path: Path,
    *,
    after_apply: Callable[[], None] | None = None,
    on_rollback_started: Callable[[Exception], None] | None = None,
    on_rollback_completed: Callable[[], None] | None = None,
    on_rollback_failed: Callable[[Exception], None] | None = None,
) -> dict[str, int]:
    """Apply and verify a backup; any post-prebackup failure attempts rollback."""
    try:
        apply_backup(database_url, selected_path)
        if after_apply:
            after_apply()
        return validate_restored_database(database_url, read_manifest(selected_path))
    except Exception as restore_exc:
        if on_rollback_started:
            on_rollback_started(restore_exc)
        try:
            apply_backup(database_url, pre_restore_path)
            if after_apply:
                after_apply()
            validate_restored_database(database_url, read_manifest(pre_restore_path))
            if on_rollback_completed:
                on_rollback_completed()
        except Exception as rollback_exc:
            if on_rollback_failed:
                on_rollback_failed(rollback_exc)
            raise RestoreExecutionError(
                str(restore_exc),
                database_state="rollback_failed",
                rollback_error=str(rollback_exc),
            ) from restore_exc
        raise RestoreExecutionError(
            str(restore_exc), database_state="rolled_back"
        ) from restore_exc


def restore_platform_backup(database_url: str, data_dir: str | Path, path: Path, *, retention_count: int = 14) -> dict[str, object]:
    verified = verify_platform_backup(database_url, path)
    # Keep the selected archive alive while the mandatory pre-backup is added,
    # even when retention is configured as 1. Normal retention resumes only
    # after restore has consumed the selected file.
    pre_backup = create_platform_backup(
        database_url, data_dir, retention_count=retention_count + 1
    )
    apply_backup(database_url, path)
    counts = validate_restored_database(database_url, read_manifest(path))
    revision = current_schema_revision(database_url)
    enforce_retention(backup_directory(data_dir), retention_count)
    return {
        "status": "completed",
        "restored_backup": verified,
        "pre_restore_backup": pre_backup,
        "revision": revision,
        "table_counts": counts,
        "rollback_instruction": f"Arıza halinde {pre_backup['name']} yedeğini aynı doğrulama akışıyla geri yükleyin.",
    }
