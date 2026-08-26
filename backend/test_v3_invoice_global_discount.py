from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parent


def test_global_discount_is_allocated_and_reconciles(tmp_path: Path) -> None:
    """F2: the document discount is allocated across items and everything reconciles.

    Σ item totals == header grand_total; Σ tax == header tax; Σ customer/company
    payable == header customer/warranty; header customer+warranty == grand_total;
    grand_total == pre-discount total − global_discount. The no-discount path
    keeps each PART item total equal to the stored work_order_parts.total_price
    (F1 parity).
    """
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'gdisc.db').as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    smoke = r'''
from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy import text
from app.db import SessionLocal
from app.main import app

D=lambda v: Decimal(str(v))

with TestClient(app) as c:
    login=c.post('/api/auth/login',json={'username':'admin','password':'admin123'}).json(); cid=login['companies'][0]['id']; uid=login['user']['id']
    h={'Authorization':'Bearer '+login['access_token'],'X-Company-ID':str(cid)}
    h['Authorization']='Bearer '+c.post('/api/auth/change-password',headers=h,json={'current_password':'admin123','new_password':'GDiscount123!'}).json()['access_token']
    customer=c.post('/api/customers',headers=h,json={'name':'GDisc'}).json()
    machine=c.post('/api/machines',headers=h,json={'customer_id':customer['id'],'brand':'S','model':'P','serial_number':'GD-M'}).json()
    warehouse=c.get('/api/warehouses',headers=h).json()[0]
    p1=c.post('/api/products',headers=h,json={'name':'P1','purchase_price':'1','sale_price':'100','vat_rate':'20','stock':'50','unit':'Adet'}).json()
    p2=c.post('/api/products',headers=h,json={'name':'P2','purchase_price':'1','sale_price':'7','vat_rate':'10','stock':'50','unit':'Adet'}).json()

    def build(warranty='PARTIAL',percent='30'):
        wo=c.post('/api/work-orders',headers=h,json={'machine_id':machine['id'],'customer_id':customer['id'],'technician_id':uid,'actual_hours':'2','labor_rate':'150','warranty_type':warranty,'warranty_percent':percent}).json()
        c.post(f"/api/work-orders/{wo['id']}/parts",headers=h,json={'product_id':p1['id'],'warehouse_id':warehouse['id'],'quantity':'2','unit_price':'100','discount':'10','tax_rate':'20'})
        c.post(f"/api/work-orders/{wo['id']}/parts",headers=h,json={'product_id':p2['id'],'warehouse_id':warehouse['id'],'quantity':'3','unit_price':'7','discount':'0','tax_rate':'10'})
        for s in ('IN_PROGRESS','COMPLETED'): assert c.patch(f"/api/work-orders/{wo['id']}/status",headers=h,json={'status':s}).status_code==200
        return wo['id']

    def items(iid):
        with SessionLocal() as db:
            return [dict(r) for r in db.execute(text("SELECT * FROM invoice_items WHERE invoice_id=:id ORDER BY id"),{'id':iid}).mappings().all()]

    def reconcile(iid,totals):
        rows=items(iid)
        s_total=sum((D(r['total']) for r in rows),Decimal('0'))
        s_tax=sum((D(r['tax_amount']) for r in rows),Decimal('0'))
        s_cust=sum((D(r['customer_payable']) for r in rows),Decimal('0'))
        s_comp=sum((D(r['company_payable']) for r in rows),Decimal('0'))
        assert s_total==D(totals['grand_total']), ('Σtotal != grand_total',s_total,totals['grand_total'])
        assert s_tax==D(totals['tax']), ('Σtax != header tax',s_tax,totals['tax'])
        assert s_cust==D(totals['customer_amount']), ('Σcustomer != customer_amount',s_cust,totals['customer_amount'])
        assert s_comp==D(totals['warranty_amount']), ('Σcompany != warranty_amount',s_comp,totals['warranty_amount'])
        assert D(totals['customer_amount'])+D(totals['warranty_amount'])==D(totals['grand_total']), ('customer+warranty!=grand',totals)
        # per-line identity: total == round(qty*unit_price) - discount_amount + tax_amount
        for r in rows:
            raw=(D(r['quantity'])*D(r['unit_price'])).quantize(Decimal('0.01'))
            assert D(r['total'])==raw-D(r['discount_amount'])+D(r['tax_amount']), ('line identity',dict(r))
        return rows

    # Pre-discount grand total for this work order: labor 300 + P1 216 + P2 23.10 = 539.10
    # (P2: raw=21.00, tax10%=2.10, total=23.10)
    def pre_total(iid):
        with SessionLocal() as db:
            return D(db.execute(text("SELECT total_labor_cost FROM work_orders WHERE id=:id"),{'id':iid}).scalar_one()) + \
                   sum((D(x) for x in db.execute(text("SELECT total_price FROM work_order_parts WHERE work_order_id=:id"),{'id':iid}).scalars().all()),Decimal('0'))

    # --- PERCENT discount 12.5% ---
    w1=build()
    g1=c.post('/api/invoices/generate',headers=h,json={'work_order_id':w1,'global_discount_type':'PERCENT','global_discount_value':'12.5'})
    assert g1.status_code==201,g1.text
    t1=g1.json()['totals']; pre1=pre_total(w1)
    assert D(t1['grand_total'])==pre1-D(t1['global_discount']), ('grand != pre-D',t1,pre1)
    reconcile(g1.json()['id'],t1)

    # --- FIXED discount 10 ---
    w2=build()
    g2=c.post('/api/invoices/generate',headers=h,json={'work_order_id':w2,'global_discount_type':'FIXED','global_discount_value':'10'})
    assert g2.status_code==201,g2.text
    t2=g2.json()['totals']; pre2=pre_total(w2)
    assert D(t2['global_discount'])==Decimal('10.00')
    assert D(t2['grand_total'])==pre2-Decimal('10.00')
    reconcile(g2.json()['id'],t2)

    # --- No discount: F1 parity preserved (PART item total == stored total_price) ---
    w3=build(warranty='NONE',percent='0')
    g3=c.post('/api/invoices/generate',headers=h,json={'work_order_id':w3})
    assert g3.status_code==201,g3.text
    t3=g3.json()['totals']; rows3=reconcile(g3.json()['id'],t3)
    assert D(t3['global_discount'])==Decimal('0.00')
    with SessionLocal() as db:
        stored=sorted(D(x) for x in db.execute(text("SELECT total_price FROM work_order_parts WHERE work_order_id=:id"),{'id':w3}).scalars().all())
    part_totals=sorted(D(r['total']) for r in rows3 if r['item_type']=='PART')
    assert part_totals==stored, ('F1 parity broken at zero discount',part_totals,stored)
    print('GLOBAL_DISCOUNT_RECONCILE_OK')
'''
    result = subprocess.run(
        [sys.executable, "-c", smoke], cwd=BACKEND, env=env, text=True, capture_output=True, timeout=180
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "GLOBAL_DISCOUNT_RECONCILE_OK" in result.stdout
