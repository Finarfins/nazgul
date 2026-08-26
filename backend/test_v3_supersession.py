"""V3 Parça Supersession — Increment 1 tests (SQLite).

Covers the manual supersession chain and its resolution:
- Chain A -> B -> C resolves A (and B) to C with the ordered supersedes list.
- A cycle attempt (C -> A on top of A -> B -> C) is rejected at create time.
- old == new is rejected; duplicate supersession for the same old part is 409.
- Deleting a link shortens the chain.
- Multi-tenant isolation: another company neither sees nor resolves the chain.
- RBAC: reads on baseline ``read``; writes on the ``stock`` permission.

The PostgreSQL 16 twin lives in test_supersession_postgresql.py.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.auth import required_permission


BACKEND = Path(__file__).resolve().parent


def test_supersession_rbac_map() -> None:
    # Reads stay on baseline read; supersession writes are a parts/inventory
    # concern gated like product writes (stock).
    assert required_permission("GET", "/api/part-supersessions") == "read"
    assert required_permission("GET", "/api/products/1/current") == "read"
    assert required_permission("POST", "/api/part-supersessions") == "stock"
    assert required_permission("DELETE", "/api/part-supersessions/1") == "stock"


def test_supersession_flow(tmp_path: Path) -> None:
    database = tmp_path / "supersession.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE], env=env, capture_output=True, text=True, cwd=str(BACKEND)
    )
    assert "SUPERSESSION_FLOW_OK" in completed.stdout, completed.stdout + completed.stderr


# Shared, DB-agnostic scenario. test_supersession_postgresql.py runs the exact
# same script against PostgreSQL 16 so SQLite/PG parity is proven, not assumed.
_SMOKE = r'''
from fastapi.testclient import TestClient
from app.main import app

with TestClient(app) as client:
    body = client.post('/api/auth/login', json={'username':'admin','password':'admin123'}).json()
    headers = {'Authorization':'Bearer '+body['access_token'],'X-Company-ID':str(body['companies'][0]['id'])}
    changed = client.post('/api/auth/change-password', headers=headers, json={
        'current_password':'admin123','new_password':'Supersession123!'})
    assert changed.status_code == 200, changed.text
    headers['Authorization'] = 'Bearer ' + changed.json()['access_token']

    # Fresh company so the run is isolated even on the shared CI PG database.
    company_a = client.post('/api/companies', headers=headers, json={'name':'Supersession Firma A'}).json()['id']
    headers['X-Company-ID'] = str(company_a)

    def product(name, code):
        response = client.post('/api/products', headers=headers, json={'name':name,'product_code':code})
        assert response.status_code == 201, response.text
        return response.json()['id']

    a = product('Filtre Eski', 'FLT-100')
    b = product('Filtre Ara', 'FLT-200')
    c = product('Filtre Guncel', 'FLT-240')

    # old == new is rejected.
    same = client.post('/api/part-supersessions', headers=headers,
                       json={'old_product_id':a,'new_product_id':a})
    assert same.status_code == 400, same.text

    # Build the chain A -> B -> C.
    ab = client.post('/api/part-supersessions', headers=headers,
                     json={'old_product_id':a,'new_product_id':b,'note':'OEM degisimi'})
    assert ab.status_code == 201, ab.text
    ab_id = ab.json()['id']
    bc = client.post('/api/part-supersessions', headers=headers,
                     json={'old_product_id':b,'new_product_id':c})
    assert bc.status_code == 201, bc.text

    # Duplicate supersession for the same old part is rejected (one successor).
    dup = client.post('/api/part-supersessions', headers=headers,
                      json={'old_product_id':a,'new_product_id':c})
    assert dup.status_code == 409, dup.text

    # Cycle attempt C -> A closes the loop and must be rejected.
    cycle = client.post('/api/part-supersessions', headers=headers,
                        json={'old_product_id':c,'new_product_id':a})
    assert cycle.status_code == 409, cycle.text

    # A resolves through the whole chain to C; supersedes lists A then B.
    resolved = client.get(f'/api/products/{a}/current', headers=headers)
    assert resolved.status_code == 200, resolved.text
    data = resolved.json()
    assert data['resolved'] is True
    assert data['current']['id'] == c
    assert [row['id'] for row in data['supersedes']] == [a, b]
    assert data['note'] == 'FLT-100 yerine FLT-240 geçti'

    # B (mid-chain) resolves to C too; C resolves to itself.
    assert client.get(f'/api/products/{b}/current', headers=headers).json()['current']['id'] == c
    c_self = client.get(f'/api/products/{c}/current', headers=headers).json()
    assert c_self['resolved'] is False and c_self['current']['id'] == c and c_self['supersedes'] == []

    # Listing shows both links with product names/codes.
    listing = client.get('/api/part-supersessions', headers=headers)
    assert listing.status_code == 200, listing.text
    assert {(row['old_product_id'], row['new_product_id']) for row in listing.json()} == {(a, b), (b, c)}
    assert client.get('/api/part-supersessions?q=FLT-100', headers=headers).json()[0]['old_code'] == 'FLT-100'

    # Deleting A -> B shortens the chain: A now resolves to itself.
    assert client.delete(f'/api/part-supersessions/{ab_id}', headers=headers).status_code == 204
    a_after = client.get(f'/api/products/{a}/current', headers=headers).json()
    assert a_after['resolved'] is False and a_after['current']['id'] == a

    # --- Hop-cap semantics (fail closed, no silent truncation) --------------
    # Build q0 -> q1 -> ... -> q20: exactly MAX_CHAIN_HOPS (20) links.
    q = [product(f'Zincir {i}', f'CHN-{i:02d}') for i in range(22)]
    for i in range(20):
        link = client.post('/api/part-supersessions', headers=headers,
                           json={'old_product_id':q[i],'new_product_id':q[i+1]})
        assert link.status_code == 201, link.text
    # (a) A chain of exactly 20 hops still resolves correctly end to end.
    at_limit = client.get(f'/api/products/{q[0]}/current', headers=headers)
    assert at_limit.status_code == 200, at_limit.text
    assert at_limit.json()['current']['id'] == q[20]
    assert [row['id'] for row in at_limit.json()['supersedes']] == q[:20]

    # Extend to 21 links (q20 -> q21): resolution from q0 now exceeds the cap.
    assert client.post('/api/part-supersessions', headers=headers,
                       json={'old_product_id':q[20],'new_product_id':q[21]}).status_code == 201
    # (c) Over-limit resolution errors instead of returning a wrong "current".
    over = client.get(f'/api/products/{q[0]}/current', headers=headers)
    assert over.status_code == 409, over.text
    # (b) A cycle attempt whose loop spans 21+ hops (q21 -> q0) is also
    # rejected fail-closed: the create-time traversal hits the cap and refuses.
    long_cycle = client.post('/api/part-supersessions', headers=headers,
                             json={'old_product_id':q[21],'new_product_id':q[0]})
    assert long_cycle.status_code == 409, long_cycle.text
    # Mid-chain resolution (within the cap) still works on the long chain.
    mid = client.get(f'/api/products/{q[5]}/current', headers=headers)
    assert mid.status_code == 200 and mid.json()['current']['id'] == q[21]

    # (d) A pre-corrupted cycle (written past the API guard, e.g. legacy or
    # manual data) surfaces as a controlled 409, never an infinite loop.
    x = product('Bozuk X', 'BRK-X')
    y = product('Bozuk Y', 'BRK-Y')
    assert client.post('/api/part-supersessions', headers=headers,
                       json={'old_product_id':x,'new_product_id':y}).status_code == 201
    from sqlalchemy import text as _sql_text
    from app.db import engine as _engine
    with _engine.begin() as _conn:
        _conn.execute(_sql_text(
            """INSERT INTO part_supersessions(company_id,old_product_id,new_product_id,created_at)
            VALUES(:cid,:old_id,:new_id,CURRENT_TIMESTAMP)"""
        ), {"cid": int(company_a), "old_id": y, "new_id": x})
    corrupted = client.get(f'/api/products/{x}/current', headers=headers)
    assert corrupted.status_code == 409, corrupted.text
    # Creating on top of a corrupted chain is also refused fail-closed.
    z = product('Bozuk Z', 'BRK-Z')
    on_corrupt = client.post('/api/part-supersessions', headers=headers,
                             json={'old_product_id':z,'new_product_id':x})
    assert on_corrupt.status_code == 409, on_corrupt.text

    # --- Multi-tenant isolation --------------------------------------------
    company_b = client.post('/api/companies', headers=headers, json={'name':'Supersession Firma B'}).json()['id']
    headers_b = {**headers, 'X-Company-ID':str(company_b)}
    assert client.get('/api/part-supersessions', headers=headers_b).json() == []
    # Company B cannot resolve or reference company A products.
    assert client.get(f'/api/products/{b}/current', headers=headers_b).status_code == 404
    assert client.post('/api/part-supersessions', headers=headers_b,
                       json={'old_product_id':b,'new_product_id':c}).status_code == 404
    assert client.delete(f'/api/part-supersessions/{bc.json()["id"]}', headers=headers_b).status_code == 404

    print('SUPERSESSION_FLOW_OK')
'''
