from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import settings

config = context.config
if config.config_file_name is not None:
    # disable_existing_loggers defaults to True, which silently sets
    # .disabled=True on every logger created before this point — including the
    # application's "yerel_hesap" logger and uvicorn's error/access loggers,
    # because startup migrations run at app.main import time. That made
    # production 500s emit no traceback at all (only alembic lines reached
    # container stdout). Keep existing loggers alive.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# During the transition, schema definitions still live in several SQLAlchemy
# MetaData objects. Migrations are therefore explicit/manual instead of relying
# on incomplete autogenerate output.
target_metadata = None
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_with_connection(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    external_connection = config.attributes.get("connection")
    if external_connection is not None:
        _run_with_connection(external_connection)
        return

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _run_with_connection(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
