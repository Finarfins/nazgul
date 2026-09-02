"""CI pipeline, required check context manifest, and shard gate validation tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
MANIFEST_FILE = REPO_ROOT / "deploy" / "ci-required-contexts.json"
CONTEXT_GATE = REPO_ROOT / "deploy" / "ci-gerekli-baglam-kapisi.py"
SHARD_GATE = REPO_ROOT / "deploy" / "ci-postgresql-shard-kapisi.py"
NEEDS_GATE = REPO_ROOT / "deploy" / "ci-yayin-needs-kapisi.py"
CALL_SITE_GATE = REPO_ROOT / "deploy" / "ci-verify-cagri-kapisi.py"
BACKEND = REPO_ROOT / "backend"


def test_pg_test_population_exact_98() -> None:
    """PostgreSQL test population must be exactly 98 files.

    95 -> 97: tarla yazma kilitleri ikizleri (`test_farm_monoculture_postgresql.py`,
    `test_farm_reentry_enforcement_postgresql.py`, göç 20260901_0064). İkizler
    ZORUNLU: ÇKS tek ürün ve giriş yasağı yazma yolları üretim diyalektinde
    SQLite'tan farklı davranıyor. 94 -> 95 BKÜ kataloğu ikizi (göç
    20260901_0063) develop'ta zaten inmişti.

    97 -> 98: `test_uretici_kayit_defteri_postgresql.py` (Uygulama Kayıt
    Çizelgesi): genişleyen bind, NUMERIC ölçeği, TIMESTAMPTZ ve çapraz kiracı
    GERÇEK PostgreSQL üzerinde ölçülüyor.

    Bu sayaç ile `ci.yml`deki eşi birlikte artmak ZORUNDA — ikisi aynı
    popülasyonu sayıyor ve biri güncellenip diğeri unutulursa kapı kendi
    kendisiyle çelişir.
    """
    pg_glob = sorted(BACKEND.glob("test_*postgresql*.py"))
    named = [
        BACKEND / "tests" / "test_company_id_default_contract.py",
        BACKEND / "tests" / "test_ci_playwright_hazirlik.py",
    ]
    all_files = pg_glob + [p for p in named if p.exists()]
    assert len(all_files) == 98, (
        f"PostgreSQL test population changed: expected 98, got {len(all_files)}"
    )


def test_ci_workflow_has_frozen_pg_population_constant() -> None:
    """ci.yml must contain BEKLENEN_PG_DOSYA_SAYISI=98 and strict equality."""
    content = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "BEKLENEN_PG_DOSYA_SAYISI=98" in content
    assert '[ "${#all_files[@]}" -ne "$BEKLENEN_PG_DOSYA_SAYISI" ]' in content


def test_ci_required_contexts_manifest_has_exact_17_contexts() -> None:
    """Manifest must contain exactly the 17 required contexts from ruleset 21651033."""
    data = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    assert len(data) == 17
    expected = {
        "alembic-chain",
        "backend-postgresql (0)",
        "backend-postgresql (1)",
        "backend-postgresql (2)",
        "backend-postgresql (3)",
        "backend-quality (0)",
        "backend-quality (1)",
        "backend-quality (2)",
        "backend-quality (3)",
        "backend-quality-aggregate",
        "backend-quality-canonical",
        "container",
        "contract-drift",
        "durum-kaydi",
        "e2e",
        "frontend",
        "verify-image-artifact",
    }
    assert set(data) == expected


def test_ci_required_contexts_gate_passes_clean_workflow() -> None:
    """Context gate must exit 0 on clean workflow."""
    res = subprocess.run(
        [sys.executable, str(CONTEXT_GATE), str(CI_WORKFLOW), str(MANIFEST_FILE)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert "K8 CI gerekli baglam kapisi YESIL: 17 baglam" in res.stdout


def test_ci_required_contexts_gate_fails_on_mut5_removed_job(tmp_path: Path) -> None:
    """MUT-5: Job removed from jobs and publish-image.needs passes K1 but fails context gate."""
    wf = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    del wf["jobs"]["contract-drift"]
    publish_needs = wf["jobs"]["publish-image"]["needs"]
    if isinstance(publish_needs, list):
        publish_needs.remove("contract-drift")
    mutated_file = tmp_path / "ci_mut5.yml"
    mutated_file.write_text(yaml.dump(wf), encoding="utf-8")

    # K1 passes because publish-image.needs matches jobs minus publish-image
    res_k1 = subprocess.run(
        [sys.executable, str(NEEDS_GATE), str(mutated_file)],
        capture_output=True,
        text=True,
    )
    assert res_k1.returncode == 0, "K1 was expected to pass on MUT-5"

    # Context gate catches the missing job
    res_ctx = subprocess.run(
        [sys.executable, str(CONTEXT_GATE), str(mutated_file), str(MANIFEST_FILE)],
        capture_output=True,
        text=True,
    )
    assert res_ctx.returncode == 1
    assert "eksik=['contract-drift']" in res_ctx.stdout


def test_ci_required_contexts_gate_fails_on_mut6_extra_shard(tmp_path: Path) -> None:
    """MUT-6: shard-index expanded to [0,1,2,3,4] emits unexpected backend-quality (4)."""
    wf = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    wf["jobs"]["backend-quality"]["strategy"]["matrix"]["shard-index"] = [0, 1, 2, 3, 4]
    mutated_file = tmp_path / "ci_mut6.yml"
    mutated_file.write_text(yaml.dump(wf), encoding="utf-8")

    res = subprocess.run(
        [sys.executable, str(CONTEXT_GATE), str(mutated_file), str(MANIFEST_FILE)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 1
    assert "backend-quality (4)" in res.stdout


def test_ci_shard_gate_passes_clean_workflow() -> None:
    """Shard gate must exit 0 on clean workflow."""
    res = subprocess.run(
        [sys.executable, str(SHARD_GATE), str(CI_WORKFLOW)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert "K7 Shard ve artifact kapisi YESIL" in res.stdout


def test_ci_shard_gate_fails_on_mut2_empty_backend_quality_matrix(tmp_path: Path) -> None:
    """MUT-2: Emptying backend-quality axis immediately fails shard gate."""
    wf = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    wf["jobs"]["backend-quality"]["strategy"]["matrix"]["shard-index"] = []
    mutated_file = tmp_path / "ci_mut2.yml"
    mutated_file.write_text(yaml.dump(wf), encoding="utf-8")

    res = subprocess.run(
        [sys.executable, str(SHARD_GATE), str(mutated_file)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 1
    assert "backend-quality matrix.shard-index tam [0, 1, 2, 3] degil" in res.stdout


def test_ci_shard_gate_fails_on_missing_sqlite_upload_error_policy(tmp_path: Path) -> None:
    """Missing if-no-files-found: error on backend-quality upload fails shard gate."""
    wf = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    for step in wf["jobs"]["backend-quality"]["steps"]:
        if step.get("name") == "Upload isolated-test shard report":
            del step["with"]["if-no-files-found"]
    mutated_file = tmp_path / "ci_missing_upload.yml"
    mutated_file.write_text(yaml.dump(wf), encoding="utf-8")

    res = subprocess.run(
        [sys.executable, str(SHARD_GATE), str(mutated_file)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 1
    assert "bos SQLite shard artifact fail-closed degil" in res.stdout


def test_k5_call_site_gate_passes_clean_workflow() -> None:
    """Call-site YAML gate must exit 0 on the committed workflow."""
    res = subprocess.run(
        [sys.executable, str(CALL_SITE_GATE), str(CI_WORKFLOW)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert "K5 verify-image-artifact cagri yeri YESIL" in res.stdout


def test_k5_call_site_gate_fails_on_step_level_if(tmp_path: Path) -> None:
    """Step-level `if: false` skipped the script while grep-K5 stayed green."""
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    mutated = text.replace(
        "      - name: Verify loaded image identity and OCI revision\n        env:",
        "      - name: Verify loaded image identity and OCI revision\n        if: false\n        env:",
        1,
    )
    assert mutated != text
    mutated_file = tmp_path / "ci_step_if.yml"
    mutated_file.write_text(mutated, encoding="utf-8")
    res = subprocess.run(
        [sys.executable, str(CALL_SITE_GATE), str(mutated_file)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 1
    assert "verify adimi anahtar kumesi kapali degil" in res.stdout
    assert "if" in res.stdout


def test_k5_call_site_gate_fails_on_duplicate_run_key(tmp_path: Path) -> None:
    """Duplicate YAML `run:` keeps the call-site line in the file; last key wins."""
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    mutated = text.replace(
        "        run: ./deploy/artifact-imaj-kimlik-kapisi.sh\n",
        '        run: ./deploy/artifact-imaj-kimlik-kapisi.sh\n        run: "true"\n',
        1,
    )
    assert mutated != text
    mutated_file = tmp_path / "ci_dup_run.yml"
    mutated_file.write_text(mutated, encoding="utf-8")
    res = subprocess.run(
        [sys.executable, str(CALL_SITE_GATE), str(mutated_file)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 1
    assert "yinelenen YAML anahtari" in res.stdout


def test_k5_call_site_gate_fails_on_extra_bash_env(tmp_path: Path) -> None:
    """Extra BASH_ENV makes the identity script exit 0 before any comparison."""
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    mutated = text.replace(
        "          BEKLENEN_OCI_REVIZYONU: ${{ github.sha }}\n        run:",
        "          BEKLENEN_OCI_REVIZYONU: ${{ github.sha }}\n"
        "          BASH_ENV: ${{ github.workspace }}/deploy/kapi-oldur.sh\n        run:",
        1,
    )
    assert mutated != text
    mutated_file = tmp_path / "ci_bash_env.yml"
    mutated_file.write_text(mutated, encoding="utf-8")
    res = subprocess.run(
        [sys.executable, str(CALL_SITE_GATE), str(mutated_file)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 1
    assert "BASH_ENV/PATH" in res.stdout


def test_k5_call_site_gate_fails_on_extra_path_env(tmp_path: Path) -> None:
    """Extra PATH in step env allows replacing docker binary."""
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    mutated = text.replace(
        "          BEKLENEN_OCI_REVIZYONU: ${{ github.sha }}\n        run:",
        "          BEKLENEN_OCI_REVIZYONU: ${{ github.sha }}\n"
        "          PATH: /fake/bin:${{ env.PATH }}\n        run:",
        1,
    )
    assert mutated != text
    mutated_file = tmp_path / "ci_path_env.yml"
    mutated_file.write_text(mutated, encoding="utf-8")
    res = subprocess.run(
        [sys.executable, str(CALL_SITE_GATE), str(mutated_file)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 1
    assert "BASH_ENV/PATH" in res.stdout


def test_k5_call_site_gate_fails_on_wrong_run_command(tmp_path: Path) -> None:
    """Wrong run script or inline command fails the gate."""
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    mutated = text.replace(
        "        run: ./deploy/artifact-imaj-kimlik-kapisi.sh\n",
        "        run: ./deploy/fake-script.sh\n",
        1,
    )
    assert mutated != text
    mutated_file = tmp_path / "ci_wrong_run.yml"
    mutated_file.write_text(mutated, encoding="utf-8")
    res = subprocess.run(
        [sys.executable, str(CALL_SITE_GATE), str(mutated_file)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 1
    assert "TAM betik cagrisi degil" in res.stdout


def test_k5_call_site_gate_fails_on_workflow_root_env(tmp_path: Path) -> None:
    """Root-level env allows injecting BASH_ENV/PATH across all jobs."""
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    mutated = "env:\n  BASH_ENV: /fake/env\n" + text
    mutated_file = tmp_path / "ci_root_env.yml"
    mutated_file.write_text(mutated, encoding="utf-8")
    res = subprocess.run(
        [sys.executable, str(CALL_SITE_GATE), str(mutated_file)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 1
    assert "workflow kokunde env var" in res.stdout



