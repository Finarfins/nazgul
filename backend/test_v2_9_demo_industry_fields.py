from pathlib import Path
import sqlite3

from seed_demo_data import build_demo


def test_demo_seed_populates_industry_product_fields(tmp_path: Path):
    db_path = tmp_path / "demo_industry.db"
    counts = build_demo(f"sqlite:///{db_path.as_posix()}")
    assert counts["products"] == 75

    with sqlite3.connect(db_path) as conn:
        populated = conn.execute(
            """SELECT COUNT(*) FROM products
               WHERE oem_number IS NOT NULL AND oem_number <> ''
                 AND brand IS NOT NULL AND brand <> ''
                 AND compatible_models IS NOT NULL AND compatible_models <> ''
                 AND technical_notes IS NOT NULL AND technical_notes <> ''"""
        ).fetchone()[0]
    assert populated == 75
