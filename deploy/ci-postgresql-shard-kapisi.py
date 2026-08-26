#!/usr/bin/env python3
"""PostgreSQL CI shard wiring'ini workflow YAML'ından fail-closed ölçer."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any


EXPECTED_SHARDS = [0, 1, 2, 3]


def fail(message: str) -> int:
    print(f"K7 PostgreSQL shard kapısı KIRMIZI: {message}")
    return 1


def named_step(job: dict[str, Any], name: str) -> dict[str, Any] | None:
    steps = job.get("steps")
    if not isinstance(steps, list):
        return None
    for step in steps:
        if isinstance(step, dict) and step.get("name") == name:
            return step
    return None


def active_script(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} run betiği yok")
    return "\n".join(
        line for line in value.splitlines() if not line.lstrip().startswith("#")
    )


def string_needs(value: object) -> set[str]:
    if isinstance(value, str) and value:
        return {value}
    if isinstance(value, list) and all(isinstance(item, str) and item for item in value):
        return set(value)
    return set()


def main() -> int:
    if len(sys.argv) != 2:
        return fail("kullanım: ci-postgresql-shard-kapisi.py <ci.yml>")

    try:
        import yaml
    except Exception as exc:
        return fail(f"YAML parser kullanılamıyor: {exc}")

    try:
        workflow = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
    except Exception as exc:
        return fail(f"workflow YAML parse edilemedi: {exc}")
    if not isinstance(workflow, dict) or not isinstance(workflow.get("jobs"), dict):
        return fail("jobs mapping yok")
    jobs: dict[str, Any] = workflow["jobs"]

    pg_job = jobs.get("backend-postgresql")
    if not isinstance(pg_job, dict):
        return fail("backend-postgresql job'ı yok")
    strategy = pg_job.get("strategy")
    if not isinstance(strategy, dict) or strategy.get("fail-fast") is not False:
        return fail("backend-postgresql strategy.fail-fast false değil")
    matrix = strategy.get("matrix")
    if not isinstance(matrix, dict) or matrix.get("shard-index") != EXPECTED_SHARDS:
        return fail(
            "matrix.shard-index tam [0, 1, 2, 3] değil: "
            f"{matrix.get('shard-index') if isinstance(matrix, dict) else matrix!r}"
        )
    max_parallel = strategy.get("max-parallel")
    if max_parallel is not None and (
        not isinstance(max_parallel, int) or max_parallel < len(EXPECTED_SHARDS)
    ):
        return fail(f"max-parallel dört shard'ı eşzamanlı çalıştırmıyor: {max_parallel!r}")

    run_step = named_step(
        pg_job, "Run PostgreSQL integration gates (isolated, fresh schema per file)"
    )
    if run_step is None:
        return fail("PostgreSQL integration adımı yok")
    env = run_step.get("env")
    if not isinstance(env, dict):
        return fail("PostgreSQL integration env mapping yok")
    if env.get("PG_SHARD_INDEX") != "${{ matrix.shard-index }}":
        return fail("PG_SHARD_INDEX matrix.shard-index'ten gelmiyor")
    if env.get("PG_SHARD_COUNT") != "4":
        return fail(f"PG_SHARD_COUNT 4 değil: {env.get('PG_SHARD_COUNT')!r}")

    try:
        run_script = active_script(run_step.get("run"), "PostgreSQL integration")
    except ValueError as exc:
        return fail(str(exc))
    if "--workers" in run_script:
        return fail("PostgreSQL job içinde --workers kullanılıyor")
    required_patterns = {
        "#44 select_test_shard importu": r"^from run_isolated_tests import select_test_shard$",
        "#44 select_test_shard çağrısı": r"^\s*for path in select_test_shard\($",
        "matrix shard index aktarımı": r"^\s*shard_index=shard_index,$",
        "dört shard sayısı aktarımı": r"^\s*shard_count=shard_count,$",
    }
    for label, pattern in required_patterns.items():
        if re.search(pattern, run_script, flags=re.MULTILINE) is None:
            return fail(f"{label} çalışan kodda yok")
    if 'python -m pytest --collect-only -q -p isolated_test_reporter "${all_files[@]}"' not in run_script:
        return fail("bağımsız PostgreSQL canonical node manifesti üretilmiyor")
    if 'python merge_postgresql_test_reports.py "$report_dir"' not in run_script:
        return fail("mevcut PostgreSQL report merger shard raporunu üretmiyor")

    loop_match = re.search(
        r'^for f in "\$\{files\[@\]\}"; do$'
        r'(?P<body>.*?)'
        r'^done$',
        run_script,
        flags=re.MULTILINE | re.DOTALL,
    )
    if loop_match is None:
        return fail("dosya başına PostgreSQL çalışma döngüsü yok")
    loop_body = loop_match.group("body")
    reset_at = loop_body.find("reset_schema")
    pytest_at = loop_body.find('python -m pytest -q -p isolated_test_reporter "$f"')
    if reset_at < 0 or pytest_at < 0 or reset_at >= pytest_at:
        return fail("her dosyada reset_schema, ayrı pytest sürecinden önce değil")

    upload = named_step(pg_job, "Upload PostgreSQL shard node reports")
    upload_with = upload.get("with") if isinstance(upload, dict) else None
    expected_artifact = "backend-test-postgresql-${{ matrix.shard-index }}-${{ github.sha }}"
    if not isinstance(upload_with, dict):
        return fail("PostgreSQL shard artifact upload adımı yok")
    if upload_with.get("name") != expected_artifact:
        return fail(f"PostgreSQL artifact adı shard-index taşımıyor: {upload_with.get('name')!r}")
    if upload_with.get("path") != "backend/postgresql-shard-report.json":
        return fail("PostgreSQL artifact mevcut report formatındaki shard raporunu yüklemiyor")
    if upload_with.get("if-no-files-found") != "error":
        return fail("boş PostgreSQL shard artifact fail-closed değil")

    canonical_upload = named_step(pg_job, "Upload canonical PostgreSQL manifest")
    canonical_with = canonical_upload.get("with") if isinstance(canonical_upload, dict) else None
    if not isinstance(canonical_upload, dict) or canonical_upload.get("if") != "matrix.shard-index == 0":
        return fail("canonical PostgreSQL manifest yalnız shard 0'dan yayımlanmıyor")
    if not isinstance(canonical_with, dict):
        return fail("canonical PostgreSQL manifest artifact adımı yok")
    if canonical_with.get("name") != "backend-test-postgresql-canonical-${{ github.sha }}":
        return fail("canonical PostgreSQL manifest artifact adı sabit değil")
    if canonical_with.get("if-no-files-found") != "error":
        return fail("canonical PostgreSQL manifest artifact fail-closed değil")

    aggregate_job = jobs.get("backend-quality-aggregate")
    if not isinstance(aggregate_job, dict):
        return fail("backend-quality-aggregate job'ı yok")
    if "backend-postgresql" not in string_needs(aggregate_job.get("needs")):
        return fail("aggregate job dört backend-postgresql matrix sonucunu beklemiyor")
    merge_step = named_step(aggregate_job, "Require exact PostgreSQL disjoint shard coverage")
    try:
        merge_script = active_script(
            merge_step.get("run") if isinstance(merge_step, dict) else None,
            "PostgreSQL disjoint-union aggregation",
        )
    except ValueError as exc:
        return fail(str(exc))
    if "python aggregate_isolated_test_reports.py" not in merge_script:
        return fail("#44 disjoint-union aggregator PostgreSQL shard'larında kullanılmıyor")
    if "backend-test-postgresql-canonical-${{ github.sha }}/postgresql-canonical-manifest.json" not in merge_script:
        return fail("PostgreSQL aggregation bağımsız canonical manifest kullanmıyor")
    for shard in EXPECTED_SHARDS:
        expected_report = (
            f"backend-test-postgresql-{shard}-${{{{ github.sha }}}}/"
            "postgresql-shard-report.json"
        )
        if expected_report not in merge_script:
            return fail(f"aggregate girdisinde PostgreSQL shard {shard} raporu yok")
    if "--expected-shards 4" not in merge_script:
        return fail("PostgreSQL aggregate expected-shards kapısı 4 değil")
    if '--aggregate-output "$RUNNER_TEMP/postgresql-test-report.json"' not in merge_script:
        return fail("birleşik PostgreSQL rapor yolu sabit değil")

    coverage_step = named_step(aggregate_job, "Require exact disjoint shard coverage")
    try:
        coverage_script = active_script(
            coverage_step.get("run") if isinstance(coverage_step, dict) else None,
            "disjoint-union aggregation",
        )
    except ValueError as exc:
        return fail(str(exc))
    if '--postgresql-report "$RUNNER_TEMP/postgresql-test-report.json"' not in coverage_script:
        return fail("reconciliation birleşik PostgreSQL shard raporunu kullanmıyor")

    print(
        "K7 PostgreSQL shard kapısı YEŞİL: 4 bağımsız job; #44 seçicisi; "
        "dosya başına reset+ayrı pytest; canonical tam-küme disjoint-union ve reconciliation"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
