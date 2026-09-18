"""Small pytest plugin that records exact collection and outcome manifests.

H41: the isolated runner kills a file at its ``--timeout``; the final report is
written only at session finish, so a killed file used to leave nothing but an
exit code. Two optional side channels make the timeout explainable:

* ``ISOLATED_TEST_PROGRESS``: one JSON line per collection/start/finish event,
  appended as it happens, so the runner knows which test was still running.
* ``ISOLATED_TEST_DEADLINE`` (epoch seconds) + ``ISOLATED_TEST_TRACEBACK``:
  faulthandler dumps every thread's stack into that file at the deadline, which
  the runner sets a few seconds before it kills the child.
"""
from __future__ import annotations

import faulthandler
import json
import os
from pathlib import Path
import time

_collected: list[str] = []
_outcomes: dict[str, str] = {}
_collection_failures: dict[str, str] = {}
_traceback_file = None


def _record_progress(event: dict[str, object]) -> None:
    progress_path = os.environ.get("ISOLATED_TEST_PROGRESS")
    if not progress_path:
        return
    with open(progress_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({**event, "time": time.time()}, ensure_ascii=False) + "\n")


def pytest_configure(config) -> None:
    global _traceback_file
    deadline = os.environ.get("ISOLATED_TEST_DEADLINE")
    target = os.environ.get("ISOLATED_TEST_TRACEBACK")
    if not deadline or not target:
        return
    remaining = float(deadline) - time.time()
    if remaining <= 0:
        return
    _traceback_file = open(target, "w", encoding="utf-8")
    faulthandler.dump_traceback_later(remaining, exit=False, file=_traceback_file)


def pytest_unconfigure(config) -> None:
    global _traceback_file
    if _traceback_file is None:
        return
    faulthandler.cancel_dump_traceback_later()
    _traceback_file.close()
    _traceback_file = None


def pytest_runtest_logstart(nodeid, location) -> None:
    _record_progress({"event": "start", "nodeid": nodeid})


def pytest_runtest_logfinish(nodeid, location) -> None:
    _record_progress({"event": "finish", "nodeid": nodeid})

_OUTCOME_PRIORITY = {
    "passed": 1,
    "skipped": 2,
    "failed": 3,
    "setup-failed": 3,
    "teardown-failed": 3,
    "collection-failed": 3,
}


def _record_outcome(nodeid: str, outcome: str) -> None:
    previous = _outcomes.get(nodeid)
    if previous is None or _OUTCOME_PRIORITY[outcome] >= _OUTCOME_PRIORITY[previous]:
        _outcomes[nodeid] = outcome


def pytest_collectreport(report) -> None:
    if report.failed:
        nodeid = report.nodeid or "<collection>"
        _collection_failures[nodeid] = "collection-failed"


def pytest_collection_finish(session) -> None:
    global _collected
    _collected = [item.nodeid for item in session.items]
    _record_progress({"event": "collected", "count": len(_collected)})


def pytest_runtest_logreport(report) -> None:
    if report.failed:
        outcome = "failed" if report.when == "call" else f"{report.when}-failed"
        _record_outcome(report.nodeid, outcome)
    elif report.skipped:
        _record_outcome(report.nodeid, "skipped")
    elif report.when == "call":
        _record_outcome(report.nodeid, "passed")


def pytest_sessionfinish(session, exitstatus) -> None:
    report_path = os.environ.get("ISOLATED_TEST_REPORT")
    if not report_path:
        return
    collected = list(_collected)
    for nodeid in _collection_failures:
        if nodeid not in collected:
            collected.append(nodeid)
    outcomes = {**_outcomes, **_collection_failures}
    report_file = Path(report_path)
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(
        json.dumps(
            {"collected": collected, "outcomes": outcomes},
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
