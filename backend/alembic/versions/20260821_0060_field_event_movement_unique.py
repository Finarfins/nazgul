"""Tarla olayı başına EN FAZLA BİR stok hareketi — kısıt ETKİYİ korur.

Konu: mobil-erp#91 (FAZ 4 outbox tüketicisi).

--- NEDEN BU KISIT VAR -------------------------------------------------------

`field_integration_events` üzerinde zaten
``UniqueConstraint(company_id, idempotency_key)`` vardı ve OLAY SATIRINI
koruyordu. Ama korunması gereken şey satır değil, **ETKİ**: stok hareketi.

Ölçülen kaçış (inceleme 4996167814): iki tüketici aynı PENDING olayı
eşzamanlı seçer, her biri KENDİ hareketini yazar, ikisi de olayı `SENT`
yapar. Olay tablosunda hiçbir kısıt ihlali görünmez — envanter iki kez
düşmüştür. Anahtar üzerinde durduğu satırı korur, YAN ETKİYİ asla.

Bu yüzden benzersizlik hareketin kendisine konuyor: bir olay + bir ürün için
EN FAZLA BİR hareket. Uygulama katmanındaki atomik talep yarışı ilk elde
çözer; bu kısıt ise güvencenin veritabanında durmasını sağlar — uygulama
mantığı yarın değişse bile.

KISMİ (partial) İNDEKS: yalnız bu tüketicinin yazdığı satırları kapsar.
`reference_type` başka olan hareketler (satış, servis, açılış) etkilenmez;
onların kendi kuralları var ve bu göç onları değiştirmez. Hem SQLite (3.8+)
hem PostgreSQL kısmi indeksi destekliyor.
"""
from alembic import op

revision = "20260821_0060"
down_revision = "20260812_0058"
branch_labels = None
depends_on = None

INDEKS = "uq_stock_movements_field_event"
REFERANS = "field_integration_event"


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS %s "
        "ON stock_movements (company_id, reference_id, product_id) "
        "WHERE reference_type = '%s'" % (INDEKS, REFERANS)
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS %s" % INDEKS)
