from app.routers.outputs import _safe_excel_cell
from app.routers.transactions import _normalized_date_sql


def test_excel_formula_injection_is_neutralized():
    assert _safe_excel_cell("=1+1") == "'=1+1"
    assert _safe_excel_cell("+SUM(A1:A2)") == "'+SUM(A1:A2)"
    assert _safe_excel_cell("normal") == "normal"
    assert _safe_excel_cell(12.5) == 12.5


def test_normalized_date_sql_is_cross_dialect():
    sql = _normalized_date_sql("h.order_date")
    forbidden = ("instr(", "TO_DATE", "~ '^", "LEFT(")
    assert not any(token in sql for token in forbidden)
    assert "substr(" in sql
    assert "LIKE '__.__.____'" in sql
