"""Кому и через какой мессенджер отправить уведомление.

Админы — всегда в Telegram (ADMIN_IDS). Клиент получает сообщение в том
мессенджере, через который записался: Telegram или MAX. Поэтому заявка из MAX
приходит админу карточкой в Telegram, а решение админа доходит до клиента MAX.

Ошибки доставки не роняют обработчик: админ мог ещё не запустить бота,
а клиент — заблокировать его. Такие случаи только логируются.
"""

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup

from core import texts
from core.config import settings
from db.models import Booking, BookingStatus, Client, Platform
from max_bot import keyboards as max_kb
from max_bot.api import Keyboard, MaxApi, MaxApiError
from telegram_bot import keyboards as tg_kb

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, tg_bot: Bot | None, max_api: MaxApi | None = None) -> None:
        self.tg_bot = tg_bot
        self.max_api = max_api

    # --- Доставка ---------------------------------------------------------

    async def telegram(
        self, chat_id: int, text: str, markup: InlineKeyboardMarkup | None = None
    ) -> bool:
        if self.tg_bot is None:
            return False
        try:
            await self.tg_bot.send_message(chat_id, text, reply_markup=markup)
        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            log.warning("Telegram: не удалось написать в чат %s: %s", chat_id, exc)
            return False
        return True

    async def max(self, user_id: int, text: str, keyboard: Keyboard | None = None) -> bool:
        if self.max_api is None:
            log.warning("MAX: нет MAX_BOT_TOKEN, сообщение пользователю %s не отправлено", user_id)
            return False
        try:
            await self.max_api.send_message(user_id, text, keyboard)
        except (MaxApiError, OSError, TimeoutError) as exc:
            log.warning("MAX: не удалось написать пользователю %s: %s", user_id, exc)
            return False
        return True

    async def client(self, client: Client, text: str, cancel_booking_id: int | None = None) -> bool:
        """Сообщение клиенту в его мессенджер; опционально — с кнопкой отмены записи."""
        if client.platform == Platform.MAX:
            keyboard = max_kb.reminder(cancel_booking_id) if cancel_booking_id else None
            return await self.max(client.external_id, text, keyboard)
        markup = tg_kb.reminder_kb(cancel_booking_id) if cancel_booking_id else None
        return await self.telegram(client.external_id, text, markup)

    async def admins(self, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
        for admin_id in settings.admin_ids:
            await self.telegram(admin_id, text, markup)

    # --- События (booking загружена с client и service: repo.get_booking) --

    async def new_booking(self, booking: Booking) -> None:
        if booking.status == BookingStatus.PENDING:
            title, markup = texts.ADMIN_TITLE_NEW, tg_kb.admin_decision_kb(booking.id)
        else:
            title, markup = texts.ADMIN_TITLE_NEW_CONFIRMED, None
        await self.admins(texts.admin_booking_card(booking, title), markup)

    async def cancelled_by_client(self, booking: Booking) -> None:
        await self.admins(texts.admin_booking_card(booking, texts.ADMIN_TITLE_CANCELLED))

    async def decision(self, booking: Booking) -> None:
        if booking.status == BookingStatus.CONFIRMED:
            await self.client(booking.client, texts.client_booking_confirmed(booking))
        else:
            await self.client(booking.client, texts.client_booking_rejected(booking))

    async def reminder(self, booking: Booking) -> bool:
        return await self.client(
            booking.client, texts.client_reminder(booking), cancel_booking_id=booking.id
        )

    async def expired(self, booking: Booking) -> None:
        await self.client(booking.client, texts.client_booking_expired(booking))
        await self.admins(texts.admin_booking_card(booking, texts.ADMIN_TITLE_EXPIRED))
