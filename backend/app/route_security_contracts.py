from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Set
from dataclasses import dataclass
from hashlib import sha256

from fastapi.routing import APIRoute


HTTP_METHODS = frozenset({"DELETE", "GET", "HEAD", "PATCH", "POST", "PUT"})
PLATFORM_ROUTE_PREFIXES = ("/api/platform/",)


@dataclass(frozen=True)
class RouteSecurityContract:
    method: str
    path: str
    permission: str
    tenant_scope: str
    review_reason: str
    permission_cases: tuple[tuple[str, str], ...] = ()


def build_route_security_contracts(
    schema: Mapping[str, object],
    *,
    public_paths: Set[str],
    route_reasons: Mapping[tuple[str, str], str],
    permission_resolver: Callable[[str, str], str],
    known_permissions: Set[str],
    permission_cases: Mapping[tuple[str, str], Mapping[str, str]] | None = None,
    registered_operations: Iterable[tuple[str, str]] | None = None,
) -> tuple[RouteSecurityContract, ...]:
    contracts: list[RouteSecurityContract] = []
    permission_cases = permission_cases or {}
    paths = schema.get("paths")
    if not isinstance(paths, Mapping):
        raise ValueError("OpenAPI schema must contain a paths mapping")

    for path, operations in paths.items():
        if not isinstance(path, str) or not path.startswith("/api"):
            continue
        if not isinstance(operations, Mapping):
            raise ValueError(f"OpenAPI operations must be a mapping: {path}")
        for raw_method in operations:
            method = str(raw_method).upper()
            if method not in HTTP_METHODS:
                continue
            contracts.append(
                _build_contract(
                    method,
                    path,
                    public_paths=public_paths,
                    route_reasons=route_reasons,
                    permission_resolver=permission_resolver,
                    known_permissions=known_permissions,
                    permission_cases=permission_cases,
                )
            )

    registered_paths = {contract.path for contract in contracts}
    stale_public_paths = sorted(public_paths - registered_paths)
    if stale_public_paths:
        raise ValueError(f"public path is not registered: {stale_public_paths}")
    registered_routes = {(contract.method, contract.path) for contract in contracts}
    stale_reasons = sorted(set(route_reasons) - registered_routes)
    if stale_reasons:
        raise ValueError(f"review reason is not registered: {stale_reasons}")
    stale_cases = sorted(set(permission_cases) - registered_routes)
    if stale_cases:
        raise ValueError(f"permission case route is not registered: {stale_cases}")
    if registered_operations is not None:
        registered = Counter(
            (method.upper(), path) for method, path in registered_operations
        )
        documented = Counter(registered_routes)
        if registered != documented:
            hidden = sorted((registered - documented).elements())
            missing = sorted((documented - registered).elements())
            raise ValueError(
                "registered API routes differ from OpenAPI: "
                f"hidden_or_duplicate={hidden}, missing={missing}"
            )
    return tuple(sorted(contracts, key=lambda item: (item.path, item.method)))


def _build_contract(
    method: str,
    path: str,
    *,
    public_paths: Set[str],
    route_reasons: Mapping[tuple[str, str], str],
    permission_resolver: Callable[[str, str], str],
    known_permissions: Set[str],
    permission_cases: Mapping[tuple[str, str], Mapping[str, str]],
) -> RouteSecurityContract:
    is_public = path in public_paths
    is_platform = path.startswith(PLATFORM_ROUTE_PREFIXES)
    route_cases = permission_cases.get((method, path), {})
    if "{kind}" in path and len(route_cases) < 2:
        raise ValueError(f"concrete permission cases required for {method} {path}")

    resolved_cases: list[tuple[str, str]] = []
    if route_cases:
        if "{" not in path:
            raise ValueError(f"permission cases require a templated path: {method} {path}")
        for concrete_path, expected_permission in sorted(route_cases.items()):
            if not _path_matches_template(path, concrete_path):
                raise ValueError(
                    f"permission case does not match {path}: {concrete_path}"
                )
            actual_permission = permission_resolver(method, concrete_path)
            if actual_permission != expected_permission:
                raise ValueError(
                    f"permission case mismatch for {method} {concrete_path}: "
                    f"expected={expected_permission}, actual={actual_permission}"
                )
            if actual_permission not in known_permissions:
                raise ValueError(
                    f"unknown permission for {method} {concrete_path}: {actual_permission}"
                )
            resolved_cases.append((concrete_path, actual_permission))
        permission = "dynamic"
    else:
        permission = "public" if is_public else permission_resolver(method, path)
        if not is_public and permission not in known_permissions:
            raise ValueError(f"unknown permission for {method} {path}: {permission}")

    tenant_scope = "public" if is_public else ("platform" if is_platform else "company")
    reason = route_reasons.get((method, path), "").strip()
    has_read_case = any(case_permission == "read" for _, case_permission in resolved_cases)
    if permission == "read" or has_read_case or tenant_scope != "company":
        if not reason:
            raise ValueError(f"explicit review reason required for {method} {path}")

    return RouteSecurityContract(
        method,
        path,
        permission,
        tenant_scope,
        reason,
        tuple(resolved_cases),
    )


def _path_matches_template(template: str, concrete_path: str) -> bool:
    template_parts = template.strip("/").split("/")
    concrete_parts = concrete_path.strip("/").split("/")
    if len(template_parts) != len(concrete_parts):
        return False
    return all(
        (part.startswith("{") and part.endswith("}")) or part == concrete
        for part, concrete in zip(template_parts, concrete_parts, strict=True)
    )


def registered_api_operations(application: object) -> tuple[tuple[str, str], ...]:
    operations: list[tuple[str, str]] = []

    def visit(routes: Iterable[object], prefix: str = "") -> None:
        for route in routes:
            if isinstance(route, APIRoute):
                path = f"{prefix}{route.path}"
                if path.startswith("/api"):
                    operations.extend(
                        (method, path)
                        for method in route.methods
                        if method in HTTP_METHODS
                    )
                continue

            original_router = getattr(route, "original_router", None)
            include_context = getattr(route, "include_context", None)
            if original_router is not None and include_context is not None:
                visit(
                    original_router.routes,
                    f"{prefix}{include_context.prefix}",
                )
                continue

            raw_path = getattr(route, "path", None)
            if isinstance(raw_path, str):
                path = f"{prefix}{raw_path}"
                if path.startswith("/api"):
                    methods = getattr(route, "methods", None)
                    if methods:
                        operations.extend(
                            (method, path)
                            for method in methods
                            if method in HTTP_METHODS
                        )
                    else:
                        operations.append((type(route).__name__.upper(), path))

    visit(application.routes)
    return tuple(sorted(operations))


def fingerprint_route_contracts(contracts: tuple[RouteSecurityContract, ...]) -> str:
    payload = "\n".join(
        "\t".join(
            (
                contract.method,
                contract.path,
                contract.permission,
                contract.tenant_scope,
                contract.review_reason,
                ",".join(
                    f"{path}={permission}"
                    for path, permission in contract.permission_cases
                ),
            )
        )
        for contract in contracts
    )
    return sha256(payload.encode("utf-8")).hexdigest()
