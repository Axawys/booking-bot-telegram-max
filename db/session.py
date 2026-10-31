from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from core.config import settings


def _sqlite_pragmas(enforce_foreign_keys: bool):
    def set_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        # В SQLite внешние ключи по умолчанию выключены — без этого не работают
        # ON DELETE CASCADE / RESTRICT.
        cursor.execute(f"PRAGMA foreign_keys={'ON' if enforce_foreign_keys else 'OFF'}")
        # WAL: чтение не блокируется записью.
        cursor.execute("PRAGMA journal_mode=WAL")
        # Ждать до 5 с, если БД занята другой записью, вместо мгновенной ошибки.
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return set_pragmas


def create_engine(url: str, *, enforce_foreign_keys: bool = True) -> AsyncEngine:
    """Async-движок. enforce_foreign_keys=False — только для миграций:
    batch-миграции SQLite пересоздают таблицы, и DROP родительской таблицы
    при включённых FK каскадно удалил бы дочерние строки.
    """
    parsed = make_url(url)
    is_sqlite = parsed.get_backend_name() == "sqlite"
    if is_sqlite and parsed.database and parsed.database != ":memory:":
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)

    engine = create_async_engine(url)
    if is_sqlite:
        event.listen(engine.sync_engine, "connect", _sqlite_pragmas(enforce_foreign_keys))
    return engine


engine = create_engine(settings.database_url)
session_factory = async_sessionmaker(engine, expire_on_commit=False)
