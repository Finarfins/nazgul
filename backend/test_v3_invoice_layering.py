from __future__ import annotations

import ast
import inspect as _inspect
import os
import subprocess
import sys
from pathlib import Path

from app import billing_service

BACKEND = Path(__file__).resolve().parent
APP = BACKEND / "app"


# --- Structural guarantees (fast, no database) -----------------------------
#
# These assertions inspect the parsed AST rather than raw source text, so that
# comments and docstrings can never cause a false pass or false failure.

def _module_ast(relpath: str) -> ast.Module:
    return ast.parse((APP / relpath).read_text(encoding="utf-8"))


def _imported_bindings(tree: ast.Module) -> list[tuple[str, str]]:
    """Return (module, imported_name) for every import statement in the tree.

    Relative imports are normalised so ``from .routers.work_order_billing import x``
    yields module ``routers.work_order_billing`` (the leading dot is dropped).
    """
    bindings: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                bindings.append((module, alias.name))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bindings.append((alias.name, alias.name))
    return bindings


def _imports_a_router(tree: ast.Module) -> bool:
    return any(
        module == "routers" or module.startswith("routers.") or ".routers" in module
        for module, _name in _imported_bindings(tree)
    )


def test_invoice_service_does_not_import_router() -> None:
    # The layering inversion (service -> router) must stay removed: the invoice
    # service may not import from any router module.
    assert not _imports_a_router(_module_ast("invoice_service.py"))


def test_billing_service_is_http_neutral() -> None:
    # The extracted domain calculation never touches the HTTP layer.
    tree = _module_ast("billing_service.py")
    # It imports neither a router nor the FastAPI Request type...
    assert not _imports_a_router(tree)
    assert all(name != "Request" for _module, name in _imported_bindings(tree))
    # ...and never references a `Request` identifier anywhere in real code
    # (AST Name/Attribute nodes exclude comments and docstrings).
    assert not any(
        (isinstance(node, ast.Name) and node.id == "Request")
        or (isinstance(node, ast.Attribute) and node.attr == "Request")
        for node in ast.walk(tree)
    )
    # The public entry point carries no HTTP request parameter.
    params = list(_inspect.signature(billing_service.build_invoice_summary).parameters)
    assert params == ["db", "cid", "work_order_id"], params


def test_generate_invoice_uses_neutral_summary() -> None:
    # generate_invoice must obtain its summary from the neutral service call
    # `build_invoice_summary(...)`, not from a router, and must not pass a
    # Request into it.
    tree = _module_ast("invoice_service.py")
    func = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "generate_invoice"
    )
    calls = [
        node for node in ast.walk(func)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "build_invoice_summary"
    ]
    assert len(calls) == 1, ast.dump(func)
    arg_names = {arg.id for arg in calls[0].args if isinstance(arg, ast.Name)}
    assert {"db", "cid"} <= arg_names
    assert "request" not in arg_names


# --- Behavioural equivalence (SQLite smoke) --------------------------------

def test_router_and_service_summaries_are_identical(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'layering.db').as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    smoke = r'''
from decimal import Decimal
from fastapi.testclient import TestClient
from app.billing_service import build_invoice_summary
from app.db import SessionLocal
from app.main import app

with TestClient(app) as c:
 login=c.post('/api/auth/login',json={'username':'admin','password':'admin123'}).json(); cid=login['companies'][0]['id']; uid=login['user']['id']
 h={'Authorization':'Bearer '+login['access_token'],'X-Company-ID':str(cid)}
 h['Authorization']='Bearer '+c.post('/api/auth/change-password',headers=h,json={'current_password':'admin123','new_password':'LayeringPass123!'}).json()['access_token']
 customer=c.post('/api/customers',headers=h,json={'name':'Layer'}).json()
 machine=c.post('/api/machines',headers=h,json={'customer_id':customer['id'],'brand':'S','model':'P','serial_number':'LAY-M'}).json()
 warehouse=c.get('/api/warehouses',headers=h).json()[0]
 product=c.post('/api/products',headers=h,json={'name':'Part','purchase_price':'5','sale_price':'10','vat_rate':'20','stock':'20','unit':'Adet'}).json()
 wo=c.post('/api/work-orders',headers=h,json={'machine_id':machine['id'],'customer_id':customer['id'],'technician_id':uid,'actual_hours':'2','labor_rate':'150','warranty_type':'PARTIAL','warranty_percent':'30'}).json()
 wid=wo['id']
 c.post(f"/api/work-orders/{wid}/parts",headers=h,json={'product_id':product['id'],'warehouse_id':warehouse['id'],'quantity':'2','unit_price':'100','discount':'10','tax_rate':'20'})
 for s in ('IN_PROGRESS','COMPLETED'): assert c.patch(f"/api/work-orders/{wid}/status",headers=h,json={'status':s}).status_code==200
 # HTTP endpoint result (money serialised as strings)
 endpoint=c.get(f"/api/work-orders/{wid}/invoice",headers=h).json()
 # Direct neutral-service result (no Request), same tenant id
 with SessionLocal() as db:
  direct=build_invoice_summary(db,cid,wid)
 assert Decimal(endpoint['totals']['grand_total'])==direct['totals']['grand_total'], (endpoint,direct)
 assert Decimal(endpoint['totals']['parts'])==direct['totals']['parts']
 assert Decimal(endpoint['totals']['labor'])==direct['totals']['labor']
 assert Decimal(endpoint['warranty']['warranty_amount'])==direct['warranty']['warranty_amount']
 # The generated invoice reuses the same figures end-to-end.
 gen=c.post('/api/invoices/generate',headers=h,json={'work_order_id':wid,'branch_prefix':'LAY'})
 assert gen.status_code==201,gen.text
 assert Decimal(gen.json()['totals']['grand_total'])==direct['totals']['grand_total']
 # Tenant isolation is preserved through the delegation.
 other=c.post('/api/companies',headers=h,json={'name':'Layer B'}).json()
 assert c.get(f"/api/work-orders/{wid}/invoice",headers={**h,'X-Company-ID':str(other['id'])}).status_code==404
 print('LAYERING_EQUIVALENCE_OK')
'''
    result = subprocess.run([sys.executable, "-c", smoke], cwd=BACKEND, env=env, text=True, capture_output=True, timeout=180)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "LAYERING_EQUIVALENCE_OK" in result.stdout
