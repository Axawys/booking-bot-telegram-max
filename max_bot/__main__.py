"""MAX-бот: long polling GET /updates и обработка событий в MaxBot.

Запуск: python -m max_bot (в Docker — отдельный сервис max-bot).
"""

import asyncio
import logging
import sys

import aiohttp
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from core.config import settings
from core.ratelimit import RateLimiter
from db.session import engine, session_factory
from max_bot.api import MaxApi, MaxApiError
from max_bot.bot import MaxBot
from notifications.notifier import Notifier

log = logging.getLogger("max_bot")

COMMANDS = [("start", "Главное меню"), ("cancel", "Прервать оформление записи")]
RETRY_DELAY = 5


async def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if settings.max_bot_token is None:
        log.error("MAX_BOT_TOKEN не задан — MAX-бот не запущен")
        return 0

    api = MaxApi(settings.max_bot_token.get_secret_value(), settings.max_api_url)
    # Админы — в Telegram: заявки из MAX уходят им через Telegram Bot API.
    tg_bot = Bot(
        token=settings.telegram_bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    bot = MaxBot(
        api,
        Notifier(tg_bot, api),
        session_factory,
        RateLimiter(
            settings.rate_limit_messages,
            settings.rate_limit_window_seconds,
            settings.rate_limit_cooldown_seconds,
        ),
    )

    try:
        try:
            me = await api.get_me()
        except MaxApiError as exc:
            log.error("MAX API отклонил токен: %s", exc)
            return 1
        log.info(
            "MAX-бот %s (id %s) запущен",
            me.get("username") or me.get("first_name"),
            me.get("user_id"),
        )
        try:
            await api.set_commands(COMMANDS)
        except MaxApiError as exc:
            log.warning("Не удалось задать команды MAX-бота: %s", exc)

        marker: int | None = None
        while True:
            try:
                data = await api.get_updates(marker, wait_seconds=30)
            except (aiohttp.ClientError, TimeoutError, MaxApiError) as exc:
                log.warning(
                    "MAX: ошибка получения событий: %s; повтор через %s с", exc, RETRY_DELAY
                )
                await asyncio.sleep(RETRY_DELAY)
                continue
            for update in data.get("updates", []):
                try:
                    await bot.handle(update)
                except Exception:
                    log.exception("MAX: ошибка обработки события %s", update.get("update_type"))
            marker = data.get("marker", marker)
    finally:
        await api.close()
        await tg_bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
