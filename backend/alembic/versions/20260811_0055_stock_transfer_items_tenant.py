"""Depo transferi satırlarını kiracıya bağla: company_id + BİLEŞİK yabancı anahtar.

``stock_transfer_items`` bugün ``company_id`` taşımıyor ve ``stock_transfers``
ile arasında yabancı anahtar yok. Satırın izolasyonu tamamen "her sorgu
ebeveyni filtrelemeyi hatırlasın" konvansiyonuna dayanıyor. Konvansiyon 220
uçta tuttu; 400'de tutmaz. Bu migration bağı VERİTABANINA yazdırıyor.

Sıra bilinçli ve zorunlu:

1. **Ebeveyne ``UNIQUE(company_id, id)``.** Bileşik yabancı anahtarın hedefi
   tekil olmak zorunda; ``id`` tek başına zaten tekil ama çocuk aynı referansta
   ``company_id``'yi de adlandırabilsin diye çift gerekiyor.
2. **Çocuğa ``company_id`` sütunu (önce NULL kabul eden).** Var olan satırlar
   henüz değeri taşımıyor.
3. **Ebeveynden backfill.** Değer uydurulmuyor; her satır kendi transferinin
   şirketini alıyor. Boş tablo varsayılmıyor.
4. **``NOT NULL``.** Ancak backfill'den SONRA; önce yapılsaydı mevcut satırı
   olan her kurulumda migration patlardı.
5. **Bileşik yabancı anahtar.** En son, çünkü hedefi 1. adım kuruyor.

SQLite notu: 1. ve 4-5. adımlar mevcut tabloya kısıt eklediği için
``batch_alter_table`` tabloyu YENİDEN KURAR. Yeniden kurulum mevcut indeksleri
ve birincil anahtarı taşımak zorunda; ``test_stock_transfer_item_tenancy.py``
bunu ölçüyor (kapı 4). Ebeveyn 1. adımda yeniden kurulurken çocuk yabancı
anahtarı HENÜZ YOK — sıra bu yüzden de önemli: FK önce eklenseydi ebeveynin
yeniden kurulumu ``PRAGMA foreign_keys=ON`` altında referansı kırardı.

PostgreSQL'de her iki adım da ``ALTER TABLE ... ADD CONSTRAINT``; tablo yeniden
kurulmadığı için ``ck_stock_transfers_source_not_target`` CHECK'i doğal olarak
yerinde kalıyor (SQLite'ta bu CHECK zaten hiç kurulmuyor, bkz. 0020).

Revision ID: 20260811_0055
Revises: 20260811_0054

NOTE (tek head koordinasyonu): ilk yazımda develop'ın head'i 20260808_0053'tü
ve paralel dalda 20260811_0054 (company_id server default kaldırma) inceleme
altındaydı. 0054 develop'a indi; ``down_revision`` ona yeniden işaret ediyor.
İkisi de 0053'e zincirlenmiş kalsaydı Alembic İKİ HEAD görür ve
``upgrade head`` "Multiple head revisions" ile durur — CI'da tam olarak bu
oldu, çünkü pull_request koşusu dalı GÜNCEL base ile birleştirip test ediyor.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260811_0055"
down_revision = "20260811_0054"
branch_labels = None
depends_on = None


PARENT = "stock_transfers"
CHILD = "stock_transfer_items"
PARENT_UNIQUE = "uq_stock_transfers_company_id"
CHILD_FK = "fk_stock_transfer_items_transfer_same_company"
CHILD_INDEX = "ix_stock_transfer_items_company_transfer"


def _index_names(inspector, table: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table)}


def _unique_names(inspector, table: str) -> set[str]:
    return {item["name"] for item in inspector.get_unique_constraints(table)}


def _fk_names(inspector, table: str) -> set[str]:
    return {item["name"] for item in inspector.get_foreign_keys(table)}


def _column_names(inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Taze veritabanları bu tabloları başlangıçta inventory.metadata'dan kurar
    # (bkz. 0020); kısıtlar zaten model tanımında. Sertleştirilecek bir şey yok.
    if not inspector.has_table(PARENT) or not inspector.has_table(CHILD):
        return

    # --- 1. Ebeveyn: bileşik yabancı anahtarın hedefi ------------------------
    if PARENT_UNIQUE not in _unique_names(inspector, PARENT):
        with op.batch_alter_table(PARENT) as batch_op:
            batch_op.create_unique_constraint(PARENT_UNIQUE, ["company_id", "id"])

    # --- 2. Çocuk: sütun, önce NULL kabul eden ------------------------------
    inspector = sa.inspect(bind)
    if "company_id" not in _column_names(inspector, CHILD):
        op.add_column(CHILD, sa.Column("company_id", sa.Integer(), nullable=True))

    # --- 3. Ebeveynden backfill: değer uydurulmuyor -------------------------
    # Sahipsiz satır (ebeveyni silinmiş) varsa NOT NULL adımı patlar; bu
    # istenen davranış — sessizce bir şirkete yazmaktansa migration durmalı.
    op.execute(
        sa.text(
            f"UPDATE {CHILD} SET company_id = ("
            f"SELECT p.company_id FROM {PARENT} p WHERE p.id = {CHILD}.transfer_id"
            ") WHERE company_id IS NULL"
        )
    )
    orphans = bind.execute(
        sa.text(f"SELECT COUNT(*) FROM {CHILD} WHERE company_id IS NULL")
    ).scalar_one()
    if orphans:
        raise RuntimeError(
            f"{CHILD}: {orphans} satırın ebeveyni yok, company_id türetilemedi. "
            "Migration durdu — sahipsiz satır sessizce bir şirkete yazılmaz."
        )

    # --- 4+5. NOT NULL ve bileşik yabancı anahtar ---------------------------
    inspector = sa.inspect(bind)
    with op.batch_alter_table(CHILD) as batch_op:
        batch_op.alter_column(
            "company_id", existing_type=sa.Integer(), nullable=False
        )
        if CHILD_FK not in _fk_names(inspector, CHILD):
            batch_op.create_foreign_key(
                CHILD_FK,
                PARENT,
                ["company_id", "transfer_id"],
                ["company_id", "id"],
            )

    inspector = sa.inspect(bind)
    if CHILD_INDEX not in _index_names(inspector, CHILD):
        op.create_index(CHILD_INDEX, CHILD, ["company_id", "transfer_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(CHILD) or not inspector.has_table(PARENT):
        return

    if CHILD_INDEX in _index_names(inspector, CHILD):
        op.drop_index(CHILD_INDEX, table_name=CHILD)

    with op.batch_alter_table(CHILD) as batch_op:
        if CHILD_FK in _fk_names(inspector, CHILD):
            batch_op.drop_constraint(CHILD_FK, type_="foreignkey")
        batch_op.drop_column("company_id")

    inspector = sa.inspect(bind)
    if PARENT_UNIQUE in _unique_names(inspector, PARENT):
        with op.batch_alter_table(PARENT) as batch_op:
            batch_op.drop_constraint(PARENT_UNIQUE, type_="unique")
