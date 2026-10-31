"""Разбор дат и времени из команд администратора (общий для Telegram и MAX)."""

import re
from datetime import date, time, timedelta

_DATE = r"(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?"
_TIME = r"(\d{1,2}):(\d{2})"
_PERIOD = re.compile(
    rf"^{_DATE}(?:\s*[-–]\s*{_DATE})?(?:\s+{_TIME}\s*[-–]\s*{_TIME})?$"
)

MAX_RANGE_DAYS = 366


class ParseError(ValueError):
    pass


def _date(day: str, month: str, year: str | None, today: date) -> tuple[date, bool]:
    try:
        return date(int(year or today.year), int(month), int(day)), year is None
    except ValueError as exc:
        raise ParseError(f"Нет такой даты: {day}.{month}") from exc


def _time(hour: str, minute: str) -> time:
    try:
        return time(int(hour), int(minute))
    except ValueError as exc:
        raise ParseError(f"Нет такого времени: {hour}:{minute}") from exc


def parse_period(text: str, today: date) -> tuple[date, date, tuple[time, time] | None]:
    """«05.11», «05.11-10.11», «28.12-05.01», «05.11 14:00-16:00», «05.11.2026-07.11.2026».

    Дата без года считается ближайшей будущей: «05.01», набранное в ноябре, —
    это январь следующего года. Время — полуинтервал [с, до).
    """
    match = _PERIOD.match(text.strip())
    if not match:
        raise ParseError("Неверный формат")
    d1, m1, y1, d2, m2, y2, h1, min1, h2, min2 = match.groups()

    start, start_guessed = _date(d1, m1, y1, today)
    if start_guessed and start < today:
        start = start.replace(year=start.year + 1)
    end = start
    if d2:
        end, _ = _date(d2, m2, y2 or str(start.year), today)
        # «28.12-05.01» — диапазон через Новый год. А «10.11-05.11» — опечатка.
        if not y2 and end < start and end.month < start.month:
            end = end.replace(year=end.year + 1)
    if end < start:
        raise ParseError("Конец диапазона раньше начала")
    if (end - start) > timedelta(days=MAX_RANGE_DAYS):
        raise ParseError(f"Диапазон больше {MAX_RANGE_DAYS} дней")

    window = None
    if h1:
        window = (_time(h1, min1), _time(h2, min2))
        if window[1] <= window[0]:
            raise ParseError("Время окончания должно быть позже начала")
    return start, end, window
