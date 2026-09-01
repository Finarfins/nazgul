"""Hayvan ilaç bekleme süresi: katalog, ilaç kaydı, süt kilidi sütunları.

Revision ID: 20260901_0065
Revises: 20260901_0064

Depo HİÇBİR mevzuat rakamı iddia etmez. Katalog ve ilaç kaydı her değerini
o değerin sahibi firma girer; katalog yalnız önerir, operatör kazanır.
Boş süre NULL demektir ve ihlal DEĞİL — katalog boş kalma SIKLIĞINI düşürür,
boşun ANLAMINI değiştirmez.

PR-1: süt kilidi etkin. PR-2: et kilidi etkin olacak; ``meat_withdrawal_days``
bu göçte zaten var çünkü bir ilacın iki süresi aynı etiketten gelir ve ikinci
göçde sütunu eklemek gereksiz veri taşıma olurdu.

`species=''` JOKER: bir türün gerçek adı boş dize olamaz. `MIXED` gerçek bir
değerdir (karma sürü ilacı) ve joker ile karıştırılamaz — boş dize ile
ayrılır. PR #18'in `crop=''` kararıyla aynı gerekçe.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260901_0065"
down_revision = "20260901_0064"
branch_labels = None
depends_on = None

KATALOG = "animal_drug_catalogue"
ILAC_KAYIT = "animal_drug_treatments"

SPECIES = ("CATTLE", "BUFFALO", "SHEEP", "GOAT", "MIXED", "")


def _var_mi(inspector, tablo: str) -> bool:
    return tablo in inspector.get_table_names()


def _sutunlar(inspector, tablo: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(tablo)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _var_mi(inspector, KATALOG):
        op.create_table(
            KATALOG,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("drug_name", sa.String(length=120), nullable=False),
            # BOŞ DİZE = bütün türler (joker); `MIXED` = gerçek karma sürü.
            sa.Column("species", sa.String(length=20), nullable=False, server_default=""),
            sa.Column("registration_no", sa.String(length=60), nullable=True),
            # Katalogun VAR OLMA SEBEBİ bu iki sütun; her ikisi NOT NULL.
            sa.Column("milk_withdrawal_days", sa.Integer(), nullable=False),
            sa.Column("meat_withdrawal_days", sa.Integer(), nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="ACTIVE"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint(
                "milk_withdrawal_days >= 0 AND milk_withdrawal_days <= 3650",
                name="ck_adc_milk_range",
            ),
            sa.CheckConstraint(
                "meat_withdrawal_days >= 0 AND meat_withdrawal_days <= 3650",
                name="ck_adc_meat_range",
            ),
            sa.CheckConstraint(
                "drug_name <> ''",
                name="ck_adc_drug_name_not_empty",
            ),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            # Aynı ilaç + tür için İKİ satır olamaz; çözüm belirsiz kalmasın.
            sa.UniqueConstraint(
                "company_id", "drug_name", "species", name="uq_adc_company_drug_species"
            ),
            # İleride bileşik yabancı anahtar hedefi olabilmesi için.
            sa.UniqueConstraint("company_id", "id", name="uq_adc_company_id"),
        )
        op.create_index(
            "ix_adc_company_species", KATALOG, ["company_id", "species"]
        )

    if not _var_mi(inspector, ILAC_KAYIT):
        op.create_table(
            ILAC_KAYIT,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("animal_id", sa.Integer(), nullable=False),
            sa.Column("catalogue_id", sa.Integer(), nullable=True),
            sa.Column("drug_name", sa.String(length=120), nullable=False),
            sa.Column("treated_on", sa.Date(), nullable=False),
            # Etkin değer operatörün girdiği ya da katalogdan çözülen.
            sa.Column("milk_withdrawal_days", sa.Integer(), nullable=True),
            sa.Column("meat_withdrawal_days", sa.Integer(), nullable=True),
            # KÖKEN KAYIT ALTINDA — PR #18'in deseni.
            sa.Column("milk_withdrawal_source", sa.String(length=20), nullable=True),
            sa.Column("meat_withdrawal_source", sa.String(length=20), nullable=True),
            # Katalogun ne dediği — denetimde "operatör katalogdaki 21 günü
            # 7 yaptı" GÖRÜNÜR olsun diye ayrı sütun.
            sa.Column("catalogue_milk_days", sa.Integer(), nullable=True),
            sa.Column("catalogue_meat_days", sa.Integer(), nullable=True),
            sa.Column("batch_no", sa.String(length=60), nullable=True),
            sa.Column("dose", sa.Numeric(18, 4), nullable=True),
            sa.Column("dose_unit", sa.String(length=32), nullable=True),
            sa.Column("veterinarian", sa.String(length=180), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column(
                "status", sa.String(length=20), nullable=False, server_default="RECORDED"
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint(
                "milk_withdrawal_days IS NULL OR "
                "(milk_withdrawal_days >= 0 AND milk_withdrawal_days <= 3650)",
                name="ck_adt_milk_range",
            ),
            sa.CheckConstraint(
                "meat_withdrawal_days IS NULL OR "
                "(meat_withdrawal_days >= 0 AND meat_withdrawal_days <= 3650)",
                name="ck_adt_meat_range",
            ),
            sa.CheckConstraint(
                "drug_name <> ''",
                name="ck_adt_drug_name_not_empty",
            ),
            sa.CheckConstraint(
                "status IN ('RECORDED', 'VOIDED')",
                name="ck_adt_status",
            ),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(
                ["company_id", "animal_id"],
                ["animals.company_id", "animals.id"],
                name="fk_adt_animal_same_company",
            ),
            sa.ForeignKeyConstraint(
                ["company_id", "catalogue_id"],
                [f"{KATALOG}.company_id", f"{KATALOG}.id"],
                name="fk_adt_catalogue_same_company",
            ),
            sa.UniqueConstraint("company_id", "id", name="uq_adt_company_id"),
        )
        op.create_index(
            "ix_adt_company_animal_status", ILAC_KAYIT,
            ["company_id", "animal_id", "status"],
        )
        op.create_index(
            "ix_adt_company_treated_on", ILAC_KAYIT,
            ["company_id", "treated_on"],
        )

    # --- mevcut tablolara sütunlar ------------------------------------------
    mevcut = _sutunlar(inspector, "milk_yields")
    if "safety_override_reason" not in mevcut or "safety_warning" not in mevcut:
        with op.batch_alter_table("milk_yields") as batch:
            if "safety_override_reason" not in mevcut:
                batch.add_column(
                    sa.Column("safety_override_reason", sa.String(length=255), nullable=True)
                )
            if "safety_warning" not in mevcut:
                batch.add_column(
                    sa.Column("safety_warning", sa.Text(), nullable=True)
                )

    firma_sutunlari = _sutunlar(inspector, "companies")
    if "herd_withdrawal_milk_policy" not in firma_sutunlari:
        op.add_column(
            "companies",
            sa.Column(
                "herd_withdrawal_milk_policy",
                sa.String(length=20),
                nullable=False,
                server_default="require_reason",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    firma_sutunlari = _sutunlar(inspector, "companies")
    if "herd_withdrawal_milk_policy" in firma_sutunlari:
        op.drop_column("companies", "herd_withdrawal_milk_policy")

    mevcut = _sutunlar(inspector, "milk_yields")
    if "safety_warning" in mevcut:
        op.drop_column("milk_yields", "safety_warning")
    if "safety_override_reason" in mevcut:
        op.drop_column("milk_yields", "safety_override_reason")

    if _var_mi(inspector, ILAC_KAYIT):
        op.drop_table(ILAC_KAYIT)

    inspector = sa.inspect(bind)
    if _var_mi(inspector, KATALOG):
        op.drop_table(KATALOG)
