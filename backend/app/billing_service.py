from __future__ import annotations

from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from .money import HUNDRED, compute_line, money, percentage, quantity

ZERO = Decimal("0")


def _resolve_labor(db: Session, cid: int, work_order_id: int, row) -> dict:
    """Pick the work order's labor source — header OR approved lines, never both.

    This is the single decision point for labor money, shared by the billing
    preview and invoice generation so they can never disagree:

    * no APPROVED labor line -> the v1 header labor (``actual_hours ×
      labor_rate``), byte-identical to the pre-FAZ-2 behaviour;
    * at least one APPROVED line -> ONLY the lines (Σ hours × frozen
      hourly_rate). The header hours are deliberately excluded, because the
      approved lines are the itemised restatement of that same work — adding
      both would bill the customer twice for it.

    DRAFT and VOID lines never contribute: approval is exactly what promotes a
    line into the billing source.

    ``entries`` carries one billable unit per invoice item, so a rate that
    differs per technician stays visible on the invoice instead of being
    flattened into a fake blended rate.
    """
    lines = db.execute(
        text(
            """SELECT id,hours,hourly_rate FROM work_order_labor_lines
            WHERE work_order_id=:id AND company_id=:cid AND line_status='APPROVED'
            ORDER BY id"""
        ),
        {"id": work_order_id, "cid": cid},
    ).mappings().all()

    header_hours = quantity(row["actual_hours"])
    header_rate = money(row["labor_rate"])
    if not lines:
        return {
            "total_hours": header_hours,
            "labor_rate": header_rate,
            "labor_total": money(header_hours * header_rate),
            "source": "header",
            "line_ids": [],
            "entries": [{"hours": header_hours, "hourly_rate": header_rate}],
        }

    entries = []
    total_hours = ZERO
    labor_total = ZERO
    for line in lines:
        line_hours = quantity(line["hours"])
        line_rate = money(line["hourly_rate"])
        # Rounded per line, like the parts lines, so the stored line amount and
        # the invoice item built from it are identical.
        line_total = money(line_hours * line_rate)
        entries.append(
            {"line_id": int(line["id"]), "hours": line_hours, "hourly_rate": line_rate}
        )
        total_hours += line_hours
        labor_total += line_total
    return {
        "total_hours": quantity(total_hours),
        # Reported for reference only: with per-line rates there is no single
        # rate, so labor_total (not hours × rate) is authoritative here.
        "labor_rate": header_rate,
        "labor_total": money(labor_total),
        "source": "lines",
        "line_ids": [entry["line_id"] for entry in entries],
        "entries": entries,
    }


def resolve_work_order_labor(db: Session, cid: int, work_order_id: int) -> dict | None:
    """Public entry point to the labor resolution above, for callers that have
    only a work order id (the absorption report).

    Additive wrapper: it fetches the header fields ``_resolve_labor`` needs and
    delegates, so a report can never drift from what the billing preview and the
    issued invoice would use. Returns ``None`` when the work order is not in
    this tenant.
    """
    row = db.execute(
        text(
            "SELECT actual_hours,labor_rate FROM work_orders "
            "WHERE id=:id AND company_id=:cid"
        ),
        {"id": work_order_id, "cid": cid},
    ).mappings().first()
    if not row:
        return None
    return _resolve_labor(db, cid, work_order_id, row)


def build_invoice_summary(db: Session, cid: int, work_order_id: int) -> dict:
    """Compute the billing summary for a work order.

    Neutral service/domain logic: it receives an already-resolved company id and
    never touches the HTTP layer. Both the billing router and the invoice service
    call this, so the reusable financial calculation lives in one place and the
    service no longer has to import a router.
    """
    lock = " FOR SHARE OF wo" if db.get_bind().dialect.name == "postgresql" else ""
    row = db.execute(text(f"""SELECT wo.id,wo.work_order_no,wo.status,wo.opened_at,
        wo.completed_at,wo.delivered_at,wo.actual_hours,wo.labor_rate,
        wo.warranty_type,wo.warranty_percent,c.id customer_id,c.name customer_name,
        c.tax_number customer_tax_number,c.address customer_address,
        m.id machine_id,m.brand,m.model,m.serial_number
        FROM work_orders wo
        JOIN customers c ON c.id=wo.customer_id AND c.company_id=wo.company_id
        JOIN machines m ON m.id=wo.machine_id AND m.company_id=wo.company_id
        WHERE wo.id=:id AND wo.company_id=:cid{lock}"""), {"id": work_order_id, "cid": cid}).mappings().first()
    if not row:
        raise HTTPException(404, "İş emri bulunamadı")
    if row["status"] not in {"COMPLETED", "DELIVERED"}:
        raise HTTPException(409, "İş emri faturalandırmaya hazır değil.")

    parts = db.execute(text("""SELECT quantity,unit_price,discount,tax_rate
        FROM work_order_parts WHERE work_order_id=:id AND company_id=:cid
          AND line_status <> 'RETURNED' ORDER BY id"""),
        {"id": work_order_id, "cid": cid}).mappings().all()
    before_tax = tax = discount = ZERO
    for part in parts:
        line = compute_line(
            part["quantity"], part["unit_price"], part["discount"], part["tax_rate"]
        )
        before_tax += line.taxable
        tax += line.tax
        discount += line.discount
    before_tax, tax, discount = money(before_tax), money(tax), money(discount)
    parts_total = money(before_tax + tax)
    labor = _resolve_labor(db, cid, work_order_id, row)
    total_hours = labor["total_hours"]
    labor_rate = labor["labor_rate"]
    labor_total = labor["labor_total"]
    grand_total = money(labor_total + parts_total)
    warranty_type = str(row["warranty_type"])
    coverage = percentage(row["warranty_percent"])
    warranty_amount = money(grand_total * coverage / HUNDRED)
    customer_amount = money(grand_total - warranty_amount)

    return {
        "customer": {"id": row["customer_id"], "name": row["customer_name"],
                     "tax_number": row["customer_tax_number"], "address": row["customer_address"]},
        "machine": {"id": row["machine_id"], "brand": row["brand"], "model": row["model"], "serial_number": row["serial_number"]},
        "work_order": {"id": row["id"], "work_order_no": row["work_order_no"], "status": row["status"],
                       "opened_at": str(row["opened_at"]), "completed_at": str(row["completed_at"]) if row["completed_at"] else None,
                       "delivered_at": str(row["delivered_at"]) if row["delivered_at"] else None},
        "labor": {"total_hours": total_hours, "labor_rate": labor_rate, "labor_total": labor_total,
                  "source": labor["source"], "line_ids": labor["line_ids"], "entries": labor["entries"]},
        "parts": {"parts_total": parts_total, "parts_before_tax": before_tax, "parts_tax": tax, "parts_discount": discount},
        "totals": {"labor": labor_total, "parts": parts_total, "tax": tax, "discount": discount, "grand_total": grand_total,
                   "labor_source": labor["source"], "labor_line_ids": labor["line_ids"]},
        "warranty": {"type": warranty_type, "coverage_percent": coverage, "customer_amount": customer_amount,
                     "warranty_amount": warranty_amount, "company_cost": warranty_amount},
        "taxes": tax,
    }
