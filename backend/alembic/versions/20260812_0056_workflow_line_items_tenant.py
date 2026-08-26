"""Sipariş ve irsaliye SATIRLARINI kiracıya bağla: company_id + BİLEŞİK FK.

Satır tabloları sözleşmesinin 2. dilimi. Desen 3. dilimde (20260811_0055,
``stock_transfer_items``) incelendi ve onaylandı; burada iki çocuk-ebeveyn
çiftine aynen uygulanıyor:

* ``sales_order_items``  -> ``sales_orders``   (FK sütunu ``sales_order_id``)
* ``delivery_note_items`` -> ``delivery_notes`` (FK sütunu ``delivery_note_id``)

Sıra çift başına aynı ve zorunlu:

1. **Ebeveyne ``UNIQUE(company_id, id)``** — bileşik FK'nın hedefi tekil olmalı.
2. **Çocuğa ``company_id``** (önce NULL kabul eden).
3. **Ebeveynden backfill** — değer uydurulmuyor, boş tablo varsayılmıyor.
4. **``NOT NULL``** — backfill'den SONRA.
5. **Bileşik FK** — en son; hedefini 1. adım kuruyor.

Sahipsiz satır (ebeveyni silinmiş) varsa migration DURUR. Sessizce bir
şirkete yazmak, bu dilimin ortadan kaldırmaya çalıştığı sessizliğin ta
kendisidir.

SQLite'ta 1. ve 4-5. adımlar tabloyu YENİDEN KURAR. Yeniden kurulumun neyi
koruduğu ``tests/tenant_schema_snapshot.py`` ile SAYILARAK ölçülüyor —
indeksler SÜTUN DEMETİYLE karşılaştırılıyor, adla değil. 3. dilimde ad
karşılaştırması yapılıyordu ve aynı adla yeniden kurulan bir indeksin sütun
sırası değişse fark edilmezdi; o açık bu dilimde kapatıldı ve
``stock_transfer_items`` da geriye dönük aynı kapıya bağlandı.

Ebeveyn 1. adımda yeniden kurulurken çocuk FK'sı HENÜZ YOK; FK önce
eklenseydi ebeveynin yeniden kurulumu ``PRAGMA foreign_keys=ON`` altında
referansı kırardı.

Revision ID: 20260812_0056
Revises: 20260811_0055
"""

from alembic import op
import sqlalchemy as sa


revision = "20260812_0056"
down_revision = "20260811_0055"
branch_labels = None
depends_on = None


#: (çocuk, ebeveyn, FK sütunu, UNIQUE adı, FK adı, indeks adı)
CIFTLER = (
    (
        "sales_order_items", "sales_orders", "sales_order_id",
        "uq_sales_orders_company_id",
        "fk_sales_order_items_order_same_company",
        "ix_sales_order_items_company_order",
    ),
    (
        "delivery_note_items", "delivery_notes", "delivery_note_id",
        "uq_delivery_notes_company_id",
        "fk_delivery_note_items_note_same_company",
        "ix_delivery_note_items_company_note",
    ),
)


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

    for cocuk, ebeveyn, fk_sutun, uq_ad, fk_ad, index_ad in CIFTLER:
        inspector = sa.inspect(bind)
        # Taze veritabanları bu tabloları workflow.metadata'dan kurar ve
        # kısıtlar zaten model tanımında; sertleştirilecek bir şey yok.
        if not inspector.has_table(ebeveyn) or not inspector.has_table(cocuk):
            continue

        # --- 1. Ebeveyn: bileşik yabancı anahtarın hedefi -------------------
        if uq_ad not in _unique_names(inspector, ebeveyn):
            with op.batch_alter_table(ebeveyn) as batch_op:
                batch_op.create_unique_constraint(uq_ad, ["company_id", "id"])

        # --- 2. Çocuk: sütun, önce NULL kabul eden --------------------------
        inspector = sa.inspect(bind)
        if "company_id" not in _column_names(inspector, cocuk):
            op.add_column(cocuk, sa.Column("company_id", sa.Integer(), nullable=True))

        # --- 3. Ebeveynden backfill ----------------------------------------
        op.execute(
            sa.text(
                f"UPDATE {cocuk} SET company_id = ("
                f"SELECT p.company_id FROM {ebeveyn} p WHERE p.id = {cocuk}.{fk_sutun}"
                ") WHERE company_id IS NULL"
            )
        )
        sahipsiz = bind.execute(
            sa.text(f"SELECT COUNT(*) FROM {cocuk} WHERE company_id IS NULL")
        ).scalar_one()
        if sahipsiz:
            raise RuntimeError(
                f"{cocuk}: {sahipsiz} satırın ebeveyni yok, company_id türetilemedi. "
                "Migration durdu — sahipsiz satır sessizce bir şirkete yazılmaz."
            )

        # --- 4+5. NOT NULL ve bileşik yabancı anahtar -----------------------
        inspector = sa.inspect(bind)
        with op.batch_alter_table(cocuk) as batch_op:
            batch_op.alter_column(
                "company_id", existing_type=sa.Integer(), nullable=False
            )
            if fk_ad not in _fk_names(inspector, cocuk):
                batch_op.create_foreign_key(
                    fk_ad, ebeveyn, ["company_id", fk_sutun], ["company_id", "id"]
                )

        inspector = sa.inspect(bind)
        if index_ad not in _index_names(inspector, cocuk):
            op.create_index(index_ad, cocuk, ["company_id", fk_sutun])


def downgrade() -> None:
    bind = op.get_bind()

    for cocuk, ebeveyn, _fk_sutun, uq_ad, fk_ad, index_ad in CIFTLER:
        inspector = sa.inspect(bind)
        if not inspector.has_table(cocuk) or not inspector.has_table(ebeveyn):
            continue

        if index_ad in _index_names(inspector, cocuk):
            op.drop_index(index_ad, table_name=cocuk)

        with op.batch_alter_table(cocuk) as batch_op:
            if fk_ad in _fk_names(inspector, cocuk):
                batch_op.drop_constraint(fk_ad, type_="foreignkey")
            batch_op.drop_column("company_id")

        inspector = sa.inspect(bind)
        if uq_ad in _unique_names(inspector, ebeveyn):
            with op.batch_alter_table(ebeveyn) as batch_op:
                batch_op.drop_constraint(uq_ad, type_="unique")
