"""«Мои записи»: список предстоящих записей и отмена с подтверждением.

Кнопки не зависят от FSM: id записи лежит в callback_data, а права проверяются
в БД — отменить можно только свою, подтверждённую и ещё не начавшуюся запись.
"""

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from core import schedule, texts
from core.config import settings
from db import repo
from db.models import Platform
from notifications.notifier import Notifier
from notifications.scheduler import BookingScheduler
from telegram_bot import keyboards as kb
from telegram_bot.callbacks import MyBookingAction, MyBookingCb
from telegram_bot.filters import AccessibleMessage
from telegram_bot.utils import safe_edit

router = Router(name="my_bookings")
router.callback_query.filter(AccessibleMessage())


async def _client_id(session: AsyncSession, user_id: int) -> int | None:
    client = await repo.get_client(session, Platform.TELEGRAM, user_id)
    return client.id if client else None


async def _render_list(
    session: AsyncSession, user_id: int, notice: str | None = None
) -> tuple[str, InlineKeyboardMarkup | None]:
    client_id = await _client_id(session, user_id)
    bookings = (
        await repo.get_upcoming_bookings(session, client_id, schedule.now_local())
        if client_id
        else []
    )
    markup = kb.my_bookings_kb(bookings) if bookings else None
    return texts.my_bookings_list(bookings, notice), markup


@router.message(F.text == texts.BTN_MY_BOOKINGS)
async def show_my_bookings(message: Message, session: AsyncSession) -> None:
    text, markup = await _render_list(session, message.from_user.id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(MyBookingCb.filter(F.action == MyBookingAction.LIST))
async def back_to_list(callback: CallbackQuery, msg: Message, session: AsyncSession) -> None:
    await safe_edit(msg, *await _render_list(session, callback.from_user.id))
    await callback.answer()


@router.callback_query(MyBookingCb.filter(F.action == MyBookingAction.ASK))
async def ask_cancel(
    callback: CallbackQuery, callback_data: MyBookingCb, msg: Message, session: AsyncSession
) -> None:
    client_id = await _client_id(session, callback.from_user.id)
    booking = (
        await repo.get_upcoming_booking(
            session, callback_data.booking_id, client_id, schedule.now_local()
        )
        if client_id
        else None
    )
    if booking is None:
        await callback.answer(texts.MY_BOOKING_NOT_FOUND_ALERT, show_alert=True)
        await safe_edit(msg, *await _render_list(session, callback.from_user.id))
        return
    await safe_edit(msg, texts.my_booking_cancel_ask(booking), kb.my_booking_cancel_kb(booking.id))
    await callback.answer()


@router.callback_query(MyBookingCb.filter(F.action == MyBookingAction.CANCEL))
async def do_cancel(
    callback: CallbackQuery,
    callback_data: MyBookingCb,
    msg: Message,
    session: AsyncSession,
    notifier: Notifier,
    scheduler: BookingScheduler,
) -> None:
    user_id = callback.from_user.id
    client_id = await _client_id(session, user_id)
    now = schedule.now_local()
    booking = (
        await repo.get_upcoming_booking(session, callback_data.booking_id, client_id, now)
        if client_id
        else None
    )
    # Текст уведомления готовим до commit внутри cancel_booking.
    notice = texts.my_booking_cancelled(booking) if booking else None

    if booking is None or not await repo.cancel_booking(
        session, callback_data.booking_id, client_id, now
    ):
        await callback.answer(texts.MY_BOOKING_NOT_FOUND_ALERT, show_alert=True)
        await safe_edit(msg, *await _render_list(session, user_id))
        return

    await callback.answer(texts.MY_BOOKING_CANCELLED_ALERT)
    await safe_edit(msg, *await _render_list(session, user_id, notice))
    await scheduler.sync_booking(callback_data.booking_id)
    if settings.admin_ids:
        cancelled = await repo.get_booking(session, callback_data.booking_id)
        await notifier.cancelled_by_client(cancelled)
