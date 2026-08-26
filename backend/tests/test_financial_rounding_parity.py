from decimal import Decimal

from app.money import allocate_vat_inclusive_document_discount, money
from app.routers.transactions import _totals as transaction_totals
from app.routers.workflow import _totals as workflow_totals
from app.schemas import TransactionCreate, WorkflowDocumentCreate


class _Mappings:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return _Mappings(self._rows)


class _ProductDb:
    def execute(self, _statement, params):
        return _Result([{"id": product_id, "name": f"Ürün {product_id}"}
                        for product_id in params["product_ids"]])


ITEMS = [
    {"product_id": 1, "quantity": "1", "unit_price": "0.05", "vat_rate": 20,
     "discount_percent": "0"},
    {"product_id": 2, "quantity": "1", "unit_price": "0.10", "vat_rate": 10,
     "discount_percent": "0"},
]


def test_largest_remainder_closes_one_cent_document_discount():
    lines = allocate_vat_inclusive_document_discount(
        [Decimal("0.05"), Decimal("0.10")], [20, 10], Decimal("0.01")
    )
    assert [line.document_discount for line in lines] == [Decimal("0.00"), Decimal("0.01")]
    assert money(sum((line.total for line in lines), Decimal("0"))) == Decimal("0.14")
    assert all(line.subtotal + line.tax == line.total for line in lines)


def test_transaction_and_workflow_preview_share_cent_exact_totals():
    db = _ProductDb()
    transaction = TransactionCreate(
        entity_id=1, transaction_date="2026-08-01", discount_percent="6.6667", items=ITEMS
    )
    workflow = WorkflowDocumentCreate(
        entity_id=1, document_date="2026-08-01", discount_percent="6.6667", items=ITEMS
    )

    transaction_result = transaction_totals(db, transaction, 1)
    workflow_result = workflow_totals(db, workflow, 1)
    transaction_rows, transaction_subtotal, transaction_tax = transaction_result[:3]
    transaction_final = transaction_result[5]
    workflow_rows, workflow_subtotal, workflow_tax, workflow_final = workflow_result[:4]

    assert workflow_final == transaction_final == Decimal("0.14")
    assert workflow_subtotal == transaction_subtotal
    assert workflow_tax == transaction_tax
    assert [row[2:6] for row in workflow_rows] == [row[2:6] for row in transaction_rows]
    assert money(sum((row[4] for row in workflow_rows), Decimal("0"))) == workflow_final
