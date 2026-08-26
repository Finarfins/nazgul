"""Regression cover for ``POST /api/products/bulk-stock``.

The sibling money endpoint ``bulk-price`` grew three guards in #157
(``test_bulk_price_contract.py``). ``bulk-stock`` is the same shape -- one POST
that mutates an unbounded set of rows in the caller's tenant -- but it shipped
without two of them, so this file mirrors that test pattern assertion for
assertion:

* ``BUG-B`` -- an explicitly empty ``product_ids`` list is refused with 400
  instead of silently re-stocking the whole catalogue. The falsy list used to
  drop the id filter entirely. Omitting the key still means "all products".
* Foreign or unknown ids used to be answered 200 ``{"updated": 0}``, which is
  indistinguishable from a successful no-op. They are now 404 and the batch is
  rejected **whole**, before the first ``stock_movements`` row is written.

The third bulk-price guard (a computed value that invalidates the result) has no
new equivalent here: for stock the business rule is the negative-stock policy,
which already answers 409 and rolls the transaction back. That rule is not
re-litigated -- ``test_v2_9_stock_policy_surface.py`` pins ``set``/``-3`` as a
legitimate manager-override scenario -- but its **atomicity** across a
multi-product batch is asserted below, because that is what the 404 path relies
on too.

Same execution model as the bulk-price file: one isolated subprocess per
scenario, each with its own clean database created by the real migration chain.
``run_bulk_stock_guards`` is the entry point the PostgreSQL twin re-uses.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent

# Shared bootstrap: log in as the seeded admin, satisfy the forced first-login
# rotation, and expose the helpers the scenario bodies build on.
_BOOTSTRAP = r'''
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def ok(response):
    assert response.status_code < 300, (response.status_code, response.text)
    return response.json() if response.content else None


def login(username, password):
    return ok(client.post('/api/auth/login', json={'username': username, 'password': password}))


_admin = login('admin', 'admin123')
COMPANY_A = _admin['companies'][0]['id']
HA = {'Authorization': 'Bearer ' + _admin['access_token'], 'X-Company-ID': str(COMPANY_A)}
_rotated = ok(client.post('/api/auth/change-password', headers=HA, json={
    'current_password': 'admin123', 'new_password': 'BulkStock!Rot2026'}))
HA['Authorization'] = 'Bearer ' + _rotated['access_token']

MOVEMENT_DATE = '2026-07-13'


def make_product(name, stock, headers=None):
    return ok(client.post('/api/products', headers=headers or HA, json={
        'name': name, 'purchase_price': 10, 'sale_price': 20,
        'vat_rate': 20, 'stock': stock, 'unit': 'Adet'}))['id']


def detail(product_id, headers=None):
    return ok(client.get('/api/products/%s' % product_id, headers=headers or HA))


def read_stock(product_id, headers=None):
    # Total across warehouses: bulk-stock writes to the default warehouse, so the
    # sum moves exactly when the endpoint wrote and stays put when it did not.
    rows = detail(product_id, headers)['warehouse_stocks']
    return round(sum(float(row['quantity']) for row in rows), 3)


def count_movements(product_id, headers=None):
    # The stock quantity alone cannot tell a rolled-back write from one that was
    # never attempted; the movement ledger can.
    return len(detail(product_id, headers)['movements'])


def bulk_stock(payload, headers=None):
    body = {'movement_date': MOVEMENT_DATE, 'note': 'Toplu stok testi', **payload}
    return client.post('/api/products/bulk-stock', headers=headers or HA, json=body)
'''


# Mirrors bulk-price's BUG-B plus its foreign/unknown-id signal, and proves the
# batch is atomic on the way out. Shared with the PostgreSQL twin, so it must be
# self-contained and must not depend on scenario ordering.
_GUARDS = r'''
one = make_product('Stok Kapsam A', stock=100)
two = make_product('Stok Kapsam B', stock=200)

# --- BUG-B: an explicitly empty list is refused --------------------------------
response = bulk_stock({'method': 'add', 'value': 5, 'product_ids': []})
assert response.status_code == 400, response.text
assert response.json()['detail'] == 'Ürün seçilmedi', response.text
assert read_stock(one) == 100.0, detail(one)['warehouse_stocks']
assert read_stock(two) == 200.0, detail(two)['warehouse_stocks']

# --- ...while an omitted key still means "every product in this company" -------
before = ok(client.get('/api/products', headers=HA, params={'limit': 2000}))
response = bulk_stock({'method': 'add', 'value': 1})
assert response.status_code == 200, response.text
assert response.json()['updated'] == len(before), (response.text, len(before))
assert read_stock(one) == 101.0
assert read_stock(two) == 201.0

# --- Unknown ids are refused, not reported as a successful no-op ---------------
movements_before = count_movements(one)
response = bulk_stock({'method': 'add', 'value': 7, 'product_ids': [999999]})
assert response.status_code == 404, response.text
assert response.json()['detail'] == 'Ürün bulunamadı', response.text

# A single unknown id rejects the WHOLE batch: the valid row beside it must not
# move, and no movement row may be written for it either.
response = bulk_stock({'method': 'add', 'value': 7, 'product_ids': [one, 999999]})
assert response.status_code == 404, response.text
assert read_stock(one) == 101.0, detail(one)['warehouse_stocks']
assert count_movements(one) == movements_before, detail(one)['movements']

# --- Cross-tenant ids are the same refusal ------------------------------------
company_b = ok(client.post('/api/companies', headers=HA, json={'name': 'Bulk Stock Tenant B'}))['id']
HB = {**HA, 'X-Company-ID': str(company_b)}
theirs = make_product('Tenant B Ürünü', stock=50, headers=HB)

response = bulk_stock({'method': 'add', 'value': 9, 'product_ids': [theirs]})
assert response.status_code == 404, response.text
assert read_stock(theirs, headers=HB) == 50.0

# Mixed batch: a partial-scope bug would surface here and nowhere else.
response = bulk_stock({'method': 'add', 'value': 9, 'product_ids': [one, theirs]})
assert response.status_code == 404, response.text
assert read_stock(one) == 101.0
assert read_stock(theirs, headers=HB) == 50.0

# The reverse direction is equally closed.
response = bulk_stock({'method': 'add', 'value': 3, 'product_ids': [one]}, headers=HB)
assert response.status_code == 404, response.text
assert read_stock(one) == 101.0

# --- A legitimate single-tenant batch still works -----------------------------
# Proves every refusal above failed on scope/selection and not on some unrelated
# validation error that would have made the whole endpoint inert.
response = bulk_stock({'method': 'add', 'value': 4, 'product_ids': [one, two]})
assert response.status_code == 200, response.text
assert response.json()['updated'] == 2, response.text
assert read_stock(one) == 105.0
assert read_stock(two) == 205.0

response = bulk_stock({'method': 'set', 'value': 42, 'product_ids': [two]})
assert response.status_code == 200, response.text
assert read_stock(two) == 42.0

# --- Atomicity under the negative-stock policy --------------------------------
# The 404 path above relies on rejecting before the first write; this proves the
# transaction is equally all-or-nothing when the refusal happens mid-batch. Two
# safe products bracket the offending one so that at least one write is
# guaranteed to precede the failure whatever row order the dialect returns.
early = make_product('Atomik Once', stock=100)
offender = make_product('Atomik Eksi', stock=1)
late = make_product('Atomik Sonra', stock=100)
baseline = {pid: (read_stock(pid), count_movements(pid)) for pid in (early, offender, late)}

response = bulk_stock({'method': 'add', 'value': -5, 'product_ids': [early, offender, late]})
assert response.status_code == 409, response.text
for pid in (early, offender, late):
    quantity, movements = baseline[pid]
    assert read_stock(pid) == quantity, (pid, detail(pid)['warehouse_stocks'])
    assert count_movements(pid) == movements, (pid, detail(pid)['movements'])

# The same batch without the offending row still succeeds.
response = bulk_stock({'method': 'add', 'value': -5, 'product_ids': [early, late]})
assert response.status_code == 200, response.text
assert read_stock(early) == 95.0
assert read_stock(late) == 95.0

client.close()
'''


def _run(scenario: str, database_url: str, timeout: int = 180) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    result = subprocess.run(
        [sys.executable, "-c", _BOOTSTRAP + scenario],
        cwd=BACKEND,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


def run_bulk_stock_guards(database_url: str, timeout: int = 300) -> None:
    """Entry point shared with ``test_bulk_stock_contract_postgresql``."""
    _run(_GUARDS, database_url, timeout=timeout)


def test_bulk_stock_selection_is_explicit_scoped_and_atomic(tmp_path: Path) -> None:
    """BUG-B, the foreign/unknown-id signal, and whole-batch atomicity."""
    run_bulk_stock_guards(f"sqlite:///{(tmp_path / 'bulk-stock-guards.db').as_posix()}")


def test_bulk_stock_rejects_unknown_method_and_warehouse(tmp_path: Path) -> None:
    """INVARIANT: ``method`` is allowlisted and the warehouse stays tenant-scoped.

    Checked in the same test as the selection guards would hide an ordering bug:
    the warehouse 404 deliberately still precedes every selection check, so a
    request that is wrong in both ways keeps answering "Depo bulunamadı".
    """
    _run(r'''
product = make_product('Method Probe', stock=10)

for method in ('multiply', 'set; DROP TABLE products', '', 'ADD'):
    response = bulk_stock({'method': method, 'value': 1, 'product_ids': [product]})
    assert response.status_code == 400, (method, response.status_code, response.text)
    assert response.json()['detail'] == 'Geçersiz işlem', (method, response.text)

# A foreign warehouse is refused before the selection is even considered.
company_b = ok(client.post('/api/companies', headers=HA, json={'name': 'Bulk Stock WH Tenant'}))['id']
HB = {**HA, 'X-Company-ID': str(company_b)}
warehouse_b = ok(client.get('/api/warehouses', headers=HB))[0]['id']

response = bulk_stock({'method': 'add', 'value': 1, 'warehouse_id': warehouse_b,
                       'product_ids': [product]})
assert response.status_code == 404, response.text
assert response.json()['detail'] == 'Depo bulunamadı', response.text

# Wrong warehouse AND an empty selection: the warehouse check still answers first.
response = bulk_stock({'method': 'add', 'value': 1, 'warehouse_id': warehouse_b,
                       'product_ids': []})
assert response.status_code == 404, response.text
assert response.json()['detail'] == 'Depo bulunamadı', response.text

# Nothing was mutated and the products table still exists.
assert read_stock(product) == 10.0, detail(product)['warehouse_stocks']
assert len(ok(client.get('/api/products', headers=HA))) >= 1

client.close()
''', f"sqlite:///{(tmp_path / 'bulk-stock-allowlist.db').as_posix()}")
