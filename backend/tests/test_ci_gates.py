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


def test_pg_test_population_exact_104() -> None:
    """PostgreSQL test population must be exactly 104 files.

    95 -> 97: tarla yazma kilitleri ikizleri (`test_farm_monoculture_postgresql.py`,
    `test_farm_reentry_enforcement_postgresql.py`, göç 20260901_0064). İkizler
    ZORUNLU: ÇKS tek ürün ve giriş yasağı yazma yolları üretim diyalektinde
    SQLite'tan farklı davranıyor. 94 -> 95 BKÜ kataloğu ikizi (göç
    20260901_0063) develop'ta zaten inmişti.

    97 -> 98: `test_uretici_kayit_defteri_postgresql.py` (Uygulama Kayıt
    Çizelgesi): genişleyen bind, NUMERIC ölçeği, TIMESTAMPTZ ve çapraz kiracı
    GERÇEK PostgreSQL üzerinde ölçülüyor. Bu artış develop'a PR #22 ile indi.

    98 -> 99: BKÜ içe aktarma ikizi (`test_farm_bku_ice_aktarma_postgresql.py`,
    göç 20260902_0065). TABAN DEVELOP'UN 98'İDİR ve delta ÖLÇÜLEREK bulundu,
    daha önceki 97 -> 98 ölçümümün üzerine aritmetik yapılarak DEĞİL: o ölçüm,
    tabanı PR #22 ile değiştiği anda geçersiz oldu.

    İKİZ ZORUNLU ve gerekçesi kardeşininkinden GÜÇLÜ: "bir bozuk satır dosyayı
    düşürmez" kuralını ayakta tutan şey SAVEPOINT ve savepoint YOKKEN SQLite
    koşusu YEŞİL KALIR — PostgreSQL'de başarısız bir deyim işlemi ABORTED
    yapar, SQLite'ta yapmaz. Yani bu dilimin ana iddiasının kırılması YALNIZ
    üretim diyalektinde görünür.

    99 -> 100: birim dönüşümü ikizi (`test_birim_donusumu_postgresql.py`, göç
    20260902_0066). SAYIM ÖLÇÜLDÜ: 98 `postgresql`-adlı glob + 2
    adlandırılmış özel dosya = 100.

    100 -> 101: parti/SKT ikizi (`test_parti_skt_postgresql.py`, göç
    20260903_0067). SAYIM YİNE ÖLÇÜLDÜ, önceki ölçümün ÜZERİNE ARİTMETİK
    YAPILARAK DEĞİL: taban develop'ta `ls backend/test_*postgresql*.py | wc -l`
    -> 98 çıktı, o PR bir dosya ekliyor, yani 99 `postgresql`-adlı + 2 özel
    = 101.

    101 -> 102: kantar fişi ikizi (`test_kantar_fisi_postgresql.py`,
    göç 20260904_0069). SAYIM YİNE ÖLÇÜLDÜ: o dalda
    `ls backend/test_*postgresql*.py | wc -l` -> 100 (yeni dosya DAHİL), yani
    100 `postgresql`-adlı + 2 özel = 102.

    102 -> 103: bu PR'ın müstahsil makbuzu ikizi
    (`test_mustahsil_makbuzu_postgresql.py`, göç 20260905_0070). SAYIM YİNE
    ÖLÇÜLDÜ, önceki ölçümün ÜZERİNE ARİTMETİK YAPILARAK DEĞİL: bu dalda
    `ls backend/test_*postgresql*.py | wc -l` -> 101 (yeni dosya DAHİL),
    yani 101 `postgresql`-adlı + 2 özel = 103.

    103 -> 104: bu PR'ın D2 ikizi (`test_d2_avans_tescil_postgresql.py`,
    göç 20260906_0071). SAYIM YİNE ÖLÇÜLDÜ, önceki ölçümün ÜZERİNE ARİTMETİK
    YAPILARAK DEĞİL: bu dalda `ls backend/test_*postgresql*.py | wc -l` ->
    102 (yeni dosya DAHİL), yani 102 `postgresql`-adlı + 2 özel = 104.

    İKİZ ZORUNLU ve gerekçesi ÜÇ tanedir, üçü de yalnız üretim diyalektinde
    görünür: (a) D2'nin YEDİ bileşik yabancı anahtarının TEK işi çapraz
    kiracı referansı engellemektir ve SQLite'ta yabancı anahtar uygulaması
    varsayılan olarak KAPALIDIR; (b) `uq_payments_company_id` olmadan
    PostgreSQL göçü REDDEDER ("there is no unique constraint matching given
    keys") ama SQLite SESSİZCE geçirir, yani şemaların AYRIŞMASI yalnız
    burada görünür; (c) `NUMERIC(18,2)` ölçeği ve
    `0 <= remaining_amount <= amount` aralığı SQLite'ta DAYATILMAZ — yanlış
    ölçekli ya da aşırı mahsup edilmiş bir avans orada SESSİZCE geçerdi.

    İKİZ ZORUNLU: 0070'in BEŞ bileşik yabancı anahtarının TEK işi çapraz
    kiracı referansı engellemektir ve SQLite'ta yabancı anahtar uygulaması
    varsayılan olarak KAPALIDIR — yani ana kiracı iddiası SQLite koşusunda
    YEŞİL KALIR. `NUMERIC` ölçeği ve kısmi benzersiz indeks de yalnız
    üretim diyalektinde gerçekten dayatılır.

    İKİZ ZORUNLU ve gerekçesi ÖLÇÜLDÜ, iddia edilmedi: temiz bir SQLite
    şemasında `PRAGMA foreign_keys` **0** döner ve çapraz kiracı bir fiş
    (`company_id` A firmasının, `harvest_id` B firmasının hasadı) SESSİZCE
    KABUL EDİLİR. Göçün iki bileşik yabancı anahtarının TEK işi budur, yani
    savunma yalnız üretim diyalektinde ölçülebilir. Ayrıca `NUMERIC(24,10)`
    katsayı ölçeği ve `NUMERIC(18,4)` miktar ölçeği SQLite'ta DAYATILMAZ;
    katsayı "o gün neye inanıldığının" kanıtı olduğu için yuvarlanmış bir
    katsayı kanıtı SESSİZCE bozardı.

    İKİZ ZORUNLU ve gerekçesi üç tanedir, üçü de yalnız üretim diyalektinde
    görünür: (a) `CHECK (quantity >= 0 AND quantity <> 'NaN'::numeric)`in NaN
    yarısı YALNIZ PostgreSQL'de vardır ve PostgreSQL `NaN`ı her sonlu sayının
    üstüne sıralar; (b) dağıtım paylarının `NUMERIC(18,4)` ölçeği SQLite'ta
    dayatılmaz, yani yanlış ölçekli bir yazma orada SESSİZCE geçerdi;
    (c) bileşik yabancı anahtar SQLite'ta varsayılan olarak UYGULANMAZ
    (`PRAGMA foreign_keys` kapalı), yani çapraz kiracı referans orada YEŞİL
    kalırdı.

    ADIN VE DÜZYAZININ SAYIYLA BİRLİKTE HAREKET ETMESİ ZORUNLUDUR. Bu test
    bir tur boyunca `..._exact_99` ADIYLA `== 100` İDDİA ETTİ ve `ci.yml`in
    yorumu `97 + 2 = 99` derken sabit `100`dü. Değeri doğru olan ama gerekçesi
    onu yalanlayan bir çivi, sonraki okuyucu için TUZAKTIR: okuyucu gerekçeye
    güvenip sayıyı "düzeltmeye" kalkar. Bu yüzden ad, düzyazı ve sayı ÜÇÜ
    BİRDEN güncellenir.

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
    assert len(all_files) == 104, (
        f"PostgreSQL test population changed: expected 104, got {len(all_files)}"
    )


def test_ci_workflow_has_frozen_pg_population_constant() -> None:
    """ci.yml must contain BEKLENEN_PG_DOSYA_SAYISI=104 and strict equality.

    ÜÇÜNCÜ ÇİVİ. Sayı bu depoda ÜÇ yerde yaşıyor: `ci.yml`in sabiti,
    `test_pg_test_population_exact_104`in adı/iddiası, ve BURASI. Üçü aynı
    popülasyonu sayıyor; biri güncellenip öteki unutulursa kapı KENDİ
    KENDİSİYLE ÇELİŞİR — ve bu tam olarak `test_pg_test_population_exact_104`
    düzyazısının anlattığı tuzaktır (bir tur boyunca ad `_99`, iddia `100`,
    `ci.yml` yorumu `97 + 2 = 99` idi).
    """
    content = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "BEKLENEN_PG_DOSYA_SAYISI=104" in content
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



