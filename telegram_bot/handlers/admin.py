"""Админка: команды и кнопки подтверждения заявок. Доступно только ADMIN_IDS."""

import re
from datetime import date, datetime, time, timedelta

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from core import schedule, texts
from db import repo
from telegram_bot import keyboards as kb
from telegram_bot import notify
from telegram_bot.callbacks import AdminAction, AdminBookingCb
from telegram_bot.filters import AccessibleMessage, IsAdmin
from telegram_bot.utils import safe_edit

router = Router(name="admin")
# Для остальных пользователей роутер «невидим»: апдейт уходит дальше по цепочке.
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

_DAY_ARG = re.compile(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?$")


# --- Команды --------------------------------------------------------------


@router.message(Command("admin"))
async def cmd_admin(message: Message, session: AsyncSession) -> None:
    pending = await repo.count_pending_bookings(session, schedule.now_local())
    await message.answer(f"{texts.ADMIN_HELP}\n\n{texts.admin_pending_count(pending)}")


@router.message(Command("pending"))
async def cmd_pending(message: Message, session: AsyncSession) -> None:
    bookings = await repo.get_pending_bookings(session, schedule.now_local())
    if not bookings:
        await message.answer(texts.ADMIN_NO_PENDING)
        return
    # Каждая заявка — отдельной карточкой со своими кнопками.
    for booking in bookings:
        await message.answer(
            texts.admin_booking_card(booking, texts.ADMIN_TITLE_PENDING),
            reply_markup=kb.admin_decision_kb(booking.id),
        )


async def _send_day(message: Message, session: AsyncSession, day: date) -> None:
    start = datetime.combine(day, time.min)
    end = start + timedelta(days=1)
    bookings = await repo.get_bookings_between(session, start, end)
    closed = await repo.get_closed_slots_between(session, start, end)
    closure = texts.day_closure(*schedule.day_state(day, closed))
    await message.answer(texts.admin_day_schedule(day, bookings, closure))


@router.message(Command("today"))
async def cmd_today(message: Message, session: AsyncSession) -> None:
    await _send_day(message, session, schedule.now_local().date())


@router.message(Command("tomorrow"))
async def cmd_tomorrow(message: Message, session: AsyncSession) -> None:
    await _send_day(message, session, schedule.now_local().date() + timedelta(days=1))


@router.message(Command("day"))
async def cmd_day(message: Message, command: CommandObject, session: AsyncSession) -> None:
    match = _DAY_ARG.match((command.args or "").strip())
    if not match:
        await message.answer(texts.ADMIN_DAY_USAGE)
        return
    day_, month, year = match.groups()
    try:
        day = date(int(year or schedule.now_local().year), int(month), int(day_))
    except ValueError:
        await message.answer(texts.ADMIN_DAY_USAGE)
        return
    await _send_day(message, session, day)


@router.message(Command("stats"))
async def cmd_stats(message: Message, session: AsyncSession) -> None:
    now = schedule.now_local()
    today = datetime.combine(now.date(), time.min)
    tomorrow = today + timedelta(days=1)
    await message.answer(
        texts.admin_stats(
            today=await repo.booking_stats(session, today, tomorrow),
            week=await repo.booking_stats(session, now, now + timedelta(days=7)),
            month=await repo.booking_stats(session, now - timedelta(days=30), now),
            clients=await repo.count_clients(session),
        )
    )


# --- Подтверждение заявок -------------------------------------------------


@router.callback_query(AdminBookingCb.filter(), AccessibleMessage())
async def decide(
    callback: CallbackQuery,
    callback_data: AdminBookingCb,
    msg: Message,
    session: AsyncSession,
    bot: Bot,
) -> None:
    approve = callback_data.action == AdminAction.CONFIRM
    booking = await repo.decide_booking(session, callback_data.booking_id, approve)

    if booking is None:
        # Решение уже принято (другим админом или раньше) либо запись отменил клиент.
        current = await repo.get_booking(session, callback_data.booking_id)
        if current is None:
            await callback.answer(texts.ADMIN_NOT_FOUND_ALERT, show_alert=True)
            await safe_edit(msg, msg.html_text)
            return
        await callback.answer(texts.ADMIN_ALREADY_DECIDED_ALERT, show_alert=True)
        await safe_edit(msg, texts.admin_booking_card(current, texts.ADMIN_TITLE_PENDING))
        return

    await safe_edit(
        msg,
        texts.admin_booking_card(booking, texts.ADMIN_TITLE_PENDING, callback.from_user.full_name),
    )
    await notify.notify_client_decision(bot, booking)
    await callback.answer(texts.ADMIN_CONFIRMED_ALERT if approve else texts.ADMIN_REJECTED_ALERT)
