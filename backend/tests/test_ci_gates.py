"""CI pipeline, required check context manifest, and shard gate validation tests."""

from __future__ import annotations

import json
import re
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
PG_TWINS_PIN = BACKEND / "tests" / "pins" / "pg_twins.txt"
PG_OZEL = (
    "tests/test_company_id_default_contract.py",
    "tests/test_ci_playwright_hazirlik.py",
)
PG_PHANTOM = "test_h7_phantom_postgresql.py"
PG_BAYAT = "test_h7_bayat_postgresql.py"


def _pg_envanter_satirlari(yol: Path) -> list[str]:
    """Inventory lines; blank lines are a defect because ``wc -l`` counts them."""
    ham = yol.read_text(encoding="utf-8")
    assert ham.endswith("\n"), f"{yol.name} trailing newline yok; wc -l sapar"
    satirlar = ham.splitlines()
    bos = [i for i, s in enumerate(satirlar, 1) if not s.strip() or s != s.strip()]
    assert not bos, f"{yol.name} boş ya da dolgulu satır: {bos}"
    return satirlar


def _pg_disk_kumesi(kok: Path = BACKEND) -> set[str]:
    """``test_*postgresql*.py`` glob ∪ the two named specials (ci.yml ile aynı)."""
    adlar = {p.name for p in kok.glob("test_*postgresql*.py")}
    for ozel in PG_OZEL:
        if (kok / ozel).is_file():
            adlar.add(ozel)
    return adlar


def _iddia_pg_kumesi(satirlar: list[str], disk: set[str]) -> None:
    """Set equality, named. Duplicates are not collapsed away."""
    yinelenen = sorted({ad for ad in satirlar if satirlar.count(ad) > 1})
    assert not yinelenen, f"pg_twins.txt yinelenen satır: {yinelenen}"
    liste = set(satirlar)
    eklenen = sorted(disk - liste)
    eksik = sorted(liste - disk)
    assert not eklenen and not eksik, (
        "PG ikiz envanteri ayrıştı; diskte olup listede yok="
        f"{eklenen} listede olup diskte yok={eksik}"
    )


def test_pg_twins_inventory_matches_disk() -> None:
    """PostgreSQL twins are an inventory, not an integer.

    Tree-wide counters (`BEKLENEN_PG_DOSYA_SAYISI=112`) force every PR that
    adds a twin to collide on the same literal. Two parallel PRs each writing
    112→113 cannot merge without a rewrite, and the surviving number then
    matches neither justification. The pin is `tests/pins/pg_twins.txt`
    (one filename per line, sorted, plus the two specials). The gate is
    set(file) == set(glob ∪ specials). CI reads ``wc -l``, not a literal.
    """
    satirlar = _pg_envanter_satirlari(PG_TWINS_PIN)
    assert satirlar == sorted(satirlar), "pg_twins.txt sıralı olmalı"
    _iddia_pg_kumesi(satirlar, _pg_disk_kumesi())
    for ozel in PG_OZEL:
        assert ozel in satirlar, ozel


def test_pg_twin_on_disk_missing_from_list_fails_by_name() -> None:
    """Twin added to disk but not to the list is red by that filename."""
    satirlar = _pg_envanter_satirlari(PG_TWINS_PIN)
    disk = _pg_disk_kumesi() | {PG_PHANTOM}
    with pytest.raises(AssertionError) as hata:
        _iddia_pg_kumesi(satirlar, disk)
    assert PG_PHANTOM in str(hata.value)


def test_pg_twin_listed_missing_on_disk_fails_by_name(tmp_path: Path) -> None:
    """Listed but missing on disk is red by that filename."""
    satirlar = _pg_envanter_satirlari(PG_TWINS_PIN) + [PG_BAYAT]
    satirlar.sort()
    kopya = tmp_path / "pg_twins.txt"
    kopya.write_text("\n".join(satirlar) + "\n", encoding="utf-8")
    with pytest.raises(AssertionError) as hata:
        _iddia_pg_kumesi(_pg_envanter_satirlari(kopya), _pg_disk_kumesi())
    assert PG_BAYAT in str(hata.value)


def test_pg_twin_duplicate_line_fails_by_name(tmp_path: Path) -> None:
    """A duplicate inventory line is red by that filename, not by a count."""
    satirlar = _pg_envanter_satirlari(PG_TWINS_PIN)
    yinelenen = satirlar[0]
    kopya = tmp_path / "pg_twins.txt"
    kopya.write_text("\n".join(satirlar + [yinelenen]) + "\n", encoding="utf-8")
    with pytest.raises(AssertionError) as hata:
        _iddia_pg_kumesi(_pg_envanter_satirlari(kopya), _pg_disk_kumesi())
    assert yinelenen in str(hata.value)


def test_pg_population_integer_pin_is_gone() -> None:
    """The three numeric sites must not come back as literals."""
    kaynak = Path(__file__).read_text(encoding="utf-8")
    assert re.search(r"test_pg_test_population_exact_\d+", kaynak) is None
    content = CI_WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"BEKLENEN_PG_DOSYA_SAYISI=\d+", content) is None


def test_ci_workflow_reads_pg_population_from_inventory() -> None:
    """ci.yml derives the PG count from pg_twins.txt via wc -l, not a literal.

    The old third pin grepped `BEKLENEN_PG_DOSYA_SAYISI=112`. Two PRs adding
    twins collided on that literal. The inventory line count is the source;
    glob ∪ specials must equal it.
    """
    content = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "tests/pins/pg_twins.txt" in content
    assert "wc -l < tests/pins/pg_twins.txt" in content
    assert '[ "${#all_files[@]}" -ne "$BEKLENEN_PG_DOSYA_SAYISI" ]' in content
    assert re.search(r"BEKLENEN_PG_DOSYA_SAYISI=\d+", content) is None


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





# --- H25: autoload kapalı adımlarda pytest-asyncio ZORUNLU ---------------------
#
# Ölçülen kusur (PR #115, 2026-09-10): "Değişen SQLite testlerini ters sırada
# koştur" adımı PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 kurup `-p pytest_asyncio.plugin`
# vermiyordu; tests/test_import_route_body_limit.py içindeki 7 async test HER
# sırada düştü (ileri 7F/6P, ters 7F/6P). Kusur yalnız async testi olan bir dosya
# DEĞİŞTİĞİNDE görünür oluyordu. Aşağıdaki kapı, autoload'ı kapatan her adımın
# her pytest çağrısında bayrağı arar; run_isolated_tests.py'nin iki çağrı yerini
# de aynı hizada tutar.

H25_AUTOLOAD_ANAHTARI = "PYTEST_DISABLE_PLUGIN_AUTOLOAD"
H25_BAYRAK = re.compile(r"(?<!\S)-p\s+pytest_asyncio\.plugin(?!\S)")
# Komut KONUMUNDAKİ pytest: satır başı ya da `;`, `&`, `|`, `(`, `!` sonrası,
# önünde isteğe bağlı VAR=deger önekleri. `pip install pytest` içindeki çıplak
# `pytest` argüman konumunda olduğundan eşleşmez.
H25_PYTEST_CAGRISI = re.compile(
    r"(?:^|(?<=[;&|(!]))[ \t]*"
    # VAR=deger öneki; tırnaklı değer içinde boşluk olabilir
    # (DATABASE_URL="sqlite:///$RUNNER_TEMP/ters-$(basename "$dosya").db").
    r"(?:[A-Za-z_][A-Za-z0-9_]*=(?:\"[^\"\n]*\"|'[^'\n]*'|[^\s\"'])*[ \t]+)*"
    r"(?P<komut>(?:python(?:3(?:\.\d+)?)?[ \t]+-m[ \t]+pytest|pytest))(?=\s|$)"
    r"(?P<argumanlar>[^\n;&|)]*)",
    re.MULTILINE,
)


def _h25_autoload_kapali_cagrilar(wf: dict) -> list[tuple[str, str, str]]:
    """Every pytest invocation whose effective env sets the autoload-off key.

    Returns (job_id, step_name, invocation_text). Env is merged workflow →
    job → step; a key set at any level counts, because pytest reads the
    process environment and does not care where GitHub put it.
    """
    kok_env = wf.get("env") or {}
    cagrilar: list[tuple[str, str, str]] = []
    for is_id, is_tanimi in (wf.get("jobs") or {}).items():
        is_env = {**kok_env, **(is_tanimi.get("env") or {})}
        for sira, adim in enumerate(is_tanimi.get("steps") or [], 1):
            adim_env = {**is_env, **(adim.get("env") or {})}
            if H25_AUTOLOAD_ANAHTARI not in adim_env:
                continue
            betik = adim.get("run")
            if not isinstance(betik, str):
                continue
            adim_adi = adim.get("name") or f"<adsız adım #{sira}>"
            for eslesme in H25_PYTEST_CAGRISI.finditer(betik):
                cagri = (eslesme.group("komut") + eslesme.group("argumanlar")).strip()
                cagrilar.append((is_id, adim_adi, cagri))
    return cagrilar


def _h25_ihlaller(wf: dict) -> list[str]:
    return [
        f"{is_id} / {adim} :: {cagri}"
        for is_id, adim, cagri in _h25_autoload_kapali_cagrilar(wf)
        if not H25_BAYRAK.search(cagri)
    ]


H25_TERS_SIRA_ADIMI = "Değişen SQLite testlerini ters sırada koştur"


def test_h25_ci_autoload_kapali_her_pytest_cagrisi_asyncio_yukler() -> None:
    """Autoload kapalıyken bayraksız pytest = her async test düşer; kapı adı verir."""
    wf = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    cagrilar = _h25_autoload_kapali_cagrilar(wf)
    olculen = len(cagrilar)
    # Boş eşleşme yeşil yanmasın: ters-sıra adımının iki çağrısı (collect + run)
    # görülmek ZORUNDA; regex bozulursa burada düşer, aşağıda değil.
    ters_sira = [c for c in cagrilar if c[1] == H25_TERS_SIRA_ADIMI]
    assert len(ters_sira) == 2, (
        f"ters-sıra adımında 2 pytest çağrısı bekleniyordu, ölçülen {len(ters_sira)}; "
        f"toplam autoload-kapalı çağrı {olculen}: {cagrilar}"
    )
    ihlaller = _h25_ihlaller(wf)
    assert not ihlaller, (
        f"autoload kapalı {olculen} pytest çağrısından {len(ihlaller)} tanesi "
        "`-p pytest_asyncio.plugin` vermiyor (async testler HER sırada düşer):\n  "
        + "\n  ".join(ihlaller)
    )


@pytest.mark.parametrize("cagri_indeksi", [0, 1], ids=["collect-only", "run"])
def test_h25_ci_bayragi_sokulen_kopya_adimi_ve_cagriyi_adiyla_duser(
    tmp_path: Path, cagri_indeksi: int
) -> None:
    """ci.yml kopyasında TEK çağrının bayrağı sökülür; kapı o adımı ve çağrıyı adlar."""
    wf = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    once = _h25_ihlaller(wf)
    hedef = None
    for adim in wf["jobs"]["backend-quality-canonical"]["steps"]:
        if adim.get("name") == H25_TERS_SIRA_ADIMI:
            hedef = adim
    assert hedef is not None, "ters-sıra adımı ci.yml'de bulunamadı"
    eslesmeler = list(H25_BAYRAK.finditer(hedef["run"]))
    assert len(eslesmeler) == 2, f"ters-sıra adımında 2 bayrak bekleniyordu: {len(eslesmeler)}"
    e = eslesmeler[cagri_indeksi]
    hedef["run"] = hedef["run"][: e.start()] + hedef["run"][e.end() :]
    kopya = tmp_path / "ci.yml"
    kopya.write_text(yaml.dump(wf, allow_unicode=True), encoding="utf-8")

    sonra = _h25_ihlaller(yaml.safe_load(kopya.read_text(encoding="utf-8")))
    yeni = sorted(set(sonra) - set(once))
    assert len(yeni) == 1, f"tam 1 yeni ihlal bekleniyordu, ölçülen {len(yeni)}: {yeni}"
    (ihlal,) = yeni
    assert ihlal.startswith(f"backend-quality-canonical / {H25_TERS_SIRA_ADIMI} :: "), ihlal
    beklenen_parca = "--collect-only" if cagri_indeksi == 0 else "--no-header"
    assert beklenen_parca in ihlal, ihlal
    assert "pytest_asyncio" not in ihlal, ihlal


def test_h25_pytest_cagri_regexi_pip_install_ve_argumani_saymaz() -> None:
    """`pip install pytest` çağrı DEĞİLDİR; sayaç yalnız komut konumunu sayar."""
    betik = (
        "python -m pip install pytest pytest-asyncio\n"
        "pip install pytest\n"
        "python run_isolated_tests.py --timeout 180\n"
        "X=1 python -m pytest -q a.py; pytest -p pytest_asyncio.plugin b.py && python3 -m pytest c.py\n"
        "mapfile -t d < <(python -m pytest --collect-only -q e.py | grep '::' | tac)\n"
        'if ! DATABASE_URL="sqlite:///$T/ters-$(basename "$dosya").db" python -m pytest -q f.py; then\n'
    )
    cagrilar = [
        (m.group("komut") + m.group("argumanlar")).strip()
        for m in H25_PYTEST_CAGRISI.finditer(betik)
    ]
    assert cagrilar == [
        "python -m pytest -q a.py",
        "pytest -p pytest_asyncio.plugin b.py",
        "python3 -m pytest c.py",
        "python -m pytest --collect-only -q e.py",
        "python -m pytest -q f.py",
    ], cagrilar


RUNNER = BACKEND / "run_isolated_tests.py"


def _h25_runner_pytest_komut_listeleri(kaynak: str) -> list[list[str]]:
    """AST: `[..., "-m", "pytest", ...]` biçimindeki her liste; grep değil."""
    import ast

    listeler: list[list[str]] = []
    for dugum in ast.walk(ast.parse(kaynak)):
        if not isinstance(dugum, ast.List):
            continue
        sabitler = [
            e.value for e in dugum.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
        ]
        for i in range(len(sabitler) - 1):
            if sabitler[i] == "-m" and sabitler[i + 1] == "pytest":
                listeler.append(sabitler)
                break
    return listeler


def _h25_runner_bayraksiz(listeler: list[list[str]]) -> list[list[str]]:
    return [
        liste
        for liste in listeler
        if not any(
            liste[i] == "-p" and liste[i + 1] == "pytest_asyncio.plugin"
            for i in range(len(liste) - 1)
        )
    ]


def test_h25_isolated_runner_iki_cagri_yeri_de_asyncio_yukler() -> None:
    """Runner autoload'ı kapatır (env) ve HER iki pytest komut listesi bayrağı taşır."""
    kaynak = RUNNER.read_text(encoding="utf-8")
    assert f'env["{H25_AUTOLOAD_ANAHTARI}"] = "1"' in kaynak, "runner autoload'ı kapatmıyor mu?"
    listeler = _h25_runner_pytest_komut_listeleri(kaynak)
    assert len(listeler) == 2, (
        f"runner'da 2 pytest komut listesi bekleniyordu, ölçülen {len(listeler)}"
    )
    bayraksiz = _h25_runner_bayraksiz(listeler)
    assert not bayraksiz, f"{len(bayraksiz)}/{len(listeler)} komut listesi bayraksız: {bayraksiz}"


def test_h25_isolated_runner_bayragi_sokulen_kopya_duser() -> None:
    kaynak = RUNNER.read_text(encoding="utf-8")
    sokuk, adet = re.subn(r'"-p",\s*"pytest_asyncio\.plugin",\s*', "", kaynak, count=1)
    assert adet == 1
    listeler = _h25_runner_pytest_komut_listeleri(sokuk)
    assert len(listeler) == 2
    assert len(_h25_runner_bayraksiz(listeler)) == 1
