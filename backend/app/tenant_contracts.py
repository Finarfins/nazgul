from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TenantContract:
    """Static tenant-isolation expectations for a sensitive router function."""

    router_path: str
    function_name: str
    required_fragments: tuple[str, ...] = ("company_id(request)", "company_id=:cid")


DEFAULT_CONTRACTS: tuple[TenantContract, ...] = (
    TenantContract("app/routers/products.py", "detail"),
    # Cross-branch stock visibility answers "which branch has this part"; a
    # tenant leak here would expose another dealer's warehouse inventory.
    TenantContract("app/routers/products.py", "product_warehouse_stock"),
)


def function_source(path: Path, function_name: str) -> str:
    """Return one top-level function's exact source or raise a clear error."""

    source = path.read_text(encoding="utf-8")
    module = ast.parse(source, filename=str(path))
    function = next(
        (
            node
            for node in module.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function_name
        ),
        None,
    )
    if function is None:
        raise AssertionError(f"{path}: function {function_name!r} was not found")
    return ast.get_source_segment(source, function) or ""


def validate_function_contract(source: str, *, required_fragments: tuple[str, ...]) -> list[str]:
    """Return tenant contract violations without executing application code."""

    violations: list[str] = []
    for fragment in required_fragments:
        if fragment not in source:
            violations.append(f"missing required tenant fragment: {fragment}")

    tenant_resolution = source.find("company_id(request)")
    first_database_access = source.find("db.execute(")
    if first_database_access >= 0 and (
        tenant_resolution < 0 or tenant_resolution > first_database_access
    ):
        violations.append("database access occurs before tenant resolution")

    return violations


def validate_repository_contracts(
    backend_root: Path,
    contracts: tuple[TenantContract, ...] = DEFAULT_CONTRACTS,
) -> list[str]:
    """Validate all registered tenant contracts and return human-readable failures."""

    failures: list[str] = []
    for contract in contracts:
        path = backend_root / contract.router_path
        try:
            source = function_source(path, contract.function_name)
        except (OSError, SyntaxError, AssertionError) as exc:
            failures.append(f"{contract.router_path}:{contract.function_name}: {exc}")
            continue

        for violation in validate_function_contract(
            source,
            required_fragments=contract.required_fragments,
        ):
            failures.append(
                f"{contract.router_path}:{contract.function_name}: {violation}"
            )
    return failures


def assert_repository_contracts(backend_root: Path) -> None:
    """Raise one compact assertion suitable for pytest and CI output."""

    failures = validate_repository_contracts(backend_root)
    if failures:
        raise AssertionError("Tenant contract failures:\n- " + "\n- ".join(failures))
