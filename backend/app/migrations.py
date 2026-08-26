from datetime import UTC, datetime
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

MIGRATIONS = [
    ('009_command_analytics', 'Komut paleti ve hazır analiz altyapısı'),
    ('010_workflow_documents', 'Teklif sipariş irsaliye ve iade belge zinciri'),
    ('011_finance_core', 'Kasa banka POS virman ve çek senet altyapısı'),
    ('012_cari_crm', 'Cari risk vade aktif pasif ve not altyapısı'),
    ('013_customer_center', 'Yetkili kişiler ve müşteri görevleri altyapısı'),
]


def run_migrations(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
          CREATE TABLE IF NOT EXISTS schema_migrations(
            version TEXT PRIMARY KEY,
            description TEXT NOT NULL,
            applied_at TEXT NOT NULL
          )
        """))
        existing = {row[0] for row in conn.execute(text('SELECT version FROM schema_migrations'))}
        for version, description in MIGRATIONS:
            if version not in existing:
                conn.execute(text("INSERT INTO schema_migrations(version,description,applied_at) VALUES(:v,:d,:a)"),
                             {'v': version, 'd': description, 'a': datetime.now(UTC).isoformat(timespec='seconds')})


def migration_status(engine: Engine) -> list[dict]:
    run_migrations(engine)
    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(text('SELECT * FROM schema_migrations ORDER BY applied_at'))]
