"""Guard: requirements.lock stays a complete, hashed pin of requirements.txt.

The production image installs from requirements.lock with --require-hashes, so
the lock must exist, carry hashes, and pin every direct dependency. Fails if a
package is added to requirements.txt without recompiling the lock.
"""
from __future__ import annotations

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
LOCK = BACKEND / "requirements.lock"
REQS = BACKEND / "requirements.txt"


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def _direct_names() -> set[str]:
    names: set[str] = set()
    for line in REQS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        name = re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0]
        if name:
            names.add(_norm(name))
    return names


def _locked_pins() -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        # Names may carry extras, e.g. "uvicorn[standard]==0.51.0".
        match = re.match(r"^([A-Za-z0-9._-]+)(?:\[[^\]]*\])?==([^\s\\]+)", line)
        if match:
            pins[_norm(match.group(1))] = match.group(2)
    return pins


def test_lock_exists_and_is_hashed():
    assert LOCK.is_file(), "backend/requirements.lock is missing"
    assert "--hash=sha256:" in LOCK.read_text(encoding="utf-8"), "lock must carry hashes"


def test_lock_pins_every_direct_dependency():
    pins = _locked_pins()
    missing = sorted(name for name in _direct_names() if name not in pins)
    assert not missing, f"requirements.lock missing pins for: {missing} (recompile the lock)"
