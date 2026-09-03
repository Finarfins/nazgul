from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "Dockerfile"
PRODUCTION_TAG = "yerel-hesap-pro:uretim-imaji-kapisi"
EXPECTED_RUNTIME_REMOVAL = (
    "rm -rf /app/backend/tests "
    "/app/backend/test_*.py "
    "/app/backend/donmus_saat.py "
    "/app/backend/conftest.py "
    "/app/backend/pytest.ini "
    "/app/backend/run_isolated_tests.py "
    "/app/backend/isolated_test_reporter.py "
    "/app/backend/aggregate_isolated_test_reports.py "
    "/app/backend/merge_postgresql_test_reports.py "
    "/app/backend/non_twin_skip_exceptions.json "
    "/app/backend/sandbox "
    "/app/backend/requirements-dev.txt "
    "/app/backend/LEGACY_TEST_MIGRATION_PLAN.md "
    "/app/backend/tools/capture_frontend_fixtures.py"
)

# Uretim imajinda BULUNMAMASI gereken yollar; find ile sayilan test_*.py
# disindaki her sey burada. Kapinin tek tek `test -e` ile olctugu liste budur.
YASAKLI_URETIM_YOLLARI = (
    "/app/backend/tests",
    "/app/backend/donmus_saat.py",
    "/app/backend/non_twin_skip_exceptions.json",
    "/app/backend/sandbox",
    "/app/backend/requirements-dev.txt",
    "/app/backend/LEGACY_TEST_MIGRATION_PLAN.md",
    "/app/backend/tools/capture_frontend_fixtures.py",
)


def _production_stage(content: str) -> str:
    stage = "FROM runtime-base AS production"
    start = content.index(stage)
    return content[start:]


def test_production_stage_removes_test_assets_only_in_that_stage() -> None:
    content = DOCKERFILE.read_text(encoding="utf-8")
    production_stage = _production_stage(content)
    # `next(...)` VARSAYILANSIZ birakilirsa satir yoksa StopIteration ile patlar:
    # kapi kirmizi olur ama NEDENI okunmaz. Varsayilan + mesajli assert, "RUN rm
    # satiri test stage'e tasindi" mutasyonunu ADIYLA soyler.
    removal_line = next(
        (
            line
            for line in production_stage.splitlines()
            if line.startswith("RUN rm -rf /app/backend/tests")
        ),
        None,
    )
    assert removal_line is not None, "production stage'de RUN rm satırı yok"
    assert removal_line == f"RUN {EXPECTED_RUNTIME_REMOVAL}", removal_line
    test_stage = content[content.index("FROM runtime-base AS test"):content.index("FROM runtime-base AS production")]
    assert EXPECTED_RUNTIME_REMOVAL not in test_stage
    assert production_stage.index("USER root") < production_stage.index("RUN rm -rf")
    assert production_stage.index("RUN rm -rf") < production_stage.index("USER app")


def _docker_is_available() -> bool:
    return shutil.which("docker") is not None


def test_production_image_contains_no_test_assets() -> None:
    if not _docker_is_available():
        pytest.skip("DOKER YOK: docker komutu bulunamadı; imaj tabanlı kapı ölçülemedi")

    build = subprocess.run(
        [
            "docker",
            "build",
            "--provenance=false",
            "--sbom=false",
            "--target",
            "production",
            "--build-arg",
            "VITE_TURNSTILE_SITE_KEY=ci-dummy-public-key",
            "--tag",
            PRODUCTION_TAG,
            str(REPO_ROOT),
        ],
        capture_output=True,
        text=False,
    )
    assert build.returncode == 0, build.stdout + build.stderr

    test_count = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "sh",
            PRODUCTION_TAG,
            "-c",
            "find /app/backend -name 'test_*.py' | wc -l",
        ],
        capture_output=True,
        text=False,
    )
    assert test_count.returncode == 0, test_count.stdout + test_count.stderr
    assert test_count.stdout.strip() == b"0", test_count.stdout

    frozen_clock = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "sh",
            PRODUCTION_TAG,
            "-c",
            " && ".join(f"test ! -e {yol}" for yol in YASAKLI_URETIM_YOLLARI),
        ],
        capture_output=True,
        text=False,
    )
    assert frozen_clock.returncode == 0, (
        "üretim imajı yasaklı yol taşıyor, beklenen yokluk: "
        + ", ".join(YASAKLI_URETIM_YOLLARI)
    )


def test_stage_still_contains_tests() -> None:
    if not _docker_is_available():
        pytest.skip("DOKER YOK: docker komutu bulunamadı; test stage sayımı ölçülemedi")

    build = subprocess.run(
        [
            "docker",
            "build",
            "--provenance=false",
            "--sbom=false",
            "--target",
            "test",
            "--build-arg",
            "VITE_TURNSTILE_SITE_KEY=ci-dummy-public-key",
            "--tag",
            "yerel-hesap-test:uretim-imaji-kapisi",
            str(REPO_ROOT),
        ],
        capture_output=True,
        text=False,
    )
    assert build.returncode == 0, build.stdout + build.stderr

    test_count = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "sh",
            "yerel-hesap-test:uretim-imaji-kapisi",
            "-c",
            # `;` DEGIL `&&`: noktali virgul `test -d`nin cikis kodunu YUTAR, kabuk
            # yalnizca `wc -l`in kodunu dondururdu. Ustelik sayim /app/backend
            # GENELINDE yapilinca backend KOKUNDEKI 290 test_*.py tek basina
            # toplami >0 tutar; tests/ TAMAMEN silinmis bir imajda bile kapi yesil
            # kalirdi (olculdu: doctored imajda eski komut 290 basip 0 ile cikti).
            # Bu yuzden sayim tests/ ALTINDA yapilir.
            "test -d /app/backend/tests && "
            "find /app/backend/tests -name 'test_*.py' | wc -l",
        ],
        capture_output=True,
        text=False,
    )
    assert test_count.returncode == 0, (
        "test stage'de /app/backend/tests yok ya da sayım çalışmadı: "
        + (test_count.stdout + test_count.stderr).decode("utf-8", "replace")
    )
    assert int(test_count.stdout.decode("utf-8").strip()) > 0, test_count.stdout

    # Toplam (kok + tests/) sayim IKINCI bir iddia olarak; birinin digerini
    # maskelemesini engeller.
    toplam = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "sh",
            "yerel-hesap-test:uretim-imaji-kapisi",
            "-c",
            "find /app/backend -name 'test_*.py' | wc -l",
        ],
        capture_output=True,
        text=False,
    )
    assert toplam.returncode == 0, toplam.stdout + toplam.stderr
    assert int(toplam.stdout.decode("utf-8").strip()) > 0, toplam.stdout
