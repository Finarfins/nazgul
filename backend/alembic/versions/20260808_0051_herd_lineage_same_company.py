"""Hayvancılık V1: anne/baba soy bağını kiracı içinde zorla.

Revision ID: 20260808_0051
Revises: 20260808_0050

0049 soy sütunlarını nullable oluşturdu ancak açıklamasındaki bileşik yabancı
anahtarları eklemedi. Bu takip migration'ı uygulanmış 0049'u değiştirmek yerine
iki bağı mevcut kurulumlara da taşır. SQLite ALTER CONSTRAINT desteklemediği
için tabloyu Alembic batch işlemiyle yeniden kurar; PostgreSQL kısıtları
yerinde ekler.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260808_0051"
down_revision = "20260808_0050"
branch_labels = None
depends_on = None

MOTHER_FK = "fk_animals_mother_same_company"
FATHER_FK = "fk_animals_father_same_company"


def _existing_foreign_keys(bind) -> set[str]:
    inspector = sa.inspect(bind)
    if "animals" not in inspector.get_table_names():
        return set()
    return {
        fk["name"] for fk in inspector.get_foreign_keys("animals") if fk.get("name")
    }


def upgrade() -> None:
    bind = op.get_bind()
    existing = _existing_foreign_keys(bind)
    missing = [
        (MOTHER_FK, ["company_id", "mother_id"]),
        (FATHER_FK, ["company_id", "father_id"]),
    ]
    missing = [(name, columns) for name, columns in missing if name not in existing]
    if not missing:
        return

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("animals", recreate="always") as batch_op:
            for name, columns in missing:
                batch_op.create_foreign_key(
                    name, "animals", columns, ["company_id", "id"]
                )
        return

    for name, columns in missing:
        op.create_foreign_key(
            name, "animals", "animals", columns, ["company_id", "id"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing = _existing_foreign_keys(bind)
    removable = [name for name in (FATHER_FK, MOTHER_FK) if name in existing]
    if not removable:
        return

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("animals", recreate="always") as batch_op:
            for name in removable:
                batch_op.drop_constraint(name, type_="foreignkey")
        return

    for name in removable:
        op.drop_constraint(name, "animals", type_="foreignkey")
