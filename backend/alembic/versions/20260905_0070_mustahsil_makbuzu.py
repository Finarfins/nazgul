"""Müstahsil makbuzu: `producer_receipts` + `producer_receipt_items`.

Konu: çiftçiden (müstahsilden) yapılan alımın KAĞIDI ve o kağıdın üstündeki
KESİNTİLER — gelir vergisi stopajı ve sosyal güvenlik (Bağ-Kur) kesintisi.
Bu göç kağıdı KAYDEDER; deftere hiçbir şey yazmaz, stok defterine
DOKUNMAZ ve hiçbir mevcut satırı değiştirmez.

--- ORANLAR KULLANICIDAN GELİR, KODDA SABİT DEĞİLDİR (SAHİP KARARI) ---------

`withholding_rate` ve `social_security_rate` SATIR BAŞINA GİRİLİR ve
şemada hiçbir varsayılan taşımazlar. Kodda yasal bir oran sabiti YOKTUR ve
BİLEREK yoktur.

Gerekçe: müstahsil stopaj oranı ürünün cinsine (borsaya kayıtlı mı, zirai
mi, hayvansal mı) ve yürürlükteki tebliğe göre değişir; tek bir sayıyı
koda gömmek, tebliğ değiştiği gün ESKİ oranla yazılmış satırlar bırakır
ve hangi satırın hangi oranla yazıldığı kayıttan OKUNAMAZDI. Oranı satırın
üstünde tutmak, 0066'nın `entered_factor` duruşunun aynısıdır: satır o gün
NEYE İNANILDIĞININ tek kanıtıdır.

Şemanın zorlayabildiği tek şey ARALIKTIR (0..100); oranın DOĞRU olup
olmadığı bu göçün cevaplayabileceği bir soru değildir.

--- KDV YOKTUR VE BU BİR EKSİKLİK DEĞİLDİR ---------------------------------

Müstahsil makbuzunda KDV HANESİ YOKTUR: satıcı çiftçi KDV mükellefi
değildir. `purchases` tablosunun KDV'si FİYATA DAHİL hesaplanır
(`transactions.py`); o formülün buraya kopyalanması, olmayan bir vergiyi
tutardan DÜŞERDİ ve net ödenecek sessizce eksik çıkardı.

Bu yüzden bu iki tabloda `vat_rate`/`vat_amount` sütunu YOKTUR — alan
eklenip sıfır bırakılsaydı, bir gün birinin onu doldurması ŞEMACA
mümkün olurdu.

--- TOPLAMLAR SATIRDAN GELİR, BAŞLIKTA YENİDEN YUVARLANMAZ -----------------

Başlığın üç toplamı (`withholding_total`, `social_security_total`,
`net_payable`) SATIRLARIN TOPLAMIDIR. Yuvarlama SATIRDA yapılır
(ROUND_HALF_UP, 0.01) ve toplam alınırken TEKRAR yuvarlanmaz.

Niye: başlıkta yeniden yuvarlamak, satırların toplamı ile başlığın
toplamını AYRIŞTIRIR (n satırda 0.005'lik farklar birikir) ve makbuzun
kendi içinde tutmayan bir kağıt üretirdi. Aritmetiğin tamamı
`app/mustahsil.py` içindeki SAF fonksiyondadır; bu göç onu ZORLAYAMAZ,
yalnız ölçeği (NUMERIC(18,2)) sabitler.

--- `suppliers(company_id, id)` TEKİLLİĞİ BURADA KURULUYOR ------------------

Bileşik yabancı anahtarın hedefi bir UNIQUE ister. `purchases` ve
`products` bu kısıtı ZATEN taşıyor (0058 ve 0062), ama `suppliers`
TAŞIMIYOR.

ÖLÇÜLDÜ, VARSAYILMADI: `alembic upgrade head` (0069'a kadar) sonrası temiz
bir SQLite şemasında SQLAlchemy `inspect(...).get_unique_constraints(...)`
şunu verdi:

    suppliers  -> []
    purchases  -> [('uq_purchases_company_id', ['company_id', 'id'])]
    products   -> [('uq_products_company_id',  ['company_id', 'id'])]

Bu yüzden `uq_suppliers_company_id` BU GÖÇTE kuruluyor — 0058'in var olan
kalıbıyla (`batch_alter_table` + `create_unique_constraint`), çünkü SQLite'ta
var olan bir tabloya UNIQUE eklemek tabloyu YENİDEN KURAR.

--- `receipt_no` TEKİLLİĞİ: DİYALEKTE GÖRE İKİ AYRI KISIT -------------------

Numara TASLAKTA YOKTUR (NULL) ve yalnız `issue` ile atanır. İstenen kural
"aynı firmada aynı numara iki kez olmasın, ama numarasız taslak SINIRSIZ
olsun".

PostgreSQL'de bu KISMİ (partial) bir benzersiz indekstir:
`WHERE receipt_no IS NOT NULL`. SQLite kısmi indeksi DESTEKLER (3.8.0+) ama
`UniqueConstraint` ile ifade EDİLEMEZ; her iki diyalektte de bu yüzden
`op.create_index(..., unique=True, sqlite_where=/postgresql_where=)`
kullanılıyor.

Düz bir `UniqueConstraint("company_id","receipt_no")` YETMEZDİ: iki
diyalekt de NULL'ları çakıştırmaz, yani taslaklar için çalışırdı — ama
kısmi indeks NİYETİ KAYDA GEÇİRİR ve "NULL'lar çakışmaz" davranışına
BAĞIMLI OLMAYI bırakır.

Revision ID: 20260905_0070
Revises: 20260904_0069
"""

from alembic import op
import sqlalchemy as sa


revision = "20260905_0070"
down_revision = "20260904_0069"
branch_labels = None
depends_on = None


MAKBUZ = "producer_receipts"
KALEM = "producer_receipt_items"
TEDARIKCI = "suppliers"
ALIM = "purchases"
FIS = "field_harvest_tickets"
URUN = "products"

UQ_TEDARIKCI = "uq_suppliers_company_id"
UQ_MAKBUZ = "uq_producer_receipts_company_id"
IX_MAKBUZ_NO = "ux_producer_receipts_company_receipt_no"

# 0066'NIN TİPLERİ, ADIYLA — aynı sözlük, aynı ölçek.
MIKTAR = sa.Numeric(18, 4)
KATSAYI = sa.Numeric(24, 10)
ORAN = sa.Numeric(7, 4)
TUTAR = sa.Numeric(18, 2)

DURUMLAR = ("draft", "issued", "cancelled")


def _tablolar(inspector) -> set[str]:
    return set(inspector.get_table_names())


def _unique_adlari(inspector, table: str) -> set[str]:
    return {item["name"] for item in inspector.get_unique_constraints(table)}


def upgrade() -> None:
    bind = op.get_bind()

    # --- 1. Bileşik yabancı anahtarın hedefi: suppliers(company_id, id) ----
    inspector = sa.inspect(bind)
    if inspector.has_table(TEDARIKCI):
        if UQ_TEDARIKCI not in _unique_adlari(inspector, TEDARIKCI):
            with op.batch_alter_table(TEDARIKCI) as batch_op:
                batch_op.create_unique_constraint(
                    UQ_TEDARIKCI, ["company_id", "id"]
                )

    inspector = sa.inspect(bind)
    if MAKBUZ not in _tablolar(inspector):
        op.create_table(
            MAKBUZ,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("supplier_id", sa.Integer(), nullable=False),
            # Alım belgesine bağ İSTEĞE BAĞLIDIR: makbuz alımdan ÖNCE de
            # kesilebilir ve her müstahsil alımı `purchases`a düşmez.
            sa.Column("purchase_id", sa.Integer(), nullable=True),
            # Kantar fişine bağ İSTEĞE BAĞLIDIR (0069). Verildiğinde kalemin
            # taban miktarı fişin TÜRETİLEN netinden ÖNERİLİR; kullanıcı
            # üstüne yazabilir ve o zaman İKİSİ DE saklanır
            # (`ticket_net_snapshot`), çünkü "fiş ne diyordu" ile "kağıda ne
            # yazıldı" AYRI iki olgudur.
            sa.Column("ticket_id", sa.Integer(), nullable=True),
            # Makbuz numarası TASLAKTA YOKTUR: `issue` atar
            # (`document_sequences`, önek "MM"). Numarayı taslakta atamak,
            # hiç kesilmeyen makbuzlar için seride DELİK bırakırdı.
            sa.Column("receipt_no", sa.String(length=60), nullable=True),
            sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
            # Üç toplam da SATIRLARIN toplamıdır; başlıkta YENİDEN
            # YUVARLANMAZ (bkz. başlık).
            sa.Column("gross_amount", TUTAR, nullable=False),
            sa.Column("withholding_total", TUTAR, nullable=False),
            sa.Column("social_security_total", TUTAR, nullable=False),
            sa.Column("net_payable", TUTAR, nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["company_id"], ["companies.id"],
                name="fk_producer_receipts_company",
            ),
            sa.ForeignKeyConstraint(
                ["company_id", "supplier_id"],
                [f"{TEDARIKCI}.company_id", f"{TEDARIKCI}.id"],
                name="fk_producer_receipts_supplier_same_company",
            ),
            sa.ForeignKeyConstraint(
                ["company_id", "purchase_id"],
                [f"{ALIM}.company_id", f"{ALIM}.id"],
                name="fk_producer_receipts_purchase_same_company",
            ),
            sa.ForeignKeyConstraint(
                ["company_id", "ticket_id"],
                [f"{FIS}.company_id", f"{FIS}.id"],
                name="fk_producer_receipts_ticket_same_company",
            ),
            sa.CheckConstraint(
                "status IN ('draft', 'issued', 'cancelled')",
                name="ck_producer_receipts_status",
            ),
            # Numara ve tarih DURUMLA birlikte hareket eder: taslakta numara
            # OLMAZ, kesilmiş bir makbuzda numarasızlık OLMAZ. Bunu şemada
            # tutmak, `issue` yolundaki bir hatanın numarasız "issued" satır
            # bırakmasını ENGELLER.
            sa.CheckConstraint(
                "(status = 'draft' AND receipt_no IS NULL) "
                "OR (status IN ('issued', 'cancelled') AND receipt_no IS NOT NULL)",
                name="ck_producer_receipts_no_follows_status",
            ),
            sa.CheckConstraint(
                "gross_amount >= 0", name="ck_producer_receipts_gross_nonnegative"
            ),
            sa.CheckConstraint(
                "withholding_total >= 0",
                name="ck_producer_receipts_withholding_nonnegative",
            ),
            sa.CheckConstraint(
                "social_security_total >= 0",
                name="ck_producer_receipts_ss_nonnegative",
            ),
            # Kalemlerin bileşik yabancı anahtarının hedefi.
            sa.UniqueConstraint("company_id", "id", name=UQ_MAKBUZ),
        )
        op.create_index(
            "ix_producer_receipts_company_supplier", MAKBUZ,
            ["company_id", "supplier_id"],
        )
        op.create_index(
            "ix_producer_receipts_company_status", MAKBUZ,
            ["company_id", "status"],
        )
        # KISMİ benzersiz indeks: numarasız taslaklar SINIRSIZ, numara
        # atanmışsa firma içinde TEK. Bkz. başlıktaki gerekçe.
        op.create_index(
            IX_MAKBUZ_NO, MAKBUZ, ["company_id", "receipt_no"], unique=True,
            sqlite_where=sa.text("receipt_no IS NOT NULL"),
            postgresql_where=sa.text("receipt_no IS NOT NULL"),
        )

    inspector = sa.inspect(bind)
    if KALEM not in _tablolar(inspector):
        op.create_table(
            KALEM,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("receipt_id", sa.Integer(), nullable=False),
            # Ürün kartına bağ İSTEĞE BAĞLIDIR: müstahsilden alınan her şey
            # ürün kartı taşımaz. Ama bağ VERİLDİĞİNDE taban birim ORADAN
            # okunur ve bildirilmemişse yazma REDDEDİLİR (0066,
            # `TABAN_BILDIRILMEMIS`) — girileni taban SAYMAK bir olgu
            # uydurmak olurdu.
            sa.Column("product_id", sa.Integer(), nullable=True),
            sa.Column("description", sa.String(length=300), nullable=True),
            # 0066'NIN SÖZLÜĞÜ, AYNEN.
            sa.Column("entered_quantity", MIKTAR, nullable=False),
            sa.Column("entered_unit", sa.String(length=40), nullable=False),
            sa.Column("entered_factor", KATSAYI, nullable=False),
            sa.Column("base_quantity", MIKTAR, nullable=False),
            # Fişin TÜRETİLEN neti, kalem yazıldığı ANDAKİ hâliyle. Kullanıcı
            # `base_quantity`yi üstüne yazsa bile bu KALIR: öneriyle kararın
            # ayrıştığı yer burada GÖRÜNÜR olur.
            sa.Column("ticket_net_snapshot", MIKTAR, nullable=True),
            sa.Column("unit_price", TUTAR, nullable=False),
            sa.Column("line_gross", TUTAR, nullable=False),
            # ORANLAR KULLANICIDAN — varsayılan YOK, sabit YOK (bkz. başlık).
            sa.Column("withholding_rate", ORAN, nullable=False),
            sa.Column("withholding_amount", TUTAR, nullable=False),
            sa.Column("social_security_rate", ORAN, nullable=False),
            sa.Column("social_security_amount", TUTAR, nullable=False),
            sa.Column("line_net", TUTAR, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["company_id"], ["companies.id"],
                name="fk_producer_receipt_items_company",
            ),
            sa.ForeignKeyConstraint(
                ["company_id", "receipt_id"],
                [f"{MAKBUZ}.company_id", f"{MAKBUZ}.id"],
                name="fk_producer_receipt_items_receipt_same_company",
            ),
            sa.ForeignKeyConstraint(
                ["company_id", "product_id"],
                [f"{URUN}.company_id", f"{URUN}.id"],
                name="fk_producer_receipt_items_product_same_company",
            ),
            sa.CheckConstraint(
                "entered_quantity > 0",
                name="ck_producer_receipt_items_entered_positive",
            ),
            sa.CheckConstraint(
                "base_quantity > 0",
                name="ck_producer_receipt_items_base_positive",
            ),
            sa.CheckConstraint(
                "entered_factor > 0",
                name="ck_producer_receipt_items_factor_positive",
            ),
            sa.CheckConstraint(
                "unit_price >= 0",
                name="ck_producer_receipt_items_price_nonnegative",
            ),
            # ORANIN ARALIĞI — şemanın zorlayabildiği TEK şey. Oranın DOĞRU
            # olup olmadığı buradan sorulamaz; bkz. başlık.
            sa.CheckConstraint(
                "withholding_rate >= 0 AND withholding_rate <= 100",
                name="ck_producer_receipt_items_withholding_rate_range",
            ),
            sa.CheckConstraint(
                "social_security_rate >= 0 AND social_security_rate <= 100",
                name="ck_producer_receipt_items_ss_rate_range",
            ),
        )
        op.create_index(
            "ix_producer_receipt_items_company_receipt", KALEM,
            ["company_id", "receipt_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()

    inspector = sa.inspect(bind)
    if KALEM in _tablolar(inspector):
        op.drop_index("ix_producer_receipt_items_company_receipt", table_name=KALEM)
        op.drop_table(KALEM)

    inspector = sa.inspect(bind)
    if MAKBUZ in _tablolar(inspector):
        op.drop_index(IX_MAKBUZ_NO, table_name=MAKBUZ)
        op.drop_index("ix_producer_receipts_company_status", table_name=MAKBUZ)
        op.drop_index("ix_producer_receipts_company_supplier", table_name=MAKBUZ)
        op.drop_table(MAKBUZ)

    # `uq_suppliers_company_id` DÜŞÜRÜLMEZ. Gerekçe 0066/0067'nin
    # `uq_products_company_id` için yazdığının aynısı: onu BU göç yaratmış
    # OLMAYABİLİR (ileride başka bir göç de aynı kısıtı isteyebilir) ve var
    # olan bir kısıtı düşürmek, ona dayanan BAŞKA bir bileşik yabancı
    # anahtarı sessizce kırardı. Fazladan bir UNIQUE zararsızdır; eksik bir
    # UNIQUE kiracı sınırını deler.
