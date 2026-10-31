import calendar
from collections.abc import Iterable
from datetime import date, datetime

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core import texts
from db.models import Service
from telegram_bot.callbacks import (
    NOOP,
    CalendarAction,
    CalendarCb,
    NavAction,
    NavCb,
    ServiceCb,
    SlotCb,
)

main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=texts.BTN_BOOK), KeyboardButton(text=texts.BTN_MY_BOOKINGS)],
        [KeyboardButton(text=texts.BTN_SERVICES), KeyboardButton(text=texts.BTN_CONTACTS)],
    ],
    resize_keyboard=True,
    is_persistent=True,
    input_field_placeholder="Выберите пункт меню",
)


def _noop(text: str = " ") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=NOOP)


def _nav(text: str, action: NavAction) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=NavCb(action=action).pack())


def services_kb(services: Iterable[Service]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for s in services:
        builder.row(
            InlineKeyboardButton(
                text=f"{s.name} · {texts.rub(s.price)}",
                callback_data=ServiceCb(id=s.id).pack(),
            )
        )
    builder.row(_nav(texts.BTN_CANCEL_INLINE, NavAction.CANCEL))
    return builder.as_markup()


def calendar_kb(
    year: int, month: int, available: set[date], can_prev: bool, can_next: bool
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    def month_nav(text: str, delta: int) -> InlineKeyboardButton:
        y, m = divmod(year * 12 + (month - 1) + delta, 12)
        cb = CalendarCb(action=CalendarAction.NAV, year=y, month=m + 1)
        return InlineKeyboardButton(text=text, callback_data=cb.pack())

    builder.row(
        month_nav("◀", -1) if can_prev else _noop(),
        _noop(f"{texts.MONTHS[month - 1]} {year}"),
        month_nav("▶", 1) if can_next else _noop(),
    )
    builder.row(*(_noop(name) for name in texts.WEEKDAYS_SHORT))

    for week in calendar.Calendar().monthdayscalendar(year, month):
        row = []
        for day in week:
            if day == 0:
                row.append(_noop())
            elif date(year, month, day) in available:
                cb = CalendarCb(action=CalendarAction.DAY, year=year, month=month, day=day)
                row.append(InlineKeyboardButton(text=str(day), callback_data=cb.pack()))
            else:
                row.append(_noop("·"))
        builder.row(*row)

    builder.row(
        _nav(texts.BTN_BACK, NavAction.BACK),
        _nav(texts.BTN_CANCEL_INLINE, NavAction.CANCEL),
    )
    return builder.as_markup()


def slots_kb(starts: Iterable[datetime]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for start in starts:
        builder.button(
            text=f"{start:%H:%M}",
            callback_data=SlotCb(hour=start.hour, minute=start.minute),
        )
    builder.adjust(4)
    builder.row(
        _nav(texts.BTN_BACK, NavAction.BACK),
        _nav(texts.BTN_CANCEL_INLINE, NavAction.CANCEL),
    )
    return builder.as_markup()


def phone_kb(saved_phone: str | None) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=texts.BTN_SEND_PHONE, request_contact=True)]]
    if saved_phone:
        rows.append([KeyboardButton(text=texts.btn_use_saved_phone(saved_phone))])
    rows.append([KeyboardButton(text=texts.BTN_CANCEL)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        input_field_placeholder="+7 900 123-45-67",
    )


def confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_nav(texts.BTN_CONFIRM, NavAction.CONFIRM)],
            [
                _nav(texts.BTN_CHANGE_TIME, NavAction.BACK),
                _nav(texts.BTN_CANCEL_INLINE, NavAction.CANCEL),
            ],
        ]
    )
