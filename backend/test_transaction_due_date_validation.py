from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import TransactionCreate


def test_invalid_due_date_has_understandable_turkish_message() -> None:
    with pytest.raises(ValidationError) as error:
        TransactionCreate.model_validate(
            {
                "entity_id": 1,
                "transaction_date": "2026-07-28",
                "due_date": "gelecek hafta",
                "items": [
                    {
                        "product_id": 1,
                        "quantity": 1,
                        "unit_price": 1,
                        "vat_rate": 0,
                    }
                ],
            }
        )

    assert (
        "Vade tarihi geçersiz. Beklenen biçim: YYYY-AA-GG veya GG.AA.YYYY"
        in str(error.value)
    )
