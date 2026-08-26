"""Kullanıcı aktivite paneli: değişmez (append-only) denetim kaydı.

``activity_logs`` yalnızca INSERT edilir. Çekirdek alanlar (company_id, user_id,
action_type, resource_type, resource_id, summary, details, correlation_id,
created_at) hiçbir uçtan güncellenmez; tek istisna arşiv üçlüsüdür
(archived_at/archived_by/archive_reason) ve o da yalnız admin'e açık iki uçtan
yazılır. CHECK kısıtı arşiv üçlüsünün ya tamamen boş ya tamamen dolu olmasını
zorlar: yarım arşivlenmiş satır oluşamaz.

Değişmezlik API disiplinine BIRAKILMAZ; veritabanı seviyesinde uygulanır:

* her koşulda DELETE yasak (trigger);
* UPDATE yalnız arşiv üçlüsüne izin verir, çekirdek kolonlara dokunan her
  güncelleme (karışık güncelleme dahil) hata verir.

Bu koruma uygulamanın kendi DB rolü için de geçerlidir: yanlış yazılmış bir iç
kod yolu, bir bakım scripti veya elle açılmış bir psql oturumu da çekirdeği
değiştiremez. Çekirdek kolon listesi elle yazılmaz — aşağıdaki tablo tanımından
türetilir, böylece tabloya eklenen yeni bir kolon sessizce korumasız kalamaz.

Migration expand-only: yalnız yeni tablo + indeks + trigger ekler, hiçbir mevcut
kolonu değiştirmez veya düşürmez.

Revision ID: 20260727_0030
Revises: 20260727_0029 (servis v2 FAZ-1)
"""

from alembic import op
import sqlalchemy as sa


revision = "20260727_0030"
down_revision = "20260727_0029"
branch_labels = None
depends_on = None


# Arşivleme, çekirdeğe dokunmayan TEK yazma yoludur.
ARCHIVE_COLUMNS = ("archived_at", "archived_by", "archive_reason")

DELETE_MESSAGE = (
    "activity_logs satirlari silinemez: append-only denetim kaydi"
)
UPDATE_MESSAGE = (
    "activity_logs cekirdek alanlari degistirilemez: "
    "yalniz arsiv alanlari guncellenebilir"
)


def _columns() -> list[sa.Column]:
    """Tablonun tek doğruluk kaynağı: hem CREATE TABLE hem trigger buradan türer."""
    return [
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        # Eylemi yapan kullanıcı. Kimliklendirilememiş bir çağrı bu katalogda
        # yoktur; yine de NULL bırakılabilir olması, kullanıcı kaydı silinse
        # bile denetim satırının hiçbir koşulda kaybolmamasını garanti eder.
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("action_type", sa.String(length=60), nullable=False),
        sa.Column("resource_type", sa.String(length=60), nullable=False),
        # Toplu fiyat güncellemesi gibi tek bir kayda bağlanmayan olaylarda NULL.
        sa.Column("resource_id", sa.Integer(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_by", sa.Integer(), nullable=True),
        sa.Column("archive_reason", sa.Text(), nullable=True),
    ]


def core_columns() -> list[str]:
    """Korunacak çekirdek kolonlar = tablonun tüm kolonları − arşiv üçlüsü."""
    return [
        column.name for column in _columns() if column.name not in ARCHIVE_COLUMNS
    ]


def _create_postgresql_guards() -> None:
    changed = "\n           OR ".join(
        f"NEW.{name} IS DISTINCT FROM OLD.{name}" for name in core_columns()
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION activity_logs_forbid_delete()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '{DELETE_MESSAGE}';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_activity_logs_no_delete
        BEFORE DELETE ON activity_logs
        FOR EACH ROW EXECUTE FUNCTION activity_logs_forbid_delete();
        """
    )
    # Karışık güncelleme (arşiv üçlüsü + çekirdek kolon aynı UPDATE'te) de
    # buraya takılır: çekirdek kolonlardan biri gerçekten değiştiği anda hata.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION activity_logs_forbid_core_update()
        RETURNS trigger AS $$
        BEGIN
            IF {changed}
            THEN
                RAISE EXCEPTION '{UPDATE_MESSAGE}';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_activity_logs_core_immutable
        BEFORE UPDATE ON activity_logs
        FOR EACH ROW EXECUTE FUNCTION activity_logs_forbid_core_update();
        """
    )


def _create_sqlite_guards() -> None:
    # SQLite'ta ``BEFORE UPDATE OF <kolonlar>`` tetikleyicisi, kolon SET
    # listesinde ANILDIĞI anda çalışır (değer gerçekten değişmese bile).
    # PostgreSQL tarafı değer karşılaştırması yapar. İkisi de aynı invariantı
    # korur; SQLite yalnızca bir tık daha katıdır ve bu kasıtlıdır.
    guarded = ", ".join(core_columns())
    op.execute(
        f"""
        CREATE TRIGGER trg_activity_logs_no_delete
        BEFORE DELETE ON activity_logs
        BEGIN
            SELECT RAISE(ABORT, '{DELETE_MESSAGE}');
        END;
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_activity_logs_core_immutable
        BEFORE UPDATE OF {guarded} ON activity_logs
        BEGIN
            SELECT RAISE(ABORT, '{UPDATE_MESSAGE}');
        END;
        """
    )


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("activity_logs"):
        return

    op.create_table(
        "activity_logs",
        *_columns(),
        sa.CheckConstraint(
            "(archived_at IS NULL AND archived_by IS NULL AND archive_reason IS NULL)"
            " OR (archived_at IS NOT NULL AND archived_by IS NOT NULL"
            " AND archive_reason IS NOT NULL)",
            name="ck_activity_logs_archive_triplet",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        # Kiracı bileşik anahtarı: ileride bir alt tablo bu satıra
        # (company_id, id) ile bağlanabilsin diye repo konvansiyonu korunuyor.
        sa.UniqueConstraint("company_id", "id", name="uq_activity_logs_company_id"),
    )
    op.create_index(
        "ix_activity_logs_company_created",
        "activity_logs",
        ["company_id", sa.text("created_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "ix_activity_logs_company_user",
        "activity_logs",
        ["company_id", "user_id"],
    )
    op.create_index(
        "ix_activity_logs_company_resource",
        "activity_logs",
        ["company_id", "resource_type", "resource_id"],
    )

    if bind.dialect.name == "postgresql":
        _create_postgresql_guards()
    else:
        _create_sqlite_guards()


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("activity_logs"):
        return
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_activity_logs_core_immutable ON activity_logs")
        op.execute("DROP TRIGGER IF EXISTS trg_activity_logs_no_delete ON activity_logs")
        op.execute("DROP FUNCTION IF EXISTS activity_logs_forbid_core_update()")
        op.execute("DROP FUNCTION IF EXISTS activity_logs_forbid_delete()")
    else:
        op.execute("DROP TRIGGER IF EXISTS trg_activity_logs_core_immutable")
        op.execute("DROP TRIGGER IF EXISTS trg_activity_logs_no_delete")
    op.drop_table("activity_logs")
