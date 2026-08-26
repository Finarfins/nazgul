#!/usr/bin/env python3
"""Run every active pytest file in an isolated subprocess and workspace."""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

BACKEND = Path(__file__).resolve().parent
CONFTEST = BACKEND / "conftest.py"
ALLOWED_TERMINAL_OUTCOMES = frozenset(
    {
        "passed",
        "skipped",
        "failed",
        "setup-failed",
        "teardown-failed",
        "collection-failed",
    }
)
SUCCESSFUL_TERMINAL_OUTCOMES = frozenset({"passed", "skipped"})


def terminal_outcome_errors(
    collected: tuple[str, ...] | list[str],
    outcomes: dict[str, str],
    *,
    successful_only: bool,
) -> list[str]:
    errors: list[str] = []
    collected_set = set(collected)
    if len(collected_set) != len(collected):
        errors.append("duplicate collected node ids")

    missing = sorted(collected_set - outcomes.keys())
    if missing:
        errors.append(f"terminal outcome missing: {', '.join(missing)}")

    unexpected = sorted(outcomes.keys() - collected_set)
    if unexpected:
        errors.append(f"outcome without collected node: {', '.join(unexpected)}")

    invalid = sorted(
        f"{nodeid}={outcome}"
        for nodeid, outcome in outcomes.items()
        if outcome not in ALLOWED_TERMINAL_OUTCOMES
    )
    if invalid:
        errors.append(f"invalid terminal outcome: {', '.join(invalid)}")

    if successful_only:
        unsuccessful = sorted(
            f"{nodeid}={outcome}"
            for nodeid, outcome in outcomes.items()
            if outcome not in SUCCESSFUL_TERMINAL_OUTCOMES
        )
        if unsuccessful:
            errors.append(f"unsuccessful terminal outcome: {', '.join(unsuccessful)}")
    return errors


@dataclass(frozen=True)
class TestResult:
    index: int
    rel_path: str
    returncode: int
    elapsed: float
    stdout: str
    stderr: str
    reason: str
    collected: tuple[str, ...]
    outcomes: dict[str, str]
    collect_only: bool = False

    @property
    def passed(self) -> bool:
        if self.returncode != 0:
            return False
        if self.collect_only:
            return True
        return not terminal_outcome_errors(
            self.collected,
            self.outcomes,
            successful_only=True,
        )


def _collect_ignored_files() -> set[str]:
    tree = ast.parse(CONFTEST.read_text(encoding="utf-8"), filename=str(CONFTEST))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(
                isinstance(target, ast.Name) and target.id == "collect_ignore"
                for target in node.targets
            ):
                value = ast.literal_eval(node.value)
                return {Path(item).name for item in value}
    raise RuntimeError("conftest.py içinde collect_ignore listesi bulunamadı")


def discover_active_test_files() -> list[Path]:
    ignored = _collect_ignored_files()
    root_files = [
        path
        for path in BACKEND.glob("test_*.py")
        if path.name not in ignored
    ]
    nested_dir = BACKEND / "tests"
    nested_files = list(nested_dir.glob("test_*.py")) if nested_dir.is_dir() else []
    return sorted(
        root_files + nested_files,
        key=lambda path: path.relative_to(BACKEND).as_posix(),
    )


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(BACKEND).as_posix()
    except ValueError:
        return path.name


def _worker_directory(root: Path, index: int, path: Path) -> Path:
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", path.stem)[:60]
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:10]
    return root / f"{index:04d}-{safe_name}-{digest}"


def _copytree(source: Path, target: Path) -> None:
    if not source.is_dir():
        return
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
    )


def _prepare_workspace(
    workdir: Path, database_template: Path | None = None
) -> None:
    workdir.mkdir(parents=True, exist_ok=False)
    _copytree(BACKEND / "app", workdir / "app")
    _copytree(BACKEND / "alembic", workdir / "alembic")

    fixtures = BACKEND / "tests" / "fixtures"
    if fixtures.is_dir():
        _copytree(fixtures, workdir / "tests" / "fixtures")

    for filename in (
        "alembic.ini",
        "requirements.txt",
        "requirements-dev.txt",
        "seed_demo_data.py",
    ):
        source = BACKEND / filename
        if source.is_file():
            shutil.copy2(source, workdir / filename)

    if database_template is not None:
        shutil.copy2(database_template, workdir / "veriler.db")


def _subprocess_environment(workdir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["DATABASE_URL"] = f"sqlite:///{(workdir / 'veriler.db').as_posix()}"
    env["SUNGUR_DATA_DIR"] = str(workdir / "data")
    env["PYTHONPYCACHEPREFIX"] = str(workdir / "pycache")
    system_temp = workdir / "system-tmp"
    system_temp.mkdir(parents=True, exist_ok=True)
    for name in ("TMP", "TEMP", "TMPDIR"):
        env[name] = str(system_temp)
    existing_pythonpath = env.get("PYTHONPATH")
    python_paths = [str(workdir), str(BACKEND)]
    if existing_pythonpath:
        python_paths.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(python_paths)
    env["ISOLATED_TEST_REPORT"] = str(workdir / "pytest-report.json")
    return env


def _prepare_database_template(root: Path, timeout: int) -> Path:
    template_workdir = root / "database-template"
    _prepare_workspace(template_workdir)
    env = _subprocess_environment(template_workdir)
    command = [
        sys.executable,
        "-c",
        (
            "from app.bootstrap_data import seed_bootstrap_data\n"
            "from app.db import engine\n"
            "from app.runtime_migrations import run_database_migrations\n"
            "run_database_migrations(engine)\n"
            "seed_bootstrap_data(engine)\n"
            "with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:\n"
            "    connection.exec_driver_sql('PRAGMA wal_checkpoint(TRUNCATE)')\n"
            "engine.dispose()\n"
        ),
    ]
    completed = subprocess.run(
        command,
        cwd=template_workdir,
        env=env,
        text=True,
        capture_output=True,
        timeout=max(timeout, 1),
    )
    database_template = template_workdir / "veriler.db"
    if completed.returncode != 0 or not database_template.is_file():
        raise RuntimeError(
            "İzole test veritabanı şablonu hazırlanamadı:\n"
            f"{completed.stdout}{completed.stderr}"
        )
    wal_path = database_template.with_name(f"{database_template.name}-wal")
    if wal_path.is_file() and wal_path.stat().st_size:
        raise RuntimeError("İzole test veritabanı şablonunun WAL kaydı kapanmadı")
    try:
        with closing(sqlite3.connect(database_template)) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            revision = connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()
            company_count = connection.execute(
                "SELECT COUNT(*) FROM companies"
            ).fetchone()
            user_count = connection.execute(
                "SELECT COUNT(*) FROM app_users"
            ).fetchone()
    except sqlite3.Error as exc:
        raise RuntimeError(
            f"İzole test veritabanı şablonu doğrulanamadı: {exc}"
        ) from exc
    if (
        integrity != ("ok",)
        or not revision
        or not company_count
        or company_count[0] < 1
        or not user_count
        or user_count[0] < 1
    ):
        raise RuntimeError("İzole test veritabanı şablonu eksik veya bozuk")
    return database_template


def _decode_timeout_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def _read_worker_report(path: Path) -> tuple[tuple[str, ...], dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    collected = payload.get("collected")
    outcomes = payload.get("outcomes")
    if not isinstance(collected, list) or not all(isinstance(item, str) for item in collected):
        raise ValueError("collected listesi geçersiz")
    if not isinstance(outcomes, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in outcomes.items()
    ):
        raise ValueError("outcomes sözlüğü geçersiz")
    return tuple(collected), dict(outcomes)


def _execute_test_file(
    path: Path,
    *,
    index: int,
    total: int,
    timeout: int,
    verbose: bool,
    collect_only: bool,
    workdir: Path,
    database_template: Path,
    preprepared: bool,
) -> TestResult:
    rel_path = _display_path(path)
    if not preprepared:
        _prepare_workspace(workdir, database_template)

    env = _subprocess_environment(workdir)
    report_path = Path(env["ISOLATED_TEST_REPORT"])
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "pytest_asyncio.plugin",
        "-p",
        "isolated_test_reporter",
        "-o",
        f"cache_dir={workdir / '.pytest_cache'}",
        f"--basetemp={workdir / 'pytest-tmp'}",
        str(path.resolve()),
    ]
    if collect_only:
        command.insert(-1, "--collect-only")
    if verbose:
        command.insert(-1, "-vv")

    started = time.monotonic()
    try:
        try:
            completed = subprocess.run(
                command,
                cwd=workdir,
                env=env,
                text=True,
                capture_output=True,
                timeout=max(timeout, 1),
            )
            returncode = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
            reason = (
                "collected"
                if collect_only and returncode == 0
                else "passed" if returncode == 0 else f"exit={returncode}"
            )
        except subprocess.TimeoutExpired as exc:
            returncode = 124
            stdout = _decode_timeout_output(exc.stdout)
            stderr = _decode_timeout_output(exc.stderr)
            reason = "timeout"

        try:
            collected, outcomes = _read_worker_report(report_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            collected, outcomes = (), {}
            if returncode == 0:
                returncode = 3
                reason = f"report-error: {exc}"

        if returncode == 0 and not collect_only:
            outcome_errors = terminal_outcome_errors(
                collected,
                outcomes,
                successful_only=True,
            )
            if outcome_errors:
                returncode = 3
                reason = f"outcome-invalid: {'; '.join(outcome_errors)}"

        return TestResult(
            index=index,
            rel_path=rel_path,
            returncode=returncode,
            elapsed=time.monotonic() - started,
            stdout=stdout,
            stderr=stderr,
            reason=reason,
            collected=collected,
            outcomes=outcomes,
            collect_only=collect_only,
        )
    finally:
        if not preprepared:
            shutil.rmtree(workdir, ignore_errors=True)


def _emit_result(result: TestResult, total: int) -> None:
    status = "PASS" if result.passed else "FAIL"
    if result.reason == "timeout":
        status = "TIMEOUT"
    print(
        f"[{result.index}/{total}] {status} {result.rel_path} "
        f"({result.elapsed:.1f}s, {len(result.collected)} test)",
        flush=True,
    )
    if result.stdout.strip():
        print(result.stdout.rstrip())
    if result.stderr.strip():
        print(result.stderr.rstrip(), file=sys.stderr)


def run_test_files(
    files: list[Path],
    *,
    workers: int,
    timeout: int,
    verbose: bool = False,
    emit: bool = True,
    collect_only: bool = False,
) -> list[TestResult]:
    if workers < 1:
        raise ValueError("workers en az 1 olmalı")

    with tempfile.TemporaryDirectory(prefix="mobil-erp-isolated-tests-") as raw_root:
        root = Path(raw_root)
        database_template = _prepare_database_template(root, timeout)
        workdirs = [
            _worker_directory(root, index, path)
            for index, path in enumerate(files, start=1)
        ]
        duplicate_workdirs = {
            path for path, count in Counter(workdirs).items() if count > 1
        }
        for workdir in duplicate_workdirs:
            _prepare_workspace(workdir, database_template)

        arguments = [
            {
                "path": path,
                "index": index,
                "total": len(files),
                "timeout": timeout,
                "verbose": verbose,
                "collect_only": collect_only,
                "workdir": workdirs[index - 1],
                "database_template": database_template,
                "preprepared": workdirs[index - 1] in duplicate_workdirs,
            }
            for index, path in enumerate(files, start=1)
        ]

        results: list[TestResult] = []
        if workers == 1:
            for kwargs in arguments:
                result = _execute_test_file(**kwargs)
                results.append(result)
                if emit:
                    _emit_result(result, len(files))
        else:
            with ThreadPoolExecutor(max_workers=min(workers, len(files) or 1)) as pool:
                futures = [pool.submit(_execute_test_file, **kwargs) for kwargs in arguments]
                for future in as_completed(futures):
                    result = future.result()
                    results.append(result)
                    if emit:
                        _emit_result(result, len(files))

    return sorted(results, key=lambda result: result.index)


def select_test_shard(
    files: list[Path], *, shard_index: int, shard_count: int
) -> list[Path]:
    if shard_count < 1:
        raise ValueError("shard_count en az 1 olmalı")
    if shard_index < 0 or shard_index >= shard_count:
        raise ValueError("shard_index, shard_count aralığında olmalı")
    return [
        path
        for position, path in enumerate(files)
        if position % shard_count == shard_index
    ]


def execution_manifest(results: list[TestResult]) -> dict[str, str]:
    manifest: dict[str, str] = {}
    for result in results:
        errors = terminal_outcome_errors(
            result.collected,
            result.outcomes,
            successful_only=False,
        )
        if errors:
            raise ValueError(f"{result.rel_path}: {'; '.join(errors)}")
        for nodeid in result.collected:
            if nodeid in manifest:
                raise ValueError(f"Test birden fazla kez toplandı: {nodeid}")
            manifest[nodeid] = result.outcomes[nodeid]
    return manifest


def canonical_collection_manifest(results: list[TestResult]) -> dict[str, list[str]]:
    files: list[str] = []
    nodes: list[str] = []
    seen_files: set[str] = set()
    seen_nodes: set[str] = set()
    for result in results:
        if result.returncode != 0:
            raise ValueError(
                f"Canonical collection failed for {result.rel_path}: {result.reason}"
            )
        if result.rel_path in seen_files:
            raise ValueError(f"Duplicate canonical test file: {result.rel_path}")
        seen_files.add(result.rel_path)
        files.append(result.rel_path)
        for nodeid in result.collected:
            if nodeid in seen_nodes:
                raise ValueError(f"Duplicate canonical test node id: {nodeid}")
            seen_nodes.add(nodeid)
            nodes.append(nodeid)
    return {"files": files, "nodes": nodes}


def _canonical_payload_from_nodes(
    files: list[str],
    nodes: list[str],
) -> dict[str, list[str]]:
    file_counts = Counter(files)
    duplicate_files = sorted(path for path, count in file_counts.items() if count != 1)
    if duplicate_files:
        raise ValueError(f"Duplicate canonical test file: {', '.join(duplicate_files)}")

    node_counts = Counter(nodes)
    duplicate_nodes = sorted(nodeid for nodeid, count in node_counts.items() if count != 1)
    if duplicate_nodes:
        raise ValueError(
            f"Duplicate canonical test node id: {', '.join(duplicate_nodes)}"
        )

    expected_files = set(files)
    collected_files = {nodeid.split("::", 1)[0] for nodeid in nodes}
    missing_files = sorted(expected_files - collected_files)
    extra_files = sorted(collected_files - expected_files)
    if missing_files or extra_files:
        raise ValueError(
            "Canonical collection file set mismatch; "
            f"missing={missing_files}, extra={extra_files}"
        )
    return {"files": files, "nodes": nodes}


def collect_canonical_manifest_single_process(
    files: list[Path],
    *,
    timeout: int,
    verbose: bool = False,
) -> dict[str, list[str]]:
    with tempfile.TemporaryDirectory(prefix="mobil-erp-canonical-collection-") as raw_root:
        root = Path(raw_root)
        database_template = _prepare_database_template(root, timeout)
        workdir = root / "single-process"
        _prepare_workspace(workdir, database_template)
        env = _subprocess_environment(workdir)
        report_path = Path(env["ISOLATED_TEST_REPORT"])
        arguments_path = root / "pytest-files.txt"
        arguments_path.write_text(
            "\n".join(str(path.resolve()) for path in files) + "\n",
            encoding="utf-8",
        )
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "pytest_asyncio.plugin",
            "-p",
            "isolated_test_reporter",
            "-o",
            f"cache_dir={workdir / '.pytest_cache'}",
            f"--basetemp={workdir / 'pytest-tmp'}",
            "--collect-only",
            f"@{arguments_path}",
        ]
        if verbose:
            command.insert(-1, "-vv")

        try:
            completed = subprocess.run(
                command,
                cwd=workdir,
                env=env,
                text=True,
                capture_output=True,
                timeout=max(timeout, 1),
            )
        except subprocess.TimeoutExpired as exc:
            output = _decode_timeout_output(exc.stdout) + _decode_timeout_output(
                exc.stderr
            )
            raise ValueError(
                f"Canonical collection timed out after {max(timeout, 1)}s:\n{output}"
            ) from exc

        if completed.returncode != 0:
            output = f"{completed.stdout}{completed.stderr}".strip()
            raise ValueError(
                "Canonical collection subprocess failed "
                f"(exit={completed.returncode}):\n{output}"
            )
        try:
            collected, outcomes = _read_worker_report(report_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"Canonical collection report failed: {exc}") from exc
        if outcomes:
            raise ValueError(
                "Canonical collection unexpectedly recorded terminal outcomes: "
                + ", ".join(sorted(outcomes))
            )
        return _canonical_payload_from_nodes(
            [_display_path(path) for path in files],
            list(collected),
        )


def _write_canonical_manifest(path: Path, payload: dict[str, list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_report(path: Path, results: list[TestResult], total_seconds: float) -> None:
    payload = {
        "files": [result.rel_path for result in results],
        "passed_files": [result.rel_path for result in results if result.passed],
        "failed_files": [result.rel_path for result in results if not result.passed],
        "manifest": execution_manifest(results),
        "total_seconds": round(total_seconds, 3),
        "results": [
            {
                "file": result.rel_path,
                "returncode": result.returncode,
                "reason": result.reason,
                "elapsed": round(result.elapsed, 3),
                "collected": len(result.collected),
            }
            for result in results
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=180, help="Her test dosyası için saniye sınırı")
    parser.add_argument("--workers", type=int, default=1, help="Eşzamanlı izole test dosyası sayısı")
    parser.add_argument("--shard-index", type=int, default=0, help="Sıfır tabanlı shard sırası")
    parser.add_argument("--shard-count", type=int, default=1, help="Toplam shard sayısı")
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument("--report-json", type=Path, help="Makine okunur sonuç manifesti")
    output_group.add_argument(
        "--collect-manifest-json",
        type=Path,
        help="Testleri çalıştırmadan kanonik dosya/node manifesti üretir",
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("files", nargs="*", help="İsteğe bağlı test dosyası adları")
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers en az 1 olmalı")
    if args.shard_count < 1:
        parser.error("--shard-count en az 1 olmalı")
    if args.shard_index < 0 or args.shard_index >= args.shard_count:
        parser.error("--shard-index, --shard-count aralığında olmalı")

    discovered = discover_active_test_files()
    if args.files:
        requested = {Path(name).name for name in args.files}
        files = [path for path in discovered if path.name in requested]
        missing = requested - {path.name for path in files}
        if missing:
            print(
                f"Aktif test manifestinde bulunamadı: {', '.join(sorted(missing))}",
                file=sys.stderr,
            )
            return 2
    else:
        files = discovered

    if args.collect_manifest_json is not None:
        if args.shard_count != 1 or args.shard_index != 0:
            parser.error("--collect-manifest-json shard seçenekleriyle kullanılamaz")
        started = time.monotonic()
        try:
            payload = collect_canonical_manifest_single_process(
                files,
                timeout=args.timeout,
                verbose=args.verbose,
            )
            _write_canonical_manifest(args.collect_manifest_json, payload)
        except ValueError as exc:
            print(f"Kanonik toplama başarısız: {exc}", file=sys.stderr)
            return 1
        elapsed = time.monotonic() - started
        print(
            f"Kanonik manifest: {len(payload['files'])} dosya, "
            f"{len(payload['nodes'])} node, "
            f"{elapsed:.1f}s"
        )
        return 0

    files = select_test_shard(
        files,
        shard_index=args.shard_index,
        shard_count=args.shard_count,
    )

    started = time.monotonic()
    print(
        f"{len(files)} aktif test dosyası {args.workers} işçide, dosya başına "
        "ayrı çalışma alanında çalıştırılıyor.",
        flush=True,
    )
    results = run_test_files(
        files,
        workers=args.workers,
        timeout=args.timeout,
        verbose=args.verbose,
    )
    total = time.monotonic() - started
    if args.report_json:
        _write_report(args.report_json, results, total)

    failures = [result for result in results if not result.passed]
    if failures:
        print("\nBaşarısız test dosyaları:", file=sys.stderr)
        for result in failures:
            print(f"- {result.rel_path}: {result.reason}", file=sys.stderr)
        print(f"Toplam süre: {total:.1f}s", file=sys.stderr)
        return 1

    manifest = execution_manifest(results)
    print(
        f"\nTüm {len(files)} aktif test dosyası geçti; {len(manifest)} test sonucu "
        f"kaydedildi. Toplam süre: {total:.1f}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
