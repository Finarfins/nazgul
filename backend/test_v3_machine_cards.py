"""V3.0 Machine Cards backend regression tests.

Each scenario runs in an isolated subprocess against a fresh SQLite database and
drives the real HTTP surface via TestClient. The shared PREAMBLE authenticates as
the bootstrap admin, honours the V2.9 security contract (12-char rotation +
adopting the rotated access token), and exposes small ``make``/``mk_customer``
helpers.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent

PREAMBLE = r'''
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
assert login.status_code == 200, login.text
body = login.json()
company_a = body['companies'][0]['id']
headers = {'Authorization':'Bearer '+body['access_token'],'X-Company-ID':str(company_a)}
changed = client.post('/api/auth/change-password', headers=headers, json={
    'current_password':'admin123','new_password':'AdminRot!2026x'})
assert changed.status_code == 200, changed.text
headers['Authorization'] = 'Bearer ' + changed.json()['access_token']

def make(h=None, **over):
    payload = {'brand':'John Deere','model':'6155R'}
    payload.update(over)
    return client.post('/api/machines', headers=h or headers, json=payload)

def mk_customer(h=None, name='Cari A'):
    r = client.post('/api/customers', headers=h or headers, json={
        'name':name,'opening_balance':0,'risk_limit':0,'payment_term_days':0,'is_active':True})
    assert r.status_code == 201, r.text
    return r.json()['id']

def mk_user(username, role, initial='InitialPass!23'):
    # Create a company-A user of the given role, then satisfy the forced initial
    # rotation and adopt the rotated token, returning ready-to-use headers.
    cr = client.post('/api/users', headers=headers, json={
        'username':username,'display_name':username,'password':initial,'role':role})
    assert cr.status_code == 201, cr.text
    lb = client.post('/api/auth/login', json={'username':username,'password':initial}).json()
    h = {'Authorization':'Bearer '+lb['access_token'],'X-Company-ID':str(company_a)}
    rot = client.post('/api/auth/change-password', headers=h, json={
        'current_password':initial,'new_password':'StrongPass!2026xy'})
    assert rot.status_code == 200, rot.text
    h['Authorization'] = 'Bearer ' + rot.json()['access_token']
    return h
'''


def _run(tmp_path: Path, script: str) -> None:
    database = tmp_path / "machines.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    result = subprocess.run(
        [sys.executable, "-c", PREAMBLE + script],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


def test_create_and_detail_machine(tmp_path: Path) -> None:
    _run(tmp_path, r'''
customer_id = mk_customer()
resp = make(customer_id=customer_id, brand='  John   Deere  ', model='6155R',
            manufacturer='Deere', variant='Premium', serial_number='SN-100',
            chassis_number=' CH-100 ', registration_number='34 ABC 34',
            engine_number='ENG-1', model_year=2022, working_hours=1234.5,
            status='active', notes='ilk makine')
assert resp.status_code == 201, resp.text
created = resp.json()
assert created['brand'] == 'John Deere', created         # trimmed/collapsed
assert created['chassis_number'] == 'CH-100', created     # trimmed
assert created['customer_id'] == customer_id
assert created['customer_name'] is not None
assert created['is_active'] is True

detail = client.get(f"/api/machines/{created['id']}", headers=headers)
assert detail.status_code == 200, detail.text
assert detail.json()['serial_number'] == 'SN-100'
assert float(detail.json()['working_hours']) == 1234.5, detail.text

# Blank optional identifiers normalise to NULL (and multiple NULLs never collide).
m1 = make(chassis_number='   ', serial_number='')
m2 = make(chassis_number='', serial_number='   ')
assert m1.status_code == 201 and m2.status_code == 201, (m1.text, m2.text)
assert m1.json()['chassis_number'] is None and m1.json()['serial_number'] is None
''')


def test_list_and_filters(tmp_path: Path) -> None:
    _run(tmp_path, r'''
cust = mk_customer()
a = make(brand='Case IH', model='Puma 165', customer_id=cust)
b = make(brand='New Holland', model='T6', serial_number='NH-1')
assert a.status_code == 201 and b.status_code == 201

full = client.get('/api/machines', headers=headers)
assert full.status_code == 200, full.text
assert len(full.json()) == 2, full.text

by_q = client.get('/api/machines', headers=headers, params={'q':'holland'})
assert [m['model'] for m in by_q.json()] == ['T6'], by_q.text

by_customer = client.get('/api/machines', headers=headers, params={'customer_id':cust})
assert len(by_customer.json()) == 1 and by_customer.json()[0]['customer_id'] == cust

# pagination
page = client.get('/api/machines', headers=headers, params={'limit':1,'offset':0})
assert len(page.json()) == 1, page.text
''')


def test_update_machine(tmp_path: Path) -> None:
    _run(tmp_path, r'''
mid = make(model='6155R', working_hours=100).json()['id']
resp = client.put(f'/api/machines/{mid}', headers=headers, json={
    'brand':'John Deere','model':'6175R','working_hours':250.0,'status':'maintenance'})
assert resp.status_code == 200, resp.text
assert resp.json()['model'] == '6175R' and resp.json()['status'] == 'maintenance'
detail = client.get(f'/api/machines/{mid}', headers=headers).json()
assert float(detail['working_hours']) == 250.0, detail
''')


def test_deactivate_reactivate_and_inactive_filter(tmp_path: Path) -> None:
    _run(tmp_path, r'''
mid = make(model='ToDeactivate').json()['id']
off = client.patch(f'/api/machines/{mid}/active', headers=headers, json={'is_active':False})
assert off.status_code == 200 and off.json()['is_active'] is False, off.text

active_only = client.get('/api/machines', headers=headers, params={'active':'active'})
assert all(m['id'] != mid for m in active_only.json()), active_only.text
inactive = client.get('/api/machines', headers=headers, params={'active':'inactive'})
assert any(m['id'] == mid for m in inactive.json()), inactive.text
all_rows = client.get('/api/machines', headers=headers, params={'active':'all'})
assert any(m['id'] == mid for m in all_rows.json()), all_rows.text

on = client.patch(f'/api/machines/{mid}/active', headers=headers, json={'is_active':True})
assert on.status_code == 200 and on.json()['is_active'] is True, on.text
''')


def test_tenant_isolation(tmp_path: Path) -> None:
    _run(tmp_path, r'''
mid = make(model='TenantA').json()['id']
company_b = client.post('/api/companies', headers=headers, json={'name':'Tenant B'}).json()['id']
hb = {**headers, 'X-Company-ID':str(company_b)}

assert client.get(f'/api/machines/{mid}', headers=hb).status_code == 404
assert all(m['id'] != mid for m in client.get('/api/machines', headers=hb).json())
assert client.put(f'/api/machines/{mid}', headers=hb, json={'brand':'X','model':'Y'}).status_code == 404
assert client.patch(f'/api/machines/{mid}/active', headers=hb, json={'is_active':False}).status_code == 404
# Company A still owns it.
assert client.get(f'/api/machines/{mid}', headers=headers).status_code == 200
''')


def test_cross_tenant_customer_rejected(tmp_path: Path) -> None:
    _run(tmp_path, r'''
company_b = client.post('/api/companies', headers=headers, json={'name':'Tenant B'}).json()['id']
hb = {**headers, 'X-Company-ID':str(company_b)}
customer_b = mk_customer(hb, name='Cari B')

# Company A must not be able to attach company B's customer.
resp = make(customer_id=customer_b)
assert resp.status_code == 400, resp.text
# The same customer id is assignable inside its own tenant.
assert make(hb, customer_id=customer_b).status_code == 201
''')


def test_duplicate_chassis_same_company(tmp_path: Path) -> None:
    _run(tmp_path, r'''
assert make(chassis_number='CH-DUP').status_code == 201
dup = make(chassis_number='CH-DUP')
assert dup.status_code == 409, dup.text
''')


def test_same_chassis_allowed_across_companies(tmp_path: Path) -> None:
    _run(tmp_path, r'''
assert make(chassis_number='CH-SHARED').status_code == 201
company_b = client.post('/api/companies', headers=headers, json={'name':'Tenant B'}).json()['id']
hb = {**headers, 'X-Company-ID':str(company_b)}
assert make(hb, chassis_number='CH-SHARED').status_code == 201
''')


def test_duplicate_serial_same_company(tmp_path: Path) -> None:
    _run(tmp_path, r'''
assert make(serial_number='SN-DUP').status_code == 201
dup = make(serial_number='SN-DUP')
assert dup.status_code == 409, dup.text
# Updating another machine onto the same serial is also rejected.
other = make(serial_number='SN-OTHER').json()['id']
clash = client.put(f'/api/machines/{other}', headers=headers, json={
    'brand':'John Deere','model':'6155R','serial_number':'SN-DUP'})
assert clash.status_code == 409, clash.text
''')


def test_malformed_and_missing_machine_id(tmp_path: Path) -> None:
    _run(tmp_path, r'''
assert client.get('/api/machines/not-an-int', headers=headers).status_code == 422
assert client.get('/api/machines/-5', headers=headers).status_code in (404, 422)
assert client.get('/api/machines/999999', headers=headers).status_code == 404
''')


def test_validation_rules(tmp_path: Path) -> None:
    _run(tmp_path, r'''
assert make(working_hours=-5).status_code == 422              # negative hours
assert make(brand='   ', model='6155R').status_code == 422    # blank required
assert make(model_year=1800).status_code == 422               # unreasonable year
assert make(status='banana').status_code == 422               # invalid status
''')


def test_audit_history_created(tmp_path: Path) -> None:
    _run(tmp_path, r'''
mid = make(model='Audited').json()['id']
upd = client.put(f'/api/machines/{mid}', headers=headers, json={'brand':'John Deere','model':'Audited-2'})
assert upd.status_code == 200, upd.text
logs = client.get(f'/api/history/machine/{mid}', headers=headers)
assert logs.status_code == 200, logs.text
actions = [row['action'] for row in logs.json()]
assert 'create' in actions and 'update' in actions, actions
''')


def test_no_delete_endpoint(tmp_path: Path) -> None:
    _run(tmp_path, r'''
mid = make().json()['id']
resp = client.delete(f'/api/machines/{mid}', headers=headers)
# No hard-delete surface: the machines router exposes no DELETE, so the request
# is rejected (405, or 404 from the generic fallthrough) and the row survives.
assert resp.status_code in (404, 405), resp.text
assert client.get(f'/api/machines/{mid}', headers=headers).status_code == 200
''')


def test_machine_write_rbac(tmp_path: Path) -> None:
    _run(tmp_path, r'''
# yonetici HAS the dedicated `machines` permission -> full write access.
hy = mk_user('yon1', 'yonetici')
cy = client.post('/api/machines', headers=hy, json={'brand':'Y','model':'M'})
assert cy.status_code == 201, cy.text
mid = cy.json()['id']
assert client.put(f'/api/machines/{mid}', headers=hy, json={'brand':'Y','model':'M2'}).status_code == 200
assert client.patch(f'/api/machines/{mid}/active', headers=hy, json={'is_active':False}).status_code == 200

# depo LACKS `machines` -> writes 403, reads 200.
hd = mk_user('depo1', 'depo')
assert client.post('/api/machines', headers=hd, json={'brand':'D','model':'M'}).status_code == 403
assert client.put(f'/api/machines/{mid}', headers=hd, json={'brand':'x','model':'y'}).status_code == 403
assert client.patch(f'/api/machines/{mid}/active', headers=hd, json={'is_active':True}).status_code == 403
assert client.get('/api/machines', headers=hd).status_code == 200
assert client.get(f'/api/machines/{mid}', headers=hd).status_code == 200

# satis LACKS `machines` -> write 403.
hs = mk_user('satis1', 'satis')
assert client.post('/api/machines', headers=hs, json={'brand':'S','model':'M'}).status_code == 403

# admin (via '*') -> write allowed.
assert client.post('/api/machines', headers=headers, json={'brand':'A','model':'M'}).status_code == 201
''')


def test_idempotency_key(tmp_path: Path) -> None:
    _run(tmp_path, r'''
# Same key -> same machine, no duplicate row.
h = {**headers, 'Idempotency-Key':'create-abc-123'}
r1 = client.post('/api/machines', headers=h, json={'brand':'Idem','model':'M','chassis_number':'IK-1'})
assert r1.status_code == 201, r1.text
r2 = client.post('/api/machines', headers=h, json={'brand':'Idem','model':'M','chassis_number':'IK-1'})
assert r2.status_code == 201, r2.text
assert r1.json()['id'] == r2.json()['id'], (r1.json(), r2.json())
assert len([m for m in client.get('/api/machines', headers=headers).json() if m['brand']=='Idem']) == 1

# Different key -> new machine.
h2 = {**headers, 'Idempotency-Key':'create-xyz-999'}
r3 = client.post('/api/machines', headers=h2, json={'brand':'Idem2','model':'M'})
assert r3.status_code == 201 and r3.json()['id'] != r1.json()['id']

# No key -> unchanged behaviour (duplicates allowed when unidentified).
n1 = client.post('/api/machines', headers=headers, json={'brand':'NoKey','model':'M'})
n2 = client.post('/api/machines', headers=headers, json={'brand':'NoKey','model':'M'})
assert n1.json()['id'] != n2.json()['id']

# Oversized key -> 400.
assert client.post('/api/machines', headers={**headers,'Idempotency-Key':'k'*256},
                   json={'brand':'X','model':'M'}).status_code == 400
''')


def test_offset_cap(tmp_path: Path) -> None:
    _run(tmp_path, r'''
assert client.get('/api/machines', headers=headers, params={'offset':100001}).status_code == 422
assert client.get('/api/machines', headers=headers, params={'offset':100000}).status_code == 200
assert client.get('/api/machines', headers=headers, params={'offset':0}).status_code == 200
''')
