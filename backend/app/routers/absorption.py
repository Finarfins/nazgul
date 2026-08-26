"""V3 Absorption Rate KPI — Increment 1.

Absorption rate is the classic dealer profitability metric: how much of the
dealership's fixed overhead is *absorbed* (covered) by the gross profit the
parts and service departments generate::

    absorption_rate % = (parts gross profit + service gross profit) / overhead * 100

Increment 1 computes the metric from data this ERP already stores — no new
table, no migration:

* **Parts gross profit** comes from two disjoint channels:
  - counter sales, taken from the header ``orders.subtotal`` of non-cancelled
    orders — net of VAT *and* net of the document-level discount. Summing
    ``order_items.line_subtotal`` instead would miss the document discount
    entirely (it is applied after the lines are built) and overstate the KPI, and
  - parts consumed on service jobs (``work_order_parts``), whose net revenue is
    recomputed per line through :func:`app.money.compute_line` so it matches the
    billing/invoice math exactly (VAT excluded).
  Parts COGS uses ``products.purchase_price`` — the only cost this ERP keeps per
  product. There is no moving-average / FIFO layer, so this is a
  current-cost approximation and is labelled as such in the response.

* **Service gross profit — an upper bound, not a profit.** Revenue is billed
  labour, taken from the same source the customer is actually charged from:
  for an invoiced work order the issued invoice's ``LABOR`` items, and for an
  uninvoiced one the canonical labor resolver
  (``billing_service.resolve_work_order_labor``) — approved labor lines when
  they exist, otherwise the v1 header hours × rate. ``total_labor_cost`` is
  never read here; it is the header projection and diverges from the invoice as
  soon as a work order carries approved labor lines (FAZ-2). Both invoiced and
  uninvoiced completed work orders are in scope, and the response reports the
  two groups separately. The ERP tracks **no technician cost rate**, so
  labour cost is reported as ``0.00`` with ``labor_cost_source="not_tracked"``
  and ``labor_cost_known=false``; the cost is never invented. Subtracting zero
  does not yield a gross profit, it yields the maximum the department could have
  earned had labour been free. The field is therefore named
  ``gross_profit_upper_bound``, the total is ``total_gross_profit_upper_bound``
  and the KPI is ``absorption_rate_upper_bound``, with a top-level
  ``is_upper_bound`` flag and a Turkish ``rate_note`` spelling it out. The true
  absorption rate is strictly lower. Consumers must not render these as an exact
  rate while ``is_upper_bound`` is set.

* **Overhead** is read from ``income_expenses`` rows with
  ``txn_type = 'expense'`` in the period (the same source the dashboard uses).
  Because no endpoint writes that table yet, the caller may override it with the
  ``overhead`` query parameter. The response always states which source was used
  via ``overhead_source`` (``"user_supplied"`` | ``"income_expenses"``).

Period basis: counter sales use ``orders.order_date``; service revenue and
work-order parts use ``work_orders.completed_at`` (revenue is recognised when
the job is completed, so open and cancelled work orders never contribute). A
cancelled invoice does not count as invoiced — that work order falls back to
the resolver, exactly like one that was never billed.

Known limitation: ``order_date`` and ``income_expenses.txn_date`` are local ISO
days, while ``completed_at`` is stamped in UTC. The period bounds are therefore
compared against UTC for the service side, so a job completed in the final hours
of a local day (UTC+3 here) is attributed to the previous day. Increment 1 keeps
this explicit rather than guessing a timezone; a company-level timezone setting
is the proper fix and is out of scope.

Interactive requests are limited to 366 inclusive calendar days. The service
parts path deliberately reuses the exact Python Decimal billing calculation;
the limit bounds its row and CPU cost until a set-based historical cost layer or
background export is introduced.

Money is Decimal end-to-end via :mod:`app.money`; the percentage is quantised to
four places. ``overhead <= 0`` yields ``absorption_rate = null`` plus a Turkish
``rate_note`` — never a division by zero.

The PostgreSQL 16 twin of the tests lives in
``test_absorption_rate_postgresql.py``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..billing_service import resolve_work_order_labor
from ..business_time import business_today
from ..db import get_db
from ..money import HUNDRED, PERCENT_QUANTUM, ZERO_MONEY, compute_line, decimal_value, money
from ..tenancy import company_id

router = APIRouter(prefix="/analytics", tags=["analytics"])

# Interactive API requests must stay bounded because service-part billing math
# is intentionally evaluated line-by-line through the shared Decimal contract.
MAX_PERIOD_DAYS = 366

# Draft/cancelled sales are not revenue. Kept as a literal tuple rendered into
# the SQL (no user input reaches it) so both dialects see a plain IN list.
EXCLUDED_ORDER_STATUSES = ("cancelled", "draft")
_EXCLUDED_SQL = ",".join(f"'{status}'" for status in EXCLUDED_ORDER_STATUSES)


def _parse_period(date_from: str | None, date_to: str | None) -> tuple[date, date]:
    """Resolve and validate a bounded ISO period, defaulting to year-to-date."""
    today = business_today()
    start = _parse_iso(date_from, "date_from") or date(today.year, 1, 1)
    end = _parse_iso(date_to, "date_to") or today
    if end < start:
        raise HTTPException(status_code=400, detail="Bitiş tarihi başlangıç tarihinden önce olamaz")
    inclusive_days = (end - start).days + 1
    if inclusive_days > MAX_PERIOD_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"Rapor dönemi en fazla {MAX_PERIOD_DAYS} gün olabilir",
        )
    return start, end


def _parse_iso(value: str | None, field: str) -> date | None:
    if value is None or not str(value).strip():
        return None
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field} formatı YYYY-AA-GG olmalıdır") from exc


def _parse_overhead(raw: str | None) -> Decimal | None:
    if raw is None or not str(raw).strip():
        return None
    try:
        value = money(decimal_value(raw, field_name="Genel gider"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Genel gider geçersiz") from exc
    if value < ZERO_MONEY:
        raise HTTPException(status_code=400, detail="Genel gider negatif olamaz")
    return value


def _counter_parts(db: Session, cid: int, start: date, end: date) -> tuple[Decimal, Decimal]:
    """Net-of-VAT counter parts revenue and COGS over ``orders.order_date``.

    Revenue comes from the *header* ``orders.subtotal``, not from summing
    ``order_items.line_subtotal``. Those are not the same number: the line rows
    carry only the per-line discount, while a document-level discount is applied
    afterwards by :func:`app.money.apply_global_discount` and persisted on the
    header (``_totals`` in routers/transactions.py returns
    ``subtotal_after_discount``). Summing the lines would therefore overstate
    revenue — and so the gross profit and the absorption rate — by the whole
    document discount.

    COGS still comes from the lines, grouped by the product identity columns only
    (PG16 strict GROUP BY safe), since quantities are unaffected by any discount.
    """
    revenue_row = db.execute(
        text(
            f"""
            SELECT COALESCE(SUM(o.subtotal),0) AS revenue
            FROM orders o
            WHERE o.company_id=:cid
              AND COALESCE(o.status,'completed') NOT IN ({_EXCLUDED_SQL})
              AND o.order_date>=:start AND o.order_date<=:end
            """
        ),
        {"cid": cid, "start": start.isoformat(), "end": end.isoformat()},
    ).mappings().one()

    cogs_rows = db.execute(
        text(
            f"""
            SELECT COALESCE(SUM(oi.quantity),0) AS quantity,
                   COALESCE(p.purchase_price,0) AS purchase_price
            FROM order_items oi
            JOIN orders o ON o.id=oi.order_id
            LEFT JOIN products p ON p.id=oi.product_id AND p.company_id=o.company_id
            WHERE o.company_id=:cid
              AND COALESCE(o.status,'completed') NOT IN ({_EXCLUDED_SQL})
              AND o.order_date>=:start AND o.order_date<=:end
            GROUP BY p.id,COALESCE(p.purchase_price,0)
            """
        ),
        {"cid": cid, "start": start.isoformat(), "end": end.isoformat()},
    ).mappings().all()

    revenue = money(revenue_row["revenue"])
    cogs = ZERO_MONEY
    for row in cogs_rows:
        cogs += money(decimal_value(row["quantity"]) * money(row["purchase_price"]))
    return revenue, cogs


def _service_parts(
    db: Session, cid: int, start: datetime, end: datetime
) -> tuple[Decimal, Decimal]:
    """Net-of-VAT parts revenue and COGS for parts used on completed work orders."""
    rows = db.execute(
        text(
            """
            SELECT wop.quantity,wop.unit_price,wop.discount,
                   COALESCE(p.purchase_price,0) AS purchase_price
            FROM work_order_parts wop
            JOIN work_orders w ON w.id=wop.work_order_id AND w.company_id=wop.company_id
            LEFT JOIN products p ON p.id=wop.product_id AND p.company_id=wop.company_id
            WHERE wop.company_id=:cid
              AND w.completed_at IS NOT NULL
              AND w.completed_at>=:start AND w.completed_at<=:end
            """
        ),
        {"cid": cid, "start": start, "end": end},
    ).mappings().all()

    revenue = ZERO_MONEY
    cogs = ZERO_MONEY
    for row in rows:
        # ``taxable`` is the discounted, VAT-excluded line amount — identical to
        # what the billing summary and the generated invoice line use.
        revenue += compute_line(row["quantity"], row["unit_price"], row["discount"]).taxable
        cogs += money(decimal_value(row["quantity"]) * money(row["purchase_price"]))
    return revenue, cogs


def _service_labor(db: Session, cid: int, start: datetime, end: datetime) -> dict:
    """Billed labour on work orders completed inside the period.

    SCOPE: every work order completed in the period counts, invoiced or not
    (unchanged from Increment 1). The two groups take their revenue from
    different, equally authoritative places:

    * **Invoiced** work orders report what was actually billed: the sum of the
      issued invoice's ``LABOR`` items, taken from the frozen invoice rows. A
      later labor-line or rate change cannot move an already-issued invoice, so
      it must not move the report either.
    * **Uninvoiced** work orders report what *would* be billed, through the same
      canonical resolver the preview and invoice generation use
      (``billing_service.resolve_work_order_labor``): approved labor lines when
      they exist, otherwise the v1 header hours × rate.

    ``work_orders.total_labor_cost`` is deliberately NOT read here. It is the v1
    header projection, so once a work order carries approved labor lines it
    disagrees with the invoice (invoice 850 vs projection 200) and the gap grows
    whenever the header rate is edited afterwards. The column itself is left
    untouched for its other consumers (the work-order list/detail totals).

    Cancelled invoices do not count as invoiced: such a work order falls back to
    the resolver, exactly like one that was never billed. ``invoices`` carries a
    UNIQUE ``(company_id, work_order_id, invoice_type)`` constraint, so a work
    order can never have two live invoices and the ``MIN(i.id)`` below can only
    ever collapse a single row.

    LABOR invoice items carry no VAT and no line discount (see
    ``invoice_service.generate_invoice``), and their ``total`` already has the
    document-level discount allocation folded in, so the sum is the net billed
    labour.
    """
    # ONE statement classifies every work order AND carries its invoiced labour,
    # so both facts come from a single database snapshot. Splitting this into a
    # "sum the invoiced" query plus a "list the uninvoiced" query is not safe on
    # READ COMMITTED: each statement takes its own snapshot, so an invoice
    # cancelled between the two makes the same work order land in *both* halves
    # (double count), and one issued between them makes it land in *neither*
    # (missing count). Classified together, a work order is by construction in
    # exactly one group.
    #
    # The resolver calls below run in later statements and may therefore read
    # slightly newer labor lines, but that only affects the *value* of an
    # already-classified row — it can no longer change which group it belongs
    # to, so nothing is counted twice or dropped.
    rows = db.execute(
        text(
            """
            SELECT w.id AS work_order_id,
                   inv.invoice_id AS invoice_id,
                   COALESCE(inv.labor_total,0) AS invoiced_labor
            FROM work_orders w
            LEFT JOIN (
                SELECT i.company_id AS company_id,
                       i.work_order_id AS work_order_id,
                       MIN(i.id) AS invoice_id,
                       COALESCE(SUM(
                           CASE WHEN ii.item_type='LABOR' THEN ii.total ELSE 0 END
                       ),0) AS labor_total
                FROM invoices i
                LEFT JOIN invoice_items ii
                       ON ii.invoice_id=i.id AND ii.company_id=i.company_id
                WHERE i.company_id=:cid
                  AND i.status<>'CANCELLED'
                  AND i.work_order_id IS NOT NULL
                GROUP BY i.company_id,i.work_order_id
            ) inv ON inv.company_id=w.company_id AND inv.work_order_id=w.id
            WHERE w.company_id=:cid
              AND w.completed_at IS NOT NULL
              AND w.completed_at>=:start AND w.completed_at<=:end
            ORDER BY w.id
            """
        ),
        {"cid": cid, "start": start, "end": end},
    ).mappings().all()

    invoiced_revenue = ZERO_MONEY
    invoiced_count = 0
    uninvoiced_ids: list[int] = []
    for row in rows:
        if row["invoice_id"] is None:
            uninvoiced_ids.append(int(row["work_order_id"]))
        else:
            invoiced_revenue += money(row["invoiced_labor"])
            invoiced_count += 1

    # Bounded by the same 366-day period cap as the per-line parts math above.
    uninvoiced_revenue = ZERO_MONEY
    for work_order_id in uninvoiced_ids:
        labor = resolve_work_order_labor(db, cid, work_order_id)
        if labor is not None:
            uninvoiced_revenue += money(labor["labor_total"])

    invoiced_revenue = money(invoiced_revenue)
    return {
        "revenue": money(invoiced_revenue + uninvoiced_revenue),
        "work_order_count": invoiced_count + len(uninvoiced_ids),
        "invoiced_revenue": invoiced_revenue,
        "invoiced_work_order_count": invoiced_count,
        "uninvoiced_revenue": money(uninvoiced_revenue),
        "uninvoiced_work_order_count": len(uninvoiced_ids),
    }


def _tracked_overhead(db: Session, cid: int, start: date, end: date) -> tuple[Decimal, int]:
    row = db.execute(
        text(
            """
            SELECT COALESCE(SUM(amount),0) AS overhead,COUNT(*) AS entry_count
            FROM income_expenses
            WHERE company_id=:cid
              AND LOWER(COALESCE(txn_type,''))='expense'
              AND txn_date>=:start AND txn_date<=:end
            """
        ),
        {"cid": cid, "start": start.isoformat(), "end": end.isoformat()},
    ).mappings().one()
    return money(row["overhead"]), int(row["entry_count"] or 0)


@router.get("/absorption-rate")
def absorption_rate(
    request: Request,
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    overhead: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    start, end = _parse_period(date_from, date_to)
    calculated_at = datetime.now(UTC).isoformat()
    # work_orders.completed_at is a timestamp; widen the ISO day bounds so the
    # last day of the period is fully included on both engines.
    start_ts = datetime.combine(start, time.min)
    end_ts = datetime.combine(end, time.max)

    counter_revenue, counter_cogs = _counter_parts(db, cid, start, end)
    wo_parts_revenue, wo_parts_cogs = _service_parts(db, cid, start_ts, end_ts)
    labor = _service_labor(db, cid, start_ts, end_ts)
    labor_revenue = labor["revenue"]
    work_order_count = labor["work_order_count"]

    parts_revenue = money(counter_revenue + wo_parts_revenue)
    parts_cogs = money(counter_cogs + wo_parts_cogs)
    parts_gross_profit = money(parts_revenue - parts_cogs)

    # No technician cost rate exists anywhere in the schema. Subtracting 0.00 does
    # not make the result a gross profit — it makes it the theoretical maximum the
    # service department could have earned if labour were free. Every derived
    # figure below therefore travels as an explicit UPPER BOUND, so the KPI is
    # never read as an exact profit or an exact rate.
    labor_cost_known = False
    labor_cost = ZERO_MONEY
    service_gross_profit_max = money(labor_revenue - labor_cost)
    total_gross_profit_max = money(parts_gross_profit + service_gross_profit_max)

    supplied = _parse_overhead(overhead)
    if supplied is not None:
        overhead_amount = supplied
        overhead_source = "user_supplied"
        overhead_entry_count = 0
    else:
        overhead_amount, overhead_entry_count = _tracked_overhead(db, cid, start, end)
        overhead_source = "income_expenses"

    if overhead_amount > ZERO_MONEY:
        rate_max = (total_gross_profit_max / overhead_amount * HUNDRED).quantize(PERCENT_QUANTUM)
        rate_note = (
            None
            if labor_cost_known
            else (
                "Bu oran bir ÜST SINIRDIR: servis işçilik maliyeti ERP'de takip "
                "edilmediği için sıfır kabul edildi. Gerçek emilim oranı bu "
                "değerden düşüktür."
            )
        )
    else:
        rate_max = None
        rate_note = (
            "Genel gider tanımlı değil; emilim oranı hesaplanamaz."
            if overhead_source == "income_expenses"
            else "Genel gider sıfır girildi; emilim oranı hesaplanamaz."
        )

    return {
        "calculated_at": calculated_at,
        "period": {"date_from": start.isoformat(), "date_to": end.isoformat()},
        "parts": {
            # Parts is the one exact half: revenue is the discounted header
            # subtotal and COGS is a real stored cost, so no bound is implied.
            "revenue": parts_revenue,
            "cogs": parts_cogs,
            "gross_profit": parts_gross_profit,
            "counter_revenue": counter_revenue,
            "work_order_revenue": wo_parts_revenue,
            "revenue_source": "orders.subtotal (belge indirimi düşülmüş) + work_order_parts",
            "cogs_source": "products.purchase_price",
            "cogs_method": "current_cost_approximation",
        },
        "service": {
            "labor_revenue": labor_revenue,
            "labor_cost": labor_cost,
            "labor_cost_known": labor_cost_known,
            # Upper bound, not a gross profit: labour cost is unknown, so the
            # true figure is this minus whatever the technicians actually cost.
            "gross_profit_upper_bound": service_gross_profit_max,
            "work_order_count": work_order_count,
            "labor_cost_source": "not_tracked",
            # Which source produced labor_revenue, split by group, so the figure
            # can be reconciled against the invoices it came from.
            "labor_revenue_source": (
                "invoice_items.LABOR (faturalı) + canonical labor resolver (faturasız)"
            ),
            "invoiced_labor_revenue": labor["invoiced_revenue"],
            "invoiced_work_order_count": labor["invoiced_work_order_count"],
            "uninvoiced_labor_revenue": labor["uninvoiced_revenue"],
            "uninvoiced_work_order_count": labor["uninvoiced_work_order_count"],
        },
        "total_gross_profit_upper_bound": total_gross_profit_max,
        "overhead": {
            "amount": overhead_amount,
            "source": overhead_source,
            "entry_count": overhead_entry_count,
        },
        "absorption_rate_upper_bound": rate_max,
        # True while any input is a bound rather than a measurement. Consumers
        # must not present these figures as an exact rate while this is set.
        "is_upper_bound": not labor_cost_known,
        "rate_note": rate_note,
    }
