"""Bildirimler FAZ-1: outbox bağlama + şablon/sınıf + rıza + onaylı gönderim.

Tasarım kaynağı: ``TASARIM_SERVIS_HATIRLATMA_BILDIRIM_F0.md`` rev4.

Migration **expand-only**'dir: mevcut ``notifications`` tablosuna yalnız
nullable ya da ``server_default``'lu kolonlar eklenir, hiçbir kolon düşürülmez
veya yeniden adlandırılmaz. Yeni tablolar (§2.9, §3, §4):

* ``notification_templates``    — şablon + zorunlu ``message_class`` (§4.0)
* ``notification_consents``     — kanal bazlı açık rıza (§3.1)
* ``notification_consent_events`` — append-only rıza olay günlüğü (§3.2)
* ``notifications_archive``     — durdurulan satırların soğuk saklaması (§2.9)

``notifications_archive`` değişmezliği API disiplinine bırakılmaz; veritabanı
seviyesinde trigger ile uygulanır. Arşiv satırı ne güncellenebilir ne de
silinebilir: "geri taşıma yolu yoktur" (§2.9) kuralı böylece yanlış yazılmış bir
iç kod yolu ya da elle açılmış bir psql oturumu için de geçerlidir.

Revision ID: 20260728_0033
Revises: 20260728_0032 (harman V2c tahsis)
"""

from alembic import op
import sqlalchemy as sa


revision = "20260728_0033"
down_revision = "20260728_0032"
branch_labels = None
depends_on = None


# §2.3 kapalı geçiş tablosundaki tüm durumlar. CHECK kısıtı, katalog dışı bir
# durumun veritabanına hiç girememesini sağlar; geçişlerin kendisi servis
# katmanındaki _ALLOWED_TRANSITIONS ile kapalıdır.
NOTIFICATION_STATUSES = (
    "AWAITING_APPROVAL",
    "PENDING",
    "PROCESSING",
    "RETRY_SCHEDULED",
    "SENT",
    "DELIVERED",
    "SIMULATED",
    "FAILED",
    "NONE",
    "CANCELLED",
    "REJECTED",
    "COMPLIANCE_CHECK_UNAVAILABLE",
)

MESSAGE_CLASSES = ("SERVICE_TRANSACTIONAL", "COMMERCIAL")

ARCHIVE_UPDATE_MESSAGE = (
    "notifications_archive satirlari guncellenemez: soguk denetim saklamasi"
)
ARCHIVE_DELETE_MESSAGE = (
    "notifications_archive satirlari silinemez: soguk denetim saklamasi"
)


# ---------------------------------------------------------------------------
# ``notifications`` genişletmesi (§2.2)
# ---------------------------------------------------------------------------
def _new_notification_columns() -> list[sa.Column]:
    """Hepsi nullable ya da server_default'lu: expand-only sözleşmesi."""
    return [
        # Zamanlama / yeniden deneme
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("last_error_code", sa.String(length=20), nullable=True),
        # Onay zinciri (§2.5, §6)
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("approved_by", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_mode", sa.String(length=20), nullable=True),
        # Silahlanma / durdurma (§2.9)
        sa.Column(
            "dispatch_armed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("disarmed_at", sa.DateTime(timezone=True), nullable=True),
        # F2'de gelecek kural motorunun mantıksal referansı. F1'de her zaman
        # NULL'dur; kolon şimdi açılır ki disarm yolu ve indeksi F2'de şema
        # değişikliği gerektirmesin.
        sa.Column("rule_id", sa.Integer(), nullable=True),
        # İçerik kimliği (§2.5)
        sa.Column("message_class", sa.String(length=24), nullable=True),
        sa.Column("template_id", sa.Integer(), nullable=True),
        sa.Column("template_version", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        # Rıza anlık görüntüsü — YALNIZ denetim kanıtı, karar verici değil (§3.3)
        sa.Column("consent_id", sa.Integer(), nullable=True),
        sa.Column("consent_version", sa.Integer(), nullable=True),
        sa.Column("consent_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consent_decision", sa.String(length=20), nullable=True),
        sa.Column("consent_decision_reason", sa.String(length=60), nullable=True),
        # İYS alanları F3'ün konusudur. Kolonlar §2.2 şemasına sadık kalmak ve
        # F3'te ikinci bir ALTER gerektirmemek için şimdi açılır; F1'de hiçbir
        # kod yolu bunlara yazmaz.
        sa.Column("iys_status", sa.String(length=20), nullable=True),
        sa.Column("iys_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("iys_reference", sa.String(length=120), nullable=True),
        sa.Column("iys_source", sa.String(length=20), nullable=True),
        sa.Column(
            "compliance_attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    ]


def _archive_columns() -> list[sa.Column]:
    """``notifications`` ile aynı şekil + arşiv üçlüsü (§2.9)."""
    return [
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=60), nullable=False),
        sa.Column("channel", sa.String(length=30), nullable=False),
        sa.Column("recipient", sa.String(length=255), nullable=False),
        sa.Column("template", sa.String(length=120), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lock_token", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        *_new_notification_columns(),
        # Arşiv üçlüsü
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_by", sa.String(length=40), nullable=False),
        sa.Column("archive_reason", sa.Text(), nullable=False),
    ]


def _create_archive_guards(dialect: str) -> None:
    """Arşiv soğuk saklamadır: ne UPDATE ne DELETE."""
    if dialect == "postgresql":
        op.execute(
            f"""
            CREATE OR REPLACE FUNCTION notifications_archive_forbid_write()
            RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION '{ARCHIVE_DELETE_MESSAGE}';
                END IF;
                RAISE EXCEPTION '{ARCHIVE_UPDATE_MESSAGE}';
            END;
            $$ LANGUAGE plpgsql;
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_notifications_archive_no_update
            BEFORE UPDATE ON notifications_archive
            FOR EACH ROW EXECUTE FUNCTION notifications_archive_forbid_write();
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_notifications_archive_no_delete
            BEFORE DELETE ON notifications_archive
            FOR EACH ROW EXECUTE FUNCTION notifications_archive_forbid_write();
            """
        )
        return

    op.execute(
        f"""
        CREATE TRIGGER trg_notifications_archive_no_update
        BEFORE UPDATE ON notifications_archive
        BEGIN
            SELECT RAISE(ABORT, '{ARCHIVE_UPDATE_MESSAGE}');
        END;
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_notifications_archive_no_delete
        BEFORE DELETE ON notifications_archive
        BEGIN
            SELECT RAISE(ABORT, '{ARCHIVE_DELETE_MESSAGE}');
        END;
        """
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    dialect = bind.dialect.name

    # ------------------------------------------------------------------
    # 1) notifications genişletmesi
    # ------------------------------------------------------------------
    if inspector.has_table("notifications"):
        existing = {column["name"] for column in inspector.get_columns("notifications")}
        for column in _new_notification_columns():
            if column.name not in existing:
                op.add_column("notifications", column)

        # Durum kataloğunu veritabanı seviyesinde kapat. Mevcut satırların
        # tamamı bu kümenin içindedir (PENDING/PROCESSING/SENT/DELIVERED/
        # FAILED/NONE), dolayısıyla kısıt geriye dönük uyumludur.
        states = ", ".join(f"'{state}'" for state in NOTIFICATION_STATUSES)
        if dialect == "postgresql":
            op.create_check_constraint(
                "ck_notifications_status", "notifications", f"status IN ({states})"
            )
        else:
            with op.batch_alter_table("notifications") as batch:
                batch.create_check_constraint(
                    "ck_notifications_status", f"status IN ({states})"
                )

        existing_indexes = {
            index["name"] for index in inspector.get_indexes("notifications")
        }
        # §2.4 lease yükleminin tam kapsayıcı indeksi.
        if "ix_notifications_dispatch" not in existing_indexes:
            op.create_index(
                "ix_notifications_dispatch",
                "notifications",
                ["company_id", "status", "dispatch_armed", "next_attempt_at"],
            )
        if "ix_notifications_company_scheduled" not in existing_indexes:
            op.create_index(
                "ix_notifications_company_scheduled",
                "notifications",
                ["company_id", "scheduled_for"],
            )
        # §2.9 disarm sorgusu: rule_id + company_id + status.
        if "ix_notifications_company_rule" not in existing_indexes:
            op.create_index(
                "ix_notifications_company_rule",
                "notifications",
                ["company_id", "rule_id", "status"],
            )

    # ------------------------------------------------------------------
    # 2) notification_templates (§4.1)
    # ------------------------------------------------------------------
    if not inspector.has_table("notification_templates"):
        classes = ", ".join(f"'{value}'" for value in MESSAGE_CLASSES)
        op.create_table(
            "notification_templates",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("code", sa.String(length=60), nullable=False),
            sa.Column("channel", sa.String(length=30), nullable=False),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            # §4.0: sınıfsız şablon YOKTUR. Varsayılan da yoktur; yazan taraf
            # açıkça beyan etmek zorundadır.
            sa.Column("message_class", sa.String(length=24), nullable=False),
            sa.Column(
                "is_active", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("updated_by", sa.Integer(), nullable=True),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("approved_by", sa.Integer(), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint(
                f"message_class IN ({classes})",
                name="ck_notification_templates_message_class",
            ),
            sa.UniqueConstraint(
                "company_id", "code", "channel", name="uq_notification_templates_code"
            ),
            sa.UniqueConstraint(
                "company_id", "id", name="uq_notification_templates_company_id"
            ),
        )
        op.create_index(
            "ix_notification_templates_company_active",
            "notification_templates",
            ["company_id", "is_active"],
        )

    # ------------------------------------------------------------------
    # 3) notification_consents (§3.1)
    # ------------------------------------------------------------------
    if not inspector.has_table("notification_consents"):
        op.create_table(
            "notification_consents",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("party_type", sa.String(length=20), nullable=False),
            sa.Column("party_id", sa.Integer(), nullable=False),
            sa.Column("channel", sa.String(length=30), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("granted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("source", sa.String(length=30), nullable=False),
            sa.Column("source_ref", sa.Text(), nullable=True),
            # Rızanın verildiği andaki normalize alıcı. Gönderim anında güncel
            # alıcı ile karşılaştırılır; farklıysa fail-closed (§3.3).
            sa.Column("recipient_snapshot", sa.String(length=255), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.CheckConstraint(
                "status IN ('GRANTED', 'REVOKED')",
                name="ck_notification_consents_status",
            ),
            sa.CheckConstraint(
                "party_type IN ('CUSTOMER', 'SUPPLIER')",
                name="ck_notification_consents_party_type",
            ),
            sa.UniqueConstraint(
                "company_id",
                "party_type",
                "party_id",
                "channel",
                name="uq_notification_consents_party_channel",
            ),
            sa.UniqueConstraint(
                "company_id", "id", name="uq_notification_consents_company_id"
            ),
        )

    # ------------------------------------------------------------------
    # 4) notification_consent_events — append-only (§3.2)
    # ------------------------------------------------------------------
    if not inspector.has_table("notification_consent_events"):
        op.create_table(
            "notification_consent_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("consent_id", sa.Integer(), nullable=False),
            sa.Column("action", sa.String(length=20), nullable=False),
            sa.Column("old_status", sa.String(length=20), nullable=True),
            sa.Column("new_status", sa.String(length=20), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.CheckConstraint(
                "action IN ('GRANT', 'REVOKE')",
                name="ck_notification_consent_events_action",
            ),
        )
        op.create_index(
            "ix_notification_consent_events_company_consent",
            "notification_consent_events",
            ["company_id", "consent_id"],
        )

    # ------------------------------------------------------------------
    # 5) notifications_archive + değişmezlik trigger'ları (§2.9)
    # ------------------------------------------------------------------
    if not inspector.has_table("notifications_archive"):
        op.create_table("notifications_archive", *_archive_columns())
        op.create_index(
            "ix_notifications_archive_company_archived",
            "notifications_archive",
            ["company_id", "archived_at"],
        )
        _create_archive_guards(dialect)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    dialect = bind.dialect.name

    if inspector.has_table("notifications_archive"):
        if dialect == "postgresql":
            op.execute(
                "DROP TRIGGER IF EXISTS trg_notifications_archive_no_update "
                "ON notifications_archive"
            )
            op.execute(
                "DROP TRIGGER IF EXISTS trg_notifications_archive_no_delete "
                "ON notifications_archive"
            )
            op.execute("DROP FUNCTION IF EXISTS notifications_archive_forbid_write()")
        else:
            # SQLite DROP TRIGGER sözdizimi ``ON <tablo>`` almaz.
            op.execute("DROP TRIGGER IF EXISTS trg_notifications_archive_no_update")
            op.execute("DROP TRIGGER IF EXISTS trg_notifications_archive_no_delete")
        op.drop_index(
            "ix_notifications_archive_company_archived",
            table_name="notifications_archive",
        )
        op.drop_table("notifications_archive")

    for table, indexes in (
        (
            "notification_consent_events",
            ("ix_notification_consent_events_company_consent",),
        ),
        ("notification_consents", ()),
        ("notification_templates", ("ix_notification_templates_company_active",)),
    ):
        if inspector.has_table(table):
            for index in indexes:
                op.drop_index(index, table_name=table)
            op.drop_table(table)

    if inspector.has_table("notifications"):
        existing_indexes = {
            index["name"] for index in inspector.get_indexes("notifications")
        }
        for index in (
            "ix_notifications_dispatch",
            "ix_notifications_company_scheduled",
            "ix_notifications_company_rule",
        ):
            if index in existing_indexes:
                op.drop_index(index, table_name="notifications")

        if dialect == "postgresql":
            op.drop_constraint(
                "ck_notifications_status", "notifications", type_="check"
            )
        else:
            with op.batch_alter_table("notifications") as batch:
                batch.drop_constraint("ck_notifications_status", type_="check")

        existing = {column["name"] for column in inspector.get_columns("notifications")}
        for column in reversed(_new_notification_columns()):
            if column.name in existing:
                op.drop_column("notifications", column.name)
