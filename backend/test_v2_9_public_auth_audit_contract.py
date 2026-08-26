from __future__ import annotations

import ast
from pathlib import Path


MAIN_PATH = Path(__file__).parent / "app" / "main.py"


def _audit_guard_source() -> str:
    source = MAIN_PATH.read_text(encoding="utf-8")
    module = ast.parse(source)
    function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "_write_security_audit"
    )
    guard = next(node for node in function.body if isinstance(node, ast.If))
    return ast.get_source_segment(source, guard.test) or ""


def test_public_authentication_posts_are_not_excluded_from_audit() -> None:
    guard = _audit_guard_source()

    assert 'path.startswith("/api")' in guard
    assert 'request.method in {"POST", "PUT", "PATCH", "DELETE"}' in guard
    assert "PUBLIC_API" not in guard


def test_login_and_refresh_remain_public_but_auditable() -> None:
    source = MAIN_PATH.read_text(encoding="utf-8")

    assert '"/api/auth/login"' in source
    assert '"/api/auth/refresh"' in source
    assert "_write_security_audit(request, response, started_at)" in source
