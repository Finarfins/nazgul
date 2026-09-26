"""H85 guard: CI and the test image install ONLY from the hashed locks.

H83: CI installed loose ``requirements*.txt`` ranges and SQLAlchemy 2.1.0 turned
develop red on its release day. Since H85 every backend job runs
``pip install --require-hashes -r requirements.lock`` followed by
``requirements-dev.lock`` (test plugins, compiled from requirements-dev.txt with
the same pip-compile flags, seeded from requirements.lock so the shared pins
are identical). This file fails when:

- a direct dependency of requirements.txt / requirements-dev.txt has no pin,
- the two locks disagree on a shared package (the second install would then
  silently replace what the production lock put in place),
- sqlalchemy is pinned at 2.1 or above in either lock,
- ci.yml or the Dockerfile goes back to a loose ``pip install`` (a bare
  ``requirements*.txt`` or an unpinned package name such as ``pytest``).
"""
from __future__ import annotations

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
REQS = BACKEND / "requirements.txt"
REQS_DEV = BACKEND / "requirements-dev.txt"
LOCK = BACKEND / "requirements.lock"
LOCK_DEV = BACKEND / "requirements-dev.lock"
CI = REPO / ".github" / "workflows" / "ci.yml"
DOCKERFILE = REPO / "Dockerfile"

# `pip install` in command position (python -m pip / pip / ".../bin/pip" install).
_PIP_INSTALL = re.compile(r"""(?:^|[\s;&|("'/])(?:python3?(?:\.\d+)?\s+-m\s+)?pip["']?\s+install\b(?P<args>.*)$""")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def _direct_names(path: Path) -> set[str]:
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        name = re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0]
        if name:
            names.add(_norm(name))
    return names


def _pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9._-]+)(?:\[[^\]]*\])?==([^\s\\;]+)", line)
        if match:
            pins[_norm(match.group(1))] = match.group(2)
    return pins


def _unhashed_pins(path: Path) -> list[str]:
    """Names of pins whose entry carries no ``--hash=sha256:`` line."""
    unhashed: list[str] = []
    name = None
    hashed = True
    for line in path.read_text(encoding="utf-8").splitlines() + ["END==0"]:
        match = re.match(r"^([A-Za-z0-9._-]+)(?:\[[^\]]*\])?==", line)
        if match:
            if name is not None and not hashed:
                unhashed.append(name)
            name, hashed = _norm(match.group(1)), False
        elif "--hash=sha256:" in line:
            hashed = True
    return unhashed


def _pip_install_args(text: str) -> list[str]:
    """Argument strings of every ``pip install`` command, comments skipped."""
    found: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            continue
        # YAML `- run: cmd` form: strip the key so the command is in position.
        line = re.sub(r"^-?\s*run:\s*", "", line)
        match = _PIP_INSTALL.search(line)
        if match:
            found.append(match.group("args").strip())
    return found


def _loose_installs(text: str) -> list[str]:
    """Every pip install that is neither a hashed lock install, a pip
    self-upgrade, nor a list of exact ``==`` pins."""
    loose: list[str] = []
    for args in _pip_install_args(text):
        tokens = args.split()
        if re.search(r"requirements(?:-dev)?\.txt\b", args):
            loose.append(args)
            continue
        if any(t.endswith(".lock") for t in tokens):
            if "--require-hashes" not in tokens:
                loose.append(args)
            continue
        packages = [t for t in tokens if not t.startswith("-") and t != "pip"]
        if "--upgrade" in tokens and not packages:
            continue  # `pip install --upgrade pip`
        if packages and all("==" in p for p in packages):
            continue  # e.g. PyYAML==6.0.3 in the deploy-contract venv
        loose.append(args)
    return loose


def test_both_locks_exist_and_every_pin_is_hashed():
    for lock in (LOCK, LOCK_DEV):
        assert lock.is_file(), f"{lock.name} is missing"
        assert _pins(lock), f"{lock.name} pins nothing"
        assert not _unhashed_pins(lock), f"{lock.name} unhashed pins: {_unhashed_pins(lock)}"


def test_every_requirements_txt_package_is_pinned_in_the_lock():
    missing = sorted(_direct_names(REQS) - set(_pins(LOCK)))
    assert not missing, f"requirements.lock missing pins for: {missing}"


def test_every_dev_package_is_pinned_in_the_dev_lock():
    wanted = _direct_names(REQS) | _direct_names(REQS_DEV)
    assert {"pytest", "pytest-asyncio"} <= wanted
    missing = sorted(wanted - set(_pins(LOCK_DEV)))
    assert not missing, f"requirements-dev.lock missing pins for: {missing}"


def test_shared_pins_agree_between_the_two_locks():
    """CI installs requirements.lock first, then requirements-dev.lock. A
    shared package at a different version would be silently swapped by the
    second install, and CI would no longer test what production runs.
    Only colorama/tzdata (win32-only, present in requirements.lock) may be
    absent from the Linux-compiled dev lock."""
    prod, dev = _pins(LOCK), _pins(LOCK_DEV)
    drift = {k: (prod[k], dev[k]) for k in prod.keys() & dev.keys() if prod[k] != dev[k]}
    assert not drift, f"lock drift (requirements.lock, requirements-dev.lock): {drift}"
    absent = sorted(set(prod) - set(dev))
    assert set(absent) <= {"colorama", "tzdata"}, f"missing from requirements-dev.lock: {absent}"


def test_sqlalchemy_pinned_below_2_1_in_both_locks():
    for lock in (LOCK, LOCK_DEV):
        version = _pins(lock).get("sqlalchemy")
        assert version is not None, f"{lock.name} does not pin sqlalchemy"
        major, minor = (int(x) for x in version.split(".")[:2])
        assert (major, minor) < (2, 1), f"{lock.name}: sqlalchemy=={version} (H83/H84: <2.1)"


def test_ci_has_no_loose_pip_install():
    text = CI.read_text(encoding="utf-8")
    installs = _pip_install_args(text)
    assert installs, "no pip install found in ci.yml; the parser went blind"
    loose = _loose_installs(text)
    assert not loose, f"ci.yml loose pip install(s): {loose}"
    lock_installs = [a for a in installs if a.endswith("requirements.lock")]
    dev_installs = [a for a in installs if a.endswith("requirements-dev.lock")]
    # alembic-chain, durum-kaydi, backend-quality-canonical, backend-quality,
    # backend-postgresql, contract-drift, e2e, container
    assert len(lock_installs) == len(dev_installs) == 8, (lock_installs, dev_installs)


def test_dockerfile_has_no_loose_pip_install():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert len(_pip_install_args(text)) >= 2, "Dockerfile pip installs not found; parser went blind"
    loose = _loose_installs(text)
    assert not loose, f"Dockerfile loose pip install(s): {loose}"


def test_detector_flags_the_pre_h85_forms():
    """The regex must see the exact lines H85 removed; otherwise the guards
    above are a vacuous green."""
    before = (
        '          python -m pip install "pytest>=8.2,<9" "pytest-asyncio>=0.23,<1"\n'
        "      - run: python -m pip install -r requirements-dev.txt\n"
        "      - run: python -m pip install -r backend/requirements-dev.txt\n"
        "          python -m pip install --quiet pytest pytest-asyncio\n"
        "RUN pip install --no-cache-dir -r backend/requirements-dev.txt\n"
        "      - run: python -m pip install -r requirements.lock\n"
    )
    assert len(_loose_installs(before)) == 6
    after = (
        "      - run: python -m pip install --upgrade pip\n"
        "          python -m pip install --require-hashes -r requirements.lock\n"
        '          "$RUNNER_TEMP/v/bin/pip" install --disable-pip-version-check PyYAML==6.0.3\n'
        "          # gevşek `pip install pytest` yok\n"
    )
    assert len(_pip_install_args(after)) == 3
    assert _loose_installs(after) == []
