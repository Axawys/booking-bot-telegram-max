"""Админка: закрытие и открытие времени для записи.

По умолчанию открыты все рабочие часы в пределах горизонта записи. Админ
закрывает исключения: диапазоны дат, отдельные дни или отдельное время.
Уже сделанные записи не трогаются; новые записи на закрытое время запрещает
триггер в БД (см. db.models.ClosedSlot).
"""

import calendar
from datetime import date, datetime, time, timedelta

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from core import schedule, texts
from core.config import settings
from core.parsing import ParseError, parse_period
from db import repo
from telegram_bot import keyboards as kb
from telegram_bot.callbacks import AdminCalAction, AdminCalCb, AdminDayAction, AdminDayCb
from telegram_bot.filters import AccessibleMessage, IsAdmin
from telegram_bot.utils import safe_edit

router = Router(name="admin_schedule")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin(), AccessibleMessage())

# Насколько вперёд админ может листать календарь (клиенты видят меньше).
CALENDAR_MONTHS_AHEAD = 12


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min)
    return start, start + timedelta(days=1)


# --- Календарь и день -----------------------------------------------------


async def _calendar(session: AsyncSession, year: int, month: int) -> InlineKeyboardMarkup:
    today = schedule.now_local().date()
    month_start = date(year, month, 1)
    month_end = date(year, month, calendar.monthrange(year, month)[1])
    first = max(today, month_start)

    states: dict[date, str] = {}
    if first <= month_end:
        closed = await repo.get_closed_slots_between(
            session, datetime.combine(first, time.min), _day_bounds(month_end)[1]
        )
        for day in schedule.iter_days(first, month_end):
            states[day] = schedule.day_state(day, closed)[0]

    months_from_now = (year - today.year) * 12 + (month - today.month)
    return kb.admin_calendar_kb(
        year,
        month,
        states,
        can_prev=months_from_now > 0,
        can_next=months_from_now < CALENDAR_MONTHS_AHEAD,
    )


async def _day_view(session: AsyncSession, day: date) -> tuple[str, InlineKeyboardMarkup]:
    start, end = _day_bounds(day)
    busy = await repo.get_busy_slots_between(session, start, end)
    closed = await repo.get_closed_slots_between(session, start, end)
    bookings = await repo.get_bookings_between(session, start, end)

    slots = []
    for slot in schedule.day_slot_starts(day):
        if slot in busy:
            state = texts.SLOT_BOOKED
        elif slot in closed:
            state = texts.SLOT_CLOSED
        else:
            state = texts.SLOT_OPEN
        slots.append((slot, state))

    work = schedule.day_slot_starts(day)
    state, closed_ranges = schedule.day_state(day, closed)
    text = texts.admin_day_view(day, texts.day_closure(state, closed_ranges), len(bookings))
    markup = kb.admin_day_kb(
        day,
        slots,
        # Закрытое время может быть «под» записью (👤): кнопки считаем по всем слотам.
        can_close=any(slot not in closed for slot in work),
        can_open=bool(closed),
    )
    return text, markup


@router.message(Command("schedule"))
async def cmd_schedule(message: Message, session: AsyncSession) -> None:
    today = schedule.now_local().date()
    await message.answer(
        texts.ADMIN_CALENDAR, reply_markup=await _calendar(session, today.year, today.month)
    )


@router.callback_query(AdminCalCb.filter(F.action == AdminCalAction.NAV))
async def calendar_nav(
    callback: CallbackQuery, callback_data: AdminCalCb, msg: Message, session: AsyncSession
) -> None:
    markup = await _calendar(session, callback_data.year, callback_data.month)
    await safe_edit(msg, texts.ADMIN_CALENDAR, markup)
    await callback.answer()


@router.callback_query(AdminCalCb.filter(F.action == AdminCalAction.DAY))
async def open_day_view(
    callback: CallbackQuery, callback_data: AdminCalCb, msg: Message, session: AsyncSession
) -> None:
    day = date(callback_data.year, callback_data.month, callback_data.day)
    await safe_edit(msg, *await _day_view(session, day))
    await callback.answer()


@router.callback_query(AdminDayCb.filter())
async def day_action(
    callback: CallbackQuery, callback_data: AdminDayCb, msg: Message, session: AsyncSession
) -> None:
    day = date(callback_data.year, callback_data.month, callback_data.day)
    slot = datetime.combine(day, time(callback_data.hour, callback_data.minute))

    match callback_data.action:
        case AdminDayAction.BOOKED:
            booking = await repo.get_slot_booking(session, slot)
            if booking is not None:
                await callback.answer(texts.admin_slot_booking_alert(booking), show_alert=True)
                return
            # Запись успели отменить — слот теперь свободен; просто обновим вид.
            alert = None
        case AdminDayAction.TOGGLE:
            closed = await repo.toggle_closed_slot(session, slot)
            alert = texts.admin_slot_toggled(slot, closed)
        case AdminDayAction.CLOSE_DAY:
            await repo.close_slots(session, schedule.full_day_slots(day))
            alert = texts.BTN_ADMIN_CLOSE_DAY
        case AdminDayAction.OPEN_DAY:
            await repo.open_slots_between(session, *_day_bounds(day))
            alert = texts.BTN_ADMIN_OPEN_DAY

    await safe_edit(msg, *await _day_view(session, day))
    await callback.answer(alert)


# --- Команды /close и /open -----------------------------------------------


def _period_slots(start: date, end: date, window: tuple[time, time] | None) -> list[datetime]:
    slots: list[datetime] = []
    for day in schedule.iter_days(start, end):
        slots += schedule.window_slots(day, *window) if window else schedule.full_day_slots(day)
    return slots


@router.message(Command("close"))
async def cmd_close(message: Message, command: CommandObject, session: AsyncSession) -> None:
    try:
        start, end, window = parse_period(command.args or "", schedule.now_local().date())
    except ParseError as exc:
        await message.answer(texts.admin_period_usage("close", str(exc) if command.args else None))
        return

    slots = _period_slots(start, end, window)
    await repo.close_slots(session, slots)

    # Сохранённые записи, которые попадают в закрытое время.
    closed = set(slots)
    period_start, period_end = datetime.combine(start, time.min), _day_bounds(end)[1]
    kept = [
        b
        for b in await repo.get_bookings_between(session, period_start, period_end)
        if any(s in closed for s in schedule.iter_slot_starts(b.starts_at, b.ends_at))
    ]
    await message.answer(
        texts.admin_period_closed(texts.format_period(start, end, window), kept, command.args)
    )


@router.message(Command("open"))
async def cmd_open(message: Message, command: CommandObject, session: AsyncSession) -> None:
    try:
        start, end, window = parse_period(command.args or "", schedule.now_local().date())
    except ParseError as exc:
        await message.answer(texts.admin_period_usage("open", str(exc) if command.args else None))
        return

    if window:
        await repo.open_slots(session, _period_slots(start, end, window))
    else:
        await repo.open_slots_between(
            session, datetime.combine(start, time.min), _day_bounds(end)[1]
        )
    await message.answer(
        texts.admin_period_opened(
            texts.format_period(start, end, window), settings.booking_horizon_days
        )
    )
