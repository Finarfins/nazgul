from __future__ import annotations

from collections import Counter
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from aggregate_isolated_test_reports import (
    aggregate_report_payloads,
    reconcile_sqlite_skips_with_postgresql,
)
from merge_postgresql_test_reports import merge_postgresql_report_payloads
from run_isolated_tests import (
    _decode_captured_output,
    _subprocess_environment,
    canonical_collection_manifest,
    collect_canonical_manifest_single_process,
    configure_runner_streams,
    execution_manifest,
    main,
    run_test_files,
    select_test_shard,
)


LOCK_TEST = """
import os
from pathlib import Path
import time

def test_worker_has_exclusive_directory():
    marker = Path.cwd() / "worker-owner.lock"
    descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(descriptor, b"owner")
        time.sleep(0.4)
    finally:
        os.close(descriptor)
"""

SKIP_TEST = """
import pytest

def test_explicit_skip():
    pytest.skip("contractual skip")
"""

DATABASE_TEMPLATE_TEST = """
import os
from pathlib import Path
import sqlite3

def test_worker_receives_complete_database_template():
    database = Path(os.environ["DATABASE_URL"].removeprefix("sqlite:///"))
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()
        assert connection.execute("SELECT COUNT(*) FROM companies").fetchone()[0] >= 1
        assert connection.execute("SELECT COUNT(*) FROM app_users").fetchone()[0] >= 1
"""

CANONICAL_FIXTURES = Path(__file__).parent / "fixtures" / "canonical_collection"


def _write_test(path: Path, source: str) -> Path:
    path.write_text(source, encoding="utf-8")
    return path


def test_serial_and_parallel_collect_and_pass_the_identical_test_set(tmp_path: Path) -> None:
    files = [
        _write_test(tmp_path / "test_worker_a.py", LOCK_TEST),
        _write_test(tmp_path / "test_worker_b.py", LOCK_TEST),
        _write_test(tmp_path / "test_worker_skip.py", SKIP_TEST),
    ]

    parallel = run_test_files(files, workers=2, timeout=20, emit=False)
    assert all(result.passed for result in parallel), parallel

    serial = run_test_files(files, workers=1, timeout=20, emit=False)
    assert all(result.passed for result in serial), serial
    assert [result.rel_path for result in serial] == [
        result.rel_path for result in parallel
    ]
    assert execution_manifest(serial) == execution_manifest(parallel)
    assert set(execution_manifest(parallel).values()) == {"passed", "skipped"}


def test_collected_node_without_terminal_outcome_cannot_pass(tmp_path: Path) -> None:
    files = [_write_test(tmp_path / "test_sharded_worker.py", LOCK_TEST)]
    result = run_test_files(files, workers=1, timeout=20, emit=False)[0]
    missing_node = result.collected[0]
    mutated = replace(
        result,
        outcomes={
            nodeid: outcome
            for nodeid, outcome in result.outcomes.items()
            if nodeid != missing_node
        },
    )

    assert not mutated.passed
    with pytest.raises(ValueError, match="terminal outcome missing"):
        execution_manifest([mutated])


def test_collect_only_builds_canonical_file_and_node_manifest(tmp_path: Path) -> None:
    files = [
        _write_test(tmp_path / "test_collect_a.py", LOCK_TEST),
        _write_test(tmp_path / "test_collect_skip.py", SKIP_TEST),
    ]
    results = run_test_files(
        files,
        workers=2,
        timeout=20,
        emit=False,
        collect_only=True,
    )

    manifest = canonical_collection_manifest(results)

    assert manifest["files"] == ["test_collect_a.py", "test_collect_skip.py"]
    assert len(manifest["nodes"]) == 2


def test_single_process_canonical_matches_per_file_manifest_exactly() -> None:
    files = [
        CANONICAL_FIXTURES / "test_plain.py",
        CANONICAL_FIXTURES / "test_state_seed.py",
    ]
    per_file_results = run_test_files(
        files,
        workers=2,
        timeout=30,
        emit=False,
        collect_only=True,
    )
    assert all(result.passed for result in per_file_results), per_file_results
    per_file = canonical_collection_manifest(per_file_results)

    single_process = collect_canonical_manifest_single_process(files, timeout=30)

    assert set(single_process["files"]) == set(per_file["files"])
    assert set(single_process["nodes"]) == set(per_file["nodes"])
    assert Counter(single_process["nodes"]) == Counter(per_file["nodes"])
    print(
        "SINGLE-PROCESS CANONICAL PARITY GREEN: "
        f"files={len(single_process['files'])} nodes={len(single_process['nodes'])}"
    )


def test_single_process_canonical_broken_import_is_attributed() -> None:
    files = [
        CANONICAL_FIXTURES / "test_plain.py",
        CANONICAL_FIXTURES / "test_broken_import.py",
    ]

    with pytest.raises(ValueError) as failure:
        collect_canonical_manifest_single_process(files, timeout=30)

    message = str(failure.value)
    assert "test_broken_import.py" in message
    assert "dependency_missing_for_canonical_gate" in message
    assert "exit=2" in message
    print("SINGLE-PROCESS BROKEN IMPORT GATE GREEN: test_broken_import.py exit=2")


def test_shared_interpreter_collection_dependency_is_rejected_by_union() -> None:
    files = [
        CANONICAL_FIXTURES / "test_state_seed.py",
        CANONICAL_FIXTURES / "test_state_dependent.py",
    ]
    canonical = collect_canonical_manifest_single_process(files, timeout=30)
    shared_state_node = next(
        nodeid
        for nodeid in canonical["nodes"]
        if nodeid.endswith("test_state_dependent.py::test_only_after_seed_import")
    )

    shard_results = run_test_files(files, workers=2, timeout=30, emit=False)
    assert all(result.passed for result in shard_results), shard_results
    shard_manifest = execution_manifest(shard_results)
    assert shared_state_node not in shard_manifest
    shard_payload = {
        "files": [result.rel_path for result in shard_results],
        "passed_files": [result.rel_path for result in shard_results],
        "failed_files": [],
        "manifest": shard_manifest,
    }

    with pytest.raises(ValueError, match="shard node union mismatch.*test_only_after_seed_import"):
        aggregate_report_payloads(canonical, [shard_payload], expected_shards=1)
    print(
        "SHARED-INTERPRETER UNION GATE GREEN: "
        f"canonical-only-node={shared_state_node} aggregation=RED"
    )


def _canonical_payload() -> dict[str, list[str]]:
    return {
        "files": ["test_alpha.py", "test_beta.py"],
        "nodes": ["test_alpha.py::test_alpha", "test_beta.py::test_beta"],
    }


def _shard_payloads() -> list[dict[str, object]]:
    return [
        {
            "files": ["test_alpha.py"],
            "passed_files": ["test_alpha.py"],
            "failed_files": [],
            "manifest": {"test_alpha.py::test_alpha": "passed"},
        },
        {
            "files": ["test_beta.py"],
            "passed_files": ["test_beta.py"],
            "failed_files": [],
            "manifest": {"test_beta.py::test_beta": "skipped"},
        },
    ]


def _postgresql_report() -> dict[str, object]:
    return {
        "manifest": {"test_beta.py::test_beta": "passed"},
    }


def _empty_skip_exceptions() -> dict[str, object]:
    return {"version": 1, "exceptions": []}


def test_shard_aggregation_requires_exact_disjoint_union() -> None:
    aggregate = aggregate_report_payloads(
        _canonical_payload(),
        _shard_payloads(),
        expected_shards=2,
    )

    assert aggregate["outcomes"] == {"passed": 1, "skipped": 1}


def test_shard_aggregation_rejects_a_dropped_file() -> None:
    shards = _shard_payloads()
    shards[1] = {
        "files": [],
        "passed_files": [],
        "failed_files": [],
        "manifest": {},
    }

    with pytest.raises(ValueError, match="shard file union mismatch"):
        aggregate_report_payloads(
            _canonical_payload(), shards, expected_shards=2
        )


def test_shard_aggregation_rejects_duplicate_or_nonterminal_nodes() -> None:
    duplicate = _shard_payloads()
    duplicate[1]["manifest"] = {"test_alpha.py::test_alpha": "skipped"}
    with pytest.raises(ValueError, match="node appears in multiple shards"):
        aggregate_report_payloads(
            _canonical_payload(), duplicate, expected_shards=2
        )

    nonterminal = _shard_payloads()
    nonterminal[1]["manifest"] = {"test_beta.py::test_beta": "not-run"}
    with pytest.raises(ValueError, match="disallowed terminal outcome"):
        aggregate_report_payloads(
            _canonical_payload(), nonterminal, expected_shards=2
        )


def test_sqlite_skip_must_pass_in_postgresql_report() -> None:
    aggregate = aggregate_report_payloads(
        _canonical_payload(), _shard_payloads(), expected_shards=2
    )

    result = reconcile_sqlite_skips_with_postgresql(
        aggregate["manifest"],
        _postgresql_report(),
        _empty_skip_exceptions(),
    )

    assert result == {
        "sqlite_skipped": 1,
        "postgresql_passed": 1,
        "exceptions": 0,
    }


def test_missing_postgresql_twin_outcome_is_rejected() -> None:
    aggregate = aggregate_report_payloads(
        _canonical_payload(), _shard_payloads(), expected_shards=2
    )

    with pytest.raises(ValueError, match="did not pass.*missing"):
        reconcile_sqlite_skips_with_postgresql(
            aggregate["manifest"],
            {"manifest": {}},
            _empty_skip_exceptions(),
        )


def test_postgresql_twin_that_skips_is_rejected() -> None:
    aggregate = aggregate_report_payloads(
        _canonical_payload(), _shard_payloads(), expected_shards=2
    )

    with pytest.raises(ValueError, match="did not pass.*skipped"):
        reconcile_sqlite_skips_with_postgresql(
            aggregate["manifest"],
            {"manifest": {"test_beta.py::test_beta": "skipped"}},
            _empty_skip_exceptions(),
        )


def test_non_twin_skip_is_rejected_with_empty_exception_list() -> None:
    shards = _shard_payloads()
    shards[0]["manifest"] = {"test_alpha.py::test_alpha": "skipped"}
    aggregate = aggregate_report_payloads(
        _canonical_payload(), shards, expected_shards=2
    )

    with pytest.raises(ValueError, match="test_alpha.py::test_alpha=missing"):
        reconcile_sqlite_skips_with_postgresql(
            aggregate["manifest"],
            _postgresql_report(),
            _empty_skip_exceptions(),
        )


def test_checked_in_non_twin_skip_exception_list_matches_expectations() -> None:
    path = Path(__file__).parents[1] / "non_twin_skip_exceptions.json"

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "version": 1,
        "exceptions": [],
    }


def test_postgresql_report_merge_requires_terminal_outcomes() -> None:
    reports = {
        "alpha.json": {
            "collected": ["test_alpha.py::test_alpha"],
            "outcomes": {"test_alpha.py::test_alpha": "passed"},
        },
        "beta.json": {
            "collected": ["test_beta.py::test_beta"],
            "outcomes": {"test_beta.py::test_beta": "passed"},
        },
    }

    merged = merge_postgresql_report_payloads(reports)

    assert merged["outcomes"] == {"passed": 2}

    reports["beta.json"]["outcomes"] = {}
    with pytest.raises(ValueError, match="terminal outcome missing"):
        merge_postgresql_report_payloads(reports)


def test_postgresql_subdirectory_report_is_written_and_merged(
    tmp_path: Path,
) -> None:
    backend = Path(__file__).parents[1]
    test_path = backend / "tests" / "fixtures" / "test_nested_pg_lane.py"
    relative_test_path = test_path.relative_to(backend)
    expected_node_id = f"{relative_test_path.as_posix()}::test_nested_pg_lane"

    sqlite_results = run_test_files(
        [test_path],
        workers=1,
        timeout=30,
        emit=False,
    )
    assert all(result.passed for result in sqlite_results), sqlite_results
    sqlite_manifest = execution_manifest(sqlite_results)
    assert sqlite_manifest == {expected_node_id: "passed"}

    report_directory = tmp_path / "postgresql-test-reports"
    report_path = report_directory / relative_test_path.with_suffix(".json")
    environment = os.environ.copy()
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    environment["ISOLATED_TEST_REPORT"] = str(report_path)
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(backend), environment.get("PYTHONPATH")))
    )

    pytest_run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "isolated_test_reporter",
            str(relative_test_path),
        ],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert pytest_run.returncode == 0, pytest_run.stdout + pytest_run.stderr
    assert report_path.is_file()
    postgresql_payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert postgresql_payload["outcomes"] == sqlite_manifest
    assert postgresql_payload["collected"] == [expected_node_id]

    merged_path = tmp_path / "postgresql-test-report.json"
    merge_run = subprocess.run(
        [
            sys.executable,
            "merge_postgresql_test_reports.py",
            str(report_directory),
            "--output",
            str(merged_path),
        ],
        cwd=backend,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert merge_run.returncode == 0, merge_run.stdout + merge_run.stderr
    merged = json.loads(merged_path.read_text(encoding="utf-8"))
    assert merged["files"] == [relative_test_path.with_suffix(".json").as_posix()]
    assert merged["manifest"] == sqlite_manifest
    assert merged["outcomes"] == {"passed": 1}
    print(
        "POSTGRESQL NODE-ID PARITY GREEN: "
        f"node={expected_node_id} "
        f"sqlite={sqlite_manifest} postgresql={postgresql_payload['outcomes']}"
    )


def test_each_worker_receives_complete_database_template(tmp_path: Path) -> None:
    files = [
        _write_test(tmp_path / "test_template_a.py", DATABASE_TEMPLATE_TEST),
        _write_test(tmp_path / "test_template_b.py", DATABASE_TEMPLATE_TEST),
    ]

    results = run_test_files(files, workers=2, timeout=20, emit=False)
    assert all(result.passed for result in results), results


def test_shards_are_disjoint_and_cover_the_complete_file_set() -> None:
    files = [Path(f"test_{index}.py") for index in range(11)]
    shards = [
        select_test_shard(files, shard_index=index, shard_count=4)
        for index in range(4)
    ]

    assert sorted(path for shard in shards for path in shard) == sorted(files)
    assert sum(len(shard) for shard in shards) == len(
        {path for shard in shards for path in shard}
    )


def test_parallel_failure_is_attributed_to_the_right_file(tmp_path: Path) -> None:
    passing = _write_test(
        tmp_path / "test_intentional_pass.py",
        "def test_ok():\n    assert True\n",
    )
    failing = _write_test(
        tmp_path / "test_intentional_failure.py",
        "def test_boom():\n    assert False, 'deliberate parallel failure'\n",
    )

    results = run_test_files(
        [passing, failing],
        workers=2,
        timeout=20,
        emit=True,
    )

    failures = [result for result in results if not result.passed]
    assert [result.rel_path for result in failures] == [failing.name]
    assert failures[0].reason == "exit=1"
    assert "deliberate parallel failure" in failures[0].stdout
    failed_outcomes = {
        nodeid: outcome
        for nodeid, outcome in execution_manifest(results).items()
        if outcome != "passed"
    }
    assert len(failed_outcomes) == 1
    assert next(iter(failed_outcomes)).endswith("test_intentional_failure.py::test_boom")
    assert next(iter(failed_outcomes.values())) == "failed"


def test_parallel_collection_failure_is_attributed_to_the_right_file(tmp_path: Path) -> None:
    passing = _write_test(
        tmp_path / "test_collection_pass.py",
        "def test_ok():\n    assert True\n",
    )
    broken = _write_test(
        tmp_path / "test_collection_broken.py",
        "raise RuntimeError('deliberate collection failure')\n",
    )

    results = run_test_files(
        [passing, broken],
        workers=2,
        timeout=20,
        emit=True,
    )

    failures = [result for result in results if not result.passed]
    assert [result.rel_path for result in failures] == [broken.name]
    assert failures[0].reason == "exit=2"
    assert "deliberate collection failure" in failures[0].stdout
    failed_outcomes = {
        nodeid: outcome
        for nodeid, outcome in execution_manifest(results).items()
        if outcome != "passed"
    }
    assert len(failed_outcomes) == 1
    assert next(iter(failed_outcomes)).endswith("test_collection_broken.py")
    assert next(iter(failed_outcomes.values())) == "collection-failed"


def test_decode_captured_output_never_none_and_replaces_bad_bytes() -> None:
    assert _decode_captured_output(None) == ""
    assert _decode_captured_output(b"") == ""
    assert _decode_captured_output("hazır") == "hazır"
    # Bytes that are not valid UTF-8 (and that PYTHONUTF8=1 / Linux would
    # reject under errors='strict') must not raise; replacement keeps the rest.
    with pytest.raises(UnicodeDecodeError):
        b"before\xff\xfeafter".decode("utf-8", errors="strict")
    decoded = _decode_captured_output(b"before\xff\xfeafter")
    assert decoded.startswith("before")
    assert decoded.endswith("after")
    assert "\ufffd" in decoded


def test_non_cp1254_child_bytes_are_reported_not_crashed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Child emitting undecodable bytes: runner reports the file, no crash.

    Locale-default ``text=True`` under UTF-8 mode (canonical ``PYTHONUTF8=1``,
    also Linux CI) raised UnicodeDecodeError before a ``TestResult`` existed,
    so the file was never reported and ``result.stdout`` was unreachable.
    Pin ``encoding='utf-8'`` + ``errors='replace'``; inject the replaced
    noise into the captured pipe so we prove the report path stays intact
    (pytest's own FD capture would otherwise swallow a raw ``os.write``).
    """
    real_run = subprocess.run
    seen_kwargs: list[dict] = []

    def run_and_inject_noise(*args, **kwargs):
        seen_kwargs.append(kwargs)
        assert kwargs.get("encoding") == "utf-8", kwargs
        assert kwargs.get("errors") == "replace", kwargs
        completed = real_run(*args, **kwargs)
        noise = _decode_captured_output(b"NOISE\xff\xfeMARKER\n")
        return subprocess.CompletedProcess(
            completed.args,
            completed.returncode,
            noise + (completed.stdout or ""),
            completed.stderr or "",
        )

    monkeypatch.setattr(subprocess, "run", run_and_inject_noise)

    noisy = _write_test(
        tmp_path / "test_noisy_stdout_bytes.py",
        "def test_ok():\n    assert True\n",
    )

    results = run_test_files([noisy], workers=1, timeout=20, emit=False)

    assert len(results) == 1
    result = results[0]
    assert result.rel_path == noisy.name
    assert result.stdout is not None
    assert isinstance(result.stdout, str)
    assert result.passed, (result.reason, result.stdout, result.stderr)
    assert "NOISE" in result.stdout
    assert "MARKER" in result.stdout
    assert "\ufffd" in result.stdout
    worker_calls = [k for k in seen_kwargs if k.get("capture_output")]
    assert worker_calls, seen_kwargs
    assert all(k.get("encoding") == "utf-8" for k in worker_calls)
    assert all(k.get("errors") == "replace" for k in worker_calls)


def test_subprocess_environment_sets_pythonioencoding_utf8(tmp_path: Path) -> None:
    env = _subprocess_environment(tmp_path)
    assert env.get("PYTHONIOENCODING") == "utf-8"


def test_configure_runner_streams_handles_non_reconfigurable_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = io.StringIO()
    monkeypatch.setattr(sys, "stdout", fake)
    monkeypatch.setattr(sys, "stderr", fake)
    configure_runner_streams()


def test_child_failing_with_replacement_char_under_cp1254_stdout_reports_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing child emitting '\\ufffd' under a cp1254 parent stdout does not crash.

    On Turkish Windows consoles (cp1254), printing a child's failure output
    containing characters unrepresentable in cp1254 (like \\ufffd from replacement
    decoding of malformed bytes or non-cp1254 unicode) crashed the runner with
    UnicodeEncodeError before reporting the failure.
    At runner start, sys.stdout and sys.stderr are reconfigured to UTF-8 with
    errors='replace'. This test simulates an initial cp1254 parent stream,
    verifies that writing \\ufffd to it would raise UnicodeEncodeError, runs a
    failing child that emits '\\ufffd' in a FAIL line, and asserts that the runner
    completes cleanly and reports the failing file.
    """
    raw_out = io.BytesIO()
    cp1254_stdout = io.TextIOWrapper(raw_out, encoding="cp1254", errors="strict")
    raw_err = io.BytesIO()
    cp1254_stderr = io.TextIOWrapper(raw_err, encoding="cp1254", errors="strict")

    # Prove that the initial stream rejects \ufffd with UnicodeEncodeError
    with pytest.raises(UnicodeEncodeError):
        cp1254_stdout.write("FAIL line with \ufffd\n")

    monkeypatch.setattr(sys, "stdout", cp1254_stdout)
    monkeypatch.setattr(sys, "stderr", cp1254_stderr)

    real_run = subprocess.run

    def run_and_inject_fail(*args, **kwargs):
        completed = real_run(*args, **kwargs)
        if kwargs.get("capture_output") and "pytest" in args[0]:
            fail_output = (
                "FAILURES\n_ test_fail _\n> assert False\n"
                "E AssertionError: fail with \ufffd char\n"
            )
            return subprocess.CompletedProcess(
                completed.args,
                1,
                fail_output,
                completed.stderr or "",
            )
        return completed

    monkeypatch.setattr(subprocess, "run", run_and_inject_fail)

    test_file = _write_test(
        tmp_path / "test_failing_child_ufffd.py",
        "def test_fail():\n    assert False\n",
    )

    # emit=True triggers _emit_result which writes result.stdout to sys.stdout
    results = run_test_files([test_file], workers=1, timeout=20, emit=True)

    assert len(results) == 1
    result = results[0]
    assert result.rel_path == test_file.name
    assert not result.passed
    assert "\ufffd" in result.stdout
    assert "AssertionError" in result.stdout

    # Verify stdout was reconfigured and output was written cleanly
    cp1254_stdout.flush()
    printed_output = raw_out.getvalue().decode("utf-8", errors="replace")
    assert "FAIL" in printed_output
    assert "\ufffd" in printed_output
    assert test_file.name in printed_output
