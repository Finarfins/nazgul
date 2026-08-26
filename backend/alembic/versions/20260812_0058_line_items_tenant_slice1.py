"""Kalan dört SATIR tablosunu kiracıya bağla: company_id + BİLEŞİK FK.

Satır tabloları sözleşmesinin 1. ve SON dilimi. Desen 3. dilimde
(20260811_0055, ``stock_transfer_items``) kuruldu, 2. dilimde (20260812_0056)
iki çifte uygulandı; burada kalan dört çifte aynen uygulanıyor:

* ``order_items``    -> ``orders``    (FK sütunu ``order_id``)
* ``purchase_items`` -> ``purchases`` (FK sütunu ``purchase_id``)
* ``quote_items``    -> ``quotes``    (FK sütunu ``quote_id``)
* ``return_items``   -> ``returns``   (FK sütunu ``return_id``)

Sıra çift başına aynı ve zorunlu:

1. **Ebeveyne ``UNIQUE(company_id, id)``** — bileşik FK'nın hedefi tekil olmalı.
2. **Çocuğa ``company_id``** (önce NULL kabul eden).
3. **Ebeveynden backfill** — değer uydurulmuyor, boş tablo varsayılmıyor.
4. **``NOT NULL``** — backfill'den SONRA.
5. **Bileşik FK + kapsayan indeks** — en son; hedefini 1. adım kuruyor.

Sahipsiz satır (ebeveyni silinmiş) varsa migration DURUR. Bugün üretimde bu
tabloların hepsi BOŞ — tek şirket, sıfır sipariş/alış/teklif/iade — yani
backfill'in yapacağı bir iş yok. Bu bir ZAMANLAMA olgusudur, gevşeme gerekçesi
değil: aynı migration bir gün dolu bir müşteri veritabanında koşacak ve o gün
sahipsiz satırı sessizce bir şirkete yazmak, bu sözleşmenin ortadan kaldırmaya
çalıştığı sessizliğin ta kendisi olur.

SQLite'ta 1. ve 4-5. adımlar tabloyu YENİDEN KURAR. Yeniden kurulumun neyi
koruduğu ``tests/tenant_schema_snapshot.py`` ile SAYILARAK ölçülüyor —
indeksler SÜTUN DEMETİYLE karşılaştırılıyor, adla değil.

Ebeveyn 1. adımda yeniden kurulurken çocuk FK'sı HENÜZ YOK; FK önce eklenseydi
ebeveynin yeniden kurulumu ``PRAGMA foreign_keys=ON`` altında referansı kırardı.

``returns`` iki CONFIG kaydının (satış ve alış iadesi) ortak ebeveynidir; tablo
tek olduğu için UNIQUE ve FK de tektir — çift başına döngü aynı ebeveyni iki kez
görmez, çünkü ``return_items`` tek çocuk olarak bir kez listelenir.

ZİNCİR SIRASI DOSYA NUMARASIYLA AYNI DEĞİL — BİLEREK.

Bu göç önce ``20260812_0057``in üstüne yazıldı; inceleme sürerken #72 aynı
ebeveyni işaret eden ``20260812_0059``u indirdi ve zincir ÇATALLANDI (iki baş).
``alembic-chain`` kapısı bunu develop'a dokunulmadan yakaladı.

Çözüm İŞARETİ TAŞIMAK oldu, yeniden adlandırmak değil: dosya ``0058`` kaldı,
``down_revision`` ``0059``a taşındı. Zincir bu yüzden ``0057 -> 0059 -> 0058``
diye okunur. Dosyayı ``0060``a çevirmek testlere, şema kapısına ve kayıt
girdisine yayılırdı; kazancı yoktu. **Bu bir hata değildir; düzeltmeyin.**

Revision ID: 20260812_0058
Revises: 20260812_0059
"""

from alembic import op
import sqlalchemy as sa


revision = "20260812_0058"
down_revision = "20260812_0059"
branch_labels = None
depends_on = None


#: (çocuk, ebeveyn, FK sütunu, UNIQUE adı, FK adı, indeks adı)
CIFTLER = (
    (
        "order_items", "orders", "order_id",
        "uq_orders_company_id",
        "fk_order_items_order_same_company",
        "ix_order_items_company_order",
    ),
    (
        "purchase_items", "purchases", "purchase_id",
        "uq_purchases_company_id",
        "fk_purchase_items_purchase_same_company",
        "ix_purchase_items_company_purchase",
    ),
    (
        "quote_items", "quotes", "quote_id",
        "uq_quotes_company_id",
        "fk_quote_items_quote_same_company",
        "ix_quote_items_company_quote",
    ),
    (
        "return_items", "returns", "return_id",
        "uq_returns_company_id",
        "fk_return_items_return_same_company",
        "ix_return_items_company_return",
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
        # Taze veritabanları bu tabloları metadata'dan kurar ve kısıtlar zaten
        # model tanımında; sertleştirilecek bir şey yok.
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
