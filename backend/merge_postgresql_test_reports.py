#!/usr/bin/env python3
"""Merge per-file PostgreSQL pytest reporter output into one CI artifact."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any, Mapping

from run_isolated_tests import terminal_outcome_errors


def merge_postgresql_report_payloads(
    reports: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    if not reports:
        raise ValueError("no PostgreSQL pytest reports found")

    combined: dict[str, str] = {}
    files: list[str] = []
    for source, payload in sorted(reports.items()):
        collected = payload.get("collected")
        outcomes = payload.get("outcomes")
        if not isinstance(collected, list) or not all(
            isinstance(nodeid, str) for nodeid in collected
        ):
            raise ValueError(f"{source}: collected must be a string list")
        if not collected:
            raise ValueError(f"{source}: collected no PostgreSQL test nodes")
        if not isinstance(outcomes, dict) or not all(
            isinstance(nodeid, str) and isinstance(outcome, str)
            for nodeid, outcome in outcomes.items()
        ):
            raise ValueError(f"{source}: outcomes must map node ids to outcomes")

        errors = terminal_outcome_errors(
            collected,
            dict(outcomes),
            successful_only=False,
        )
        if errors:
            raise ValueError(f"{source}: {'; '.join(errors)}")

        for nodeid in collected:
            if nodeid in combined:
                raise ValueError(f"PostgreSQL node appears in multiple reports: {nodeid}")
            combined[nodeid] = outcomes[nodeid]
        files.append(source)

    counts = Counter(combined.values())
    return {
        "files": files,
        "passed_files": files,
        "failed_files": [],
        "manifest": dict(sorted(combined.items())),
        "outcomes": dict(sorted(counts.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report_directory", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    try:
        report_paths = sorted(args.report_directory.rglob("*.json"))
        reports = {
            path.relative_to(args.report_directory).as_posix(): json.loads(
                path.read_text(encoding="utf-8")
            )
            for path in report_paths
        }
        if not all(isinstance(payload, dict) for payload in reports.values()):
            raise ValueError("PostgreSQL report roots must be objects")
        merged = merge_postgresql_report_payloads(reports)
        args.output.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"POSTGRESQL REPORT RED: {exc}", file=sys.stderr)
        return 1

    print(
        "POSTGRESQL REPORT GREEN: "
        f"{len(merged['files'])} files, {len(merged['manifest'])} nodes, "
        f"outcomes={merged['outcomes']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
