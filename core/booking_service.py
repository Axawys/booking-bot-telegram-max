"""Логика записи, общая для Telegram и MAX.

Здесь решается, *что* показать клиенту (доступные дни и время) и *можно ли*
оформить запись. Мессенджер-специфичны только кнопки и отправка сообщений —
они в telegram_bot/ и max_bot/.

Свободность времени проверяется в коде только ради удобства. Гарантию даёт БД:
уникальный индекс booking_slots и триггер закрытого времени (см. db.repo).
"""

import calendar
import enum
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from core import schedule, texts
from core.config import settings
from db import repo
from db.models import BookingStatus, Platform


def is_admin(platform: Platform, user_id: int) -> bool:
    """Админы задаются Telegram ID: из MAX админом быть нельзя."""
    return platform == Platform.TELEGRAM and settings.is_admin(user_id)


# --- Что показать ---------------------------------------------------------


@dataclass(frozen=True)
class MonthView:
    year: int
    month: int
    available: set[date]
    can_prev: bool
    can_next: bool


async def month_view(
    session: AsyncSession, duration: int, year: int | None = None, month: int | None = None
) -> MonthView:
    """Дни месяца, на которые есть свободное время для услуги длительностью duration."""
    now = schedule.now_local()
    first, last = schedule.bookable_range(now, await repo.get_booking_horizon(session))
    if year is None or month is None:
        year, month = first.year, first.month

    month_start = date(year, month, 1)
    month_end = date(year, month, calendar.monthrange(year, month)[1])
    lo, hi = max(first, month_start), min(last, month_end)

    available: set[date] = set()
    if lo <= hi:
        # Один запрос на весь видимый диапазон вместо запроса на каждый день.
        busy = await repo.get_unavailable_slots_between(
            session,
            datetime.combine(lo, time.min),
            datetime.combine(hi + timedelta(days=1), time.min),
        )
        for day in schedule.iter_days(lo, hi):
            if schedule.available_starts(day, duration, busy, now):
                available.add(day)

    return MonthView(
        year=year,
        month=month,
        available=available,
        can_prev=(year, month) > (first.year, first.month),
        can_next=(year, month) < (last.year, last.month),
    )


async def is_day_open(session: AsyncSession, day: date) -> bool:
    horizon = await repo.get_booking_horizon(session)
    return schedule.is_bookable_day(day, schedule.now_local(), horizon)


async def free_starts(session: AsyncSession, day: date, duration: int) -> list[datetime]:
    busy = await repo.get_unavailable_slots(session, day)
    return schedule.available_starts(day, duration, busy, schedule.now_local())


# --- Оформление записи ----------------------------------------------------


async def limit_error(session: AsyncSession, platform: Platform, user_id: int) -> str | None:
    """Антиспам: лимиты на число записей клиента. None — можно записываться.

    Проверка в коде, а не в БД: это политика, а не целостность данных, и
    в редкой гонке клиент может превысить лимит на одну запись.
    """
    if is_admin(platform, user_id):
        return None
    client = await repo.get_client(session, platform, user_id)
    if client is None:
        return None
    active = await repo.count_active_bookings(session, client.id, schedule.now_local())
    if active >= settings.max_active_bookings:
        return texts.book_limit_active(settings.max_active_bookings)
    if await repo.count_recent_bookings(session, client.id) >= settings.max_bookings_per_day:
        return texts.BOOK_LIMIT_DAILY
    return None


class Outcome(enum.StrEnum):
    CREATED = "created"
    TOO_LATE = "too_late"  # до начала меньше booking_min_lead_minutes
    OUT_OF_HORIZON = "out_of_horizon"  # админ сократил горизонт записи
    LIMIT = "limit"  # лимиты антиспама; текст в PlaceResult.message
    SLOT_TAKEN = "slot_taken"  # уникальный индекс: время заняли
    SLOT_CLOSED = "slot_closed"  # триггер: админ закрыл время
    SERVICE_GONE = "service_gone"  # услуга отключена


@dataclass(frozen=True)
class PlaceResult:
    outcome: Outcome
    booking_id: int | None = None
    status: BookingStatus | None = None
    message: str | None = None


async def place_booking(
    session: AsyncSession,
    *,
    platform: Platform,
    user_id: int,
    full_name: str,
    service_id: int,
    starts_at: datetime,
    phone: str,
) -> PlaceResult:
    """Финальные проверки и создание записи. Уведомления — забота вызывающего."""
    now = schedule.now_local()
    if starts_at < now + timedelta(minutes=settings.booking_min_lead_minutes):
        return PlaceResult(Outcome.TOO_LATE)
    horizon = await repo.get_booking_horizon(session)
    if not schedule.is_bookable_day(starts_at.date(), now, horizon):
        return PlaceResult(Outcome.OUT_OF_HORIZON)
    # Лимиты ещё раз: за время оформления клиент мог записаться в другом окне.
    if error := await limit_error(session, platform, user_id):
        return PlaceResult(Outcome.LIMIT, message=error)

    client = await repo.upsert_client(session, platform, user_id, full_name)
    client_id = client.id
    await repo.set_client_phone(session, client_id, phone)

    # Если есть администраторы — заявка ждёт их подтверждения, но слоты
    # занимаются сразу: время уже не достанется другому клиенту.
    status = BookingStatus.PENDING if settings.requires_confirmation else BookingStatus.CONFIRMED
    try:
        booking = await repo.create_booking(
            session, client_id, service_id, starts_at, settings.slot_minutes, status
        )
    except repo.SlotAlreadyBookedError:
        return PlaceResult(Outcome.SLOT_TAKEN)
    except repo.SlotClosedError:
        return PlaceResult(Outcome.SLOT_CLOSED)
    except ValueError:
        return PlaceResult(Outcome.SERVICE_GONE)
    return PlaceResult(Outcome.CREATED, booking_id=booking.id, status=status)
