from __future__ import annotations

import ast
from pathlib import Path


MAIN_PATH = Path(__file__).resolve().parents[1] / "app" / "main.py"
PUBLIC_PROBES = {"live", "ready", "health"}
FORBIDDEN_PUBLIC_KEYS = {"version", "migration", "migrations", "alembic", "current", "expected"}


def _public_probe_returns() -> dict[str, set[str]]:
    tree = ast.parse(MAIN_PATH.read_text(encoding="utf-8"))
    result: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name not in PUBLIC_PROBES:
            continue
        keys: set[str] = set()
        for child in ast.walk(node):
            if not isinstance(child, ast.Return) or not isinstance(child.value, ast.Dict):
                continue
            for key in child.value.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    keys.add(key.value)
        result[node.name] = keys
    return result


def test_public_health_probes_do_not_expose_deployment_metadata() -> None:
    probe_keys = _public_probe_returns()

    assert probe_keys.keys() == PUBLIC_PROBES
    for probe, keys in probe_keys.items():
        exposed = keys & FORBIDDEN_PUBLIC_KEYS
        assert not exposed, f"{probe} exposes deployment metadata: {exposed}"


def test_public_health_probes_keep_coarse_operational_contract() -> None:
    probe_keys = _public_probe_returns()

    assert probe_keys["live"] == {"status"}
    assert probe_keys["ready"] == {"status", "database"}
    assert probe_keys["health"] == {"status", "database"}
