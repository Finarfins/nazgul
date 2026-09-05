from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session


def validate_payment_reference(
    db: Session,
    company_id: int,
    entity_type: str,
    entity_id: int,
    reference_type: str,
    reference_id: int,
) -> None:
    config = {
        ("customer", "order"): ("orders", "customer_id"),
        ("customer", "late_fee"): ("receivable_charge_documents", "customer_id"),
        ("supplier", "purchase"): ("purchases", "supplier_id"),
        # D2: avans ve müstahsil makbuzu ödemesi. İKİSİ DE `supplier_id`
        # taşıyan tablolara bakar, yani doğrulama kalıbı DEĞİŞMEDİ — kapalı
        # kümeye YALNIZ iki giriş eklendi.
        ("supplier", "supplier_advance"): ("supplier_advances", "supplier_id"),
        ("supplier", "producer_receipt"): ("producer_receipts", "supplier_id"),
    }.get((entity_type, reference_type))
    if config is None:
        raise HTTPException(422, "Ödeme ile belge türü uyuşmuyor")
    table, entity_column = config
    matches = db.execute(
        text(
            f"SELECT 1 FROM {table} "
            f"WHERE id=:reference_id AND company_id=:cid AND {entity_column}=:entity_id"
        ),
        {
            "reference_id": reference_id,
            "cid": company_id,
            "entity_id": entity_id,
        },
    ).first()
    if not matches:
        raise HTTPException(409, "Ödeme belgesi ve cari hesabı uyuşmuyor")


def validate_return_reference(
    db: Session,
    company_id: int,
    return_type: str,
    entity_id: int,
    source_type: str | None,
    source_id: int | None,
) -> None:
    if source_type is None and source_id is None:
        return
    expected = {
        "sale_return": ("order", "customer"),
        "purchase_return": ("purchase", "supplier"),
    }.get(return_type)
    if expected is None or source_type != expected[0] or source_id is None:
        raise HTTPException(422, "İade ile kaynak belge türü uyuşmuyor")
    validate_payment_reference(
        db,
        company_id,
        expected[1],
        entity_id,
        source_type,
        source_id,
    )
