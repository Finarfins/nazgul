"""Isolated runner meta-tests: worker execution, failure attribution, output.

H41: split from ``test_isolated_test_runner.py``. The single file ran 117 s
alone and 177-210 s under ``--workers 4``, right at the runner's own 180 s
per-file cap. The tests share no fixture -- every ``run_test_files`` call
builds its own ~7 s database template -- so the split is by theme, balanced
by measured time: toplama/manifest/shard there, yurutme/raporlama here.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from run_isolated_tests import (
    _decode_captured_output,
    _subprocess_environment,
    configure_runner_streams,
    execution_manifest,
    print_failure_summary,
    run_test_files,
    timeout_failure_text,
)


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


def _write_test(path: Path, source: str) -> Path:
    path.write_text(source, encoding="utf-8")
    return path


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


HUNG_TEST = """
import time

def test_finishes_first():
    assert True

def _deliberately_hung_helper():
    time.sleep(120)

def test_hangs_until_killed():
    _deliberately_hung_helper()
"""


def test_timed_out_file_reports_running_test_and_its_stack(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """H41: a killed file used to report ``timeout`` and nothing else.

    The runner now says which test was still running, how many finished, and
    where the hung test was blocked (faulthandler dump written a few seconds
    before the kill), both inline and in the final failure summary.
    """
    hung = _write_test(tmp_path / "test_hung_file.py", HUNG_TEST)

    result = run_test_files([hung], workers=1, timeout=20, emit=True)[0]

    assert result.reason == "timeout"
    assert result.returncode == 124
    assert not result.passed
    text = result.failure_text
    assert "20s sınırını aştı" in text, text
    assert "Toplanan test: 2; tamamlanan: 1" in text, text
    assert "Zaman aşımında çalışan test: " in text, text
    running_line = next(
        line for line in text.splitlines() if line.startswith("Zaman aşımında çalışan test: ")
    )
    assert "test_hung_file.py::test_hangs_until_killed (" in running_line, text
    assert "test_hung_file.py::test_finishes_first" in text, text
    assert "Yığın dökümü" in text, text
    assert "_deliberately_hung_helper" in text, text
    assert text in capsys.readouterr().out

    print_failure_summary([result], total=result.elapsed)
    summary = capsys.readouterr().err
    assert "- test_hung_file.py: timeout" in summary, summary
    assert "    Zaman aşımında çalışan test: " in summary, summary
    assert "_deliberately_hung_helper" in summary, summary


def test_timeout_text_survives_a_progress_line_cut_by_the_kill(tmp_path: Path) -> None:
    progress = tmp_path / "pytest-progress.jsonl"
    progress.write_text(
        json.dumps({"event": "collected", "count": 2, "time": 1.0}) + "\n"
        + json.dumps({"event": "start", "nodeid": "t.py::a", "time": 1.0}) + "\n"
        + json.dumps({"event": "finish", "nodeid": "t.py::a", "time": 3.5}) + "\n"
        + '{"event": "start", "nodeid": "t.py::b", "ti',
        encoding="utf-8",
    )

    text = timeout_failure_text(
        timeout=180,
        killed_at=200.0,
        progress_path=progress,
        traceback_path=tmp_path / "missing-traceback.txt",
        stdout=".",
        stderr="",
    )

    assert "Toplanan test: 2; tamamlanan: 1" in text
    assert "Zaman aşımında çalışan test yok" in text
    assert "2.5s t.py::a" in text
    assert "Yığın dökümü" not in text
