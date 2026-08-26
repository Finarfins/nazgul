from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core_schema import metadata
from app.movement_references import validate_payment_reference, validate_return_reference


def test_movement_references_require_same_tenant_and_entity(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'references.db').as_posix()}")
    metadata.create_all(engine)
    with Session(engine) as db:
        first_customer = int(
            db.execute(
                text(
                    "INSERT INTO customers(name,company_id) VALUES('Bir',1) RETURNING id"
                )
            ).scalar_one()
        )
        second_customer = int(
            db.execute(
                text(
                    "INSERT INTO customers(name,company_id) VALUES('İki',1) RETURNING id"
                )
            ).scalar_one()
        )
        order_id = int(
            db.execute(
                text(
                    """INSERT INTO orders(
                    customer_id,order_date,final_total,company_id
                    ) VALUES(:customer_id,'2026-01-01',100,1) RETURNING id"""
                ),
                {"customer_id": first_customer},
            ).scalar_one()
        )
        db.commit()

        validate_payment_reference(
            db, 1, "customer", first_customer, "order", order_id
        )
        with pytest.raises(HTTPException) as wrong_customer:
            validate_payment_reference(
                db, 1, "customer", second_customer, "order", order_id
            )
        assert wrong_customer.value.status_code == 409
        with pytest.raises(HTTPException) as wrong_tenant:
            validate_payment_reference(
                db, 2, "customer", first_customer, "order", order_id
            )
        assert wrong_tenant.value.status_code == 409
        with pytest.raises(HTTPException) as wrong_type:
            validate_payment_reference(
                db, 1, "supplier", first_customer, "order", order_id
            )
        assert wrong_type.value.status_code == 422
        validate_return_reference(
            db, 1, "sale_return", first_customer, "order", order_id
        )
        with pytest.raises(HTTPException) as wrong_return_customer:
            validate_return_reference(
                db, 1, "sale_return", second_customer, "order", order_id
            )
        assert wrong_return_customer.value.status_code == 409
        with pytest.raises(HTTPException) as wrong_return_tenant:
            validate_return_reference(
                db, 2, "sale_return", first_customer, "order", order_id
            )
        assert wrong_return_tenant.value.status_code == 409

        invalid_pairs = (
            ("sale_return", "purchase", order_id),
            ("purchase_return", "order", order_id),
            ("sale_return", "order", None),
            ("sale_return", None, order_id),
        )
        for return_type, source_type, source_id in invalid_pairs:
            with pytest.raises(HTTPException) as invalid:
                validate_return_reference(
                    db,
                    1,
                    return_type,
                    first_customer,
                    source_type,
                    source_id,
                )
            assert invalid.value.status_code == 422
