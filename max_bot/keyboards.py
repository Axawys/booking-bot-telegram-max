"""Inline-клавиатуры MAX. В MAX нет reply-клавиатуры, поэтому главное меню —
тоже inline-кнопки под сообщением.

Payload кнопок — короткие строки вида «svc:3», «cal:day:2026:11:5».
"""

import calendar
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from typing import Any

from core import texts
from core.booking_service import MonthView
from db.models import Booking, Service
from max_bot.api import Keyboard

NOOP = "noop"
# Пустая ячейка календаря: у кнопки MAX текст обязателен и не может быть пробелом.
_BLANK = "⠀"

BTN_MAIN_MENU = "☰ Главное меню"


def cb(text: str, payload: str) -> dict[str, Any]:
    return {"type": "callback", "text": text, "payload": payload}


def _nav_row(back: bool = True) -> list[dict[str, Any]]:
    row = [cb(texts.BTN_CANCEL_INLINE, "nav:cancel")]
    return [cb(texts.BTN_BACK, "nav:back"), *row] if back else row


def main_menu() -> Keyboard:
    return [
        [cb(texts.BTN_BOOK, "menu:book"), cb(texts.BTN_MY_BOOKINGS, "menu:my")],
        [cb(texts.BTN_SERVICES, "menu:services"), cb(texts.BTN_CONTACTS, "menu:contacts")],
    ]


def back_to_menu() -> Keyboard:
    return [[cb(BTN_MAIN_MENU, "menu")]]


def services(items: Iterable[Service]) -> Keyboard:
    rows = [
        [cb(f"{s.name} · {texts.format_duration(s.duration_minutes)}", f"svc:{s.id}")]
        for s in items
    ]
    return [*rows, _nav_row(back=False)]


def month(view: MonthView) -> Keyboard:
    y, m = view.year, view.month

    def nav(text: str, delta: int) -> dict[str, Any]:
        ny, nm = divmod(y * 12 + (m - 1) + delta, 12)
        return cb(text, f"cal:nav:{ny}:{nm + 1}")

    rows: Keyboard = [
        [
            nav("◀", -1) if view.can_prev else cb(_BLANK, NOOP),
            cb(f"{texts.MONTHS[m - 1]} {y}", NOOP),
            nav("▶", 1) if view.can_next else cb(_BLANK, NOOP),
        ],
        [cb(name, NOOP) for name in texts.WEEKDAYS_SHORT],
    ]
    for week in calendar.Calendar().monthdayscalendar(y, m):
        row = []
        for d in week:
            if d == 0:
                row.append(cb(_BLANK, NOOP))
            elif date(y, m, d) in view.available:
                row.append(cb(str(d), f"cal:day:{y}:{m}:{d}"))
            else:
                row.append(cb("·", NOOP))
        rows.append(row)
    rows.append(_nav_row())
    return rows


def slots(starts: Sequence[datetime]) -> Keyboard:
    buttons = [cb(f"{s:%H:%M}", f"slot:{s.hour}:{s.minute}") for s in starts]
    rows = [buttons[i : i + 4] for i in range(0, len(buttons), 4)]
    return [*rows, _nav_row()]


def phone(saved_phone: str | None) -> Keyboard:
    rows: Keyboard = [[{"type": "request_contact", "text": texts.BTN_SEND_PHONE}]]
    if saved_phone:
        rows.append([cb(texts.btn_use_saved_phone(saved_phone), "phone:saved")])
    rows.append(_nav_row(back=False))
    return rows


def confirm() -> Keyboard:
    return [
        [cb(texts.BTN_CONFIRM, "nav:confirm")],
        [cb(texts.BTN_CHANGE_TIME, "nav:back"), cb(texts.BTN_CANCEL_INLINE, "nav:cancel")],
    ]


def my_bookings(bookings: Sequence[Booking]) -> Keyboard:
    rows = [[cb(texts.btn_cancel_booking(b), f"my:ask:{b.id}")] for b in bookings]
    return [*rows, [cb(BTN_MAIN_MENU, "menu")]]


def cancel_ask(booking_id: int) -> Keyboard:
    return [
        [cb(texts.BTN_CANCEL_BOOKING_YES, f"my:cancel:{booking_id}")],
        [cb(texts.BTN_KEEP_BOOKING, "my:list")],
    ]


def reminder(booking_id: int) -> Keyboard:
    """Кнопка в напоминании ведёт в тот же сценарий отмены, что и «Мои записи»."""
    return [[cb(texts.BTN_REMINDER_CANCEL, f"my:ask:{booking_id}")]]
