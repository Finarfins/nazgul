"""Absorption report labour revenue must come from the billing source.

Before FAZ-2 the report summed ``work_orders.total_labor_cost`` — the v1 header
projection. Once a work order carries approved labor lines that projection is
no longer what the customer is billed, so the report diverged from the invoice
(invoice 850 vs report 200), and editing the header rate afterwards widened the
gap further.

The report now reads the issued invoice's LABOR items for invoiced work orders
and the canonical resolver for uninvoiced ones. These tests pin that down; each
of (a) and (b) fails on the pre-fix query.

``run_absorption_labor_source_smoke`` is shared with the PostgreSQL twin.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parent


def run_absorption_labor_source_smoke(database_url: str) -> None:
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


def test_absorption_labor_source_sqlite(tmp_path: Path) -> None:
    run_absorption_labor_source_smoke(
        f"sqlite:///{(tmp_path / 'absorption-labor.db').as_posix()}"
    )


def test_report_sql_does_not_mention_the_header_projection_tripwire() -> None:
    """NARROW TRIPWIRE — not a correctness proof.

    All this checks is that the literal string ``total_labor_cost`` does not
    reappear inside a ``text(...)`` SQL literal in the report module. It cannot
    see a projection reached through a view, an f-string fragment, a helper in
    another module, or any other indirection, and it says nothing about whether
    the numbers are right.

    The actual protection is behavioural: the poison tests in the smoke below
    write a deliberately wrong ``total_labor_cost`` and assert the reported
    revenue does not move — on both the invoiced and the uninvoiced side. Treat
    this tripwire only as a fast signal that the obvious regression came back.
    """
    import ast

    path = BACKEND / "app" / "routers" / "absorption.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offending: list[str] = []
    for call in ast.walk(tree):
        if not (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "text"
            and call.args
        ):
            continue
        for node in ast.walk(call.args[0]):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "total_labor_cost" in node.value:
                    offending.append(f"line {call.lineno}: {' '.join(node.value.split())[:120]}")
    assert not offending, (
        "The absorption report SQL mentions work_orders.total_labor_cost again; "
        "it is the v1 header projection and diverges from the invoice once "
        "approved labor lines exist. Check the poison tests too — this tripwire "
        "only catches the literal case:\n" + "\n".join(offending)
    )


_SMOKE = r'''
from decimal import Decimal

from fastapi.testclient import TestClient

from app.db import engine
from app.main import app

ADMIN_PW = 'AbsorpLabor!123'
PERIOD = {'date_from':'2026-01-01', 'date_to':'2026-12-31', 'overhead':'1000'}


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
    admin_id = int(body['user']['id'])

    customer = client.post('/api/customers', headers=headers, json={'name':'Absorp Müşteri'})
    assert customer.status_code == 201, customer.text
    machine = client.post('/api/machines', headers=headers, json={
        'customer_id':customer.json()['id'], 'brand':'Sungur', 'model':'ABS-1',
        'serial_number':'ABS-SER-1',
    })
    assert machine.status_code == 201, machine.text
    machine_id = machine.json()['id']

    def new_work_order():
        response = client.post('/api/work-orders', headers=headers, json={
            'machine_id':machine_id, 'technician_id':admin_id,
            'actual_hours':'2', 'labor_rate':'100',   # header projection = 200.00
        })
        assert response.status_code == 201, response.text
        return response.json()

    def complete(work_order_id):
        for status in ('IN_PROGRESS', 'COMPLETED'):
            moved = client.patch(f'/api/work-orders/{work_order_id}/status',
                                 headers=headers, json={'status':status})
            assert moved.status_code == 200, moved.text

    def report():
        response = client.get('/api/analytics/absorption-rate', headers=headers, params=PERIOD)
        assert response.status_code == 200, response.text
        return response.json()['service']

    HEADER_AMOUNT = Decimal('200.00')     # 2h x 100
    LINES_AMOUNT = Decimal('850.00')      # 3x150 + 2x200

    # ---- (a) onaylı satırlar + kesilmiş fatura -> rapor 850, 200 DEĞİL --------
    wo = new_work_order()
    wo_id = wo['id']
    first = client.post(f'/api/work-orders/{wo_id}/labor-lines', headers=headers,
                        json={'technician_user_id':admin_id, 'hours':'3', 'hourly_rate':'150'})
    second = client.post(f'/api/work-orders/{wo_id}/labor-lines', headers=headers,
                         json={'technician_user_id':admin_id, 'hours':'2', 'hourly_rate':'200'})
    assert first.status_code == 201 and second.status_code == 201
    for line in (first, second):
        approve = client.post(
            f"/api/work-orders/{wo_id}/labor-lines/{line.json()['id']}/approve", headers=headers)
        assert approve.status_code == 200, approve.text
    complete(wo_id)
    invoice = client.post('/api/invoices/generate', headers=headers, json={'work_order_id':wo_id})
    assert invoice.status_code == 201, invoice.text

    service = report()
    assert Decimal(str(service['labor_revenue'])) == LINES_AMOUNT, service
    assert Decimal(str(service['labor_revenue'])) != HEADER_AMOUNT, service
    assert Decimal(str(service['invoiced_labor_revenue'])) == LINES_AMOUNT, service
    assert service['invoiced_work_order_count'] == 1, service
    assert service['uninvoiced_work_order_count'] == 0, service

    # ---- (b) sonradan header oranı 999 -> rapor HÂLÂ 850 ---------------------
    # Faturalı iş emri dondurulduğu için PUT reddedilir; header'ı doğrudan
    # güncelleyip raporun fatura snapshot'ına bağlı kaldığını kanıtlıyoruz.
    frozen = client.put(f'/api/work-orders/{wo_id}', headers=headers, json={
        'machine_id':machine_id, 'technician_id':admin_id,
        'actual_hours':'2', 'labor_rate':'999',
    })
    assert frozen.status_code == 409, frozen.text

    from sqlalchemy import text as sql_text
    from app.db import SessionLocal
    with SessionLocal() as session:
        session.execute(sql_text(
            "UPDATE work_orders SET labor_rate=999, total_labor_cost=1998 "
            "WHERE id=:id AND company_id=:cid"),
            {"id": wo_id, "cid": int(headers['X-Company-ID'])})
        session.commit()

    service = report()
    assert Decimal(str(service['labor_revenue'])) == LINES_AMOUNT, service
    assert Decimal(str(service['labor_revenue'])) != Decimal('1998.00'), service

    # ---- (c) onaylı satırsız eski tip faturalı iş emri -> header tutarı ------
    legacy = new_work_order()
    complete(legacy['id'])
    legacy_invoice = client.post('/api/invoices/generate', headers=headers,
                                 json={'work_order_id':legacy['id']})
    assert legacy_invoice.status_code == 201, legacy_invoice.text
    service = report()
    assert Decimal(str(service['labor_revenue'])) == LINES_AMOUNT + HEADER_AMOUNT, service
    assert service['invoiced_work_order_count'] == 2, service

    # ---- faturasız iş emri: canonical resolver ile ---------------------------
    open_wo = new_work_order()
    complete(open_wo['id'])
    service = report()
    assert Decimal(str(service['uninvoiced_labor_revenue'])) == HEADER_AMOUNT, service
    assert service['uninvoiced_work_order_count'] == 1, service
    assert Decimal(str(service['labor_revenue'])) == LINES_AMOUNT + HEADER_AMOUNT * 2, service

    # ...ve onaylı satır eklenince faturasız taraf da satırları kullanır.
    extra = client.post(f"/api/work-orders/{open_wo['id']}/labor-lines", headers=headers,
                        json={'technician_user_id':admin_id, 'hours':'4', 'hourly_rate':'50'})
    assert extra.status_code == 201, extra.text
    approve_extra = client.post(
        f"/api/work-orders/{open_wo['id']}/labor-lines/{extra.json()['id']}/approve",
        headers=headers)
    assert approve_extra.status_code == 200, approve_extra.text
    service = report()
    assert Decimal(str(service['uninvoiced_labor_revenue'])) == Decimal('200.00'), service  # 4x50
    # Önizlemeyle özdeş olmalı.
    preview = client.get(f"/api/work-orders/{open_wo['id']}/invoice", headers=headers).json()
    assert Decimal(str(preview['totals']['labor'])) == Decimal('200.00'), preview
    assert preview['totals']['labor_source'] == 'lines', preview

    # ---- POISON (faturasız taraf): total_labor_cost yanlış yazılır ----------
    # Faturalı tarafın simetriği: rapor faturasız iş emrinde de header
    # projeksiyonunu değil canonical resolver'ı okumalı, o yüzden kolonu
    # kasıtlı olarak bozunca sonuç DEĞİŞMEMELİ.
    poison_wo = new_work_order()
    complete(poison_wo['id'])
    before_poison = report()
    with SessionLocal() as session:
        session.execute(sql_text(
            "UPDATE work_orders SET total_labor_cost=777777 "
            "WHERE id=:id AND company_id=:cid"),
            {"id": poison_wo['id'], "cid": int(headers['X-Company-ID'])})
        session.commit()
    after_poison = report()
    assert after_poison['uninvoiced_labor_revenue'] == before_poison['uninvoiced_labor_revenue'], (
        'faturasız taraf zehirlenen header projeksiyonundan etkilendi',
        before_poison, after_poison)
    assert after_poison['labor_revenue'] == before_poison['labor_revenue'], (
        before_poison, after_poison)
    # ...ve onaylı satır eklenince de projeksiyon değil satırlar okunur.
    poison_line = client.post(f"/api/work-orders/{poison_wo['id']}/labor-lines",
                              headers=headers,
                              json={'technician_user_id':admin_id, 'hours':'1', 'hourly_rate':'10'})
    assert poison_line.status_code == 201, poison_line.text
    approve_poison = client.post(
        f"/api/work-orders/{poison_wo['id']}/labor-lines/{poison_line.json()['id']}/approve",
        headers=headers)
    assert approve_poison.status_code == 200, approve_poison.text
    poisoned = report()
    # Bu iş emrinin katkısı 200.00 (header) yerine 10.00 (satır) olur; 777777 asla.
    delta = (Decimal(str(poisoned['uninvoiced_labor_revenue']))
             - Decimal(str(after_poison['uninvoiced_labor_revenue'])))
    assert delta == Decimal('-190.00'), (after_poison, poisoned)

    # ---- Aktif fatura tekilliği: DB kısıtı zaten garantiliyor ---------------
    # invoices üzerinde UNIQUE(company_id, work_order_id, invoice_type) var, yani
    # iş emri başına (tip başına) tek fatura satırı; iptal edilmiş olsa bile
    # ikincisi açılamaz. "En fazla bir aktif fatura" bundan doğrudan çıkar.
    duplicate = client.post('/api/invoices/generate', headers=headers,
                            json={'work_order_id':wo_id})
    assert duplicate.status_code == 409, duplicate.text

    # ---- iptal edilmiş fatura: faturasız gibi davranır -----------------------
    cancel = client.post(f"/api/invoices/{legacy_invoice.json()['id']}/cancel", headers=headers,
                         json={'reason':'Rapor kaynağı testi'})
    assert cancel.status_code == 200, cancel.text
    service = report()
    # Kalan tek faturalı: satır kaynaklı iş emri (850). Faturasızlar: iptal
    # edilen legacy (header 200), open_wo (satır 200), poison_wo (satır 10).
    assert service['invoiced_work_order_count'] == 1, service
    assert service['uninvoiced_work_order_count'] == 3, service
    assert Decimal(str(service['invoiced_labor_revenue'])) == LINES_AMOUNT, service
    assert Decimal(str(service['uninvoiced_labor_revenue'])) == Decimal('410.00'), service
    # Toplam: iptal edilen fatura yerine resolver aynı header tutarını verir.
    assert Decimal(str(service['labor_revenue'])) == Decimal('1260.00'), service
    # Her iş emri TAM BİR KEZ sayılır: gruplar ayrıktır ve toplam parçaların
    # toplamına eşittir (çift/eksik sayım olamaz).
    assert (Decimal(str(service['invoiced_labor_revenue']))
            + Decimal(str(service['uninvoiced_labor_revenue']))
            == Decimal(str(service['labor_revenue']))), service
    assert (service['invoiced_work_order_count'] + service['uninvoiced_work_order_count']
            == service['work_order_count']), service

engine.dispose()
'''
