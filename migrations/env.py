import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection

import db.models  # noqa: F401 — регистрирует модели в Base.metadata
from core.config import settings
from db.base import Base
from db.session import create_engine

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _configure(**kwargs) -> None:
    context.configure(
        target_metadata=target_metadata,
        # SQLite почти не умеет ALTER TABLE: batch mode пересоздаёт таблицу.
        render_as_batch=True,
        compare_server_default=True,
        **kwargs,
    )


def run_migrations_offline() -> None:
    _configure(
        url=settings.database_url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()
        # FK во время миграций выключены — проверяем целостность явно.
        if connection.dialect.name == "sqlite":
            broken = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if broken:
                raise RuntimeError(f"Нарушены внешние ключи после миграции: {broken}")


async def run_async_migrations() -> None:
    engine = create_engine(settings.database_url, enforce_foreign_keys=False)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
