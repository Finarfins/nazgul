"""Small pytest plugin that records exact collection and outcome manifests."""
from __future__ import annotations

import json
import os
from pathlib import Path

_collected: list[str] = []
_outcomes: dict[str, str] = {}
_collection_failures: dict[str, str] = {}

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
