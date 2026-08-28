#!/usr/bin/env python3
"""Workflow job/matrix baglamlarini gerekli check manifestine karsi fail-closed olcer.

BU KAPININ OLCUGU VE OLCMEDIGI:
- OLCER: .github/workflows/ci.yml icindeki joblarin ve matrix varyasyonlarinin
  urettigi GitHub check-run baglam (context) kumesini hesaplar ve deponun
  surum kontrollu manifesti (deploy/ci-required-contexts.json) ile tam
  kume esitligini (set equality) zorlar.
  * Bir job hem jobs hem publish-image.needs ten silinirse (MUT-5): YAKALAR (eksik baglam).
  * Shard sayisi veya matrix ekseni genisletilirse (MUT-6): YAKALAR (beklenmeyen baglam).
  * Bir matrix ekseni bosaltilirsa: YAKALAR (eksik baglam).
  * publish-image gibi PRda kosmayan push-only joblari if kosuluyla haric tutar.

- OLCMEDIGI SINIR:
  Canli GitHub rulesetini (GitHub Web UI / API uzerinden yapilan degisiklikleri)
  dogrudan olcmez. GitHub APIye calisma aninda sorgu atmaz; bu nedenle canli ruleset ile
  manifest arasindaki ayrismayi goremez. Bu kapi workflow manifestsiz degisti miyi yakalar,
  canli ruleset degisti miyi degil.
"""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path
from typing import Any

PUBLISH_IF_CONDITION = "github.event_name == 'push' && github.ref == 'refs/heads/develop'"


def fail(message: str) -> int:
    print(f"K8 CI gerekli baglam kapisi KIRMIZI: {message}")
    return 1


def expand_job_contexts(job_id: str, job: dict[str, Any]) -> set[str]:
    strategy = job.get("strategy")
    if not isinstance(strategy, dict):
        return {job_id}
    matrix = strategy.get("matrix")
    if not isinstance(matrix, dict) or not matrix:
        return {job_id}

    keys = [k for k in matrix.keys() if k not in ("include", "exclude")]
    if not keys:
        return {job_id}

    dim_values = []
    for k in keys:
        vals = matrix[k]
        if not isinstance(vals, list) or not vals:
            raise ValueError(f"{job_id} matrix.{k} bos veya liste degil: {vals!r}")
        dim_values.append(vals)

    contexts: set[str] = set()
    for combo in itertools.product(*dim_values):
        combo_str = ", ".join(str(v) for v in combo)
        contexts.add(f"{job_id} ({combo_str})")
    return contexts


def main() -> int:
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        return fail("kullanim: ci-gerekli-baglam-kapisi.py <ci.yml> [manifest.json]")

    try:
        import yaml
    except Exception as exc:
        return fail(f"YAML parser kullanilamiyor: {exc}")

    workflow_path = Path(sys.argv[1]).resolve()
    manifest_path = (
        Path(sys.argv[2]).resolve()
        if len(sys.argv) == 3
        else workflow_path.parents[2] / "deploy" / "ci-required-contexts.json"
    )

    try:
        workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return fail(f"workflow YAML parse edilemedi: {exc}")

    if not isinstance(workflow, dict):
        return fail("workflow koku mapping degil")
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        return fail("jobs mapping yok veya bos")

    if not manifest_path.exists():
        return fail(f"gerekli baglam manifesti bulunamadi: {manifest_path}")

    try:
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return fail(f"manifest JSON parse edilemedi: {exc}")

    if not isinstance(manifest_data, list) or not all(
        isinstance(item, str) and item for item in manifest_data
    ):
        return fail("manifest gecerli string listesi degil")

    required_contexts = set(manifest_data)

    emitted_contexts: set[str] = set()
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            return fail(f"job {job_id} mapping degil")
        if job.get("if") == PUBLISH_IF_CONDITION or job_id == "publish-image":
            continue
        try:
            contexts = expand_job_contexts(job_id, job)
        except ValueError as exc:
            return fail(str(exc))
        emitted_contexts.update(contexts)

    if emitted_contexts != required_contexts:
        missing = sorted(required_contexts - emitted_contexts)
        unexpected = sorted(emitted_contexts - required_contexts)
        return fail(
            "workflow baglamlari gerekli baglam manifesti ile tam esit degil; "
            f"eksik={missing}, beklenmeyen={unexpected}, "
            f"beklenen_sayi={len(required_contexts)}, uretilen_sayi={len(emitted_contexts)}"
        )

    print(
        f"K8 CI gerekli baglam kapisi YESIL: {len(emitted_contexts)} baglam "
        "manifest ile tam kume esitliginde"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())