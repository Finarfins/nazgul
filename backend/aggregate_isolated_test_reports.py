#!/usr/bin/env python3
"""Fail closed unless real shard reports exactly cover canonical collection."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any, Mapping

from run_isolated_tests import ALLOWED_TERMINAL_OUTCOMES, SUCCESSFUL_TERMINAL_OUTCOMES


def _string_list(payload: Mapping[str, Any], key: str, source: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{source}: {key} must be a string list")
    if len(value) != len(set(value)):
        raise ValueError(f"{source}: {key} contains duplicates")
    return value


def _skip_exceptions(payload: Mapping[str, Any]) -> dict[str, str]:
    if payload.get("version") != 1:
        raise ValueError("non-twin skip exception version must be 1")
    entries = payload.get("exceptions")
    if not isinstance(entries, list):
        raise ValueError("non-twin skip exceptions must be a list")

    exceptions: dict[str, str] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"non-twin skip exception {index} must be an object")
        nodeid = entry.get("nodeid")
        reason = entry.get("reason")
        if not isinstance(nodeid, str) or not nodeid.strip():
            raise ValueError(f"non-twin skip exception {index} has no nodeid")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"non-twin skip exception {nodeid} has no reason")
        if nodeid in exceptions:
            raise ValueError(f"duplicate non-twin skip exception: {nodeid}")
        exceptions[nodeid] = reason
    return exceptions


def aggregate_report_payloads(
    canonical: Mapping[str, Any],
    shard_reports: list[Mapping[str, Any]],
    *,
    expected_shards: int,
) -> dict[str, Any]:
    if len(shard_reports) != expected_shards:
        raise ValueError(
            f"expected {expected_shards} shard reports, received {len(shard_reports)}"
        )

    canonical_files = _string_list(canonical, "files", "canonical manifest")
    canonical_nodes = _string_list(canonical, "nodes", "canonical manifest")
    expected_files = set(canonical_files)
    expected_nodes = set(canonical_nodes)
    if not expected_files or not expected_nodes:
        raise ValueError("canonical manifest must contain files and nodes")

    seen_files: set[str] = set()
    seen_nodes: set[str] = set()
    combined_manifest: dict[str, str] = {}

    for shard_index, payload in enumerate(shard_reports):
        source = f"shard {shard_index}"
        files = _string_list(payload, "files", source)
        passed_files = _string_list(payload, "passed_files", source)
        failed_files = _string_list(payload, "failed_files", source)
        if failed_files:
            raise ValueError(f"{source}: failed files present: {', '.join(failed_files)}")
        if set(passed_files) != set(files):
            raise ValueError(f"{source}: passed_files does not exactly match files")

        duplicate_files = sorted(seen_files.intersection(files))
        if duplicate_files:
            raise ValueError(f"files appear in multiple shards: {', '.join(duplicate_files)}")
        seen_files.update(files)

        manifest = payload.get("manifest")
        if not isinstance(manifest, dict) or not all(
            isinstance(nodeid, str) and isinstance(outcome, str)
            for nodeid, outcome in manifest.items()
        ):
            raise ValueError(f"{source}: manifest must map node ids to outcomes")

        for nodeid, outcome in manifest.items():
            if outcome not in SUCCESSFUL_TERMINAL_OUTCOMES:
                raise ValueError(f"{source}: disallowed terminal outcome {nodeid}={outcome}")
            if nodeid in seen_nodes:
                raise ValueError(f"node appears in multiple shards: {nodeid}")
            seen_nodes.add(nodeid)
            combined_manifest[nodeid] = outcome

    missing_files = sorted(expected_files - seen_files)
    extra_files = sorted(seen_files - expected_files)
    if missing_files or extra_files:
        raise ValueError(
            f"shard file union mismatch; missing={missing_files}, extra={extra_files}"
        )

    missing_nodes = sorted(expected_nodes - seen_nodes)
    extra_nodes = sorted(seen_nodes - expected_nodes)
    if missing_nodes or extra_nodes:
        raise ValueError(
            f"shard node union mismatch; missing={missing_nodes}, extra={extra_nodes}"
        )

    outcome_counts = Counter(combined_manifest.values())
    return {
        "files": sorted(seen_files),
        "manifest": dict(sorted(combined_manifest.items())),
        "outcomes": dict(sorted(outcome_counts.items())),
    }


def reconcile_sqlite_skips_with_postgresql(
    sqlite_manifest: Mapping[str, str],
    postgresql_report: Mapping[str, Any],
    exception_payload: Mapping[str, Any],
) -> dict[str, int]:
    postgresql_manifest = postgresql_report.get("manifest")
    if not isinstance(postgresql_manifest, dict) or not all(
        isinstance(nodeid, str) and isinstance(outcome, str)
        for nodeid, outcome in postgresql_manifest.items()
    ):
        raise ValueError("PostgreSQL report manifest must map node ids to outcomes")
    invalid = sorted(
        f"{nodeid}={outcome}"
        for nodeid, outcome in postgresql_manifest.items()
        if outcome not in ALLOWED_TERMINAL_OUTCOMES
    )
    if invalid:
        raise ValueError("invalid PostgreSQL terminal outcome: " + ", ".join(invalid))

    exceptions = _skip_exceptions(exception_payload)
    sqlite_skips = {
        nodeid for nodeid, outcome in sqlite_manifest.items() if outcome == "skipped"
    }
    postgresql_passes = {
        nodeid
        for nodeid, outcome in postgresql_manifest.items()
        if outcome == "passed"
    }

    stale_exceptions = sorted(exceptions.keys() - sqlite_skips)
    if stale_exceptions:
        raise ValueError(
            "non-twin skip exception is stale: " + ", ".join(stale_exceptions)
        )
    redundant_exceptions = sorted(exceptions.keys() & postgresql_passes)
    if redundant_exceptions:
        raise ValueError(
            "non-twin skip exception also passed in PostgreSQL: "
            + ", ".join(redundant_exceptions)
        )

    unreconciled = sorted(sqlite_skips - postgresql_passes - exceptions.keys())
    if unreconciled:
        details = [
            f"{nodeid}={postgresql_manifest.get(nodeid, 'missing')}"
            for nodeid in unreconciled
        ]
        raise ValueError(
            "SQLite-skipped node did not pass in backend-postgresql: "
            + ", ".join(details)
        )

    return {
        "sqlite_skipped": len(sqlite_skips),
        "postgresql_passed": len(sqlite_skips & postgresql_passes),
        "exceptions": len(exceptions),
    }


def _read_json_object(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: JSON root must be an object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("canonical_manifest", type=Path)
    parser.add_argument("shard_reports", nargs="+", type=Path)
    parser.add_argument("--postgresql-report", type=Path)
    parser.add_argument("--skip-exceptions", type=Path)
    parser.add_argument("--expected-shards", type=int, default=4)
    parser.add_argument("--aggregate-output", type=Path)
    args = parser.parse_args()

    try:
        canonical = _read_json_object(args.canonical_manifest)
        shards = [_read_json_object(path) for path in args.shard_reports]
        aggregate = aggregate_report_payloads(
            canonical,
            shards,
            expected_shards=args.expected_shards,
        )
        if bool(args.postgresql_report) != bool(args.skip_exceptions):
            raise ValueError(
                "--postgresql-report ve --skip-exceptions birlikte verilmelidir"
            )
        reconciliation = None
        if args.postgresql_report is not None and args.skip_exceptions is not None:
            reconciliation = reconcile_sqlite_skips_with_postgresql(
                aggregate["manifest"],
                _read_json_object(args.postgresql_report),
                _read_json_object(args.skip_exceptions),
            )
        if args.aggregate_output is not None:
            args.aggregate_output.write_text(
                json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    except ValueError as exc:
        print(f"SHARD AGGREGATION RED: {exc}", file=sys.stderr)
        return 1

    print(
        "SHARD AGGREGATION GREEN: "
        f"{len(aggregate['files'])} files, {len(aggregate['manifest'])} nodes, "
        f"outcomes={aggregate['outcomes']}"
    )
    if reconciliation is not None:
        print(
            "CROSS-JOB SKIP RECONCILIATION GREEN: "
            f"sqlite_skipped={reconciliation['sqlite_skipped']}, "
            f"postgresql_passed={reconciliation['postgresql_passed']}, "
            f"exceptions={reconciliation['exceptions']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
