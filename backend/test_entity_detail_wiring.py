"""The cari detail endpoints must be the ones their own definitions declare.

``app/entity_detail.py`` used to be ``entity_detail_accuracy.py`` and installed
itself from ``app/routers/__init__.py`` by reassigning ``route.endpoint`` and
``route.dependant.call`` on two already-registered routes. Reading
``customers.py``/``finance.py`` therefore told you nothing about what actually
ran: the bodies at those ``@router.get`` lines were dead code that still looked
authoritative, and the real handler was installed from a module neither file
mentioned.

The patch is gone and both routers call ``entity_detail`` directly. This test is
the tripwire that keeps it gone -- a future "just patch it at import time" would
pass every behavioural suite in the repo and only fail here.

Behaviour is covered, unchanged, by the existing suites (``test_cari_360_bounded_summary``,
``test_v3_customer_center``, ``test_detail_workflows``, ``test_v2_5_cari_crm``,
``test_cari_pg_boolean_parity_postgresql``); this file deliberately asserts only
the wiring.
"""
from __future__ import annotations

from fastapi.routing import APIRoute


def _route(router, path: str) -> APIRoute:
    matches = [
        route
        for route in router.routes
        if isinstance(route, APIRoute) and route.path == path and "GET" in route.methods
    ]
    assert len(matches) == 1, (path, matches)
    return matches[0]


def test_cari_detail_routes_run_the_function_they_declare() -> None:
    from app.routers import customers, finance

    for router, path, declared in (
        (customers.router, "/customers/{customer_id}", customers.customer_detail),
        (finance.router, "/suppliers/{supplier_id}", finance.supplier_detail),
    ):
        route = _route(router, path)
        # Both must hold: ``endpoint`` is what OpenAPI names the operation after,
        # ``dependant.call`` is what actually executes. The old patch rewrote both,
        # so checking only one would not have caught it either.
        assert route.endpoint is declared, (path, route.endpoint)
        assert route.dependant.call is declared, (path, route.dependant.call)


def test_the_monkey_patch_module_is_gone() -> None:
    """The replaced module must not come back under its old name or shape."""
    import importlib

    import app.entity_detail as entity_detail

    assert callable(entity_detail.entity_detail)
    for removed in ("install_entity_detail_accuracy", "_replace_get_route"):
        assert not hasattr(entity_detail, removed), removed

    try:
        importlib.import_module("app.entity_detail_accuracy")
    except ModuleNotFoundError:
        return
    raise AssertionError("app.entity_detail_accuracy geri geldi")
