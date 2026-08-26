"""Servis v2 FAZ-3 — parça rezervasyonu, çıkış/iade ve servis aracı deposu.

Pins the locked criteria:

1. legacy lines migrate to ISSUED with ``reserved_quantity=0`` (v1 already
   decremented their stock — reserving the same quantity again would
   double-count it), and IMMEDIATE stays the default so the v1 add-issues-now
   behaviour is untouched;
2. availability is ``on_hand - reserved`` and the guard is inside the UPDATE;
3. cancelling releases reservations AND returns issued stock in one transaction,
   with idempotency keys so a retry cannot return twice;
4. reservation is a company setting, defaulting to IMMEDIATE;
5. the invoice freeze still comes from the central predicate.

``run_servis_v2_faz3_reservation_smoke`` is shared with the PostgreSQL twin.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parent


def run_servis_v2_faz3_reservation_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_servis_v2_faz3_reservation_sqlite(tmp_path: Path) -> None:
    run_servis_v2_faz3_reservation_smoke(
        f"sqlite:///{(tmp_path / 'servis-faz3.db').as_posix()}"
    )


def test_reservation_defaults_and_states() -> None:
    from app.work_order_stock import (
        LINE_ISSUED,
        LINE_RESERVED,
        LINE_RETURNED,
        MODE_IMMEDIATE,
        MODE_RESERVE,
        VALID_SERVICE_PARTS_MODES,
    )

    assert {LINE_RESERVED, LINE_ISSUED, LINE_RETURNED} == {
        "RESERVED", "ISSUED", "RETURNED"
    }
    assert VALID_SERVICE_PARTS_MODES == {MODE_IMMEDIATE, MODE_RESERVE}
    # Locked criterion (4): reservation is opt-in; the default reproduces v1.
    assert MODE_IMMEDIATE == "IMMEDIATE"


def test_legacy_rows_migrate_to_issued_not_reserved() -> None:
    """Locked criterion (1), read off the migration's executed SQL.

    Backfilling ``reserved_quantity=quantity`` would double-count every legacy
    line, because v1 had already taken that quantity out of stock. Only the SQL
    handed to ``op.execute`` is inspected — the prose that *explains* the trap
    naturally mentions it and must not trip the check.
    """
    import ast

    path = (
        BACKEND / "alembic" / "versions"
        / "20260728_0036_work_order_part_reservations.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    executed: list[str] = []
    for call in ast.walk(tree):
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "execute"
            and call.args
            and isinstance(call.args[0], ast.Constant)
            and isinstance(call.args[0].value, str)
        ):
            executed.append(" ".join(call.args[0].value.split()))

    backfill = [sql for sql in executed if "work_order_parts" in sql]
    assert backfill, executed
    assert any(
        "line_status='ISSUED'" in sql and "reserved_quantity=0" in sql
        for sql in backfill
    ), backfill
    assert not any("reserved_quantity=quantity" in sql for sql in executed), executed


_SMOKE = r'''
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text as sql_text

from app.db import SessionLocal, engine
from app.main import app

ADMIN_PW = 'ServisFaz3!123'


def admin_headers(client):
    login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
    if login.status_code == 200:
        body = login.json()
        headers = {'Authorization':'Bearer '+body['access_token'],
                   'X-Company-ID':str(body['companies'][0]['id'])}
        changed = client.post('/api/auth/change-password', headers=headers,
                              json={'current_password':'admin123','new_password':ADMIN_PW})
        assert changed.status_code == 200, changed.text
        headers['Authorization'] = 'Bearer '+changed.json()['access_token']
        return headers, body
    login = client.post('/api/auth/login', json={'username':'admin','password':ADMIN_PW})
    assert login.status_code == 200, login.text
    body = login.json()
    headers = {'Authorization':'Bearer '+body['access_token'],
               'X-Company-ID':str(body['companies'][0]['id'])}
    return headers, body


with TestClient(app) as client:
    headers, body = admin_headers(client)
    cid = int(headers['X-Company-ID'])
    admin_id = int(body['user']['id'])

    def set_mode(mode):
        with SessionLocal() as session:
            session.execute(sql_text(
                "UPDATE companies SET service_parts_mode=:m WHERE id=:cid"),
                {"m": mode, "cid": cid})
            session.commit()

    def stock_row(warehouse_id, product_id):
        with SessionLocal() as session:
            row = session.execute(sql_text(
                "SELECT quantity,reserved_quantity FROM warehouse_stocks "
                "WHERE company_id=:cid AND warehouse_id=:w AND product_id=:p"),
                {"cid": cid, "w": warehouse_id, "p": product_id}).mappings().first()
        return (Decimal(str(row['quantity'])), Decimal(str(row['reserved_quantity']))) if row else (None, None)

    def seed_stock(warehouse_id, product_id, qty):
        # Ürün oluşturma varsayılan depo için satırı zaten açmış olabilir.
        with SessionLocal() as session:
            updated = session.execute(sql_text(
                "UPDATE warehouse_stocks SET quantity=:q,reserved_quantity=0 "
                "WHERE company_id=:cid AND warehouse_id=:w AND product_id=:p"),
                {"q": qty, "cid": cid, "w": warehouse_id, "p": product_id})
            if not updated.rowcount:
                session.execute(sql_text(
                    "INSERT INTO warehouse_stocks(company_id,warehouse_id,product_id,quantity,critical_stock,reserved_quantity) "
                    "VALUES(:cid,:w,:p,:q,0,0)"),
                    {"cid": cid, "w": warehouse_id, "p": product_id, "q": qty})
            session.commit()

    def movements(work_order_id):
        with SessionLocal() as session:
            rows = session.execute(sql_text(
                "SELECT movement_type,quantity FROM stock_movements "
                "WHERE company_id=:cid AND reference_type='work_order' AND reference_id=:id "
                "ORDER BY id"), {"cid": cid, "id": work_order_id}).mappings().all()
        return [(r['movement_type'], Decimal(str(r['quantity']))) for r in rows]

    warehouses = client.get('/api/warehouses', headers=headers)
    assert warehouses.status_code == 200, warehouses.text
    warehouse_id = warehouses.json()[0]['id']

    product = client.post('/api/products', headers=headers,
                          json={'name':'F3 Parça', 'price':'100', 'stock':'0'})
    assert product.status_code == 201, product.text
    product_id = product.json()['id']
    seed_stock(warehouse_id, product_id, 10)

    customer = client.post('/api/customers', headers=headers, json={'name':'F3 Müşteri'})
    assert customer.status_code == 201, customer.text
    machine = client.post('/api/machines', headers=headers, json={
        'customer_id':customer.json()['id'], 'brand':'Sungur', 'model':'F3',
        'serial_number':'F3-SER-1',
    })
    assert machine.status_code == 201, machine.text
    machine_id = machine.json()['id']

    def new_work_order():
        response = client.post('/api/work-orders', headers=headers, json={
            'machine_id':machine_id, 'technician_id':admin_id,
            'actual_hours':'1', 'labor_rate':'100',
        })
        assert response.status_code == 201, response.text
        return response.json()['id']

    def add_part(work_order_id, qty):
        return client.post(f'/api/work-orders/{work_order_id}/parts', headers=headers,
                           json={'product_id':product_id, 'warehouse_id':warehouse_id,
                                 'quantity':qty, 'unit_price':'100'})

    # ---- (4) VARSAYILAN MOD = ANINDA DÜŞÜM (v1 davranışı birebir) -----------
    assert Decimal(str(stock_row(warehouse_id, product_id)[0])) == Decimal('10')
    wo_immediate = new_work_order()
    added = add_part(wo_immediate, '3')
    assert added.status_code == 201, added.text
    assert added.json()['line_status'] == 'ISSUED', added.json()
    assert Decimal(str(added.json()['reserved_quantity'])) == Decimal('0')
    on_hand, reserved = stock_row(warehouse_id, product_id)
    assert on_hand == Decimal('7'), on_hand      # anında düşüm
    assert reserved == Decimal('0'), reserved
    # v1'in yazmadığı hareket defteri satırı artık yazılıyor.
    assert movements(wo_immediate) == [('out', Decimal('-3'))], movements(wo_immediate)

    # ---- (2) REZERVE MODU: available = on_hand - reserved -------------------
    set_mode('RESERVE')
    wo_reserve = new_work_order()
    reserved_line = add_part(wo_reserve, '4')
    assert reserved_line.status_code == 201, reserved_line.text
    assert reserved_line.json()['line_status'] == 'RESERVED'
    assert Decimal(str(reserved_line.json()['reserved_quantity'])) == Decimal('4')
    part_id = reserved_line.json()['id']
    on_hand, reserved = stock_row(warehouse_id, product_id)
    assert on_hand == Decimal('7'), on_hand      # fiziksel stok DEĞİŞMEZ
    assert reserved == Decimal('4'), reserved
    # Rezervasyon hareket defterine yazılmaz (fiziksel çıkış yok).
    assert movements(wo_reserve) == [], movements(wo_reserve)

    # Kullanılabilirin ötesine rezervasyon reddedilir (7-4=3 kaldı).
    over = add_part(new_work_order(), '4')
    assert over.status_code == 409, over.text
    # Tam kalan kadarı kabul edilir.
    exact = add_part(new_work_order(), '3')
    assert exact.status_code == 201, exact.text
    exact_wo = exact.json()['work_order_id']
    on_hand, reserved = stock_row(warehouse_id, product_id)
    assert (on_hand, reserved) == (Decimal('7'), Decimal('7')), (on_hand, reserved)
    # Artık hiç kullanılabilir yok.
    assert add_part(new_work_order(), '1').status_code == 409

    # ---- ÇIKIŞ: rezervasyon fiziksel düşüşe döner ---------------------------
    issued = client.post(f'/api/work-orders/{wo_reserve}/parts/{part_id}/issue', headers=headers)
    assert issued.status_code == 200, issued.text
    assert issued.json()['line_status'] == 'ISSUED'
    on_hand, reserved = stock_row(warehouse_id, product_id)
    assert (on_hand, reserved) == (Decimal('3'), Decimal('3')), (on_hand, reserved)
    assert movements(wo_reserve) == [('out', Decimal('-4'))], movements(wo_reserve)
    # Tekrar çıkış: durum artık RESERVED değil -> 409, stok oynamaz.
    assert client.post(f'/api/work-orders/{wo_reserve}/parts/{part_id}/issue',
                       headers=headers).status_code == 409
    assert stock_row(warehouse_id, product_id) == (Decimal('3'), Decimal('3'))

    # ---- İADE: çıkışı yapılmış satır geri döner ----------------------------
    returned = client.post(f'/api/work-orders/{wo_reserve}/parts/{part_id}/return', headers=headers)
    assert returned.status_code == 200, returned.text
    assert returned.json()['line_status'] == 'RETURNED'
    on_hand, reserved = stock_row(warehouse_id, product_id)
    assert (on_hand, reserved) == (Decimal('7'), Decimal('3')), (on_hand, reserved)
    assert movements(wo_reserve) == [('out', Decimal('-4')), ('in', Decimal('4'))]
    # İade terminaldir; tekrar iade ve düzenleme reddedilir.
    assert client.post(f'/api/work-orders/{wo_reserve}/parts/{part_id}/return',
                       headers=headers).status_code == 409
    assert client.put(f'/api/work-orders/{wo_reserve}/parts/{part_id}', headers=headers,
                      json={'product_id':product_id, 'warehouse_id':warehouse_id,
                            'quantity':'1', 'unit_price':'100'}).status_code == 409

    # ---- (3) İPTAL: rezervasyon bırakılır + çıkışlar iade edilir ------------
    cancel_wo = new_work_order()
    reserved_part = add_part(cancel_wo, '2')
    assert reserved_part.status_code == 201, reserved_part.text
    before_cancel = stock_row(warehouse_id, product_id)
    cancelled = client.patch(f'/api/work-orders/{cancel_wo}/status', headers=headers,
                             json={'status':'CANCELLED'})
    assert cancelled.status_code == 200, cancelled.text
    after_cancel = stock_row(warehouse_id, product_id)
    # Rezervasyon bırakıldı: fiziksel stok aynı, rezerve azaldı.
    assert after_cancel[0] == before_cancel[0], (before_cancel, after_cancel)
    assert after_cancel[1] == before_cancel[1] - Decimal('2'), (before_cancel, after_cancel)
    assert movements(cancel_wo) == [], movements(cancel_wo)   # fiziksel hareket yok
    lines = client.get(f'/api/work-orders/{cancel_wo}/parts', headers=headers).json()
    assert [item['line_status'] for item in lines['items']] == ['RETURNED'], lines

    # Çıkışı yapılmış satır iptalde fiziksel olarak iade edilir.
    cancel_issued_wo = new_work_order()
    issued_part = add_part(cancel_issued_wo, '2')
    assert issued_part.status_code == 201, issued_part.text
    issue_it = client.post(
        f"/api/work-orders/{cancel_issued_wo}/parts/{issued_part.json()['id']}/issue",
        headers=headers)
    assert issue_it.status_code == 200, issue_it.text
    before_cancel = stock_row(warehouse_id, product_id)
    cancelled = client.patch(f'/api/work-orders/{cancel_issued_wo}/status', headers=headers,
                             json={'status':'CANCELLED'})
    assert cancelled.status_code == 200, cancelled.text
    after_cancel = stock_row(warehouse_id, product_id)
    assert after_cancel[0] == before_cancel[0] + Decimal('2'), (before_cancel, after_cancel)
    assert movements(cancel_issued_wo) == [('out', Decimal('-2')), ('in', Decimal('2'))]

    # İdempotency: iptal tekrar denenirse (terminal durum) stok bir daha oynamaz.
    again = client.patch(f'/api/work-orders/{cancel_issued_wo}/status', headers=headers,
                         json={'status':'CANCELLED'})
    assert again.status_code == 409, again.text
    assert stock_row(warehouse_id, product_id) == after_cancel

    # ---- (5) Fatura freeze merkezî predicate'ten ----------------------------
    billed_wo = new_work_order()
    billed_part = add_part(billed_wo, '1')
    assert billed_part.status_code == 201, billed_part.text
    issue_billed = client.post(
        f"/api/work-orders/{billed_wo}/parts/{billed_part.json()['id']}/issue", headers=headers)
    assert issue_billed.status_code == 200, issue_billed.text
    for status in ('IN_PROGRESS', 'COMPLETED'):
        assert client.patch(f'/api/work-orders/{billed_wo}/status', headers=headers,
                            json={'status':status}).status_code == 200
    invoice = client.post('/api/invoices/generate', headers=headers,
                          json={'work_order_id':billed_wo})
    assert invoice.status_code == 201, invoice.text
    assert add_part(billed_wo, '1').status_code == 409
    assert client.post(f"/api/work-orders/{billed_wo}/parts/{billed_part.json()['id']}/return",
                       headers=headers).status_code == 409

    # ---- Servis aracı deposu = NORMAL depo (ayrı stok tablosu yok) ----------
    vehicle = client.post('/api/warehouses', headers=headers,
                          json={'name':'Servis Aracı 01', 'is_active':True})
    assert vehicle.status_code in (200, 201), vehicle.text
    vehicle_id = vehicle.json()['id']
    with SessionLocal() as session:
        session.execute(sql_text(
            "UPDATE warehouses SET warehouse_type='SERVICE_VEHICLE', technician_user_id=:u "
            "WHERE id=:id AND company_id=:cid"),
            {"u": admin_id, "id": vehicle_id, "cid": cid})
        session.commit()
    seed_stock(vehicle_id, product_id, 5)
    vehicle_wo = new_work_order()
    from_vehicle = client.post(f'/api/work-orders/{vehicle_wo}/parts', headers=headers,
                               json={'product_id':product_id, 'warehouse_id':vehicle_id,
                                     'quantity':'2', 'unit_price':'100'})
    assert from_vehicle.status_code == 201, from_vehicle.text
    assert from_vehicle.json()['line_status'] == 'RESERVED'
    assert stock_row(vehicle_id, product_id) == (Decimal('5'), Decimal('2'))

    # ---- Mod IMMEDIATE'e dönünce v1 yolu aynen çalışır ---------------------
    set_mode('IMMEDIATE')
    back_to_v1 = new_work_order()
    v1_line = client.post(f'/api/work-orders/{back_to_v1}/parts', headers=headers,
                          json={'product_id':product_id, 'warehouse_id':vehicle_id,
                                'quantity':'1', 'unit_price':'100'})
    assert v1_line.status_code == 201, v1_line.text
    assert v1_line.json()['line_status'] == 'ISSUED'
    assert stock_row(vehicle_id, product_id) == (Decimal('4'), Decimal('2'))

    # ---- Tenant izolasyonu --------------------------------------------------
    company_b = client.post('/api/companies', headers=headers, json={'name':'F3 Firma B'})
    assert company_b.status_code == 201, company_b.text
    headers_b = {**headers, 'X-Company-ID':str(company_b.json()['id'])}
    assert client.post(f'/api/work-orders/{wo_reserve}/parts/{part_id}/issue',
                       headers=headers_b).status_code == 404
    assert client.post(f'/api/work-orders/{wo_reserve}/parts/{part_id}/return',
                       headers=headers_b).status_code == 404

engine.dispose()
'''
