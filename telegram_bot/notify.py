"""Уведомления администраторам и клиентам.

Ошибки доставки не роняют обработчик: админ мог ещё не запустить бота,
а клиент — заблокировать его. Такие случаи только логируются.
"""

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup

from core import texts
from core.config import settings
from db.models import Booking, BookingStatus, Platform
from telegram_bot import keyboards as kb

log = logging.getLogger(__name__)


async def send(bot: Bot, chat_id: int, text: str, markup: InlineKeyboardMarkup | None = None) -> bool:
    try:
        await bot.send_message(chat_id, text, reply_markup=markup)
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        log.warning("Не удалось отправить сообщение в чат %s: %s", chat_id, exc)
        return False
    return True


async def notify_admins(bot: Bot, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    for admin_id in settings.admin_ids:
        await send(bot, admin_id, text, markup)


async def notify_admins_new_booking(bot: Bot, booking: Booking) -> None:
    """Booking должна быть загружена с client и service (repo.get_booking)."""
    if booking.status == BookingStatus.PENDING:
        title, markup = texts.ADMIN_TITLE_NEW, kb.admin_decision_kb(booking.id)
    else:
        title, markup = texts.ADMIN_TITLE_NEW_CONFIRMED, None
    text = texts.admin_booking_card(booking, title)
    for admin_id in settings.admin_ids:
        await send(bot, admin_id, text, markup)


async def notify_admins_cancelled(bot: Bot, booking: Booking) -> None:
    text = texts.admin_booking_card(booking, texts.ADMIN_TITLE_CANCELLED)
    for admin_id in settings.admin_ids:
        await send(bot, admin_id, text)


async def notify_client_decision(bot: Bot, booking: Booking) -> None:
    if booking.client.platform != Platform.TELEGRAM:
        return  # клиентов MAX уведомит MAX-бот
    if booking.status == BookingStatus.CONFIRMED:
        text = texts.client_booking_confirmed(booking)
    else:
        text = texts.client_booking_rejected(booking)
    await send(bot, booking.client.external_id, text)
