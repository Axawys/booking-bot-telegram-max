import asyncio
import logging

from aiogram import Bot, Dispatcher

from core.config import settings
from db.session import engine, session_factory
from telegram_bot.handlers import router
from telegram_bot.middlewares import DbSessionMiddleware


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    bot = Bot(token=settings.telegram_bot_token.get_secret_value())
    dp = Dispatcher()
    dp.update.middleware(DbSessionMiddleware(session_factory))
    dp.include_router(router)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
