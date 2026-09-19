"""Isolated runner meta-tests: collection, canonical manifest, shard aggregation.

H41: worker execution / failure attribution / output tests live in
``test_isolated_test_runner_yurutme.py`` so neither file sits at the runner's
180 s per-file cap under ``--workers 4`` (see that file's docstring).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
import json
from pathlib import Path

import pytest

from aggregate_isolated_test_reports import (
    aggregate_report_payloads,
    reconcile_sqlite_skips_with_postgresql,
)
from merge_postgresql_test_reports import merge_postgresql_report_payloads
from run_isolated_tests import (
    canonical_collection_manifest,
    collect_canonical_manifest_single_process,
    execution_manifest,
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
