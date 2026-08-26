from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


BACKEND = Path(__file__).resolve().parent


def run_payment_allocation_smoke(database_url: str, *, concurrent: bool = False) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    env["ALLOCATION_CONCURRENT"] = "1" if concurrent else "0"
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "PAYMENT_ALLOCATION_FOUNDATION_OK" in completed.stdout


def test_payment_allocation_foundation_sqlite(tmp_path: Path) -> None:
    database = tmp_path / "payment-allocation.db"
    run_payment_allocation_smoke(f"sqlite:///{database.as_posix()}")


_SMOKE = r'''
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import os
from threading import Barrier

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.allocation_reconciliation import build_reconciliation_report
from app.db import SessionLocal, engine
from app.main import app
from app.payment_allocation_engine import (
    allocate_payment,
    allocate_payment_with_retry,
    delete_unallocated_payment,
)


def dec(value):
    return Decimal(str(value))


with TestClient(app):
    inspector = inspect(engine)
    assert inspector.has_table("payment_allocations")
    assert inspector.has_table("payment_idempotency")
    assert inspector.has_table("receivable_legacy_residuals")
    indexes = {row["name"]: row for row in inspector.get_indexes("payment_allocations")}
    assert "ix_payment_allocations_active_order" in indexes

    with SessionLocal() as db:
        cid = int(db.execute(text("SELECT id FROM companies ORDER BY id LIMIT 1")).scalar_one())
        customer = int(db.execute(
            text("INSERT INTO customers(name,company_id) VALUES('Tahsis Müşteri',:cid) RETURNING id"),
            {"cid": cid},
        ).scalar_one())
        imported_customer = int(db.execute(
            text("INSERT INTO customers(name,company_id) VALUES('Import Müşteri',:cid) RETURNING id"),
            {"cid": cid},
        ).scalar_one())
        first = int(db.execute(text(
            """INSERT INTO orders(
                customer_id,order_date,due_date,final_total,status,paid_amount,
                payment_term,payment_method,note,company_id
            ) VALUES(
                :customer,'2026-01-01','2026-06-01',100,'completed',0,
                'HARMAN_VADELI','credit','İlk',:cid
            ) RETURNING id"""
        ), {"customer": customer, "cid": cid}).scalar_one())
        second = int(db.execute(text(
            """INSERT INTO orders(
                customer_id,order_date,due_date,final_total,status,paid_amount,
                payment_term,payment_method,note,company_id
            ) VALUES(
                :customer,'2026-01-02','2026-07-01',100,'completed',0,
                'HARMAN_VADELI','credit','İkinci',:cid
            ) RETURNING id"""
        ), {"customer": customer, "cid": cid}).scalar_one())
        imported = int(db.execute(text(
            """INSERT INTO orders(
                customer_id,order_date,due_date,final_total,status,paid_amount,
                payment_term,payment_method,note,company_id
            ) VALUES(
                :customer,'2026-01-03','2026-08-01',100,'draft',0,
                'HARMAN_VADELI','credit','BizimHesap içe aktarma',:cid
            ) RETURNING id"""
        ), {"customer": imported_customer, "cid": cid}).scalar_one())
        direct_payment = int(db.execute(text(
            """INSERT INTO payments(
                entity_type,entity_id,amount,payment_date,company_id,payment_method,
                reference_type,reference_id
            ) VALUES('customer',:customer,30,'2026-05-01',:cid,'cash','order',:order_id)
            RETURNING id"""
        ), {"customer": customer, "cid": cid, "order_id": first}).scalar_one())
        fifo_payment = int(db.execute(text(
            """INSERT INTO payments(
                entity_type,entity_id,amount,payment_date,company_id,payment_method
            ) VALUES('customer',:customer,90,'2026-05-02',:cid,'cash') RETURNING id"""
        ), {"customer": customer, "cid": cid}).scalar_one())
        db.commit()

    with SessionLocal() as db:
        direct = allocate_payment(db, cid, direct_payment, "direct-1")
        assert direct.allocated_amount == dec("30.00")
        assert [(row.order_id, row.amount, row.allocation_type) for row in direct.allocations] == [
            (first, dec("30.00"), "document_create")
        ]
        paid = dec(db.execute(
            text("SELECT paid_amount FROM orders WHERE id=:id"), {"id": first}
        ).scalar_one())
        assert paid == dec("30.00")

    with SessionLocal() as db:
        fifo = allocate_payment(db, cid, fifo_payment, "fifo-1")
        assert fifo.allocated_amount == dec("90.00")
        assert fifo.unallocated_amount == dec("0.00")
        assert [(row.order_id, row.amount) for row in fifo.allocations] == [
            (first, dec("70.00")),
            (second, dec("20.00")),
        ]
        paid_rows = db.execute(
            text("SELECT id,paid_amount FROM orders WHERE id IN (:first,:second) ORDER BY id"),
            {"first": first, "second": second},
        ).all()
        assert [(int(row[0]), dec(row[1])) for row in paid_rows] == [
            (first, dec("30.00")),
            (second, dec("0.00")),
        ]

    with SessionLocal() as db:
        replay = allocate_payment(db, cid, fifo_payment, "fifo-1")
        assert replay == fifo
        count = int(db.execute(
            text("SELECT COUNT(*) FROM payment_allocations WHERE payment_id=:id"),
            {"id": fifo_payment},
        ).scalar_one())
        assert count == 2
        assert db.execute(text(
            """SELECT COUNT(*) FROM entity_change_logs
            WHERE company_id=:cid AND entity_type='payment_allocation'
              AND entity_id=:payment_id"""
        ), {"cid": cid, "payment_id": fifo_payment}).scalar_one() == 1

    with SessionLocal() as db:
        oversized = int(db.execute(text(
            """INSERT INTO payments(
                entity_type,entity_id,amount,payment_date,company_id,payment_method,
                reference_type,reference_id
            ) VALUES('customer',:customer,90,'2026-05-03',:cid,'cash','order',:order_id)
            RETURNING id"""
        ), {"customer": customer, "cid": cid, "order_id": second}).scalar_one())
        db.commit()
        try:
            allocate_payment(db, cid, oversized, "oversized-direct")
            raise AssertionError("Belge kalanını aşan direct ödeme reddedilmeliydi")
        except HTTPException as exc:
            assert exc.status_code == 409
            db.rollback()
        assert db.execute(
            text("SELECT COUNT(*) FROM payment_allocations WHERE payment_id=:id"),
            {"id": oversized},
        ).scalar_one() == 0

    with SessionLocal() as db:
        company_b = int(db.execute(text(
            """INSERT INTO companies(name,is_active,created_at)
            VALUES('Tahsis B',TRUE,CURRENT_TIMESTAMP) RETURNING id"""
        )).scalar_one())
        customer_b = int(db.execute(
            text("INSERT INTO customers(name,company_id) VALUES('Tenant B',:cid) RETURNING id"),
            {"cid": company_b},
        ).scalar_one())
        cross_tenant = int(db.execute(text(
            """INSERT INTO payments(
                entity_type,entity_id,amount,payment_date,company_id,payment_method,
                reference_type,reference_id
            ) VALUES('customer',:customer,10,'2026-05-03',:cid,'cash','order',:order_id)
            RETURNING id"""
        ), {"customer": customer_b, "cid": company_b, "order_id": first}).scalar_one())
        db.commit()
        try:
            allocate_payment(db, company_b, cross_tenant, "cross-tenant")
            raise AssertionError("Tenant dışı belge reddedilmeliydi")
        except HTTPException as exc:
            assert exc.status_code == 409
            db.rollback()
        assert db.execute(
            text("SELECT COUNT(*) FROM payment_allocations WHERE payment_id=:id"),
            {"id": cross_tenant},
        ).scalar_one() == 0

    with SessionLocal() as db:
        report = build_reconciliation_report(db, cid)
        by_id = {row.order_id: row for row in report}
        assert by_id[imported].status == "confirmed"
        assert by_id[imported].evidence_source == "bizimhesap_draft_zero_paid"
        assert by_id[first].status == "quarantined"
        assert by_id[first].candidate_residual == dec("0.00")
        assert db.execute(text("SELECT COUNT(*) FROM receivable_legacy_residuals")).scalar_one() == 0
        db.rollback()

    with SessionLocal() as db:
        return_customer = int(db.execute(
            text("INSERT INTO customers(name,company_id) VALUES('İadeli Cari',:cid) RETURNING id"),
            {"cid": cid},
        ).scalar_one())
        return_order = int(db.execute(text(
            """INSERT INTO orders(
                customer_id,order_date,due_date,final_total,status,paid_amount,
                payment_term,payment_method,company_id
            ) VALUES(
                :customer,'2026-01-04','2026-06-15',100,'completed',0,
                'HARMAN_VADELI','credit',:cid
            ) RETURNING id"""
        ), {"customer": return_customer, "cid": cid}).scalar_one())
        db.execute(text(
            """INSERT INTO returns(
                return_type,entity_id,return_date,total,status,company_id
            ) VALUES('sale_return',:customer,'2026-04-01',80,'completed',:cid)"""
        ), {"customer": return_customer, "cid": cid})
        return_payment = int(db.execute(text(
            """INSERT INTO payments(
                entity_type,entity_id,amount,payment_date,company_id,payment_method
            ) VALUES('customer',:customer,50,'2026-05-01',:cid,'cash') RETURNING id"""
        ), {"customer": return_customer, "cid": cid}).scalar_one())
        db.commit()
        try:
            allocate_payment(db, cid, return_payment, "return-overlap")
            raise AssertionError("İade sonrası gerçek kalanı aşan ödeme reddedilmeliydi")
        except HTTPException as exc:
            assert exc.status_code == 409
            db.rollback()
        assert db.execute(
            text("SELECT COUNT(*) FROM payment_allocations WHERE payment_id=:id"),
            {"id": return_payment},
        ).scalar_one() == 0

        db.execute(text(
            """INSERT INTO payment_allocations(
                company_id,payment_id,order_id,amount,allocation_type,effective_date
            ) VALUES(:cid,:payment_id,:order_id,50,'fifo','2026-05-01')"""
        ), {"cid": cid, "payment_id": return_payment, "order_id": return_order})
        db.commit()
        overrun = {
            row.order_id: row
            for row in build_reconciliation_report(db, cid)
            if row.order_id is not None
        }[return_order]
        assert overrun.active_allocation_total == dec("50.00")
        assert overrun.document_allocation_excess == dec("30.00")
        assert overrun.payment_allocation_excess == dec("0.00")
        assert overrun.status == "quarantined"
        assert overrun.evidence_source == "order_allocation_excess"

        db.execute(text(
            """INSERT INTO payment_allocations(
                company_id,payment_id,order_id,amount,allocation_type,effective_date
            ) VALUES(:cid,:payment_id,:order_id,10,'fifo','2026-05-01')"""
        ), {"cid": cid, "payment_id": return_payment, "order_id": return_order})
        db.commit()
        payment_overrun = {
            row.order_id: row
            for row in build_reconciliation_report(db, cid)
            if row.order_id is not None
        }[return_order]
        assert payment_overrun.document_allocation_excess == dec("40.00")
        assert payment_overrun.payment_allocation_excess == dec("10.00")
        # V2c splits the single "allocation exceeds receivable" verdict into
        # separate quarantine reasons; payment excess outranks order excess.
        assert payment_overrun.evidence_source == "payment_allocation_excess"

    with SessionLocal() as db:
        negative_customer = int(db.execute(
            text("INSERT INTO customers(name,company_id) VALUES('Negatif Fark',:cid) RETURNING id"),
            {"cid": cid},
        ).scalar_one())
        negative_order = int(db.execute(text(
            """INSERT INTO orders(
                customer_id,order_date,due_date,final_total,status,paid_amount,
                payment_term,payment_method,company_id
            ) VALUES(
                :customer,'2026-01-04','2026-09-01',100,'completed',10,
                'HARMAN_VADELI','credit',:cid
            ) RETURNING id"""
        ), {"customer": negative_customer, "cid": cid}).scalar_one())
        db.execute(text(
            """INSERT INTO payments(
                entity_type,entity_id,amount,payment_date,company_id,payment_method,
                reference_type,reference_id
            ) VALUES('customer',:customer,20,'2026-05-03',:cid,'cash','order',:order_id)"""
        ), {"customer": negative_customer, "cid": cid, "order_id": negative_order})
        db.commit()
        negative = {
            row.order_id: row
            for row in build_reconciliation_report(db, cid)
            if row.order_id is not None
        }[negative_order]
        assert negative.candidate_residual == dec("-10.00")
        assert negative.status == "quarantined"
        assert negative.evidence_source == "negative_paid_vs_linked"
        db.rollback()

    if os.environ.get("ALLOCATION_CONCURRENT") == "1":
        with SessionLocal() as db:
            race_customer = int(db.execute(
                text("INSERT INTO customers(name,company_id) VALUES('Yarış',:cid) RETURNING id"),
                {"cid": cid},
            ).scalar_one())
            race_order = int(db.execute(text(
                """INSERT INTO orders(
                    customer_id,order_date,due_date,final_total,status,paid_amount,
                    payment_term,payment_method,company_id
                ) VALUES(
                    :customer,'2026-01-05','2026-10-01',100,'completed',0,
                    'HARMAN_VADELI','credit',:cid
                ) RETURNING id"""
            ), {"customer": race_customer, "cid": cid}).scalar_one())
            race_payment = int(db.execute(text(
                """INSERT INTO payments(
                    entity_type,entity_id,amount,payment_date,company_id,payment_method
                ) VALUES('customer',:customer,60,'2026-05-04',:cid,'cash') RETURNING id"""
            ), {"customer": race_customer, "cid": cid}).scalar_one())
            db.commit()
        barrier = Barrier(2)

        def allocate_concurrently():
            barrier.wait()
            return allocate_payment_with_retry(
                SessionLocal, cid, race_payment, "parallel-fifo"
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: allocate_concurrently(), range(2)))
        assert results[0] == results[1]
        with SessionLocal() as db:
            count, total = db.execute(text(
                """SELECT COUNT(*),COALESCE(SUM(amount),0)
                FROM payment_allocations WHERE payment_id=:payment_id"""
            ), {"payment_id": race_payment}).one()
            assert int(count) == 1
            assert dec(total) == dec("60.00")
            assert dec(db.execute(
                text("SELECT paid_amount FROM orders WHERE id=:id"), {"id": race_order}
            ).scalar_one()) == dec("0.00")

        with SessionLocal() as db:
            delete_race_customer = int(db.execute(
                text("INSERT INTO customers(name,company_id) VALUES('Silme Yarışı',:cid) RETURNING id"),
                {"cid": cid},
            ).scalar_one())
            delete_race_order = int(db.execute(text(
                """INSERT INTO orders(
                    customer_id,order_date,due_date,final_total,status,paid_amount,
                    payment_term,payment_method,company_id
                ) VALUES(
                    :customer,'2026-01-06','2026-11-01',100,'completed',0,
                    'HARMAN_VADELI','credit',:cid
                ) RETURNING id"""
            ), {"customer": delete_race_customer, "cid": cid}).scalar_one())
            delete_race_payment = int(db.execute(text(
                """INSERT INTO payments(
                    entity_type,entity_id,amount,payment_date,company_id,payment_method
                ) VALUES('customer',:customer,40,'2026-05-05',:cid,'cash') RETURNING id"""
            ), {"customer": delete_race_customer, "cid": cid}).scalar_one())
            db.commit()
        delete_barrier = Barrier(2)

        def allocate_delete_race():
            delete_barrier.wait()
            try:
                allocate_payment_with_retry(
                    SessionLocal,
                    cid,
                    delete_race_payment,
                    "delete-race-allocation",
                )
                return "allocated"
            except HTTPException as exc:
                assert exc.status_code == 404
                return "payment_deleted"

        def delete_during_allocation():
            delete_barrier.wait()
            with SessionLocal() as db:
                try:
                    delete_unallocated_payment(db, cid, delete_race_payment)
                    db.commit()
                    return "deleted"
                except HTTPException as exc:
                    db.rollback()
                    assert exc.status_code == 409
                    return "allocation_won"

        with ThreadPoolExecutor(max_workers=2) as executor:
            allocation_future = executor.submit(allocate_delete_race)
            deletion_future = executor.submit(delete_during_allocation)
            race_outcomes = {allocation_future.result(), deletion_future.result()}
        assert race_outcomes in (
            {"allocated", "allocation_won"},
            {"payment_deleted", "deleted"},
        )

print("PAYMENT_ALLOCATION_FOUNDATION_OK")
'''
