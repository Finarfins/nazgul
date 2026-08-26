from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import sessionmaker

from app.core_schema import document_sequences, initialize_core_schema, orders
from app.document_engine import next_document_no


def _engine(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'sequence.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
        pool_pre_ping=True,
    )
    initialize_core_schema(engine)
    return engine


def test_document_numbers_do_not_reuse_deleted_primary_keys(tmp_path: Path):
    engine = _engine(tmp_path)
    Session = sessionmaker(bind=engine)
    with Session.begin() as db:
        first = next_document_no(db, "orders", 1, "SAT")
        db.execute(
            insert(orders).values(
                customer_id=1,
                order_date="2026-07-13",
                document_no=first,
                company_id=1,
            )
        )
    with Session.begin() as db:
        second = next_document_no(db, "orders", 1, "SAT")
    assert first == "SAT-000001"
    assert second == "SAT-000002"


def test_document_sequences_are_isolated_by_company_and_prefix(tmp_path: Path):
    engine = _engine(tmp_path)
    Session = sessionmaker(bind=engine)
    with Session.begin() as db:
        assert next_document_no(db, "orders", 1, "SAT") == "SAT-000001"
        assert next_document_no(db, "orders", 2, "SAT") == "SAT-000001"
        assert next_document_no(db, "orders", 1, "WEB") == "WEB-000001"


def test_concurrent_document_numbers_are_unique(tmp_path: Path):
    engine = _engine(tmp_path)
    Session = sessionmaker(bind=engine)

    def allocate(_index: int) -> str:
        with Session.begin() as db:
            return next_document_no(db, "orders", 1, "SAT")

    with ThreadPoolExecutor(max_workers=8) as executor:
        numbers = list(executor.map(allocate, range(24)))

    assert len(numbers) == 24
    assert len(set(numbers)) == 24
    assert sorted(numbers) == [f"SAT-{value:06d}" for value in range(1, 25)]

    with Session() as db:
        row = db.execute(
            select(document_sequences.c.current_value).where(
                document_sequences.c.company_id == 1,
                document_sequences.c.sequence_key == "orders:SAT",
            )
        ).scalar_one()
    assert row == 24


def test_rejects_dynamic_table_or_unsafe_prefix(tmp_path: Path):
    engine = _engine(tmp_path)
    Session = sessionmaker(bind=engine)
    with Session.begin() as db:
        for table, prefix in (("orders; DROP TABLE orders", "SAT"), ("orders", "SAT';--")):
            try:
                next_document_no(db, table, 1, prefix)
            except ValueError:
                pass
            else:
                raise AssertionError("Unsafe document identifier was accepted")
