"""Расчёт свободного времени. Чистая логика без БД и мессенджеров.

Все datetime здесь — naive, в локальном времени бизнеса (settings.timezone).
"""

from collections.abc import Iterable, Iterator
from datetime import date, datetime, time, timedelta
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


def iter_days(start: date, end: date) -> Iterator[date]:
    """Дни от start до end включительно."""
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def full_day_slots(day: date) -> list[datetime]:
    """Все слоты суток, а не только рабочих часов: закрытый так день остаётся
    закрытым, даже если потом расширить часы работы."""
    start = datetime.combine(day, time.min)
    return list(iter_slot_starts(start, start + timedelta(days=1)))


def window_slots(day: date, start: time, end: time) -> list[datetime]:
    """Слоты дня, начинающиеся в полуинтервале [start, end)."""
    return [s for s in full_day_slots(day) if start <= s.time() < end]


def merge_slots(slots: Iterable[datetime]) -> list[tuple[datetime, datetime]]:
    """Соседние слоты -> интервалы: [10:00, 10:30, 11:00, 14:00] -> 10:00–11:30, 14:00–14:30."""
    step = timedelta(minutes=settings.slot_minutes)
    ranges: list[tuple[datetime, datetime]] = []
    for slot in sorted(slots):
        if ranges and ranges[-1][1] == slot:
            ranges[-1] = (ranges[-1][0], slot + step)
        else:
            ranges.append((slot, slot + step))
    return ranges


def day_state(day: date, closed: set[datetime]) -> tuple[str, list[tuple[datetime, datetime]]]:
    """Состояние дня по рабочим часам: open / closed / partial и закрытые интервалы."""
    work = day_slot_starts(day)
    closed_work = [slot for slot in work if slot in closed]
    if not closed_work:
        return "open", []
    if len(closed_work) == len(work):
        return "closed", merge_slots(closed_work)
    return "partial", merge_slots(closed_work)
