"""Regression cover for ``POST /api/products/bulk-price``.

This endpoint performs a **tenant-wide bulk money mutation** and until now its only
coverage lived in ``test_inventory_reports.py``, which is quarantined by
``conftest.collect_ignore`` *and* depends on a committed ``veriler.db`` fixture that
is gitignored -- so it can never run in CI. These tests replace that coverage with
the modern pattern: one isolated subprocess per scenario, each with its own clean
SQLite database created by the real migration chain.

These tests first landed as characterization -- they pinned three real defects the
endpoint shipped with. Those defects are now fixed and every assertion below states
the **correct** behaviour:

* ``BUG-A`` -- percentage math moved out of SQL into ``app.money``. It used to be
  ``ROUND(col*(1+:v/100),2)``; on SQLite the bound integer made ``:v/100`` truncate,
  so +10%/+25%/+50% changed nothing while still reporting success, and the two
  dialects disagreed. Both now produce the same Decimal result.
* ``BUG-B`` -- an explicitly empty ``product_ids`` list is refused with 400 instead
  of silently repricing the whole catalogue. Omitting the key still means "all".
* ``BUG-C`` -- a computed price of zero or below is refused with 400, before write.

Plus the foreign/unknown-id signal, which used to be 200 ``{"updated": 0}`` and is
now 404 so an unauthorized or unknown id is distinguishable from a real no-op.

See ``docs/bulk-price-bug-raporu.md`` for the findings and their evidence.
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
    'current_password': 'admin123', 'new_password': 'BulkPrice!Rot2026'}))
HA['Authorization'] = 'Bearer ' + _rotated['access_token']


def make_product(name, sale, purchase=10, vat=20, headers=None):
    return ok(client.post('/api/products', headers=headers or HA, json={
        'name': name, 'purchase_price': purchase, 'sale_price': sale,
        'vat_rate': vat, 'stock': 0, 'unit': 'Adet'}))['id']


def read_product(product_id, headers=None):
    rows = ok(client.get('/api/products', headers=headers or HA, params={'limit': 2000}))
    for row in rows:
        if row['id'] == product_id:
            return row
    raise AssertionError('product %s not visible' % product_id)


def bulk_price(payload, headers=None):
    return client.post('/api/products/bulk-price', headers=headers or HA, json=payload)


def money(value):
    # NUMERIC is serialized as a JSON number on SQLite and as a string on
    # PostgreSQL; normalize both to a 2dp float for comparison.
    return round(float(value), 2)
'''


def _run(scenario: str, tmp_path: Path, name: str, timeout: int = 180) -> None:
    database = tmp_path / f"{name}.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
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


def test_bulk_price_applies_set_and_fixed_without_touching_the_vat_rate(tmp_path: Path) -> None:
    """Happy path: ``set`` and ``fixed`` land exactly, at 2dp, on the chosen field only.

    Unit prices in this project are VAT-inclusive (see ``app/money.py``
    ``apply_global_discount``), so a bulk price change must move the gross price and
    leave ``vat_rate`` untouched -- otherwise every document built on that rate would
    silently re-base.
    """
    _run(r'''
first = make_product('Bulk Set A', sale=100, purchase=40, vat=20)
second = make_product('Bulk Set B', sale=250, purchase=90, vat=10)

# --- set --------------------------------------------------------------------
response = bulk_price({'field': 'sale_price', 'method': 'set', 'value': 150.50,
                       'product_ids': [first, second]})
assert response.status_code == 200, response.text
assert response.json() == {'updated': 2}, response.text

for product_id, expected_vat, expected_purchase in ((first, 20, 40), (second, 10, 90)):
    row = read_product(product_id)
    assert money(row['sale_price']) == 150.50, row
    # VAT-inclusive contract: the rate is untouched by a gross price change.
    assert money(row['vat_rate']) == expected_vat, row
    # The unselected money column must not move either.
    assert money(row['purchase_price']) == expected_purchase, row

# --- fixed (absolute delta) -------------------------------------------------
response = bulk_price({'field': 'sale_price', 'method': 'fixed', 'value': 10.25,
                       'product_ids': [first]})
assert response.status_code == 200, response.text
assert money(read_product(first)['sale_price']) == 160.75, read_product(first)

# --- 2dp money quantum ------------------------------------------------------
cent = make_product('Bulk Cent', sale=19.99, purchase=5, vat=20)
assert bulk_price({'field': 'sale_price', 'method': 'fixed', 'value': 0.01,
                   'product_ids': [cent]}).status_code == 200
assert money(read_product(cent)['sale_price']) == 20.00, read_product(cent)

# --- purchase_price is independently addressable ----------------------------
assert bulk_price({'field': 'purchase_price', 'method': 'set', 'value': 77.77,
                   'product_ids': [first]}).status_code == 200
row = read_product(first)
assert money(row['purchase_price']) == 77.77, row
assert money(row['sale_price']) == 160.75, row

client.close()
''', tmp_path, "bulk-price-happy")


def test_bulk_price_cannot_reach_another_tenants_products(tmp_path: Path) -> None:
    """INVARIANT: the update is company-scoped and fails closed on foreign ids.

    An id outside the caller's company is answered 404 ("Ürün bulunamadı") rather
    than 200 ``{"updated": 0}``, so an unauthorized or unknown id cannot be mistaken
    for a successful no-op. A batch containing one is rejected **whole**: no partial
    application.
    """
    _run(r'''
mine = make_product('Tenant A Product', sale=100)

company_b = ok(client.post('/api/companies', headers=HA, json={'name': 'Bulk Tenant B'}))['id']
HB = {**HA, 'X-Company-ID': str(company_b)}
theirs = make_product('Tenant B Product', sale=100, headers=HB)

# 1. Foreign id alone: refused, and the other tenant's price is intact.
response = bulk_price({'field': 'sale_price', 'method': 'set', 'value': 1,
                       'product_ids': [theirs]})
assert response.status_code == 404, response.text
assert money(read_product(theirs, headers=HB)['sale_price']) == 100.00

# 2. Mixed batch: the whole request is rejected. This is the important one -- a
#    partial-scope bug would surface here and nowhere else, and the caller's OWN
#    row must not move either.
response = bulk_price({'field': 'sale_price', 'method': 'set', 'value': 55,
                       'product_ids': [mine, theirs]})
assert response.status_code == 404, response.text
assert money(read_product(mine)['sale_price']) == 100.00
assert money(read_product(theirs, headers=HB)['sale_price']) == 100.00

# A legitimate single-tenant batch still works, so step 2 failed on scope and not
# on some unrelated validation error.
response = bulk_price({'field': 'sale_price', 'method': 'set', 'value': 55,
                       'product_ids': [mine]})
assert response.status_code == 200, response.text
assert response.json() == {'updated': 1}, response.text
assert money(read_product(mine)['sale_price']) == 55.00

# 3. The reverse direction is equally closed.
response = bulk_price({'field': 'sale_price', 'method': 'set', 'value': 3,
                       'product_ids': [mine]}, headers=HB)
assert response.status_code == 404, response.text
assert money(read_product(mine)['sale_price']) == 55.00

# 4. A tenant-wide call (no ids at all) must still stop at the tenant boundary.
response = bulk_price({'field': 'sale_price', 'method': 'set', 'value': 9}, headers=HB)
assert response.status_code == 200, response.text
assert money(read_product(mine)['sale_price']) == 55.00
assert money(read_product(theirs, headers=HB)['sale_price']) == 9.00

client.close()
''', tmp_path, "bulk-price-tenant")


def test_bulk_price_requires_the_stock_permission(tmp_path: Path) -> None:
    """INVARIANT: only roles carrying ``stock`` may run a bulk price mutation.

    ``required_permission`` maps ``POST /api/products/*`` to ``stock``
    (``app/auth.py``). Sales, accounting and reporting roles must be refused even
    though they can read the very same products.
    """
    _run(r'''
product = make_product('Permission Probe', sale=100)

REFUSED = (('satis', 'SalesInit!2026'), ('muhasebe', 'AcctInit!2026'), ('rapor', 'ReportInit!26'))
ALLOWED = (('depo', 'StockInit!2026'),)

def provision(role, password):
    created = client.post('/api/users', headers=HA, json={
        'username': 'role_' + role, 'display_name': 'Role ' + role,
        'password': password, 'role': role})
    assert created.status_code == 201, created.text
    body = login('role_' + role, password)
    headers = {'Authorization': 'Bearer ' + body['access_token'],
               'X-Company-ID': str(body['companies'][0]['id'])}
    rotated = ok(client.post('/api/auth/change-password', headers=headers, json={
        'current_password': password, 'new_password': role.title() + 'Rotated!2026'}))
    headers['Authorization'] = 'Bearer ' + rotated['access_token']
    return headers

for role, password in REFUSED:
    headers = provision(role, password)
    # Baseline read access is intact -- this is an authorization boundary, not a
    # broken session.
    assert client.get('/api/products', headers=headers).status_code == 200, role
    for payload in ({'field': 'sale_price', 'method': 'percent', 'value': 10, 'product_ids': [product]},
                    {'field': 'sale_price', 'method': 'set', 'value': 1},
                    {'field': 'purchase_price', 'method': 'fixed', 'value': 5, 'product_ids': [product]}):
        response = bulk_price(payload, headers=headers)
        assert response.status_code == 403, (role, payload, response.status_code, response.text)
    assert money(read_product(product)['sale_price']) == 100.00, role

for role, password in ALLOWED:
    headers = provision(role, password)
    response = bulk_price({'field': 'sale_price', 'method': 'set', 'value': 123.45,
                           'product_ids': [product]}, headers=headers)
    assert response.status_code == 200, (role, response.text)
    assert money(read_product(product)['sale_price']) == 123.45

client.close()
''', tmp_path, "bulk-price-rbac")


def test_bulk_price_rejects_unknown_field_and_method(tmp_path: Path) -> None:
    """INVARIANT: ``field`` and ``method`` are allowlisted before reaching SQL.

    Both are interpolated into the UPDATE statement, so the allowlist is the only
    thing standing between this endpoint and SQL injection. Verified explicitly
    rather than trusted.
    """
    _run(r'''
product = make_product('Allowlist Probe', sale=100)

for payload in (
    {'field': 'stock', 'method': 'set', 'value': 1, 'product_ids': [product]},
    {'field': 'name', 'method': 'set', 'value': 1, 'product_ids': [product]},
    {'field': 'sale_price=1--', 'method': 'set', 'value': 1, 'product_ids': [product]},
    {'field': 'sale_price', 'method': 'multiply', 'value': 2, 'product_ids': [product]},
    {'field': 'sale_price', 'method': 'set; DROP TABLE products', 'value': 1, 'product_ids': [product]},
):
    response = bulk_price(payload)
    assert response.status_code == 400, (payload, response.status_code, response.text)

# Nothing was mutated and the products table still exists.
assert money(read_product(product)['sale_price']) == 100.00
assert len(ok(client.get('/api/products', headers=HA))) >= 1

client.close()
''', tmp_path, "bulk-price-allowlist")


def test_bulk_price_percentage_is_exact_on_every_dialect(tmp_path: Path) -> None:
    """BUG-A: percentages are computed in Decimal, identically on SQLite and PG.

    The old SQL expression ``ROUND(col*(1+:v/100),2)`` truncated on SQLite because
    the bound value arrived as an integer, so anything that was not a multiple of
    100 was silently wrong (+10% left the price untouched). No dialect branch is
    needed any more: the arithmetic happens in ``app.money`` before the write.
    """
    _run(r'''
cases = [(10, 100.00, 110.00), (25, 100.00, 125.00), (50, 100.00, 150.00),
         (100, 100.00, 200.00), (150, 100.00, 250.00), (200, 100.00, 300.00),
         (1, 100.00, 101.00), (-10, 100.00, 90.00), (-99, 100.00, 1.00)]
for percent, start, expected in cases:
    product = make_product('Percent %s' % percent, sale=start)
    response = bulk_price({'field': 'sale_price', 'method': 'percent',
                           'value': percent, 'product_ids': [product]})
    assert response.status_code == 200, (percent, response.text)
    assert response.json() == {'updated': 1}, response.text
    assert money(read_product(product)['sale_price']) == expected, (percent, read_product(product))

# Rounding is ROUND_HALF_UP at the 2dp money quantum, not SQL's dialect default.
# 33.33 * 1.15 = 38.3295 -> 38.33
odd = make_product('Percent Rounding', sale=33.33)
assert bulk_price({'field': 'sale_price', 'method': 'percent', 'value': 15,
                   'product_ids': [odd]}).status_code == 200
assert money(read_product(odd)['sale_price']) == 38.33, read_product(odd)

# A batch applies the percentage per row, not one shared value.
first = make_product('Batch Percent A', sale=100)
second = make_product('Batch Percent B', sale=250)
response = bulk_price({'field': 'sale_price', 'method': 'percent', 'value': 10,
                       'product_ids': [first, second]})
assert response.json() == {'updated': 2}, response.text
assert money(read_product(first)['sale_price']) == 110.00
assert money(read_product(second)['sale_price']) == 275.00

client.close()
''', tmp_path, "bulk-price-percent")


def test_bulk_price_refuses_empty_selection_and_non_positive_results(tmp_path: Path) -> None:
    """BUG-B and BUG-C: the selection and the resulting price are both validated.

    An explicitly empty ``product_ids`` list is a client bug ("nothing selected"),
    not a request to reprice the catalogue, so it is refused. Omitting the key
    entirely still means "every product" -- that half is intentional and stays.
    Any computed price of zero or below is refused before the write, so no batch
    can land a partially applied update.
    """
    _run(r'''
# --- BUG-B: empty list is refused; omitted key still means "all products" ---
one = make_product('Scope A', sale=100)
two = make_product('Scope B', sale=200)

response = bulk_price({'field': 'sale_price', 'method': 'set', 'value': 1,
                       'product_ids': []})
assert response.status_code == 400, response.text
assert response.json()['detail'] == 'Ürün seçilmedi', response.text
assert money(read_product(one)['sale_price']) == 100.00
assert money(read_product(two)['sale_price']) == 200.00

before = ok(client.get('/api/products', headers=HA, params={'limit': 2000}))
response = bulk_price({'field': 'sale_price', 'method': 'set', 'value': 2})
assert response.status_code == 200, response.text
assert response.json() == {'updated': len(before)}, (response.text, len(before))
after = ok(client.get('/api/products', headers=HA, params={'limit': 2000}))
assert all(money(row['sale_price']) == 2.00 for row in after), after

# --- BUG-C: zero and negative results are refused before the write ----------
target = make_product('Bound Target', sale=100)
for payload, detail in (
    ({'field': 'sale_price', 'method': 'set', 'value': -50}, 'Fiyat sıfır veya negatif olamaz'),
    ({'field': 'sale_price', 'method': 'set', 'value': 0}, 'Fiyat sıfır veya negatif olamaz'),
    ({'field': 'sale_price', 'method': 'fixed', 'value': -100}, 'Fiyat sıfır veya negatif olamaz'),
    ({'field': 'sale_price', 'method': 'fixed', 'value': -150}, 'Fiyat sıfır veya negatif olamaz'),
    ({'field': 'sale_price', 'method': 'percent', 'value': -100}, 'Fiyat sıfır veya negatif olamaz'),
    ({'field': 'sale_price', 'method': 'percent', 'value': -200}, 'Yüzde değeri -100\'ün altında olamaz'),
    ({'field': 'purchase_price', 'method': 'set', 'value': -1}, 'Fiyat sıfır veya negatif olamaz'),
):
    response = bulk_price({**payload, 'product_ids': [target]})
    assert response.status_code == 400, (payload, response.status_code, response.text)
    assert response.json()['detail'] == detail, (payload, response.text)
assert money(read_product(target)['sale_price']) == 100.00, read_product(target)
assert money(read_product(target)['purchase_price']) == 10.00, read_product(target)

# A single offending row rejects the WHOLE batch -- no partial application.
safe = make_product('Bound Safe', sale=500)
low = make_product('Bound Low', sale=40)
response = bulk_price({'field': 'sale_price', 'method': 'fixed', 'value': -50,
                       'product_ids': [safe, low]})
assert response.status_code == 400, response.text
assert money(read_product(safe)['sale_price']) == 500.00, read_product(safe)
assert money(read_product(low)['sale_price']) == 40.00, read_product(low)

# The same batch without the offending row still succeeds.
response = bulk_price({'field': 'sale_price', 'method': 'fixed', 'value': -50,
                       'product_ids': [safe]})
assert response.status_code == 200, response.text
assert money(read_product(safe)['sale_price']) == 450.00

# --- Unknown ids are refused, not reported as a successful no-op ------------
response = bulk_price({'field': 'sale_price', 'method': 'set', 'value': 5,
                       'product_ids': [999999]})
assert response.status_code == 404, response.text
assert response.json()['detail'] == 'Ürün bulunamadı', response.text

response = bulk_price({'field': 'sale_price', 'method': 'set', 'value': 5,
                       'product_ids': [safe, 999999]})
assert response.status_code == 404, response.text
assert money(read_product(safe)['sale_price']) == 450.00

client.close()
''', tmp_path, "bulk-price-boundaries")
