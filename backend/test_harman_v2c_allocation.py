from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


BACKEND = Path(__file__).resolve().parent


def _run(source: str, database_url: str, *, charge_flag: bool) -> str:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    env["PAYMENT_ALLOCATION_ENGINE_ENABLED"] = "true"
    env["RECEIVABLE_CHARGE_ALLOCATION_ENABLED"] = "true" if charge_flag else "false"
    completed = subprocess.run(
        [sys.executable, "-c", source],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed.stdout


def run_charge_allocation_smoke(database_url: str) -> None:
    output = _run(_ENABLED_SMOKE, database_url, charge_flag=True)
    assert "HARMAN_V2C_ENABLED_OK" in output


def run_charge_allocation_disabled_smoke(database_url: str) -> None:
    output = _run(_DISABLED_SMOKE, database_url, charge_flag=False)
    assert "HARMAN_V2C_DISABLED_OK" in output


def run_charge_allocation_race_smoke(database_url: str) -> None:
    output = _run(_RACE_SMOKE, database_url, charge_flag=True)
    assert "HARMAN_V2C_RACE_OK" in output


def test_charge_allocation_sqlite(tmp_path: Path) -> None:
    run_charge_allocation_smoke(
        f"sqlite:///{(tmp_path / 'v2c-enabled.db').as_posix()}"
    )


def test_charge_allocation_disabled_sqlite(tmp_path: Path) -> None:
    run_charge_allocation_disabled_smoke(
        f"sqlite:///{(tmp_path / 'v2c-disabled.db').as_posix()}"
    )


def run_fifo_reversal_pin_smoke(database_url: str) -> None:
    output = _run(_PIN_SMOKE, database_url, charge_flag=True)
    assert "HARMAN_V2C_PIN_OK" in output


def test_fifo_reversal_pins_allocation_time_order_sqlite(tmp_path: Path) -> None:
    """FIFO sırası TAHSİS ANINDA belirlenir, sonradan yeniden yazılmaz.

    Anapara kapanıp bir sonraki ödeme late-fee'ye tahsis edildikten sonra
    anapara tahsisinin reverse edilmesi (anaparanın yeniden açılması) mevcut
    late-fee tahsisini GERİYE TAŞIMAZ; yalnız bundan sonraki bağlantısız
    ödeme önce yeniden açılan anaparaya gider. Davranış değişikliği değil,
    mevcut semantiğin regresyon pin'idir."""
    run_fifo_reversal_pin_smoke(
        f"sqlite:///{(tmp_path / 'v2c-pin.db').as_posix()}"
    )


def run_payment_update_charge_smoke(database_url: str) -> None:
    output = _run(_PUT_SMOKE, database_url, charge_flag=True)
    assert "HARMAN_V2C_PUT_OK" in output


def test_payment_update_writes_charge_allocation_sqlite(tmp_path: Path) -> None:
    """PUT /api/payments/{id} bir late-fee belgesine bağlanınca ödeme satırı
    ile tahsis satırı aynı transaction'da yazılır; aşımda ikisi birden geri
    alınır (409 + tam rollback)."""
    run_payment_update_charge_smoke(
        f"sqlite:///{(tmp_path / 'v2c-put.db').as_posix()}"
    )


def test_flag_matrix_rejects_charge_without_engine() -> None:
    """RECEIVABLE_CHARGE_ALLOCATION_ENABLED=true, motor kapalıyken uygulama
    başlamamalı; üç geçerli kombinasyon sorunsuz doğrulanır."""
    import pytest
    from pydantic import ValidationError

    from app.config import Settings

    with pytest.raises(ValidationError) as error:
        Settings(
            _env_file=None,
            payment_allocation_engine_enabled=False,
            receivable_charge_allocation_enabled=True,
        )
    assert "PAYMENT_ALLOCATION_ENGINE_ENABLED=true zorunludur" in str(error.value)

    for engine, charge in ((False, False), (True, False), (True, True)):
        settings = Settings(
            _env_file=None,
            payment_allocation_engine_enabled=engine,
            receivable_charge_allocation_enabled=charge,
        )
        assert settings.payment_allocation_engine_enabled is engine
        assert settings.receivable_charge_allocation_enabled is charge


_FIXTURE = r'''
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import text

# Importing the app runs the Alembic upgrade and bootstrap seeding, exactly as
# the other engine smokes do.
import app.main  # noqa: F401
from app.db import SessionLocal


def dec(value):
    return Decimal(str(value))


def company_id():
    with SessionLocal() as db:
        return int(db.execute(text("SELECT MIN(id) FROM companies")).scalar_one())


def make_customer(db, cid, name):
    return int(db.execute(
        text("INSERT INTO customers(name,company_id) VALUES(:name,:cid) RETURNING id"),
        {"name": name, "cid": cid},
    ).scalar_one())


def make_order(db, cid, customer_id, *, due_date, total, order_date="2025-12-01",
               document_no=None, term="HARMAN_VADELI"):
    return int(db.execute(
        text("""INSERT INTO orders(
            customer_id,order_date,due_date,final_total,status,paid_amount,
            payment_term,payment_method,company_id,document_no
        ) VALUES(
            :customer,:order_date,:due_date,:total,'completed',0,
            :term,'credit',:cid,:document_no
        ) RETURNING id"""),
        {
            "customer": customer_id,
            "order_date": order_date,
            "due_date": due_date,
            "total": total,
            "term": term,
            "cid": cid,
            "document_no": document_no,
        },
    ).scalar_one())


def make_charge(db, cid, order_id, customer_id, *, period_start, period_end, gross,
                status="posted", posted=True, reversal_of=None):
    """Insert a late-fee charge document directly.

    The V2b engine path is exercised separately; here the document is a fixture
    so the allocation behaviour can be pinned to exact amounts.

    ``due_date_snapshot`` is the ORIGINAL ORDER's due date, and
    late_fee_charge_engine.py:200 refuses an accrual period starting on or
    before it, so a real late fee always has
    ``due_date_snapshot < period_start <= period_end``. Stamping it with
    ``period_start`` itself would build a document the engine cannot produce.
    """
    snapshot = (date.fromisoformat(period_start) - timedelta(days=1)).isoformat()
    period_id = int(db.execute(
        text("""INSERT INTO receivable_charge_periods(
            company_id,order_id,installment_id,period_start,period_end,charge_kind
        ) VALUES(:cid,:order_id,NULL,:period_start,:period_end,'late_fee')
        RETURNING id"""),
        {
            "cid": cid,
            "order_id": order_id,
            "period_start": period_start,
            "period_end": period_end,
        },
    ).scalar_one())
    revision = int(db.execute(
        text("""SELECT COALESCE(MAX(revision_no),0)+1
        FROM receivable_charge_documents
        WHERE company_id=:cid AND charge_period_id=:period_id"""),
        {"cid": cid, "period_id": period_id},
    ).scalar_one())
    return int(db.execute(
        text("""INSERT INTO receivable_charge_documents(
            company_id,charge_period_id,order_id,customer_id,charge_type,
            period_start,period_end,due_date_snapshot,principal_basis,
            annual_rate_snapshot,day_count_basis_snapshot,grace_days_snapshot,
            tax_mode_snapshot,vat_rate_snapshot,calculation_snapshot,
            net_amount,vat_amount,gross_amount,status,calculation_fingerprint,
            revision_no,reversal_of_document_id,posted_at
        ) VALUES(
            :cid,:period_id,:order_id,:customer_id,'late_fee',
            :period_start,:period_end,:snapshot,1000,
            30,365,0,'NO_VAT',0,'{}',
            :gross,0,:gross,:status,'fixture',:revision,:reversal_of,:posted_at
        ) RETURNING id"""),
        {
            "cid": cid,
            "period_id": period_id,
            "order_id": order_id,
            "customer_id": customer_id,
            "period_start": period_start,
            "period_end": period_end,
            "snapshot": snapshot,
            "gross": gross,
            "status": status,
            "revision": revision,
            "reversal_of": reversal_of,
            "posted_at": "2026-06-30 00:00:00+00:00" if posted else None,
        },
    ).scalar_one())


def make_policy(db, cid, *, annual_rate=30):
    db.execute(
        text("""INSERT INTO late_fee_policies(
            company_id,customer_id,annual_rate,day_count_basis,grace_days,
            tax_mode,vat_rate,effective_from,active
        ) VALUES(:cid,NULL,:rate,365,0,'NO_VAT',0,'2025-01-01',:active)"""),
        {"cid": cid, "rate": annual_rate, "active": True},
    )


def make_payment(db, cid, customer_id, amount, *, payment_date="2026-07-01",
                 reference_type=None, reference_id=None, account_id=None):
    return int(db.execute(
        text("""INSERT INTO payments(
            entity_type,entity_id,amount,payment_date,company_id,payment_method,
            reference_type,reference_id,account_id
        ) VALUES(
            'customer',:customer,:amount,:payment_date,:cid,'cash',:rt,:rid,:account_id
        ) RETURNING id"""),
        {
            "customer": customer_id,
            "amount": amount,
            "payment_date": payment_date,
            "cid": cid,
            "rt": reference_type,
            "rid": reference_id,
            "account_id": account_id,
        },
    ).scalar_one())


def allocations_of(db, cid, payment_id):
    return [
        dict(row) for row in db.execute(
            text("""SELECT id,order_id,receivable_charge_id,amount,allocation_type,
            reversal_of_allocation_id
            FROM payment_allocations
            WHERE company_id=:cid AND payment_id=:payment_id ORDER BY id"""),
            {"cid": cid, "payment_id": payment_id},
        ).mappings()
    ]
'''


_ENABLED_SMOKE = _FIXTURE + r'''
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.allocation_reconciliation import build_reconciliation_report
from app.late_fee_charge_engine import (
    _calculation_snapshot,
    _timeline_for_order,
    reverse_late_fee_document,
)
from app.late_fee_engine import resolve_late_fee_policy
from app.payment_allocation_engine import (
    CHARGE_TARGET_NOT_FOUND,
    allocate_payment,
    manual_allocate_payment,
    reallocate_allocation,
    reverse_allocation,
)
from app.receivables_engine import calculate_net_receivables

cid = company_id()

# ---------------------------------------------------------------- compounding
# Locked regression: a produced (and allocated) late fee must never feed the
# next period's interest. The next period is computed from the principal
# timeline alone, so its amount is identical before and after allocation.
with SessionLocal() as db:
    make_policy(db, cid)
    compound_customer = make_customer(db, cid, "Bilesik Yasak")
    compound_order = make_order(
        db, cid, compound_customer, due_date="2026-01-01", total=1000
    )
    db.commit()


def second_period_amount():
    with SessionLocal() as db:
        timeline = _timeline_for_order(db, cid, compound_order, date(2026, 4, 30))
        policy = resolve_late_fee_policy(db, cid, compound_customer, date(2026, 4, 30))
        snapshot, _ = _calculation_snapshot(
            timeline,
            date(2026, 4, 1),
            date(2026, 4, 30),
            annual_rate=policy.annual_rate,
            day_count_basis=policy.day_count_basis,
            grace_days=policy.grace_days,
            tax_mode=policy.tax_mode,
            vat_rate=policy.vat_rate,
        )
        return dec(snapshot["net_amount"])


baseline = second_period_amount()
assert baseline > dec("0"), baseline

with SessionLocal() as db:
    first_charge = make_charge(
        db, cid, compound_order, compound_customer,
        period_start="2026-01-02", period_end="2026-03-31", gross=90,
    )
    partial_payment = make_payment(db, cid, compound_customer, 40)
    db.commit()
with SessionLocal() as db:
    manual_allocate_payment(
        db, cid, partial_payment, None, dec("40"), date(2026, 7, 1),
        "compound-partial", receivable_charge_id=first_charge,
    )
assert second_period_amount() == baseline, "kismi tahsis faizi degistirdi"

with SessionLocal() as db:
    rest_payment = make_payment(db, cid, compound_customer, 50)
    db.commit()
with SessionLocal() as db:
    manual_allocate_payment(
        db, cid, rest_payment, None, dec("50"), date(2026, 7, 1),
        "compound-full", receivable_charge_id=first_charge,
    )
assert second_period_amount() == baseline, "tam tahsis faizi degistirdi"

# ---------------------------------------------------------------------- FIFO
# Pool = company + customer + currency. Principal first, in (due_date, id)
# order; late fees are unreachable until every open principal is closed.
with SessionLocal() as db:
    fifo_customer = make_customer(db, cid, "FIFO Havuz")
    order_early = make_order(
        db, cid, fifo_customer, due_date="2026-02-01", total=100, document_no="F1"
    )
    order_late = make_order(
        db, cid, fifo_customer, due_date="2026-03-01", total=100, document_no="F2"
    )
    charge_early = make_charge(
        db, cid, order_early, fifo_customer,
        period_start="2026-02-02", period_end="2026-02-28", gross=30,
    )
    charge_late = make_charge(
        db, cid, order_late, fifo_customer,
        period_start="2026-03-02", period_end="2026-03-31", gross=40,
    )
    partial = make_payment(db, cid, fifo_customer, 150)
    db.commit()
with SessionLocal() as db:
    allocate_payment(db, cid, partial, "fifo-principal-only")
with SessionLocal() as db:
    rows = allocations_of(db, cid, partial)
    assert [row["order_id"] for row in rows] == [order_early, order_late], rows
    assert all(row["receivable_charge_id"] is None for row in rows), rows
    assert [dec(row["amount"]) for row in rows] == [dec("100"), dec("50")], rows

with SessionLocal() as db:
    closing = make_payment(db, cid, fifo_customer, 120)
    db.commit()
with SessionLocal() as db:
    allocate_payment(db, cid, closing, "fifo-then-late-fee")
with SessionLocal() as db:
    rows = allocations_of(db, cid, closing)
    # 50 closes the remaining principal, then the late fees in (due, id) order.
    assert [
        (row["order_id"], row["receivable_charge_id"], dec(row["amount"]))
        for row in rows
    ] == [
        (order_late, None, dec("50")),
        (None, charge_early, dec("30")),
        (None, charge_late, dec("40")),
    ], rows

# --------------------------------------------------------- direct allocation
with SessionLocal() as db:
    direct_customer = make_customer(db, cid, "Dogrudan Tahsis")
    direct_order = make_order(
        db, cid, direct_customer, due_date="2026-02-01", total=100
    )
    posted_charge = make_charge(
        db, cid, direct_order, direct_customer,
        period_start="2026-02-02", period_end="2026-02-28", gross=50,
    )
    draft_charge = make_charge(
        db, cid, direct_order, direct_customer,
        period_start="2026-03-02", period_end="2026-03-28", gross=50,
        status="draft", posted=False,
    )
    reversed_charge = make_charge(
        db, cid, direct_order, direct_customer,
        period_start="2026-04-02", period_end="2026-04-28", gross=50,
        status="reversed",
    )
    foreign_customer = make_customer(db, cid, "Baska Cari")
    foreign_order = make_order(
        db, cid, foreign_customer, due_date="2026-02-01", total=100
    )
    foreign_charge = make_charge(
        db, cid, foreign_order, foreign_customer,
        period_start="2026-02-02", period_end="2026-02-28", gross=50,
    )
    fx_account = int(db.execute(
        text("""INSERT INTO finance_accounts(
            company_id,name,account_type,currency,opening_balance,is_active,created_at
        ) VALUES(:cid,'USD Kasa','cash','USD',0,:active,'2026-01-01')
        RETURNING id"""),
        {"cid": cid, "active": True},
    ).scalar_one())
    overage_payment = make_payment(db, cid, direct_customer, 80)
    fx_payment = make_payment(
        db, cid, direct_customer, 10, account_id=fx_account
    )
    good_payment = make_payment(db, cid, direct_customer, 20)
    db.commit()

# Overage: rejected with 409 and nothing is written.
with SessionLocal() as db:
    try:
        manual_allocate_payment(
            db, cid, overage_payment, None, dec("80"), date(2026, 7, 1),
            "direct-overage", receivable_charge_id=posted_charge,
        )
        raise AssertionError("asim reddedilmeliydi")
    except HTTPException as exc:
        assert exc.status_code == 409, exc.detail
with SessionLocal() as db:
    assert allocations_of(db, cid, overage_payment) == []
    assert db.execute(
        text("""SELECT COUNT(*) FROM payment_idempotency
        WHERE company_id=:cid AND status='completed'
          AND idempotency_key='direct-overage'"""),
        {"cid": cid},
    ).scalar_one() == 0

# Currency mismatch is fail-closed.
with SessionLocal() as db:
    try:
        manual_allocate_payment(
            db, cid, fx_payment, None, dec("10"), date(2026, 7, 1),
            "direct-fx", receivable_charge_id=posted_charge,
        )
        raise AssertionError("para birimi uyusmazligi reddedilmeliydi")
    except HTTPException as exc:
        assert exc.status_code == 409, exc.detail

# Draft, reversed and foreign-customer targets are refused identically.
for label, target in (
    ("draft", draft_charge),
    ("reversed", reversed_charge),
    ("foreign", foreign_charge),
    ("missing", 10_000_000),
):
    with SessionLocal() as db:
        try:
            manual_allocate_payment(
                db, cid, good_payment, None, dec("1"), date(2026, 7, 1),
                f"direct-{label}", receivable_charge_id=target,
            )
            raise AssertionError(f"{label} hedef reddedilmeliydi")
        except HTTPException as exc:
            assert exc.status_code == 404, (label, exc.status_code)
            assert exc.detail == CHARGE_TARGET_NOT_FOUND, exc.detail

# Happy path, then reversal and reallocation on a charge target.
with SessionLocal() as db:
    created = manual_allocate_payment(
        db, cid, good_payment, None, dec("20"), date(2026, 7, 1),
        "direct-ok", receivable_charge_id=posted_charge,
    )
assert created.receivable_charge_id == posted_charge
assert created.order_id is None
with SessionLocal() as db:
    moved = reallocate_allocation(
        db, cid, created.allocation_id, direct_order, dec("20"), date(2026, 7, 2),
        "direct-realloc",
    )
assert moved.order_id == direct_order and moved.receivable_charge_id is None
with SessionLocal() as db:
    back = reallocate_allocation(
        db, cid, moved.allocation_id, None, dec("20"), date(2026, 7, 3),
        "direct-realloc-back", target_receivable_charge_id=posted_charge,
    )
assert back.receivable_charge_id == posted_charge

# ------------------------------------------- charge reversal vs. allocations
with SessionLocal() as db:
    try:
        reverse_late_fee_document(
            db, cid, posted_charge, "charge-reversal-blocked", actor_id=1
        )
        raise AssertionError("tahsisli belge reversal'i reddedilmeliydi")
    except HTTPException as exc:
        assert exc.status_code == 409, exc.detail
        db.rollback()
with SessionLocal() as db:
    reverse_allocation(
        db, cid, back.allocation_id, dec("20"), date(2026, 7, 4), "charge-alloc-reverse"
    )
with SessionLocal() as db:
    reversal_document = reverse_late_fee_document(
        db, cid, posted_charge, "charge-reversal-ok", actor_id=1
    )
assert dec(reversal_document["gross_amount"]) == dec("-50.00")

# ------------------------------------------------------- ledger-aware aging
with SessionLocal() as db:
    aged = {
        row.id: row
        for row in calculate_net_receivables(db, cid, date(2026, 7, 31))
        if row.document_type == "late_fee"
    }
    assert aged[charge_early].applied == dec("30.00")
    assert aged[charge_early].remaining == dec("0.00")
    assert aged[charge_late].applied == dec("40.00")
    # Fully reversed allocation nets back to zero applied.
    assert aged[posted_charge].applied == dec("0.00")
    assert aged[posted_charge].remaining == dec("50.00")

# --------------------------------------------------------- reconciliation x5
# One isolated scenario per quarantine reason.
def recon_reasons(db):
    return {
        row.evidence_source
        for row in build_reconciliation_report(db, cid)
        if row.status == "quarantined"
    }


with SessionLocal() as db:
    before = recon_reasons(db)

# (1) charge excess
with SessionLocal() as db:
    c1 = make_customer(db, cid, "Mutabakat Tahakkuk Asimi")
    o1 = make_order(db, cid, c1, due_date="2026-02-01", total=100)
    ch1 = make_charge(
        db, cid, o1, c1, period_start="2026-02-02", period_end="2026-02-28", gross=10
    )
    p1 = make_payment(db, cid, c1, 25)
    db.execute(
        text("""INSERT INTO payment_allocations(
            company_id,payment_id,order_id,receivable_charge_id,amount,
            allocation_type,effective_date
        ) VALUES(:cid,:payment_id,NULL,:charge_id,25,'manual','2026-07-01')"""),
        {"cid": cid, "payment_id": p1, "charge_id": ch1},
    )
    db.commit()
with SessionLocal() as db:
    assert "charge_allocation_excess" in recon_reasons(db)

# (2) payment excess on a charge-only payment
with SessionLocal() as db:
    c2 = make_customer(db, cid, "Mutabakat Odeme Asimi")
    o2 = make_order(db, cid, c2, due_date="2026-02-01", total=100)
    ch2 = make_charge(
        db, cid, o2, c2, period_start="2026-02-02", period_end="2026-02-28", gross=100
    )
    p2 = make_payment(db, cid, c2, 5)
    db.execute(
        text("""INSERT INTO payment_allocations(
            company_id,payment_id,order_id,receivable_charge_id,amount,
            allocation_type,effective_date
        ) VALUES(:cid,:payment_id,NULL,:charge_id,40,'manual','2026-07-01')"""),
        {"cid": cid, "payment_id": p2, "charge_id": ch2},
    )
    db.commit()
with SessionLocal() as db:
    assert "payment_allocation_excess" in recon_reasons(db)

# (3) order excess
with SessionLocal() as db:
    c3 = make_customer(db, cid, "Mutabakat Belge Asimi")
    o3 = make_order(db, cid, c3, due_date="2026-02-01", total=10)
    p3 = make_payment(db, cid, c3, 100)
    db.execute(
        text("""INSERT INTO payment_allocations(
            company_id,payment_id,order_id,receivable_charge_id,amount,
            allocation_type,effective_date
        ) VALUES(:cid,:payment_id,:order_id,NULL,60,'manual','2026-07-01')"""),
        {"cid": cid, "payment_id": p3, "order_id": o3},
    )
    db.commit()
with SessionLocal() as db:
    row = next(
        item
        for item in build_reconciliation_report(db, cid)
        if item.order_id == o3
    )
    assert row.evidence_source == "order_allocation_excess", row.evidence_source

# (4) orphan charge foreign key and (5) target XOR violation.
# Both anomalies are exactly what the database refuses, so they are fabricated
# with the guards briefly lifted — reconciliation exists to catch rows that
# predate the constraints or arrive through a restore.
def relax_guards(db):
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text(
            "ALTER TABLE payment_allocations "
            "DROP CONSTRAINT ck_payment_allocation_target_xor"
        ))
        db.execute(text(
            "ALTER TABLE payment_allocations "
            "DROP CONSTRAINT fk_payment_allocation_receivable_charge"
        ))
    else:
        # PRAGMAs must run before this session emits any DML, otherwise SQLite
        # ignores them for the open transaction.
        db.execute(text("PRAGMA foreign_keys = OFF"))
        db.execute(text("PRAGMA ignore_check_constraints = true"))


def restore_guards(db):
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text(
            "ALTER TABLE payment_allocations "
            "ADD CONSTRAINT ck_payment_allocation_target_xor "
            "CHECK ((order_id IS NOT NULL) <> (receivable_charge_id IS NOT NULL)) "
            "NOT VALID"
        ))
        db.execute(text(
            "ALTER TABLE payment_allocations "
            "ADD CONSTRAINT fk_payment_allocation_receivable_charge "
            "FOREIGN KEY (company_id,receivable_charge_id) "
            "REFERENCES receivable_charge_documents(company_id,id) "
            "ON DELETE RESTRICT NOT VALID"
        ))
    else:
        db.execute(text("PRAGMA ignore_check_constraints = false"))
        db.execute(text("PRAGMA foreign_keys = ON"))


with SessionLocal() as db:
    c4 = make_customer(db, cid, "Mutabakat Yetim")
    o4 = make_order(db, cid, c4, due_date="2026-02-01", total=100)
    ch4 = make_charge(
        db, cid, o4, c4, period_start="2026-02-02", period_end="2026-02-28", gross=10
    )
    p4 = make_payment(db, cid, c4, 10)
    db.commit()
with SessionLocal() as db:
    relax_guards(db)
    db.execute(
        text("""INSERT INTO payment_allocations(
            company_id,payment_id,order_id,receivable_charge_id,amount,
            allocation_type,effective_date
        ) VALUES(:cid,:payment_id,NULL,:charge_id,10,'manual','2026-07-01')"""),
        {"cid": cid, "payment_id": p4, "charge_id": ch4},
    )
    db.execute(
        text("""DELETE FROM receivable_charge_documents
        WHERE id=:id AND company_id=:cid"""),
        {"id": ch4, "cid": cid},
    )
    db.commit()
with SessionLocal() as db:
    restore_guards(db)
    db.commit()
with SessionLocal() as db:
    assert "charge_allocation_orphan_fk" in recon_reasons(db)

with SessionLocal() as db:
    c5 = make_customer(db, cid, "Mutabakat XOR")
    o5 = make_order(db, cid, c5, due_date="2026-02-01", total=100)
    ch5 = make_charge(
        db, cid, o5, c5, period_start="2026-02-02", period_end="2026-02-28", gross=10
    )
    p5 = make_payment(db, cid, c5, 10)
    db.commit()
# The XOR check must actively refuse a two-target row.
with SessionLocal() as db:
    try:
        db.execute(
            text("""INSERT INTO payment_allocations(
                company_id,payment_id,order_id,receivable_charge_id,amount,
                allocation_type,effective_date
            ) VALUES(:cid,:payment_id,:order_id,:charge_id,10,'manual','2026-07-01')"""),
            {"cid": cid, "payment_id": p5, "order_id": o5, "charge_id": ch5},
        )
        db.commit()
        raise AssertionError("XOR ihlali veritabanınca reddedilmeliydi")
    except IntegrityError:
        db.rollback()
with SessionLocal() as db:
    relax_guards(db)
    db.execute(
        text("""INSERT INTO payment_allocations(
            company_id,payment_id,order_id,receivable_charge_id,amount,
            allocation_type,effective_date
        ) VALUES(:cid,:payment_id,:order_id,:charge_id,10,'manual','2026-07-01')"""),
        {"cid": cid, "payment_id": p5, "order_id": o5, "charge_id": ch5},
    )
    db.commit()
with SessionLocal() as db:
    restore_guards(db)
    db.commit()
with SessionLocal() as db:
    assert "allocation_target_xor_violation" in recon_reasons(db)

print("HARMAN_V2C_ENABLED_OK")
'''


_DISABLED_SMOKE = _FIXTURE + r'''
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.payment_allocation_engine import (
    CHARGE_ALLOCATION_DISABLED_MESSAGE,
    allocate_payment,
    manual_allocate_payment,
    reallocate_allocation,
)
from app.receivables_engine import calculate_net_receivables

cid = company_id()

with SessionLocal() as db:
    customer = make_customer(db, cid, "Bayrak Kapali")
    order_id = make_order(db, cid, customer, due_date="2026-02-01", total=100)
    charge_id = make_charge(
        db, cid, order_id, customer,
        period_start="2026-02-02", period_end="2026-02-28", gross=30,
    )
    payment_id = make_payment(db, cid, customer, 100)
    db.commit()
with SessionLocal() as db:
    order_allocation = manual_allocate_payment(
        db, cid, payment_id, order_id, dec("100"), date(2026, 7, 1), "off-order"
    )

guarded = 0

# 1) manual (direct) allocation
with SessionLocal() as db:
    try:
        manual_allocate_payment(
            db, cid, payment_id, None, dec("1"), date(2026, 7, 1),
            "off-manual", receivable_charge_id=charge_id,
        )
        raise AssertionError("bayrak kapaliyken tahsis edilmemeliydi")
    except HTTPException as exc:
        assert exc.status_code == 422 and exc.detail == CHARGE_ALLOCATION_DISABLED_MESSAGE
        guarded += 1

# 2) reallocation
with SessionLocal() as db:
    try:
        reallocate_allocation(
            db, cid, order_allocation.allocation_id, None, dec("1"),
            date(2026, 7, 2), "off-realloc", target_receivable_charge_id=charge_id,
        )
        raise AssertionError("bayrak kapaliyken yeniden tahsis edilmemeliydi")
    except HTTPException as exc:
        assert exc.status_code == 422 and exc.detail == CHARGE_ALLOCATION_DISABLED_MESSAGE
        guarded += 1

# 3) payment create and 4) payment update over the API
with SessionLocal() as db:
    api_customer = make_customer(db, cid, "Bayrak API")
    api_order = make_order(db, cid, api_customer, due_date="2026-02-01", total=100)
    api_charge = make_charge(
        db, cid, api_order, api_customer,
        period_start="2026-02-02", period_end="2026-02-28", gross=30,
    )
    db.commit()

with TestClient(app) as client:
    # Shared-DB PG runs execute every smoke in this file against one database,
    # so an earlier smoke may already have rotated the bootstrap password.
    # Hard-coding "admin123" here made this smoke depend on running FIRST:
    # in reverse order the login returned no token and the body raised
    # KeyError: 'access_token'. (Olculdu.)
    OWN_PW = "ChargeFlagOff123!"
    headers = None
    for candidate in ("admin123", OWN_PW, "PutChargeAdmin123!"):
        login = client.post(
            "/api/auth/login", json={"username": "admin", "password": candidate}
        )
        if login.status_code != 200:
            continue
        body = login.json()
        headers = {
            "Authorization": "Bearer " + body["access_token"],
            "X-Company-ID": str(body["companies"][0]["id"]),
        }
        if candidate != OWN_PW:
            changed = client.post(
                "/api/auth/change-password",
                headers=headers,
                json={"current_password": candidate, "new_password": OWN_PW},
            )
            assert changed.status_code == 200, changed.text
            headers["Authorization"] = "Bearer " + changed.json()["access_token"]
        break
    assert headers is not None, "admin girisi hicbir bilinen sifreyle yapilamadi"
    body = {
        "entity_type": "customer",
        "entity_id": api_customer,
        "amount": "5",
        "payment_date": "2026-07-05",
        "payment_method": "cash",
        "reference_type": "late_fee",
        "reference_id": api_charge,
    }
    created = client.post("/api/payments", headers=headers, json=body)
    assert created.status_code == 422, created.text
    assert CHARGE_ALLOCATION_DISABLED_MESSAGE in created.json()["detail"]
    guarded += 1

    plain = client.post(
        "/api/payments",
        headers={**headers, "Idempotency-Key": "flag-off-plain"},
        json={
            "entity_type": "customer",
            "entity_id": api_customer,
            "amount": "5",
            "payment_date": "2026-07-05",
            "payment_method": "cash",
        },
    )
    assert plain.status_code == 201, plain.text
    updated = client.put(
        f"/api/payments/{plain.json()['id']}",
        headers={**headers, "Idempotency-Key": "flag-off-update"},
        json=body,
    )
    assert updated.status_code == 422, updated.text
    assert CHARGE_ALLOCATION_DISABLED_MESSAGE in updated.json()["detail"]
    guarded += 1

# 5) bulk document payment path over an already charge-allocated payment
with SessionLocal() as db:
    bulk_payment = make_payment(
        db, cid, customer, 5, reference_type="order", reference_id=order_id
    )
    db.execute(
        text("""INSERT INTO payment_allocations(
            company_id,payment_id,order_id,receivable_charge_id,amount,
            allocation_type,effective_date
        ) VALUES(:cid,:payment_id,NULL,:charge_id,5,'manual','2026-07-01')"""),
        {"cid": cid, "payment_id": bulk_payment, "charge_id": charge_id},
    )
    db.commit()

from app.routers.transactions import _sync_payment  # noqa: E402


class _Payload:
    entity_id = customer
    status = "completed"
    paid_amount = dec("5")
    payment_method = "cash"
    payment_account_id = None
    payment_allocations = ()
    transaction_date = "2026-07-05"


with SessionLocal() as db:
    try:
        _sync_payment("sale", order_id, _Payload(), db, cid)
        raise AssertionError("tahakkuk tahsisli belge odemesi yazilmamaliydi")
    except HTTPException as exc:
        assert exc.status_code == 422 and exc.detail == CHARGE_ALLOCATION_DISABLED_MESSAGE
        db.rollback()
        guarded += 1

assert guarded == 5, guarded

# Derivation stays ledger-aware even with the write flag off.
with SessionLocal() as db:
    aged = {
        row.id: row
        for row in calculate_net_receivables(db, cid, date(2026, 7, 31))
        if row.document_type == "late_fee"
    }
    assert aged[charge_id].applied == dec("5.00")
    assert aged[charge_id].remaining == dec("25.00")

# With an empty charge ledger the numbers are identical to the legacy shape.
with SessionLocal() as db:
    legacy_customer = make_customer(db, cid, "Bos Defter")
    legacy_order = make_order(
        db, cid, legacy_customer, due_date="2026-02-01", total=100
    )
    legacy_charge = make_charge(
        db, cid, legacy_order, legacy_customer,
        period_start="2026-02-02", period_end="2026-02-28", gross=33,
    )
    db.commit()
with SessionLocal() as db:
    row = next(
        item
        for item in calculate_net_receivables(db, cid, date(2026, 7, 31))
        if item.document_type == "late_fee" and item.id == legacy_charge
    )
    assert row.applied == dec("0.00") and row.remaining == dec("33.00")

print("HARMAN_V2C_DISABLED_OK")
'''


_RACE_SMOKE = _FIXTURE + r'''
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from fastapi import HTTPException

from app.late_fee_charge_engine import reverse_late_fee_document
from app.payment_allocation_engine import (
    allocate_payment_with_retry,
    manual_allocate_payment,
    manual_allocate_payment_with_retry,
    reallocate_allocation_with_retry,
)

cid = company_id()

# 1) Concurrent direct + FIFO on the same payment must not over-allocate.
with SessionLocal() as db:
    customer = make_customer(db, cid, "Yaris Cari")
    order_id = make_order(db, cid, customer, due_date="2026-02-01", total=60)
    charge_id = make_charge(
        db, cid, order_id, customer,
        period_start="2026-02-02", period_end="2026-02-28", gross=60,
    )
    payment_id = make_payment(db, cid, customer, 60)
    db.commit()

barrier = Barrier(2)


def run_fifo():
    barrier.wait()
    try:
        allocate_payment_with_retry(SessionLocal, cid, payment_id, "race-fifo")
        return "fifo"
    except HTTPException as exc:
        assert exc.status_code == 409, exc.detail
        return "fifo_rejected"


def run_direct():
    barrier.wait()
    try:
        manual_allocate_payment_with_retry(
            SessionLocal, cid, payment_id, None, dec("60"), date(2026, 7, 1),
            "race-direct", receivable_charge_id=charge_id,
        )
        return "direct"
    except HTTPException as exc:
        assert exc.status_code == 409, exc.detail
        return "direct_rejected"


with ThreadPoolExecutor(max_workers=2) as executor:
    futures = [executor.submit(run_fifo), executor.submit(run_direct)]
    outcomes = [future.result() for future in futures]

with SessionLocal() as db:
    total = dec(db.execute(
        text("""SELECT COALESCE(SUM(
            CASE WHEN reversal_of_allocation_id IS NULL THEN amount ELSE -amount END
        ),0) FROM payment_allocations
        WHERE company_id=:cid AND payment_id=:payment_id"""),
        {"cid": cid, "payment_id": payment_id},
    ).scalar_one())
    assert total <= dec("60"), (total, outcomes)

# 2) Charge reversal vs. a new allocation on the same charge: one winner.
with SessionLocal() as db:
    duel_customer = make_customer(db, cid, "Duello")
    duel_order = make_order(db, cid, duel_customer, due_date="2026-02-01", total=10)
    duel_charge = make_charge(
        db, cid, duel_order, duel_customer,
        period_start="2026-02-02", period_end="2026-02-28", gross=40,
    )
    duel_payment = make_payment(db, cid, duel_customer, 40)
    db.commit()

duel_barrier = Barrier(2)


def run_charge_reversal():
    duel_barrier.wait()
    with SessionLocal() as db:
        try:
            reverse_late_fee_document(db, cid, duel_charge, "duel-reversal", actor_id=1)
            return "reversed"
        except HTTPException as exc:
            db.rollback()
            assert exc.status_code in (409, 404), exc.detail
            return "reversal_rejected"


def run_new_allocation():
    duel_barrier.wait()
    try:
        manual_allocate_payment_with_retry(
            SessionLocal, cid, duel_payment, None, dec("40"), date(2026, 7, 1),
            "duel-allocation", receivable_charge_id=duel_charge,
        )
        return "allocated"
    except HTTPException as exc:
        assert exc.status_code in (404, 409), exc.detail
        return "allocation_rejected"


with ThreadPoolExecutor(max_workers=2) as executor:
    # Both tasks must be submitted before any result is awaited, otherwise the
    # barrier can never be reached.
    duel_futures = [
        executor.submit(run_charge_reversal),
        executor.submit(run_new_allocation),
    ]
    duel = {future.result() for future in duel_futures}
assert duel in (
    {"reversed", "allocation_rejected"},
    {"reversal_rejected", "allocated"},
), duel
with SessionLocal() as db:
    status = db.execute(
        text("SELECT status FROM receivable_charge_documents WHERE id=:id AND company_id=:cid"),
        {"id": duel_charge, "cid": cid},
    ).scalar_one()
    allocated = dec(db.execute(
        text("""SELECT COALESCE(SUM(
            CASE WHEN reversal_of_allocation_id IS NULL THEN amount ELSE -amount END
        ),0) FROM payment_allocations
        WHERE company_id=:cid AND receivable_charge_id=:charge_id"""),
        {"cid": cid, "charge_id": duel_charge},
    ).scalar_one())
    assert not (status == "reversed" and allocated > dec("0")), (status, allocated)

# 3) Reallocation in both directions must not deadlock.
with SessionLocal() as db:
    swap_customer = make_customer(db, cid, "Takas")
    swap_order = make_order(db, cid, swap_customer, due_date="2026-02-01", total=50)
    swap_charge = make_charge(
        db, cid, swap_order, swap_customer,
        period_start="2026-02-02", period_end="2026-02-28", gross=50,
    )
    left_payment = make_payment(db, cid, swap_customer, 20)
    right_payment = make_payment(db, cid, swap_customer, 20)
    db.commit()
with SessionLocal() as db:
    left = manual_allocate_payment(
        db, cid, left_payment, swap_order, dec("20"), date(2026, 7, 1), "swap-left"
    )
with SessionLocal() as db:
    right = manual_allocate_payment(
        db, cid, right_payment, None, dec("20"), date(2026, 7, 1), "swap-right",
        receivable_charge_id=swap_charge,
    )

swap_barrier = Barrier(2)


def move_to_charge():
    swap_barrier.wait()
    reallocate_allocation_with_retry(
        SessionLocal, cid, left.allocation_id, None, dec("20"), date(2026, 7, 2),
        "swap-to-charge", target_receivable_charge_id=swap_charge,
    )
    return "to_charge"


def move_to_order():
    swap_barrier.wait()
    reallocate_allocation_with_retry(
        SessionLocal, cid, right.allocation_id, swap_order, dec("20"),
        date(2026, 7, 2), "swap-to-order",
    )
    return "to_order"


with ThreadPoolExecutor(max_workers=2) as executor:
    swap_futures = [
        executor.submit(move_to_charge),
        executor.submit(move_to_order),
    ]
    swapped = {future.result() for future in swap_futures}
assert swapped == {"to_charge", "to_order"}, swapped

print("HARMAN_V2C_RACE_OK")
'''


_PIN_SMOKE = _FIXTURE + r'''
from app.payment_allocation_engine import (
    allocate_payment,
    reverse_allocation,
)

cid = company_id()

# FIFO order is fixed AT ALLOCATION TIME. Reversing a principal allocation
# after a later payment reached the late fee must not rewrite that later
# allocation; only payments allocated afterwards see the reopened principal.
with SessionLocal() as db:
    customer = make_customer(db, cid, "Pin Cari")
    order_id = make_order(db, cid, customer, due_date="2026-02-01", total=100)
    charge_id = make_charge(
        db, cid, order_id, customer,
        period_start="2026-02-02", period_end="2026-02-28", gross=30,
    )
    principal_payment = make_payment(db, cid, customer, 100)
    db.commit()
with SessionLocal() as db:
    allocate_payment(db, cid, principal_payment, "pin-principal")
with SessionLocal() as db:
    rows = allocations_of(db, cid, principal_payment)
    assert [(r["order_id"], r["receivable_charge_id"], dec(r["amount"])) for r in rows] == [
        (order_id, None, dec("100")),
    ], rows
    principal_allocation = int(rows[0]["id"])

with SessionLocal() as db:
    late_fee_payment = make_payment(db, cid, customer, 30, payment_date="2026-07-02")
    db.commit()
with SessionLocal() as db:
    allocate_payment(db, cid, late_fee_payment, "pin-late-fee")
with SessionLocal() as db:
    rows = allocations_of(db, cid, late_fee_payment)
    assert [(r["order_id"], r["receivable_charge_id"], dec(r["amount"])) for r in rows] == [
        (None, charge_id, dec("30")),
    ], rows

# Principal reopens; the existing late-fee allocation must stay untouched.
with SessionLocal() as db:
    reverse_allocation(
        db, cid, principal_allocation, dec("100"), date(2026, 7, 3), "pin-reverse"
    )
with SessionLocal() as db:
    rows = allocations_of(db, cid, late_fee_payment)
    assert [(r["order_id"], r["receivable_charge_id"], dec(r["amount"]),
             r["reversal_of_allocation_id"]) for r in rows] == [
        (None, charge_id, dec("30"), None),
    ], "late-fee tahsisi geriye tasindi"

# The NEXT unlinked payment goes to the reopened principal first.
with SessionLocal() as db:
    next_payment = make_payment(db, cid, customer, 50, payment_date="2026-07-04")
    db.commit()
with SessionLocal() as db:
    allocate_payment(db, cid, next_payment, "pin-next")
with SessionLocal() as db:
    rows = allocations_of(db, cid, next_payment)
    assert [(r["order_id"], r["receivable_charge_id"], dec(r["amount"])) for r in rows] == [
        (order_id, None, dec("50")),
    ], rows

print("HARMAN_V2C_PIN_OK")
'''


_PUT_SMOKE = _FIXTURE + r'''
from datetime import date as put_as_of

from fastapi.testclient import TestClient

from app.main import app
from app.receivables_engine import calculate_net_receivables

cid = company_id()

with SessionLocal() as db:
    customer = make_customer(db, cid, "PUT Cari")
    order_id = make_order(db, cid, customer, due_date="2026-02-01", total=100)
    charge_id = make_charge(
        db, cid, order_id, customer,
        period_start="2026-02-02", period_end="2026-02-28", gross=90,
    )
    ok_payment = make_payment(db, cid, customer, 60, payment_date="2026-07-01")
    over_payment = make_payment(db, cid, customer, 100, payment_date="2026-07-01")
    db.commit()

with TestClient(app) as client:
    # Shared-DB PG runs execute every smoke in this file against one database,
    # so an earlier smoke may already have rotated the bootstrap password.
    OWN_PW = "PutChargeAdmin123!"
    headers = None
    for candidate in ("admin123", "ChargeFlagOff123!", OWN_PW):
        login = client.post(
            "/api/auth/login", json={"username": "admin", "password": candidate}
        )
        if login.status_code != 200:
            continue
        body = login.json()
        headers = {
            "Authorization": "Bearer " + body["access_token"],
            "X-Company-ID": str(body["companies"][0]["id"]),
        }
        if candidate != OWN_PW:
            changed = client.post(
                "/api/auth/change-password",
                headers=headers,
                json={"current_password": candidate, "new_password": OWN_PW},
            )
            assert changed.status_code == 200, changed.text
            headers["Authorization"] = "Bearer " + changed.json()["access_token"]
        break
    assert headers is not None, "admin girisi hicbir parola ile basarili olmadi"

    def put_body(payment_amount):
        return {
            "entity_type": "customer",
            "entity_id": customer,
            "amount": str(payment_amount),
            "payment_date": "2026-07-01",
            "payment_method": "cash",
            "reference_type": "late_fee",
            "reference_id": charge_id,
        }

    # (a) payment row + allocation row land in the same transaction.
    updated = client.put(
        f"/api/payments/{ok_payment}",
        headers={**headers, "Idempotency-Key": "put-charge-ok"},
        json=put_body(60),
    )
    assert updated.status_code == 200, updated.text
    with SessionLocal() as db:
        rows = allocations_of(db, cid, ok_payment)
        assert [(r["order_id"], r["receivable_charge_id"], dec(r["amount"]))
                for r in rows] == [(None, charge_id, dec("60"))], rows
        reference = db.execute(text(
            "SELECT reference_type,reference_id FROM payments WHERE id=:id"
        ), {"id": ok_payment}).first()
        assert tuple(reference) == ("late_fee", charge_id), reference
        aged = next(
            row for row in calculate_net_receivables(db, cid, put_as_of(2026, 7, 31))
            if row.document_type == "late_fee" and row.id == charge_id
        )
        assert aged.applied == dec("60.00") and aged.remaining == dec("30.00")

    # (b) overage: 90'lik belgede 30 kaldi; 100'luk odeme baglanamaz —
    # 409 + odeme satiri DA tahsis satiri DA geri alinir.
    denied = client.put(
        f"/api/payments/{over_payment}",
        headers={**headers, "Idempotency-Key": "put-charge-over"},
        json=put_body(100),
    )
    assert denied.status_code == 409, denied.text
    with SessionLocal() as db:
        assert allocations_of(db, cid, over_payment) == []
        reference = db.execute(text(
            "SELECT reference_type,reference_id FROM payments WHERE id=:id"
        ), {"id": over_payment}).first()
        assert tuple(reference) == (None, None), reference

print("HARMAN_V2C_PUT_OK")
'''
