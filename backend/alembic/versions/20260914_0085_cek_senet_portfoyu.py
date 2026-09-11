"""CS1 ÇEK/SENET PORTFÖYÜ: `cek_senetler` defteri.

Revision ID: 20260914_0085
Revises: 20260914_0084

Kaynak: ``docs/cek-senet-kesif-2026-09-10.md`` §2.2 (sütunlar), §5 PR 1
(kapsam), §6 (kararlar). Bu göç YALNIZ yeni tabloyu kurar (ve bileşik
yabancı anahtarlarından birinin eksik hedefini, aşağıda); eski
``financial_instruments`` tablosuna DOKUNMAZ (karar 2: yalnız oluşturma).

--- KAPSAM: MODEL VE DURUM, MUHASEBE YOK ------------------------------------

Karar 1 (Seçenek A: çek alındığında cari düşer) HEDEFTİR ama CS2'nin işidir.
CS1 ``payments``a YAZMAZ; ``payment_id``, ``financial_transaction_id`` ve
``charge_document_id`` sütunları bu yüzden NULL kabul eder ve CS1 uçları
onları NULL bırakır. Sütunlar BUGÜN açılıyor ki CS2 şemaya dokunmadan
bağlasın.

--- DURUM KÜMESİ KAPALI ------------------------------------------------------

``portfoy_durumu`` altı değerli kapalı bir kümedir ve CHECK ile çivilidir:
``app/cek_senet_engine.py::DURUMLAR`` ile BİREBİR aynı ve o eşitlik bir
kapıdır (``tests/test_cs1_cek_senet.py``). Geçiş kuralları şemada DEĞİL
motordadır: bir CHECK "hangi durumdan gelindiğini" bilemez.

--- ``ck_cek_senetler_yon_taraf`` --------------------------------------------

Alınan evrakın borçlusu bir MÜŞTERİ, verilen evrakın lehtarı bir TEDARİKÇİdir.
Karşı tarafsız bir evrak portföyde "kimin çeki" sorusunu cevapsız bırakır;
CHECK iki dalı ayrı ayrı zorlar.

--- BİLEŞİK YABANCI ANAHTARLAR (0044/0062 kuralı) VE EKSİK HEDEF ------------

Beş referansın beşi de ``(company_id, <sütun>) -> <tablo>(company_id, id)``
biçimindedir; bir firmanın çekinin BAŞKA firmanın müşterisine, tedarikçisine,
kasasına ya da ödemesine bağlanması VERİTABANI SEVİYESİNDE imkânsızdır.
Hedeflerin (company_id, id) UNIQUE'i ÖLÇÜLDÜ:

* ``customers``  -> ``uq_customers_company_id``  (0026) VAR
* ``suppliers``  -> ``uq_suppliers_company_id``  (0070) VAR
* ``payments``   -> ``uq_payments_company_id``   (0071) VAR
* ``finance_accounts`` -> YOK. ``alembic/versions/`` altında o tabloya ait
  hiçbir (company_id, id) kısıtı yok; tablo baseline'dan (0000) geliyor.

Eksik olan BU GÖÇTE, 0071'in kalıbıyla kurulur (``batch_alter_table`` +
``create_unique_constraint``, adı VARSA atlanır): ``uq_finance_accounts_
company_id``. SQLite'ta bu ``finance_accounts``ı yeniden kurar; tablo küçük
bir tanım tablosudur (firma başına birkaç kasa/banka). ``downgrade`` onu
KOŞULLU düşürür (0071'in ``uq_payments_company_id`` gerekçesi: onu isteyen
tek şey bu göçün kendi tablosu; yine de adı yoksa körlemesine silinmez).

``created_by -> app_users.id`` ÇIPLAKTIR: ``app_users`` kiracı tablosu değildir
(``company_id`` sütunu yok, ölçüldü).

--- (company_id, seri_no, tur) TEKİLLİĞİ KONMADI ----------------------------

Brif "yalnız gerekçelendirilebilirse" diyor ve gerekçelendirilemedi: seri
numarası ÇEK KARNESİNE (banka + hesap) aittir, portföye değil. İki farklı
bankanın, iki farklı keşidecinin çekleri AYNI seri numarasını taşıyabilir;
aynı firmaya iki ayrı müşteriden gelen bu iki çek meşrudur. Tekillik ancak
(banka, hesap, seri) üçlüsünde anlamlı olurdu ve o sütunlar NULL kabul
ediyor. Düz ``ix_cek_senetler_company_seri`` araması duruyor.

--- ACILIŞ ŞEMASI ------------------------------------------------------------

``cek_senetler`` hiçbir ``metadata.create_all`` kapsamında DEĞİLDİR: uç
katmanının okuduğu ``Table`` nesnesi (varsa) ayrı ve hiç kurulmayan bir
metadata üzerindedir. 0072'de ölçülen kusur (açılış DDL'i tabloyu göçten ÖNCE
kurar, göç onu VAR bulup atlar) burada ÜRETİLEMEZ (kapı:
``test_ACILIS_DDLi_GOCUN_ONUNE_GECMIYOR``).

--- KİRACI TABLOSU: 121 -> 122 ----------------------------------------------

Tablo ``company_id`` taşır; ``TENANT_TABLES`` envanterine girer ve her
sorgusundan ``company_id=:cid`` yüklemi istenir.

--- GERİ ALINABİLİR ----------------------------------------------------------

``downgrade()`` tabloyu düşürür: kaybedilen şey portföy DEFTERİdir; CS1'de
``payments``a bağlı hiçbir satır doğmadığı için cari bakiye etkilenmez.
up -> down -> up SQLite'ta ve gerçek PostgreSQL 16'da koşuldu (PG ikizi:
``backend/test_cs1_cek_senet_postgresql.py``).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260914_0085"
down_revision = "20260914_0084"
branch_labels = None
depends_on = None

TABLO = "cek_senetler"
HESAP = "finance_accounts"

#: Kapalı küme. ``app/cek_senet_engine.py::DURUMLAR`` ile BİREBİR aynı.
DURUMLAR = (
    "portfoyde",
    "tahsile_verildi",
    "tahsil_edildi",
    "ciro_edildi",
    "karsiliksiz",
    "iade",
)
TURLER = ("cek", "senet")
YONLER = ("alinan", "verilen")

UQ_HESAP = "uq_finance_accounts_company_id"
UQ_FIRMA_KIMLIK = "uq_cek_senetler_company_id"
CK_TUR = "ck_cek_senetler_tur"
CK_YON = "ck_cek_senetler_yon"
CK_TUTAR = "ck_cek_senetler_tutar_pozitif"
CK_YON_TARAF = "ck_cek_senetler_yon_taraf"
CK_DURUM = "ck_cek_senetler_portfoy_durumu"
FK_MUSTERI = "fk_cek_senetler_customer"
FK_TEDARIKCI = "fk_cek_senetler_supplier"
FK_CIRO = "fk_cek_senetler_endorsed_supplier"
FK_HESAP = "fk_cek_senetler_tahsil_hesap"
FK_ODEME = "fk_cek_senetler_payment"

INDEKSLER = {
    "ix_cek_senetler_company_durum_vade": ["company_id", "portfoy_durumu", "vade"],
    "ix_cek_senetler_company_customer": ["company_id", "customer_id"],
    "ix_cek_senetler_company_supplier": ["company_id", "supplier_id"],
    "ix_cek_senetler_company_seri": ["company_id", "seri_no"],
}

# 0066/0070/0071'in tipi, ADIYLA: MONEY ailesi (``app/numeric_manifest.py``).
TUTAR = sa.Numeric(18, 2)


def _liste(degerler: tuple[str, ...]) -> str:
    return ",".join("'" + d + "'" for d in degerler)


def _unique_adlari(inspector, tablo: str) -> set[str]:
    return {k["name"] for k in inspector.get_unique_constraints(tablo)}


def upgrade() -> None:
    bind = op.get_bind()

    # --- 1. Eksik bileşik FK hedefi: finance_accounts(company_id, id) --------
    inspector = sa.inspect(bind)
    if inspector.has_table(HESAP) and UQ_HESAP not in _unique_adlari(inspector, HESAP):
        with op.batch_alter_table(HESAP) as batch_op:
            batch_op.create_unique_constraint(UQ_HESAP, ["company_id", "id"])

    # --- 2. Defter -------------------------------------------------------------
    inspector = sa.inspect(bind)
    if inspector.has_table(TABLO):
        return

    op.create_table(
        TABLO,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("tur", sa.String(length=20), nullable=False),
        sa.Column("yon", sa.String(length=20), nullable=False),
        sa.Column(
            "portfoy_durumu", sa.String(length=30), nullable=False,
            server_default="portfoyde",
        ),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("supplier_id", sa.Integer(), nullable=True),
        sa.Column("endorsed_supplier_id", sa.Integer(), nullable=True),
        sa.Column("endorsed_date", sa.Date(), nullable=True),
        sa.Column("tutar", TUTAR, nullable=False),
        sa.Column("vade", sa.Date(), nullable=False),
        sa.Column("keside_tarihi", sa.Date(), nullable=True),
        sa.Column("banka_adi", sa.String(length=160), nullable=True),
        sa.Column("sube_adi", sa.String(length=120), nullable=True),
        sa.Column("hesap_no", sa.String(length=100), nullable=True),
        sa.Column("seri_no", sa.String(length=100), nullable=False),
        sa.Column("kesideci", sa.String(length=200), nullable=True),
        sa.Column("tahsil_hesap_id", sa.Integer(), nullable=True),
        sa.Column("tahsil_tarihi", sa.Date(), nullable=True),
        # CS2'nin bağları — CS1 uçları NULL bırakır (başlık).
        sa.Column("payment_id", sa.Integer(), nullable=True),
        sa.Column("financial_transaction_id", sa.Integer(), nullable=True),
        sa.Column("charge_document_id", sa.Integer(), nullable=True),
        sa.Column("notlar", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(
            ["company_id", "customer_id"], ["customers.company_id", "customers.id"],
            name=FK_MUSTERI,
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "supplier_id"], ["suppliers.company_id", "suppliers.id"],
            name=FK_TEDARIKCI,
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "endorsed_supplier_id"],
            ["suppliers.company_id", "suppliers.id"],
            name=FK_CIRO,
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "tahsil_hesap_id"],
            ["finance_accounts.company_id", "finance_accounts.id"],
            name=FK_HESAP,
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "payment_id"], ["payments.company_id", "payments.id"],
            name=FK_ODEME,
        ),
        # ``app_users`` kiracı tablosu değil: çıplak FK (başlık).
        sa.ForeignKeyConstraint(["created_by"], ["app_users.id"]),
        # 0062'nin kuralı, TERSTEN: CS2'de bir borç belgesi ya da ciro kaydı bu
        # tabloya bileşik anahtarla bağlanabilsin.
        sa.UniqueConstraint("company_id", "id", name=UQ_FIRMA_KIMLIK),
        sa.CheckConstraint("tur IN (%s)" % _liste(TURLER), name=CK_TUR),
        sa.CheckConstraint("yon IN (%s)" % _liste(YONLER), name=CK_YON),
        sa.CheckConstraint("tutar > 0", name=CK_TUTAR),
        sa.CheckConstraint(
            "(yon = 'alinan' AND customer_id IS NOT NULL) OR "
            "(yon = 'verilen' AND supplier_id IS NOT NULL)",
            name=CK_YON_TARAF,
        ),
        sa.CheckConstraint(
            "portfoy_durumu IN (%s)" % _liste(DURUMLAR), name=CK_DURUM
        ),
    )
    for ad, sutunlar in INDEKSLER.items():
        op.create_index(ad, TABLO, sutunlar)


def downgrade() -> None:
    """Simetrik ve KOŞULLU. Kaybın sınırı başlıkta."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table(TABLO):
        for ad in INDEKSLER:
            op.drop_index(ad, table_name=TABLO)
        op.drop_table(TABLO)

    # Hedef KOŞULLU düşer: onu isteyen tek tablo yukarıda düştü; adı yoksa
    # (başka bir yol kurmadıysa) dokunulmaz.
    inspector = sa.inspect(bind)
    if inspector.has_table(HESAP) and UQ_HESAP in _unique_adlari(inspector, HESAP):
        with op.batch_alter_table(HESAP) as batch_op:
            batch_op.drop_constraint(UQ_HESAP, type_="unique")
