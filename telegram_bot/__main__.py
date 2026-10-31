import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from core.config import settings
from db.session import engine, session_factory
from telegram_bot.commands import setup_commands
from telegram_bot.handlers import setup_routers
from telegram_bot.middlewares import BanMiddleware, DbSessionMiddleware, ThrottlingMiddleware
from telegram_bot.scheduler import BookingScheduler


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    bot = Bot(
        token=settings.telegram_bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    # Порядок: антифлуд (в памяти, до БД) -> сессия БД -> проверка бана.
    dp.update.outer_middleware(
        ThrottlingMiddleware(
            limit=settings.rate_limit_messages,
            window=settings.rate_limit_window_seconds,
            cooldown=settings.rate_limit_cooldown_seconds,
        )
    )
    dp.update.middleware(DbSessionMiddleware(session_factory))
    dp.update.middleware(BanMiddleware())
    dp.include_routers(*setup_routers())

    # Задачи планировщика пересоздаются из БД при каждом старте.
    scheduler = BookingScheduler(bot, session_factory)
    dp["scheduler"] = scheduler  # доступен в хендлерах как аргумент `scheduler`

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await setup_commands(bot)
        await scheduler.start()
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
