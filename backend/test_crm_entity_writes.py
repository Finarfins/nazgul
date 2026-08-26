"""Regression cover for the CRM write endpoints on customers and suppliers.

Covered surface (all previously reachable only through quarantined files):

    POST   /api/{customers,suppliers}/{id}/notes
    DELETE /api/{customers,suppliers}/{id}/notes/{note_id}
    POST   /api/{customers,suppliers}/{id}/contacts
    DELETE /api/{customers,suppliers}/{id}/contacts/{contact_id}
    POST   /api/{customers,suppliers}/{id}/tasks
    PATCH  /api/{customers,suppliers}/{id}/tasks/{task_id}
    DELETE /api/{customers,suppliers}/{id}/tasks/{task_id}

``test_v2_5_cari_crm.py`` -- the only file that previously touched notes -- is
quarantined and copies a gitignored ``veriler.db`` fixture, so it can never run in
CI. ``test_v3_customer_center.py`` (un-quarantined alongside these tests) covers the
*customer* contact/task pair through the balance/drill-down lens, but does not touch
notes, the whole supplier side, RBAC, or any boundary input. These tests close that
gap using the isolated-subprocess pattern with a clean migrated SQLite database per
scenario.

Note the deliberate permission asymmetry verified below: customer CRM writes are
gated by ``sales`` while supplier CRM writes are gated by ``purchases``
(``app/auth.py`` ``required_permission``), so ``satis`` and ``depo`` are each
allowed on exactly one side.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent

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
    'current_password': 'admin123', 'new_password': 'CrmWrite!Rot2026'}))
HA['Authorization'] = 'Bearer ' + _rotated['access_token']


def make_entity(kind, name, headers=None):
    """kind is 'customers' or 'suppliers'."""
    return ok(client.post('/api/' + kind, headers=headers or HA, json={
        'name': name, 'opening_balance': 0, 'risk_limit': 5000,
        'payment_term_days': 30, 'is_active': True}))['id']


def detail(kind, entity_id, headers=None):
    return ok(client.get('/api/%s/%s' % (kind, entity_id), headers=headers or HA))


def truthy(value):
    # BOOLEAN round-trips as 1/0 on SQLite and True/False on PostgreSQL.
    return value in (1, True)


def provision(role, password, rotated):
    created = client.post('/api/users', headers=HA, json={
        'username': 'crm_' + role, 'display_name': 'CRM ' + role,
        'password': password, 'role': role})
    assert created.status_code == 201, created.text
    body = login('crm_' + role, password)
    headers = {'Authorization': 'Bearer ' + body['access_token'],
               'X-Company-ID': str(body['companies'][0]['id'])}
    changed = ok(client.post('/api/auth/change-password', headers=headers, json={
        'current_password': password, 'new_password': rotated}))
    headers['Authorization'] = 'Bearer ' + changed['access_token']
    return headers
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


_LIFECYCLE = r'''
KIND = '__KIND__'
entity = make_entity(KIND, 'CRM %s Lifecycle' % KIND)
base = '/api/%s/%s' % (KIND, entity)

# --- notes: create, normalize, surface on detail, delete --------------------
note = ok(client.post(base + '/notes', headers=HA, json={'note': '  Called   the customer  '}))
# add_note collapses internal whitespace and trims; the stored value is canonical.
assert note['note'] == 'Called the customer', note
assert note['created_by'] == 'admin', note
listed = detail(KIND, entity)['notes']
assert [row['id'] for row in listed] == [note['id']], listed
assert listed[0]['note'] == 'Called the customer', listed

second_note = ok(client.post(base + '/notes', headers=HA, json={'note': 'Second contact'}))
assert len(detail(KIND, entity)['notes']) == 2

removed = client.delete('%s/notes/%s' % (base, note['id']), headers=HA)
assert removed.status_code == 204, removed.text
remaining = detail(KIND, entity)['notes']
assert [row['id'] for row in remaining] == [second_note['id']], remaining
# Deleting the same note twice must not report success a second time.
assert client.delete('%s/notes/%s' % (base, note['id']), headers=HA).status_code == 404

# --- contacts: create, primary-flag exclusivity, delete ---------------------
first_contact = ok(client.post(base + '/contacts', headers=HA, json={
    'name': '  Ayse   Demir ', 'role': 'Muhasebe', 'phone': '05551112233',
    'email': 'ayse@example.com', 'is_primary': True, 'notes': 'EFT'}))
assert first_contact['name'] == 'Ayse Demir', first_contact
assert truthy(first_contact['is_primary']), first_contact

second_contact = ok(client.post(base + '/contacts', headers=HA, json={
    'name': 'Mehmet Kaya', 'role': 'Satin Alma', 'is_primary': True}))

contacts = {row['id']: row for row in detail(KIND, entity)['contacts']}
assert set(contacts) == {first_contact['id'], second_contact['id']}, contacts
# Promoting a new primary contact demotes the previous one -- exactly one primary.
assert truthy(contacts[second_contact['id']]['is_primary']), contacts
assert not truthy(contacts[first_contact['id']]['is_primary']), contacts
assert sum(1 for row in contacts.values() if truthy(row['is_primary'])) == 1, contacts

assert client.delete('%s/contacts/%s' % (base, first_contact['id']), headers=HA).status_code == 204
assert [row['id'] for row in detail(KIND, entity)['contacts']] == [second_contact['id']]
assert client.delete('%s/contacts/%s' % (base, first_contact['id']), headers=HA).status_code == 404

# --- tasks: create, status update, completion stamp, delete -----------------
task = ok(client.post(base + '/tasks', headers=HA, json={
    'title': ' Collect  payment ', 'due_date': '2026-07-20', 'priority': 'high',
    'assigned_to': 'Berkay', 'notes': 'Call in the morning'}))
assert task['title'] == 'Collect payment', task
assert task['status'] == 'open' and task['completed_at'] is None, task

stored = detail(KIND, entity)['tasks'][0]
assert stored['id'] == task['id'] and stored['status'] == 'open', stored
assert stored['priority'] == 'high' and stored['due_date'] == '2026-07-20', stored

updated = ok(client.patch('%s/tasks/%s' % (base, task['id']), headers=HA,
                          json={'status': 'completed'}))
assert updated == {'id': task['id'], 'status': 'completed'}, updated
stored = detail(KIND, entity)['tasks'][0]
assert stored['status'] == 'completed' and stored['completed_at'], stored

# Reopening clears the completion stamp again.
ok(client.patch('%s/tasks/%s' % (base, task['id']), headers=HA, json={'status': 'open'}))
stored = detail(KIND, entity)['tasks'][0]
assert stored['status'] == 'open' and stored['completed_at'] is None, stored

assert client.delete('%s/tasks/%s' % (base, task['id']), headers=HA).status_code == 204
assert detail(KIND, entity)['tasks'] == []
assert client.delete('%s/tasks/%s' % (base, task['id']), headers=HA).status_code == 404

client.close()
'''


def test_customer_crm_write_lifecycle(tmp_path: Path) -> None:
    """Notes, contacts and tasks round-trip end to end on a customer."""
    _run(_LIFECYCLE.replace('__KIND__', 'customers'), tmp_path, "crm-customer-lifecycle")


def test_supplier_crm_write_lifecycle(tmp_path: Path) -> None:
    """The supplier side must behave identically -- it is a separate router."""
    _run(_LIFECYCLE.replace('__KIND__', 'suppliers'), tmp_path, "crm-supplier-lifecycle")


def test_crm_writes_are_tenant_scoped(tmp_path: Path) -> None:
    """INVARIANT: no CRM row can be created, mutated or deleted across tenants.

    Every helper in ``app/crm.py`` filters on ``company_id`` *and* ``entity_type``,
    so this also covers the entity-type confusion case (deleting a customer note
    through the supplier path).
    """
    _run(r'''
customer = make_entity('customers', 'Tenant A Customer')
supplier = make_entity('suppliers', 'Tenant A Supplier')
note = ok(client.post('/api/customers/%s/notes' % customer, headers=HA, json={'note': 'Private'}))
contact = ok(client.post('/api/customers/%s/contacts' % customer, headers=HA, json={'name': 'Private Contact'}))
task = ok(client.post('/api/customers/%s/tasks' % customer, headers=HA, json={'title': 'Private Task'}))

company_b = ok(client.post('/api/companies', headers=HA, json={'name': 'CRM Tenant B'}))['id']
HB = {**HA, 'X-Company-ID': str(company_b)}

# 1. The parent entity itself is invisible from the other tenant.
assert client.get('/api/customers/%s' % customer, headers=HB).status_code == 404

# 2. Creating a child row against a foreign parent is refused, not silently
#    attached to the caller's tenant.
assert client.post('/api/customers/%s/notes' % customer, headers=HB,
                   json={'note': 'Injected'}).status_code == 404
assert client.post('/api/customers/%s/contacts' % customer, headers=HB,
                   json={'name': 'Injected'}).status_code == 404
assert client.post('/api/customers/%s/tasks' % customer, headers=HB,
                   json={'title': 'Injected'}).status_code == 404

# 3. Mutating or deleting an existing foreign child row is refused.
assert client.delete('/api/customers/%s/notes/%s' % (customer, note['id']), headers=HB).status_code == 404
assert client.delete('/api/customers/%s/contacts/%s' % (customer, contact['id']), headers=HB).status_code == 404
assert client.patch('/api/customers/%s/tasks/%s' % (customer, task['id']), headers=HB,
                    json={'status': 'completed'}).status_code == 404
assert client.delete('/api/customers/%s/tasks/%s' % (customer, task['id']), headers=HB).status_code == 404

# 4. Entity-type confusion inside the SAME tenant is also closed: a customer note
#    cannot be reached through the supplier path even with the right ids.
assert client.delete('/api/suppliers/%s/notes/%s' % (supplier, note['id']), headers=HA).status_code == 404
assert client.delete('/api/suppliers/%s/contacts/%s' % (supplier, contact['id']), headers=HA).status_code == 404
assert client.patch('/api/suppliers/%s/tasks/%s' % (supplier, task['id']), headers=HA,
                    json={'status': 'completed'}).status_code == 404

# 5. After every refused attempt the original rows are untouched.
current = detail('customers', customer)
assert [row['id'] for row in current['notes']] == [note['id']], current['notes']
assert [row['id'] for row in current['contacts']] == [contact['id']], current['contacts']
assert current['tasks'][0]['id'] == task['id'] and current['tasks'][0]['status'] == 'open', current['tasks']

client.close()
''', tmp_path, "crm-tenant")


def test_crm_writes_follow_sales_and_purchases_permissions(tmp_path: Path) -> None:
    """INVARIANT: customer CRM needs ``sales``; supplier CRM needs ``purchases``.

    ``satis`` (sales only) and ``depo`` (stock + purchases) are mirror images here,
    which makes the split observable rather than incidental.
    """
    _run(r'''
customer = make_entity('customers', 'Permission Customer')
supplier = make_entity('suppliers', 'Permission Supplier')

sales = provision('satis', 'SalesInit!2026', 'SalesRotated!2026')
depot = provision('depo', 'StockInit!2026', 'StockRotated!2026')
report = provision('rapor', 'ReportInit!26', 'ReportRotated!2026')


def attempt(headers, kind, entity_id):
    payloads = (
        ('post', '/api/%s/%s/notes' % (kind, entity_id), {'note': 'Attempt'}),
        ('post', '/api/%s/%s/contacts' % (kind, entity_id), {'name': 'Attempt'}),
        ('post', '/api/%s/%s/tasks' % (kind, entity_id), {'title': 'Attempt'}),
    )
    return [getattr(client, method)(path, headers=headers, json=payload).status_code
            for method, path, payload in payloads]


# Reporting role holds neither permission -> refused on both sides.
assert attempt(report, 'customers', customer) == [403, 403, 403]
assert attempt(report, 'suppliers', supplier) == [403, 403, 403]
# ...but its baseline read access is untouched.
assert client.get('/api/customers', headers=report).status_code == 200
assert client.get('/api/suppliers', headers=report).status_code == 200

# Sales role: customers yes, suppliers no.
assert attempt(sales, 'customers', customer) == [201, 201, 201]
assert attempt(sales, 'suppliers', supplier) == [403, 403, 403]

# Depot role (purchases, no sales): the exact mirror.
assert attempt(depot, 'suppliers', supplier) == [201, 201, 201]
assert attempt(depot, 'customers', customer) == [403, 403, 403]

# Task status updates follow the same split as creation.
sales_task = ok(client.post('/api/customers/%s/tasks' % customer, headers=sales, json={'title': 'Sales task'}))
assert client.patch('/api/customers/%s/tasks/%s' % (customer, sales_task['id']),
                    headers=depot, json={'status': 'completed'}).status_code == 403
assert client.patch('/api/customers/%s/tasks/%s' % (customer, sales_task['id']),
                    headers=sales, json={'status': 'completed'}).status_code == 200

# Only the authorized writes landed: 3 from the sales loop + 1 explicit task, and
# nothing at all from the refused attempts.
current = detail('customers', customer)
assert len(current['notes']) == 1, current['notes']
assert len(current['contacts']) == 1, current['contacts']
assert len(current['tasks']) == 2, current['tasks']

client.close()
''', tmp_path, "crm-rbac")


def test_crm_write_boundaries_are_rejected(tmp_path: Path) -> None:
    """Blank-after-normalization values and unknown enums are refused, not stored.

    ``app/crm.py`` collapses whitespace before validating, so a payload that passes
    Pydantic's ``min_length`` can still be empty by the time it reaches the insert.
    That second gate is what these cases exercise.
    """
    _run(r'''
customer = make_entity('customers', 'Boundary Customer')
base = '/api/customers/%s' % customer

# Blank after whitespace normalization -> 400 from the CRM layer.
for path, payload in ((base + '/notes', {'note': '   '}),
                      (base + '/contacts', {'name': '  '}),
                      (base + '/tasks', {'title': '   '})):
    response = client.post(path, headers=HA, json=payload)
    assert response.status_code == 400, (path, response.status_code, response.text)

# Empty string -> 422 from Pydantic's min_length before the router body runs.
for path, payload in ((base + '/notes', {'note': ''}),
                      (base + '/tasks', {'title': ''})):
    response = client.post(path, headers=HA, json=payload)
    assert response.status_code == 422, (path, response.status_code, response.text)

# Over-length values are bounded by the schema.
assert client.post(base + '/notes', headers=HA, json={'note': 'x' * 2001}).status_code == 422
assert client.post(base + '/tasks', headers=HA, json={'title': 'x' * 251}).status_code == 422
assert client.post(base + '/contacts', headers=HA, json={'name': 'x' * 151}).status_code == 422

# Unknown enum values are refused rather than coerced.
assert client.post(base + '/tasks', headers=HA,
                   json={'title': 'Bad priority', 'priority': 'urgent'}).status_code == 400
task = ok(client.post(base + '/tasks', headers=HA, json={'title': 'Status probe'}))
assert client.patch('%s/tasks/%s' % (base, task['id']), headers=HA,
                    json={'status': 'archived'}).status_code == 400
assert detail('customers', customer)['tasks'][0]['status'] == 'open'

# Missing parent entity -> 404, never a dangling child row.
for path, payload in (('/api/customers/999999/notes', {'note': 'Orphan'}),
                      ('/api/customers/999999/contacts', {'name': 'Orphan'}),
                      ('/api/customers/999999/tasks', {'title': 'Orphan'})):
    response = client.post(path, headers=HA, json=payload)
    assert response.status_code == 404, (path, response.status_code, response.text)

# Missing child id -> 404 on every removal path.
assert client.delete(base + '/notes/999999', headers=HA).status_code == 404
assert client.delete(base + '/contacts/999999', headers=HA).status_code == 404
assert client.delete(base + '/tasks/999999', headers=HA).status_code == 404
assert client.patch(base + '/tasks/999999', headers=HA, json={'status': 'completed'}).status_code == 404

# Nothing above created a row: only the single valid "Status probe" task exists.
current = detail('customers', customer)
assert current['notes'] == [], current['notes']
assert current['contacts'] == [], current['contacts']
assert len(current['tasks']) == 1, current['tasks']

client.close()
''', tmp_path, "crm-boundaries")
