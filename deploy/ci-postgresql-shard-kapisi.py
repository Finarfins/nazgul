#!/usr/bin/env python3
"""PostgreSQL ve SQLite CI shard wiring ve artifact politikalarini workflow YAML'indan fail-closed olcer."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any


EXPECTED_SHARDS = [0, 1, 2, 3]


def fail(message: str) -> int:
    print(f"K7 Shard ve artifact kapisi KIRMIZI: {message}")
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
        raise ValueError(f"{label} run betigi yok")
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
        return fail("kullanim: ci-postgresql-shard-kapisi.py <ci.yml>")

    try:
        import yaml
    except Exception as exc:
        return fail(f"YAML parser kullanilamiyor: {exc}")

    try:
        workflow = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
    except Exception as exc:
        return fail(f"workflow YAML parse edilemedi: {exc}")
    if not isinstance(workflow, dict) or not isinstance(workflow.get("jobs"), dict):
        return fail("jobs mapping yok")
    jobs: dict[str, Any] = workflow["jobs"]

    # 1. PostgreSQL lane matrix and execution pin
    pg_job = jobs.get("backend-postgresql")
    if not isinstance(pg_job, dict):
        return fail("backend-postgresql job'i yok")
    strategy = pg_job.get("strategy")
    if not isinstance(strategy, dict) or strategy.get("fail-fast") is not False:
        return fail("backend-postgresql strategy.fail-fast false degil")
    matrix = strategy.get("matrix")
    if not isinstance(matrix, dict) or matrix.get("shard-index") != EXPECTED_SHARDS:
        return fail(
            "backend-postgresql matrix.shard-index tam [0, 1, 2, 3] degil: "
            f"{matrix.get('shard-index') if isinstance(matrix, dict) else matrix!r}"
        )
    max_parallel = strategy.get("max-parallel")
    if max_parallel is not None and (
        not isinstance(max_parallel, int) or max_parallel < len(EXPECTED_SHARDS)
    ):
        return fail(f"backend-postgresql max-parallel dort shard'i eszamanli calistirmiyor: {max_parallel!r}")

    run_step = named_step(
        pg_job, "Run PostgreSQL integration gates (isolated, fresh schema per file)"
    )
    if run_step is None:
        return fail("PostgreSQL integration adimi yok")
    env = run_step.get("env")
    if not isinstance(env, dict):
        return fail("PostgreSQL integration env mapping yok")
    if env.get("PG_SHARD_INDEX") != "${{ matrix.shard-index }}":
        return fail("PG_SHARD_INDEX matrix.shard-index'ten gelmiyor")
    if env.get("PG_SHARD_COUNT") != "4":
        return fail(f"PG_SHARD_COUNT 4 degil: {env.get('PG_SHARD_COUNT')!r}")

    try:
        run_script = active_script(run_step.get("run"), "PostgreSQL integration")
    except ValueError as exc:
        return fail(str(exc))
    if "--workers" in run_script:
        return fail("PostgreSQL job icinde --workers kullaniliyor")
    required_patterns = {
        "#44 select_test_shard importu": r"^from run_isolated_tests import select_test_shard$",
        "#44 select_test_shard cagrisi": r"^\s*for path in select_test_shard\($",
        "matrix shard index aktarimi": r"^\s*shard_index=shard_index,$",
        "dort shard sayisi aktarimi": r"^\s*shard_count=shard_count,$",
    }
    for label, pattern in required_patterns.items():
        if re.search(pattern, run_script, flags=re.MULTILINE) is None:
            return fail(f"{label} calisan kodda yok")
    if 'python -m pytest --collect-only -q -p isolated_test_reporter "${all_files[@]}"' not in run_script:
        return fail("bagimsiz PostgreSQL canonical node manifesti uretilmiyor")
    if 'python merge_postgresql_test_reports.py "$report_dir"' not in run_script:
        return fail("mevcut PostgreSQL report merger shard raporunu uretmiyor")

    loop_match = re.search(
        r'^for f in "\$\{files\[@\]\}"; do$'
        r'(?P<body>.*?)'
        r'^done$',
        run_script,
        flags=re.MULTILINE | re.DOTALL,
    )
    if loop_match is None:
        return fail("dosya basina PostgreSQL calisma dongusu yok")
    loop_body = loop_match.group("body")
    reset_at = loop_body.find("reset_schema")
    pytest_at = loop_body.find('python -m pytest -q -p isolated_test_reporter "$f"')
    if reset_at < 0 or pytest_at < 0 or reset_at >= pytest_at:
        return fail("her dosyada reset_schema, ayri pytest surecinden once degil")

    upload = named_step(pg_job, "Upload PostgreSQL shard node reports")
    upload_with = upload.get("with") if isinstance(upload, dict) else None
    expected_artifact = "backend-test-postgresql-${{ matrix.shard-index }}-${{ github.sha }}"
    if not isinstance(upload_with, dict):
        return fail("PostgreSQL shard artifact upload adimi yok")
    if upload_with.get("name") != expected_artifact:
        return fail(f"PostgreSQL artifact adi shard-index tasimiyor: {upload_with.get('name')!r}")
    if upload_with.get("path") != "backend/postgresql-shard-report.json":
        return fail("PostgreSQL artifact mevcut report formatindaki shard raporunu yuklemiyor")
    if upload_with.get("if-no-files-found") != "error":
        return fail("bos PostgreSQL shard artifact fail-closed degil")

    canonical_upload = named_step(pg_job, "Upload canonical PostgreSQL manifest")
    canonical_with = canonical_upload.get("with") if isinstance(canonical_upload, dict) else None
    if not isinstance(canonical_upload, dict) or canonical_upload.get("if") != "matrix.shard-index == 0":
        return fail("canonical PostgreSQL manifest yalniz shard 0'dan yayimlanmiyor")
    if not isinstance(canonical_with, dict):
        return fail("canonical PostgreSQL manifest artifact adimi yok")
    if canonical_with.get("name") != "backend-test-postgresql-canonical-${{ github.sha }}":
        return fail("canonical PostgreSQL manifest artifact adi sabit degil")
    if canonical_with.get("if-no-files-found") != "error":
        return fail("canonical PostgreSQL manifest artifact fail-closed degil")

    # 2. SQLite lane matrix and artifact symmetry pin
    quality_job = jobs.get("backend-quality")
    if not isinstance(quality_job, dict):
        return fail("backend-quality job'i yok")
    q_strategy = quality_job.get("strategy")
    if not isinstance(q_strategy, dict) or q_strategy.get("fail-fast") is not False:
        return fail("backend-quality strategy.fail-fast false degil")
    q_matrix = q_strategy.get("matrix")
    if not isinstance(q_matrix, dict) or q_matrix.get("shard-index") != EXPECTED_SHARDS:
        return fail(
            "backend-quality matrix.shard-index tam [0, 1, 2, 3] degil: "
            f"{q_matrix.get('shard-index') if isinstance(q_matrix, dict) else q_matrix!r}"
        )
    q_max_parallel = q_strategy.get("max-parallel")
    if q_max_parallel is not None and (
        not isinstance(q_max_parallel, int) or q_max_parallel < len(EXPECTED_SHARDS)
    ):
        return fail(f"backend-quality max-parallel dort shard'i eszamanli calistirmiyor: {q_max_parallel!r}")

    q_run_step = named_step(quality_job, "Run every active backend test in isolation")
    if q_run_step is None:
        return fail("backend-quality test kosum adimi yok")
    q_run_cmd = q_run_step.get("run", "")
    if not isinstance(q_run_cmd, str) or "--shard-index ${{ matrix.shard-index }}" not in q_run_cmd or "--shard-count 4" not in q_run_cmd:
        return fail("backend-quality test adimi matrix shard-index ve shard-count 4 aktarmiyor")

    q_upload = named_step(quality_job, "Upload isolated-test shard report")
    q_upload_with = q_upload.get("with") if isinstance(q_upload, dict) else None
    if not isinstance(q_upload_with, dict):
        return fail("backend-quality shard artifact upload adimi yok")
    if q_upload_with.get("name") != "backend-test-shard-${{ matrix.shard-index }}-${{ github.sha }}":
        return fail(f"backend-quality artifact adi shard-index tasimiyor: {q_upload_with.get('name')!r}")
    if q_upload_with.get("path") != "backend/isolated-test-report.json":
        return fail("backend-quality artifact isolated-test-report.json yolunu yuklemiyor")
    if q_upload_with.get("if-no-files-found") != "error":
        return fail("bos SQLite shard artifact fail-closed degil (if-no-files-found: error eksik)")

    canonical_job = jobs.get("backend-quality-canonical")
    if not isinstance(canonical_job, dict):
        return fail("backend-quality-canonical job'i yok")
    c_upload = named_step(canonical_job, "Upload canonical isolated-test manifest")
    c_upload_with = c_upload.get("with") if isinstance(c_upload, dict) else None
    if not isinstance(c_upload_with, dict):
        return fail("canonical isolated-test manifest upload adimi yok")
    if c_upload_with.get("name") != "backend-test-canonical-${{ github.sha }}":
        return fail(f"canonical isolated-test manifest artifact adi sabit degil: {c_upload_with.get('name')!r}")
    if c_upload_with.get("path") != "backend/canonical-test-manifest.json":
        return fail("canonical isolated-test manifest canonical-test-manifest.json yuklemiyor")
    if c_upload_with.get("if-no-files-found") != "error":
        return fail("bos SQLite canonical manifest artifact fail-closed degil (if-no-files-found: error eksik)")

    # 3. Aggregation job wiring
    aggregate_job = jobs.get("backend-quality-aggregate")
    if not isinstance(aggregate_job, dict):
        return fail("backend-quality-aggregate job'i yok")
    agg_needs = string_needs(aggregate_job.get("needs"))
    if "backend-postgresql" not in agg_needs:
        return fail("aggregate job dort backend-postgresql matrix sonucunu beklemiyor")
    if "backend-quality" not in agg_needs:
        return fail("aggregate job dort backend-quality matrix sonucunu beklemiyor")
    if "backend-quality-canonical" not in agg_needs:
        return fail("aggregate job backend-quality-canonical sonucunu beklemiyor")

    merge_step = named_step(aggregate_job, "Require exact PostgreSQL disjoint shard coverage")
    try:
        merge_script = active_script(
            merge_step.get("run") if isinstance(merge_step, dict) else None,
            "PostgreSQL disjoint-union aggregation",
        )
    except ValueError as exc:
        return fail(str(exc))
    if "python aggregate_isolated_test_reports.py" not in merge_script:
        return fail("#44 disjoint-union aggregator PostgreSQL shard'larinda kullanilmiyor")
    if "backend-test-postgresql-canonical-${{ github.sha }}/postgresql-canonical-manifest.json" not in merge_script:
        return fail("PostgreSQL aggregation bagimsiz canonical manifest kullanmiyor")
    for shard in EXPECTED_SHARDS:
        expected_report = (
            f"backend-test-postgresql-{shard}-${{{{ github.sha }}}}/"
            "postgresql-shard-report.json"
        )
        if expected_report not in merge_script:
            return fail(f"aggregate girdisinde PostgreSQL shard {shard} raporu yok")
    if "--expected-shards 4" not in merge_script:
        return fail("PostgreSQL aggregate expected-shards kapisi 4 degil")
    if '--aggregate-output "$RUNNER_TEMP/postgresql-test-report.json"' not in merge_script:
        return fail("birlesik PostgreSQL rapor yolu sabit degil")

    coverage_step = named_step(aggregate_job, "Require exact disjoint shard coverage")
    try:
        coverage_script = active_script(
            coverage_step.get("run") if isinstance(coverage_step, dict) else None,
            "disjoint-union aggregation",
        )
    except ValueError as exc:
        return fail(str(exc))
    if '--postgresql-report "$RUNNER_TEMP/postgresql-test-report.json"' not in coverage_script:
        return fail("reconciliation birlesik PostgreSQL shard raporunu kullanmiyor")
    if "backend-test-canonical-${{ github.sha }}/canonical-test-manifest.json" not in coverage_script:
        return fail("reconciliation canonical SQLite test manifestini kullanmiyor")
    for shard in EXPECTED_SHARDS:
        expected_shard_report = (
            f"backend-test-shard-{shard}-${{{{ github.sha }}}}/"
            "isolated-test-report.json"
        )
        if expected_shard_report not in coverage_script:
            return fail(f"aggregate girdisinde SQLite shard {shard} raporu yok")

    print(
        "K7 Shard ve artifact kapisi YESIL: PostgreSQL ve SQLite 4'er bagimsiz job; "
        "if-no-files-found: error simetrisi; canonical tam-kume disjoint-union ve reconciliation"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())