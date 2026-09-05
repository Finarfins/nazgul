"""Avans, borsa tescili ve vergi yükümlülüğü defteri (D2).

Konu: D1 makbuzu KESER ve bir BORÇ doğurur; D2 o borcun KAPANMASIDIR.
Üç yeni tablo (`supplier_advances`, `tax_liabilities`,
`producer_receipt_exchange_registrations`), `producer_receipts`e TEK bir
sütun (`advance_applied_total`) ve `payments`a bileşik yabancı anahtarların
İSTEDİĞİ tekillik.

--- `payments(company_id, id)` TEKİLLİĞİ BURADA KURULUYOR -------------------

Bileşik yabancı anahtarın hedefi bir UNIQUE ister. ÖLÇÜLDÜ, VARSAYILMADI:
`app/core_schema.py`de `payments` tablosunun bildiriminde (sütunlar ve iki
`Index`) hiçbir `UniqueConstraint` YOKTUR.

`suppliers` için 0070 aynı eksiği aynı gerekçeyle kapatmıştı; kalıp
ondan devralınıyor (`batch_alter_table` + `create_unique_constraint`),
çünkü SQLite'ta var olan bir tabloya UNIQUE eklemek tabloyu YENİDEN KURAR.

`downgrade` bu kısıtı DÜŞÜRÜR — 0070'in `uq_suppliers_company_id`yi
BIRAKMA gerekçesinin TERSİ değil, TAMAMLAYICISIDIR: orada kısıt başka bir
göçün de isteyebileceği ORTAK bir hedefti; burada `payments`in tekilliğini
İSTEYEN tek şey bu göçün kendi iki tablosudur ve onlar düşerken kısıt
öksüz kalır. Yine de düşürme KOŞULLUDUR (adı VARSA), yani bir gün başka
bir göç aynı kısıtı kurarsa bu `downgrade` onu KÖRLEMESİNE silmez.

--- AVANS BİR `payments` SATIRIDIR, ONUN YANINDA DURAN BİR ŞEY DEĞİL ------

`supplier_advances.payment_id` NOT NULL'dır ve bu KASITLIDIR: çiftçiye
verilen avans PARANIN KASADAN ÇIKMASIDIR. Avansı ödeme defterinden AYRI
bir tabloda tutmak, kasadan çıkmış ama defterde görünmeyen bir para
bırakırdı ve tedarikçi bakiyesi (açılış + alımlar − ödemeler;
`routers/finance.py`, `statement.py`, `dashboard.py`) avansı SAYMAZDI.

Bağ ÇİFT YÖNLÜDÜR: `payments.reference_type='supplier_advance'` ve
`payments.reference_id` = avansın kimliği. ÖLÇÜLDÜ: `payments.reference_id`
bir yabancı anahtar TAŞIMAZ, bu yüzden yazma sırası TAVUK-YUMURTA
DEĞİLDİR — önce `reference_id` NULL bırakılarak ödeme, sonra avans, sonra
ödemenin `reference_id`si; ÜÇÜ DE TEK işlemde.

--- `remaining_amount` SÜTUNDUR, TÜRETİLEN DEĞİLDİR -----------------------

"Kalan avans" bir SORGUYLA da bulunabilirdi (avans − mahsuplar toplamı).
Sütun tercih edildi çünkü kalanın SIFIRA inmesi bir KARARDIR ve o karar
`applied_at` ile birlikte satıra yazılır; türetilmiş bir değer, mahsubun
HANGİ AN yapıldığını kaydetmezdi.

Şema `0 <= remaining_amount <= amount` ARALIĞINI zorlar; mahsubun DOĞRU
olup olmadığı bu göçün cevaplayabileceği bir soru değildir.

--- BİR AVANS EN FAZLA BİR MAKBUZA BAĞLANIR (BİLİNEN SINIR) ---------------

`receipt_id` TEK bir makbuzu gösterir. KISMİ mahsupta (avans neti AŞIYORSA)
avans AÇIK kalır ve `receipt_id` NULL durur — yani "B avansının 150'si R
makbuzuna gitti" olgusu SATIR BAZINDA OKUNAMAZ; makbuz tarafında yalnız
TOPLAM (`producer_receipts.advance_applied_total`) durur.

Bu bir EKSİKTİR ve adıyla yazılıyor: tam kayıt bir MAHSUP SATIRI tablosu
ister (avans × makbuz × tutar). D2'nin kapsamı dışında bırakıldı; PR
gövdesinde ÖLÇÜLMEDİ başlığı altında duruyor.

--- `advance_applied_total` CHECK'LİDİR; KORKU ÖLÇÜLDÜ VE ÇIKMADI ---------

Sütun `producer_receipts`e `ADD COLUMN` ile ekleniyor (NOT NULL DEFAULT 0;
iki diyalekt de var olan satırları varsayılanla DOLDURUR), sonra
`batch_alter_table` ile `CHECK (advance_applied_total >= 0)` geliyor.

BU CHECK ÖNCE YAZILMAMIŞTI ve gerekçesi YANLIŞTI: "SQLite'ta CHECK eklemek
tabloyu yeniden kurar, yeniden kurulum ise `producer_receipts`in KISMİ
benzersiz indeksinin (`ux_producer_receipts_company_receipt_no`,
`WHERE receipt_no IS NOT NULL`) yüklemini TAŞIMAZ, yani numara tekilliği
SESSİZCE gevşer" diye düşünülmüştü. ÖLÇÜLDÜ, VARSAYILMADI — ve çıkmadı:

    alembic 1.18.5 / SQLAlchemy 2.0.51, CPython 3.12, SQLite 3.49.1
    batch yeniden kurulumundan SONRA:
      ux_...receipt_no  -> "... WHERE receipt_no IS NOT NULL"  (KORUNDU)
      yabancı anahtar sayısı -> 4  (DEĞİŞMEDİ)
      INSERT ... advance_applied_total = -5  -> IntegrityError

Yani korkulan kayıp bu sürümlerde OLMUYOR ve kısıtı ATLAMAK için bir sebep
KALMIYOR. Kayıt buraya, kısıtın kendisiyle birlikte düşülüyor: bir gün
batch davranışı değişirse okuyucu neyin ölçüldüğünü ve NE ZAMAN
ölçüldüğünü bilsin.

Negatif mahsup ayrıca yazma yolunda da reddediliyor; şema son savunmadır,
tek savunma değil.

--- `tax_liabilities` DEFTERE DEĞİL, KENDİ TABLOSUNA YAZILIR --------------

D1'in ölçtüğü engel DEĞİŞMEDİ: `finance_transactions.account_id` NOT NULL
ve `ACCOUNT_TYPES` = {`cash`, `bank`, `pos`} — hiçbiri vergi dairesine olan
BORCU temsil etmez. Bu göç yeni bir hesap TÜRÜ UYDURMAZ; yükümlülüğü kendi
tablosunda tutar, `settled_at` NULL doğar ve D2'de KAPANMAZ (kapatma ucu
YOKTUR, bkz. PR gövdesi ÖLÇÜLMEDİ).

`settlement_payment_id` sütunu ŞİMDİ açılıyor ama D2'de hiçbir yazma yolu
onu doldurmaz. Sütunun ŞİMDİ olması, kapatmanın bir `payments` satırına
BAĞLANACAĞINI şemaya yazar ve o kararı ileriye taşır.

Revision ID: 20260906_0071
Revises: 20260905_0070
"""

from alembic import op
import sqlalchemy as sa


revision = "20260906_0071"
down_revision = "20260905_0070"
branch_labels = None
depends_on = None


ODEME = "payments"
AVANS = "supplier_advances"
VERGI = "tax_liabilities"
TESCIL = "producer_receipt_exchange_registrations"
MAKBUZ = "producer_receipts"
TEDARIKCI = "suppliers"

UQ_ODEME = "uq_payments_company_id"
UQ_AVANS = "uq_supplier_advances_company_id"
UQ_AVANS_ODEME = "uq_supplier_advances_company_payment"
UQ_VERGI = "uq_tax_liabilities_company_id"
UQ_VERGI_TUR = "uq_tax_liabilities_company_receipt_kind"
UQ_TESCIL = "uq_receipt_exchange_registrations_company_id"
UQ_TESCIL_MAKBUZ = "uq_receipt_exchange_registrations_receipt"

MAHSUP_SUTUNU = "advance_applied_total"
CK_MAHSUP = "ck_producer_receipts_advance_applied_nonneg"

# 0066/0070'İN TİPLERİ, ADIYLA — aynı sözlük, aynı ölçek.
TUTAR = sa.Numeric(18, 2)


def _tablolar(inspector) -> set[str]:
    return set(inspector.get_table_names())


def _unique_adlari(inspector, table: str) -> set[str]:
    return {item["name"] for item in inspector.get_unique_constraints(table)}


def _sutun_adlari(inspector, table: str) -> set[str]:
    return {item["name"] for item in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()

    # --- 1. Bileşik yabancı anahtarların hedefi: payments(company_id, id) --
    inspector = sa.inspect(bind)
    if inspector.has_table(ODEME):
        if UQ_ODEME not in _unique_adlari(inspector, ODEME):
            with op.batch_alter_table(ODEME) as batch_op:
                batch_op.create_unique_constraint(UQ_ODEME, ["company_id", "id"])

    # --- 2. Makbuzun mahsup toplamı ---------------------------------------
    inspector = sa.inspect(bind)
    if inspector.has_table(MAKBUZ):
        if MAHSUP_SUTUNU not in _sutun_adlari(inspector, MAKBUZ):
            op.add_column(
                MAKBUZ,
                sa.Column(
                    MAHSUP_SUTUNU, TUTAR, nullable=False, server_default="0"
                ),
            )
            # Kısmi indeksin ve dört yabancı anahtarın batch yeniden
            # kurulumundan SAĞ ÇIKTIĞI ÖLÇÜLDÜ (bkz. başlık).
            with op.batch_alter_table(MAKBUZ) as batch_op:
                batch_op.create_check_constraint(
                    CK_MAHSUP, f"{MAHSUP_SUTUNU} >= 0"
                )

    # --- 3. Avans defteri --------------------------------------------------
    inspector = sa.inspect(bind)
    if AVANS not in _tablolar(inspector):
        op.create_table(
            AVANS,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("supplier_id", sa.Integer(), nullable=False),
            # NOT NULL: avans PARANIN KASADAN ÇIKMASIDIR ve ödeme defterinde
            # KARŞILIĞI OLMAK ZORUNDADIR (bkz. başlık).
            sa.Column("payment_id", sa.Integer(), nullable=False),
            sa.Column("amount", TUTAR, nullable=False),
            sa.Column("remaining_amount", TUTAR, nullable=False),
            # Mahsup edildiği makbuz — KISMİ mahsupta NULL kalır (bilinen
            # sınır, bkz. başlık).
            sa.Column("receipt_id", sa.Integer(), nullable=True),
            sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["company_id"], ["companies.id"],
                name="fk_supplier_advances_company",
            ),
            sa.ForeignKeyConstraint(
                ["company_id", "supplier_id"],
                [f"{TEDARIKCI}.company_id", f"{TEDARIKCI}.id"],
                name="fk_supplier_advances_supplier_same_company",
            ),
            sa.ForeignKeyConstraint(
                ["company_id", "payment_id"],
                [f"{ODEME}.company_id", f"{ODEME}.id"],
                name="fk_supplier_advances_payment_same_company",
            ),
            sa.ForeignKeyConstraint(
                ["company_id", "receipt_id"],
                [f"{MAKBUZ}.company_id", f"{MAKBUZ}.id"],
                name="fk_supplier_advances_receipt_same_company",
            ),
            sa.CheckConstraint(
                "amount > 0", name="ck_supplier_advances_amount_positive"
            ),
            # ARALIK — mahsubun DOĞRU olup olmadığı buradan sorulamaz.
            sa.CheckConstraint(
                "remaining_amount >= 0 AND remaining_amount <= amount",
                name="ck_supplier_advances_remaining_range",
            ),
            sa.UniqueConstraint("company_id", "id", name=UQ_AVANS),
            # Bir ödeme satırı EN FAZLA bir avanstır: aynı `payments` satırını
            # iki avansın göstermesi, kasadan BİR KEZ çıkan parayı İKİ KEZ
            # mahsup ettirirdi.
            sa.UniqueConstraint("company_id", "payment_id", name=UQ_AVANS_ODEME),
        )
        op.create_index(
            "ix_supplier_advances_company_supplier", AVANS,
            ["company_id", "supplier_id"],
        )
        # FIFO mahsubun okuduğu sıra: açık avanslar, en eskisi önce.
        op.create_index(
            "ix_supplier_advances_company_open", AVANS,
            ["company_id", "supplier_id", "id"],
        )

    # --- 4. Vergi yükümlülüğü defteri --------------------------------------
    inspector = sa.inspect(bind)
    if VERGI not in _tablolar(inspector):
        op.create_table(
            VERGI,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("kind", sa.String(length=30), nullable=False),
            sa.Column("receipt_id", sa.Integer(), nullable=False),
            sa.Column("amount", TUTAR, nullable=False),
            # 'YYYY-MM' — makbuzun KESİLDİĞİ ay. Beyanname dönemi budur;
            # ödemenin yapıldığı ay DEĞİL.
            sa.Column("due_period", sa.String(length=7), nullable=False),
            sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("settlement_payment_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["company_id"], ["companies.id"],
                name="fk_tax_liabilities_company",
            ),
            sa.ForeignKeyConstraint(
                ["company_id", "receipt_id"],
                [f"{MAKBUZ}.company_id", f"{MAKBUZ}.id"],
                name="fk_tax_liabilities_receipt_same_company",
            ),
            sa.ForeignKeyConstraint(
                ["company_id", "settlement_payment_id"],
                [f"{ODEME}.company_id", f"{ODEME}.id"],
                name="fk_tax_liabilities_settlement_payment_same_company",
            ),
            sa.CheckConstraint(
                "kind IN ('withholding', 'social_security')",
                name="ck_tax_liabilities_kind",
            ),
            sa.CheckConstraint(
                "amount > 0", name="ck_tax_liabilities_amount_positive"
            ),
            sa.UniqueConstraint("company_id", "id", name=UQ_VERGI),
            # Bir makbuz bir CİNSTEN en fazla BİR yükümlülük doğurur: ikinci
            # bir yazma aynı stopajı İKİ KEZ beyan ettirirdi.
            sa.UniqueConstraint(
                "company_id", "receipt_id", "kind", name=UQ_VERGI_TUR
            ),
        )
        op.create_index(
            "ix_tax_liabilities_company_period", VERGI,
            ["company_id", "due_period"],
        )
        op.create_index(
            "ix_tax_liabilities_company_receipt", VERGI,
            ["company_id", "receipt_id"],
        )

    # --- 5. Borsa tescili ---------------------------------------------------
    inspector = sa.inspect(bind)
    if TESCIL not in _tablolar(inspector):
        op.create_table(
            TESCIL,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("receipt_id", sa.Integer(), nullable=False),
            sa.Column("registration_no", sa.String(length=60), nullable=False),
            sa.Column("exchange_name", sa.String(length=120), nullable=False),
            sa.Column("registered_on", sa.Date(), nullable=False),
            sa.Column("fee_amount", TUTAR, nullable=False),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["company_id"], ["companies.id"],
                name="fk_receipt_exchange_registrations_company",
            ),
            sa.ForeignKeyConstraint(
                ["company_id", "receipt_id"],
                [f"{MAKBUZ}.company_id", f"{MAKBUZ}.id"],
                name="fk_receipt_exchange_registrations_receipt",
            ),
            sa.CheckConstraint(
                "fee_amount >= 0",
                name="ck_receipt_exchange_registrations_fee",
            ),
            sa.UniqueConstraint("company_id", "id", name=UQ_TESCIL),
            # BİR makbuz BİR kez tescil edilir. İkinci tescil, aynı malı iki
            # kez borsaya kaydedilmiş gösterirdi.
            sa.UniqueConstraint(
                "company_id", "receipt_id", name=UQ_TESCIL_MAKBUZ
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()

    inspector = sa.inspect(bind)
    if TESCIL in _tablolar(inspector):
        op.drop_table(TESCIL)

    inspector = sa.inspect(bind)
    if VERGI in _tablolar(inspector):
        op.drop_index("ix_tax_liabilities_company_receipt", table_name=VERGI)
        op.drop_index("ix_tax_liabilities_company_period", table_name=VERGI)
        op.drop_table(VERGI)

    inspector = sa.inspect(bind)
    if AVANS in _tablolar(inspector):
        op.drop_index("ix_supplier_advances_company_open", table_name=AVANS)
        op.drop_index("ix_supplier_advances_company_supplier", table_name=AVANS)
        op.drop_table(AVANS)

    inspector = sa.inspect(bind)
    if inspector.has_table(MAKBUZ):
        if MAHSUP_SUTUNU in _sutun_adlari(inspector, MAKBUZ):
            # KISIT ÖNCE, SÜTUN SONRA — VE İKİSİ AYNI BATCH'TE. ÖLÇÜLDÜ:
            # yalnız `drop_column` çağırmak SQLite'ta
            # `OperationalError: no such column: advance_applied_total`
            # veriyordu, çünkü batch yeniden kurulumu var olan CHECK'i
            # YANSITIP yeni tabloya TAŞIYOR ve o CHECK az önce düşürülen
            # sütunu ADIYLA anıyor. Kısıtı aynı batch içinde düşürmek,
            # yeniden kurulan tanımdan onu da çıkarır.
            with op.batch_alter_table(MAKBUZ) as batch_op:
                batch_op.drop_constraint(CK_MAHSUP, type_="check")
                batch_op.drop_column(MAHSUP_SUTUNU)

    # Kısıt KOŞULLU düşer: onu isteyen iki tablo yukarıda düştü, ama bir gün
    # başka bir göç aynı kısıtı kurarsa burası onu KÖRLEMESİNE silmesin.
    inspector = sa.inspect(bind)
    if inspector.has_table(ODEME):
        if UQ_ODEME in _unique_adlari(inspector, ODEME):
            with op.batch_alter_table(ODEME) as batch_op:
                batch_op.drop_constraint(UQ_ODEME, type_="unique")
