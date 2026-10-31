"""Общие фикстуры.

Окружение задаётся до импорта приложения: настройки и движок БД создаются
при импорте модулей. Схема создаётся настоящими миграциями Alembic.
"""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="booking-bot-tests-"))
os.environ.update(
    {
        "TELEGRAM_BOT_TOKEN": "42:TEST",
        "DATABASE_URL": f"sqlite+aiosqlite:///{_TMP / 'test.db'}",
        "ADMIN_IDS": "99,98",
        "ADMIN_DIGEST_TIME": "",
    }
)

import pytest  # noqa: E402
from aiogram import Bot, Dispatcher  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import text  # noqa: E402

from db.session import engine, session_factory  # noqa: E402
from notifications.notifier import Notifier  # noqa: E402
from notifications.scheduler import BookingScheduler  # noqa: E402
from telegram_bot.handlers import setup_routers  # noqa: E402
from telegram_bot.middlewares import BanMiddleware, DbSessionMiddleware  # noqa: E402
from tests.helpers import FakeMaxApi, FakeTelegram, MaxUser, TgUser  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
# Дочерние таблицы — раньше родительских.
TABLES = [
    "sent_reminders",
    "booking_slots",
    "bookings",
    "clients",
    "closed_slots",
    "app_settings",
    "banned_users",
]


@pytest.fixture(scope="session", autouse=True)
def _migrated_db() -> None:
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")


@pytest.fixture(autouse=True)
async def _clean_db():
    async with engine.begin() as conn:
        for table in TABLES:
            await conn.execute(text(f"DELETE FROM {table}"))
    yield
    # Соединения пула привязаны к event loop теста — закрываем их.
    await engine.dispose()


@pytest.fixture
async def session():
    async with session_factory() as s:
        yield s


@pytest.fixture
def api() -> FakeTelegram:
    return FakeTelegram()


@pytest.fixture
def bot(api: FakeTelegram) -> Bot:
    return Bot("42:TEST", session=api, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


@pytest.fixture
def max_api() -> FakeMaxApi:
    return FakeMaxApi()


@pytest.fixture
def notifier(bot: Bot, max_api: FakeMaxApi) -> Notifier:
    return Notifier(bot, max_api)


@pytest.fixture
async def scheduler(notifier: Notifier):  # async: AsyncIOScheduler нужен работающий event loop
    sched = BookingScheduler(notifier, session_factory)
    sched.scheduler.start(paused=True)  # задачи планируются, но не срабатывают сами
    yield sched
    sched.shutdown()


_ban_middleware = BanMiddleware()


@pytest.fixture(scope="session")
def _dispatcher() -> Dispatcher:
    # Роутеры — синглтоны модулей и подключаются к диспетчеру один раз,
    # поэтому диспетчер общий на сессию, а состояние сбрасывается в `dp`.
    dp = Dispatcher()
    dp.update.middleware(DbSessionMiddleware(session_factory))
    dp.update.middleware(_ban_middleware)
    dp.include_routers(*setup_routers())
    return dp


@pytest.fixture
def dp(_dispatcher: Dispatcher, scheduler: BookingScheduler, notifier: Notifier) -> Dispatcher:
    _dispatcher.storage.storage.clear()
    _ban_middleware.last_notice.clear()
    _dispatcher["scheduler"] = scheduler
    _dispatcher["notifier"] = notifier
    return _dispatcher


@pytest.fixture
def user(dp: Dispatcher, bot: Bot, api: FakeTelegram):
    def make(user_id: int, name: str = "Клиент") -> TgUser:
        return TgUser(dp, bot, api, user_id, name)

    return make


@pytest.fixture
def admin(user) -> TgUser:
    return user(99, "Админ")


@pytest.fixture
def max_bot(max_api: FakeMaxApi, notifier: Notifier):
    from core.ratelimit import RateLimiter
    from max_bot.bot import MaxBot

    return MaxBot(max_api, notifier, session_factory, RateLimiter(100, 10, 30))


@pytest.fixture
def max_user(max_bot, max_api: FakeMaxApi):
    def make(user_id: int, name: str = "Клиент MAX") -> MaxUser:
        return MaxUser(max_bot, max_api, user_id, name)

    return make
