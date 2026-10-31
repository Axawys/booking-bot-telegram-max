"""Сценарий записи: услуга → дата → время → телефон → подтверждение.

Свободность времени проверяется в коде только ради удобства (не показывать
занятое). Гарантию от двойной записи даёт уникальный индекс booking_slots в БД:
если два клиента подтвердят одно время одновременно, второй получит
SlotAlreadyBookedError и вернётся к выбору времени.
"""

import calendar
from datetime import date, datetime, time, timedelta

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from sqlalchemy.ext.asyncio import AsyncSession

from core import schedule, texts
from core.config import settings
from core.phone import normalize_phone
from db import repo
from db.models import BookingStatus, Platform
from telegram_bot import keyboards as kb
from telegram_bot import notify
from telegram_bot.callbacks import (
    NOOP,
    CalendarAction,
    CalendarCb,
    NavAction,
    NavCb,
    ServiceCb,
    SlotCb,
)
from telegram_bot.filters import AccessibleMessage
from telegram_bot.states import BookingForm
from telegram_bot.utils import safe_edit

router = Router(name="booking")
router.callback_query.filter(AccessibleMessage())


# --- Вспомогательные функции ----------------------------------------------


def _details(data: dict) -> dict:
    return {
        "service_name": data["service_name"],
        "price": data["price"],
        "duration": data["duration"],
        "starts_at": datetime.fromisoformat(data["starts_at"]),
        "phone": data["phone"],
    }


async def _show_services(msg: Message, state: FSMContext, session: AsyncSession, *, edit: bool) -> None:
    services = await repo.get_active_services(session)
    if not services:
        await state.clear()
        await (safe_edit(msg, texts.BOOK_NO_SERVICES) if edit else msg.answer(texts.BOOK_NO_SERVICES))
        return
    await state.set_state(BookingForm.service)
    markup = kb.services_kb(services)
    if edit:
        await safe_edit(msg, texts.BOOK_CHOOSE_SERVICE, markup)
    else:
        await msg.answer(texts.BOOK_CHOOSE_SERVICE, reply_markup=markup)


async def _show_calendar(
    msg: Message, state: FSMContext, session: AsyncSession, year: int | None = None, month: int | None = None
) -> None:
    data = await state.get_data()
    now = schedule.now_local()
    first, last = schedule.bookable_range(now)
    if year is None or month is None:
        year, month = first.year, first.month

    month_start = date(year, month, 1)
    month_end = date(year, month, calendar.monthrange(year, month)[1])
    lo, hi = max(first, month_start), min(last, month_end)

    available: set[date] = set()
    if lo <= hi:
        # Один запрос на весь видимый диапазон вместо запроса на каждый день.
        busy = await repo.get_busy_slots_between(
            session,
            datetime.combine(lo, time.min),
            datetime.combine(hi + timedelta(days=1), time.min),
        )
        day = lo
        while day <= hi:
            if schedule.available_starts(day, data["duration"], busy, now):
                available.add(day)
            day += timedelta(days=1)

    await state.set_state(BookingForm.date)
    await safe_edit(
        msg,
        texts.book_choose_date(data["service_name"]),
        kb.calendar_kb(
            year,
            month,
            available,
            can_prev=(year, month) > (first.year, first.month),
            can_next=(year, month) < (last.year, last.month),
        ),
    )


async def _show_times(
    msg: Message, state: FSMContext, session: AsyncSession, day: date, notice: str | None = None
) -> bool:
    """Показать свободное время на день. False — если свободного времени нет."""
    data = await state.get_data()
    busy = await repo.get_busy_slots(session, day)
    starts = schedule.available_starts(day, data["duration"], busy, schedule.now_local())
    if not starts:
        return False
    await state.update_data(day=day.isoformat())
    await state.set_state(BookingForm.time)
    await safe_edit(msg, texts.book_choose_time(data["service_name"], day, notice), kb.slots_kb(starts))
    return True


async def _back_to_times(
    msg: Message, state: FSMContext, session: AsyncSession, notice: str | None = None
) -> None:
    """К выбору времени на ранее выбранный день, а если оно кончилось — к календарю."""
    data = await state.get_data()
    day = date.fromisoformat(data["day"])
    if not await _show_times(msg, state, session, day, notice):
        await _show_calendar(msg, state, session, day.year, day.month)


async def _show_summary(msg: Message, state: FSMContext, *, edit: bool) -> None:
    data = await state.get_data()
    await state.set_state(BookingForm.confirm)
    text = texts.book_summary(**_details(data))
    if edit:
        await safe_edit(msg, text, kb.confirm_kb())
    else:
        await msg.answer(text, reply_markup=kb.confirm_kb())


# --- Старт и отмена -------------------------------------------------------


@router.message(F.text == texts.BTN_BOOK)
async def start_booking(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await _show_services(message, state, session, edit=False)


@router.message(Command("cancel"))
@router.message(F.text == texts.BTN_CANCEL)
async def cancel_by_text(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(texts.BOOK_CANCELLED, reply_markup=kb.main_menu)


@router.callback_query(NavCb.filter(F.action == NavAction.CANCEL))
async def cancel_by_button(callback: CallbackQuery, msg: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    await safe_edit(msg, texts.BOOK_CANCELLED)
    # На шаге телефона главное меню было заменено — вернуть его.
    if data.get("menu_hidden"):
        await msg.answer(texts.BACK_TO_MENU, reply_markup=kb.main_menu)
    await callback.answer()


@router.callback_query(F.data == NOOP)
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()


# --- Шаг 1: услуга --------------------------------------------------------


@router.callback_query(BookingForm.service, ServiceCb.filter())
async def pick_service(
    callback: CallbackQuery,
    callback_data: ServiceCb,
    msg: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    service = await repo.get_service(session, callback_data.id)
    if service is None or not service.is_active:
        await callback.answer(texts.BOOK_ERROR, show_alert=True)
        await _show_services(msg, state, session, edit=True)
        return
    await state.update_data(
        service_id=service.id,
        service_name=service.name,
        price=service.price,
        duration=service.duration_minutes,
    )
    await _show_calendar(msg, state, session)
    await callback.answer()


# --- Шаг 2: дата ----------------------------------------------------------


@router.callback_query(BookingForm.date, CalendarCb.filter(F.action == CalendarAction.NAV))
async def calendar_nav(
    callback: CallbackQuery,
    callback_data: CalendarCb,
    msg: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    await _show_calendar(msg, state, session, callback_data.year, callback_data.month)
    await callback.answer()


@router.callback_query(BookingForm.date, CalendarCb.filter(F.action == CalendarAction.DAY))
async def pick_day(
    callback: CallbackQuery,
    callback_data: CalendarCb,
    msg: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    day = date(callback_data.year, callback_data.month, callback_data.day)
    if not schedule.is_bookable_day(day, schedule.now_local()):
        await callback.answer(texts.BOOK_DAY_UNAVAILABLE_ALERT, show_alert=True)
        await _show_calendar(msg, state, session)
        return
    if not await _show_times(msg, state, session, day):
        # Пока календарь был открыт, день успели занять — обновим его.
        await callback.answer(texts.BOOK_NO_SLOTS_ALERT, show_alert=True)
        await _show_calendar(msg, state, session, day.year, day.month)
        return
    await callback.answer()


@router.callback_query(BookingForm.date, NavCb.filter(F.action == NavAction.BACK))
async def back_to_services(
    callback: CallbackQuery, msg: Message, state: FSMContext, session: AsyncSession
) -> None:
    await _show_services(msg, state, session, edit=True)
    await callback.answer()


# --- Шаг 3: время ---------------------------------------------------------


@router.callback_query(BookingForm.time, SlotCb.filter())
async def pick_slot(
    callback: CallbackQuery,
    callback_data: SlotCb,
    msg: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    data = await state.get_data()
    day = date.fromisoformat(data["day"])
    starts_at = datetime.combine(day, time(callback_data.hour, callback_data.minute))

    busy = await repo.get_busy_slots(session, day)
    if starts_at not in schedule.available_starts(day, data["duration"], busy, schedule.now_local()):
        await callback.answer(texts.BOOK_SLOT_GONE_ALERT, show_alert=True)
        await _back_to_times(msg, state, session)
        return

    await state.update_data(starts_at=starts_at.isoformat())
    await callback.answer()

    # Телефон уже указан в этой записи (клиент вернулся «Изменить время»).
    if data.get("phone"):
        await _show_summary(msg, state, edit=True)
        return

    await safe_edit(msg, texts.book_picked(data["service_name"], starts_at))
    client = await repo.get_client(session, Platform.TELEGRAM, callback.from_user.id)
    saved_phone = client.phone if client else None
    await state.update_data(saved_phone=saved_phone, menu_hidden=True)
    await state.set_state(BookingForm.phone)
    await msg.answer(texts.BOOK_ASK_PHONE, reply_markup=kb.phone_kb(saved_phone))


@router.callback_query(BookingForm.time, NavCb.filter(F.action == NavAction.BACK))
async def back_to_calendar(
    callback: CallbackQuery, msg: Message, state: FSMContext, session: AsyncSession
) -> None:
    day = date.fromisoformat((await state.get_data())["day"])
    await _show_calendar(msg, state, session, day.year, day.month)
    await callback.answer()


# --- Шаг 4: телефон -------------------------------------------------------


async def _accept_phone(message: Message, state: FSMContext, phone: str) -> None:
    await state.update_data(phone=phone)
    # Убрать клавиатуру с кнопкой «Отправить номер» и показать итог записи.
    await message.answer(texts.BOOK_PHONE_ACCEPTED, reply_markup=ReplyKeyboardRemove())
    await _show_summary(message, state, edit=False)


@router.message(BookingForm.phone, F.contact)
async def phone_from_contact(message: Message, state: FSMContext) -> None:
    phone = normalize_phone(message.contact.phone_number)
    if phone is None:
        await message.answer(texts.BOOK_PHONE_INVALID)
        return
    await _accept_phone(message, state, phone)


@router.message(BookingForm.phone, F.text)
async def phone_from_text(message: Message, state: FSMContext) -> None:
    saved_phone = (await state.get_data()).get("saved_phone")
    if saved_phone and message.text == texts.btn_use_saved_phone(saved_phone):
        phone = saved_phone
    else:
        phone = normalize_phone(message.text)
    if phone is None:
        await message.answer(texts.BOOK_PHONE_INVALID)
        return
    await _accept_phone(message, state, phone)


@router.message(BookingForm.phone)
async def phone_invalid(message: Message) -> None:
    await message.answer(texts.BOOK_PHONE_INVALID)


# --- Шаг 5: подтверждение -------------------------------------------------


@router.callback_query(BookingForm.confirm, NavCb.filter(F.action == NavAction.CONFIRM))
async def confirm(
    callback: CallbackQuery, msg: Message, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    data = await state.get_data()
    starts_at = datetime.fromisoformat(data["starts_at"])

    # Пока клиент думал, время могло стать слишком близким или прошедшим.
    earliest = schedule.now_local() + timedelta(minutes=settings.booking_min_lead_minutes)
    if starts_at < earliest:
        await callback.answer()
        await _back_to_times(msg, state, session, texts.BOOK_TOO_LATE)
        return

    user = callback.from_user
    client = await repo.upsert_client(session, Platform.TELEGRAM, user.id, user.full_name)
    client_id = client.id
    await repo.set_client_phone(session, client_id, data["phone"])

    # Если есть администраторы — заявка ждёт их подтверждения, но слоты
    # занимаются сразу: время уже не достанется другому клиенту.
    status = BookingStatus.PENDING if settings.requires_confirmation else BookingStatus.CONFIRMED
    try:
        booking = await repo.create_booking(
            session, client_id, data["service_id"], starts_at, settings.slot_minutes, status
        )
    except repo.SlotAlreadyBookedError:
        # Сработал уникальный индекс: кто-то успел занять это время раньше.
        await callback.answer()
        await _back_to_times(msg, state, session, texts.BOOK_SLOT_TAKEN)
        return
    except ValueError:
        await state.clear()
        await callback.answer()
        await safe_edit(msg, texts.BOOK_ERROR)
        await msg.answer(texts.BACK_TO_MENU, reply_markup=kb.main_menu)
        return

    booking_id = booking.id
    await state.clear()
    await callback.answer("✅ Готово!")
    if status == BookingStatus.PENDING:
        await safe_edit(msg, texts.book_pending(**_details(data)))
    else:
        await safe_edit(msg, texts.book_success(**_details(data)))
    await msg.answer(texts.BACK_TO_MENU, reply_markup=kb.main_menu)

    if settings.admin_ids:
        await notify.notify_admins_new_booking(bot, await repo.get_booking(session, booking_id))


@router.callback_query(BookingForm.confirm, NavCb.filter(F.action == NavAction.BACK))
async def change_time(
    callback: CallbackQuery, msg: Message, state: FSMContext, session: AsyncSession
) -> None:
    await _back_to_times(msg, state, session)
    await callback.answer()
