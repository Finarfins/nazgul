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
    "/app/backend/merge_postgresql_test_reports.py"
)


def _production_stage(content: str) -> str:
    stage = "FROM runtime-base AS production"
    start = content.index(stage)
    return content[start:]


def test_production_stage_removes_test_assets_only_in_that_stage() -> None:
    content = DOCKERFILE.read_text(encoding="utf-8")
    production_stage = _production_stage(content)
    removal_line = next(
        line
        for line in production_stage.splitlines()
        if line.startswith("RUN rm -rf /app/backend/tests")
    )
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
            "test ! -d /app/backend/tests && test ! -e /app/backend/donmus_saat.py",
        ],
        capture_output=True,
        text=False,
    )
    assert frozen_clock.returncode == 0, frozen_clock.stdout + frozen_clock.stderr


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
            "test -d /app/backend/tests; find /app/backend -name 'test_*.py' | wc -l",
        ],
        capture_output=True,
        text=False,
    )
    assert test_count.returncode == 0, test_count.stdout + test_count.stderr
    assert int(test_count.stdout.decode("utf-8").strip()) > 0, test_count.stdout
