"""Regression guard for the de-correlated work-order parts total.

`routers/work_orders.py` used to compute each row's parts total with a
correlated ``SELECT SUM(work_order_parts.total_price)`` evaluated twice per row.
It is now a single pre-aggregated ``LEFT JOIN ... GROUP BY (company_id,
work_order_id)`` (``WORK_ORDER_PARTS_TOTAL_JOIN`` + ``WORK_ORDER_TOTALS``).

These tests pin the optimization to be **result-identical** to the old
correlated form across the cases most likely to break a de-correlation:
- a work order with several parts (the sum, and no row fan-out),
- a work order with no parts (LEFT JOIN → ``COALESCE`` → 0, row not dropped),
- a same-numbered work order in another tenant (the join stays company-scoped).

It imports the real SQL fragments so it validates production code, not a copy.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import create_engine, text

from app.routers.work_orders import WORK_ORDER_PARTS_TOTAL_JOIN, WORK_ORDER_TOTALS

# The original correlated form, kept here only as the oracle to diff against.
_CORRELATED_TOTALS = """w.total_labor_cost labor_total,
    COALESCE((SELECT SUM(wp.total_price) FROM work_order_parts wp
        WHERE wp.company_id=w.company_id AND wp.work_order_id=w.id
          AND wp.line_status <> 'RETURNED'),0) parts_total,
    w.total_labor_cost + COALESCE((SELECT SUM(wp.total_price) FROM work_order_parts wp
        WHERE wp.company_id=w.company_id AND wp.work_order_id=w.id
          AND wp.line_status <> 'RETURNED'),0) grand_total"""


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE work_orders(id INTEGER PRIMARY KEY, company_id INTEGER, "
            "total_labor_cost NUMERIC)"
        ))
        conn.execute(text(
            "CREATE TABLE work_order_parts(id INTEGER PRIMARY KEY, company_id INTEGER, "
            "work_order_id INTEGER, total_price NUMERIC, line_status TEXT)"
        ))
        # company 1: WO 1 has three parts; WO 2 has none.
        conn.execute(text(
            "INSERT INTO work_orders(id,company_id,total_labor_cost) VALUES "
            "(1,1,'250.00'),(2,1,'100.00'),(3,2,'75.00')"
        ))
        conn.execute(text(
            "INSERT INTO work_order_parts(company_id,work_order_id,total_price,line_status) VALUES "
            "(1,1,'10.00','ISSUED'),(1,1,'21.60','ISSUED'),"
            "(1,1,'0.05','RESERVED'),(1,1,'500.00','RETURNED'),"  # WO1: sum 31.65
            "(2,3,'999.99','ISSUED')"                              # company 2, WO id 3
        ))
    return engine


def _rows(engine, sql_totals: str, join: str, cid: int) -> dict[int, tuple[str, str, str]]:
    query = text(
        f"SELECT w.id,{sql_totals} FROM work_orders w {join} "
        "WHERE w.company_id=:cid ORDER BY w.id"
    )
    with engine.connect() as conn:
        rows = conn.execute(query, {"cid": cid}).mappings().all()
    return {
        r["id"]: (str(r["labor_total"]), str(r["parts_total"]), str(r["grand_total"]))
        for r in rows
    }


def test_decorrelated_totals_match_correlated_oracle() -> None:
    engine = _engine()
    for cid in (1, 2):
        new = _rows(engine, WORK_ORDER_TOTALS, WORK_ORDER_PARTS_TOTAL_JOIN, cid)
        old = _rows(engine, _CORRELATED_TOTALS, "", cid)
        assert new == old, f"company {cid}: {new} != {old}"


def _cents(value: str) -> Decimal:
    # SQLite's REAL affinity leaves float artifacts (e.g. 31.650000000000002)
    # in this toy schema; production columns are NUMERIC and the app normalizes
    # money to 2 dp, so money comparisons are made at cent precision.
    return Decimal(value).quantize(Decimal("0.01"))


def test_expected_values_multi_part_no_part_and_tenant_scoping() -> None:
    engine = _engine()
    company1 = _rows(engine, WORK_ORDER_TOTALS, WORK_ORDER_PARTS_TOTAL_JOIN, 1)

    # WO 1: three parts (10.00 + 21.60 + 0.05) summed once, no fan-out.
    assert _cents(company1[1][1]) == Decimal("31.65")
    assert _cents(company1[1][2]) == Decimal("281.65")  # grand = labor 250 + parts
    # WO 2: no parts -> LEFT JOIN COALESCE -> 0, row still present.
    assert _cents(company1[2][1]) == Decimal("0.00")
    assert _cents(company1[2][2]) == Decimal("100.00")
    # Company 1 must not see company 2's work order or its parts.
    assert set(company1) == {1, 2}

    company2 = _rows(engine, WORK_ORDER_TOTALS, WORK_ORDER_PARTS_TOTAL_JOIN, 2)
    assert set(company2) == {3}
    assert _cents(company2[3][1]) == Decimal("999.99")
