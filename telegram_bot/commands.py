"""Меню команд Telegram (кнопка «/» в поле ввода)."""

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

from core.config import settings

log = logging.getLogger(__name__)

USER_COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="cancel", description="Прервать оформление записи"),
]

ADMIN_COMMANDS = [
    BotCommand(command="admin", description="Панель администратора"),
    BotCommand(command="pending", description="Заявки на подтверждение"),
    BotCommand(command="today", description="Расписание на сегодня"),
    BotCommand(command="tomorrow", description="Расписание на завтра"),
    BotCommand(command="day", description="Расписание на дату: /day ДД.ММ"),
    BotCommand(command="stats", description="Статистика"),
    BotCommand(command="schedule", description="Доступное время: календарь"),
    BotCommand(command="close", description="Закрыть даты: /close ДД.ММ-ДД.ММ"),
    BotCommand(command="open", description="Открыть даты: /open ДД.ММ-ДД.ММ"),
    BotCommand(command="horizon", description="На сколько дней вперёд открыта запись"),
    BotCommand(command="ban", description="Забанить: /ban ID причина"),
    BotCommand(command="unban", description="Разбанить: /unban ID"),
    BotCommand(command="banned", description="Список забаненных"),
]


async def setup_commands(bot: Bot) -> None:
    await bot.set_my_commands(USER_COMMANDS, scope=BotCommandScopeDefault())
    for admin_id in settings.admin_ids:
        try:
            await bot.set_my_commands(
                USER_COMMANDS + ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id)
            )
        except TelegramBadRequest as exc:
            # Чат ещё не существует: админ не нажимал /start у бота.
            log.warning("Не удалось задать команды админу %s: %s", admin_id, exc)
