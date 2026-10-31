"""Расчёт свободного времени. Чистая логика без БД и мессенджеров.

Все datetime здесь — naive, в локальном времени бизнеса (settings.timezone).
"""

from collections.abc import Iterator
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from core.config import settings


def now_local() -> datetime:
    return datetime.now(ZoneInfo(settings.timezone)).replace(tzinfo=None)


def iter_slot_starts(
    starts_at: datetime, ends_at: datetime, slot_minutes: int | None = None
) -> Iterator[datetime]:
    """Начала слотов сетки в полуинтервале [starts_at, ends_at)."""
    step = timedelta(minutes=slot_minutes or settings.slot_minutes)
    current = starts_at
    while current < ends_at:
        yield current
        current += step


def day_slot_starts(day: date) -> list[datetime]:
    """Все начала слотов в пределах рабочего дня."""
    return list(
        iter_slot_starts(
            datetime.combine(day, settings.work_start),
            datetime.combine(day, settings.work_end),
        )
    )


def available_starts(
    day: date, duration_minutes: int, busy: set[datetime], now: datetime
) -> list[datetime]:
    """Времена, на которые можно записать услугу длительностью duration_minutes.

    Время подходит, если до него не меньше booking_min_lead_minutes, услуга
    успевает закончиться до конца рабочего дня и ни один её слот не занят.
    Это проверка для удобства пользователя; от гонок защищает уникальный
    индекс в БД (см. db.models.BookingSlot).
    """
    earliest = now + timedelta(minutes=settings.booking_min_lead_minutes)
    day_end = datetime.combine(day, settings.work_end)
    duration = timedelta(minutes=duration_minutes)

    result = []
    for start in day_slot_starts(day):
        end = start + duration
        if start < earliest or end > day_end:
            continue
        if any(slot in busy for slot in iter_slot_starts(start, end)):
            continue
        result.append(start)
    return result


def bookable_range(now: datetime) -> tuple[date, date]:
    """Первый и последний день, на которые открыта запись."""
    first = now.date()
    return first, first + timedelta(days=settings.booking_horizon_days)


def is_bookable_day(day: date, now: datetime) -> bool:
    first, last = bookable_range(now)
    return first <= day <= last
