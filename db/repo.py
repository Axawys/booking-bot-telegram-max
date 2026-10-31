"""Запросы к БД. Функции, которые что-то меняют, сами делают commit."""

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.schedule import iter_slot_starts
from db.models import Booking, BookingSlot, BookingStatus, Client, Platform, Service


class SlotAlreadyBookedError(Exception):
    """Хотя бы один слот уже занят: сработал уникальный индекс booking_slots."""


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# --- Услуги ---------------------------------------------------------------


async def get_active_services(session: AsyncSession) -> Sequence[Service]:
    stmt = (
        select(Service)
        .where(Service.is_active)
        .order_by(Service.sort_order, Service.id)
    )
    return (await session.scalars(stmt)).all()


async def get_service(session: AsyncSession, service_id: int) -> Service | None:
    return await session.get(Service, service_id)


# --- Клиенты --------------------------------------------------------------


async def upsert_client(
    session: AsyncSession, platform: Platform, external_id: int, full_name: str
) -> Client:
    """Найти клиента по (платформа, ID) или создать. Имя обновляется."""
    stmt = (
        sqlite_insert(Client)
        .values(platform=platform, external_id=external_id, full_name=full_name)
        .on_conflict_do_update(
            index_elements=[Client.platform, Client.external_id],
            set_={"full_name": full_name},
        )
        .returning(Client)
        # Иначе при повторном вызове в той же сессии вернётся объект из
        # identity map со старыми значениями, а не только что обновлённая строка.
        .execution_options(populate_existing=True)
    )
    client = await session.scalar(stmt)
    await session.commit()
    return client


async def get_client(
    session: AsyncSession, platform: Platform, external_id: int
) -> Client | None:
    stmt = select(Client).where(Client.platform == platform, Client.external_id == external_id)
    return await session.scalar(stmt)


async def set_client_phone(session: AsyncSession, client_id: int, phone: str) -> None:
    await session.execute(update(Client).where(Client.id == client_id).values(phone=phone))
    await session.commit()


# --- Расписание и записи --------------------------------------------------


async def get_busy_slots_between(
    session: AsyncSession, start: datetime, end: datetime
) -> set[datetime]:
    """Начала занятых слотов в полуинтервале [start, end)."""
    stmt = select(BookingSlot.starts_at).where(
        BookingSlot.starts_at >= start,
        BookingSlot.starts_at < end,
    )
    return set((await session.scalars(stmt)).all())


async def get_busy_slots(session: AsyncSession, day: date) -> set[datetime]:
    """Начала всех занятых слотов за день — для построения выбора времени."""
    day_start = datetime.combine(day, time.min)
    return await get_busy_slots_between(session, day_start, day_start + timedelta(days=1))


async def create_booking(
    session: AsyncSession,
    client_id: int,
    service_id: int,
    starts_at: datetime,
    slot_minutes: int,
) -> Booking:
    """Создать запись и занять её слоты одной транзакцией.

    Проверка «свободно ли время» в коде здесь намеренно не делается: два клиента
    могут одновременно увидеть слот свободным. Гарантию даёт уникальный индекс
    booking_slots.starts_at — второй INSERT упадёт с IntegrityError.

    Принимает id, а не ORM-объекты: после отката (слот занят) все объекты
    сессии становятся expired, и обращение к ним в async-коде падает.
    """
    service = await session.get(Service, service_id)
    if service is None or not service.is_active:
        raise ValueError(f"Услуга {service_id} не найдена или отключена")
    if service.duration_minutes % slot_minutes:
        raise ValueError(
            f"Длительность услуги ({service.duration_minutes} мин) "
            f"не кратна шагу сетки ({slot_minutes} мин)"
        )
    minutes_from_midnight = starts_at.hour * 60 + starts_at.minute
    if minutes_from_midnight % slot_minutes or starts_at.second or starts_at.microsecond:
        raise ValueError(f"Время {starts_at:%H:%M:%S} не попадает в сетку слотов")

    ends_at = starts_at + timedelta(minutes=service.duration_minutes)
    booking = Booking(
        client_id=client_id,
        service=service,
        starts_at=starts_at,
        ends_at=ends_at,
        slots=[
            BookingSlot(starts_at=slot)
            for slot in iter_slot_starts(starts_at, ends_at, slot_minutes)
        ],
    )
    session.add(booking)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if "booking_slots.starts_at" in str(exc.orig):
            raise SlotAlreadyBookedError from exc
        raise
    return booking


async def get_upcoming_bookings(
    session: AsyncSession, client_id: int, now: datetime
) -> Sequence[Booking]:
    stmt = (
        select(Booking)
        .where(
            Booking.client_id == client_id,
            Booking.status == BookingStatus.CONFIRMED,
            Booking.starts_at >= now,
        )
        .options(selectinload(Booking.service))
        .order_by(Booking.starts_at)
    )
    return (await session.scalars(stmt)).all()


async def get_upcoming_booking(
    session: AsyncSession, booking_id: int, client_id: int, now: datetime
) -> Booking | None:
    """Предстоящая подтверждённая запись, только если она принадлежит клиенту."""
    stmt = (
        select(Booking)
        .where(
            Booking.id == booking_id,
            Booking.client_id == client_id,
            Booking.status == BookingStatus.CONFIRMED,
            Booking.starts_at > now,
        )
        .options(selectinload(Booking.service))
    )
    return await session.scalar(stmt)


async def cancel_booking(
    session: AsyncSession, booking_id: int, client_id: int, now: datetime
) -> bool:
    """Отменить предстоящую запись клиента и освободить её слоты.

    False — если отменять нечего: запись чужая, уже отменена или уже началась.
    """
    stmt = (
        select(Booking)
        .where(
            Booking.id == booking_id,
            Booking.client_id == client_id,
            Booking.status == BookingStatus.CONFIRMED,
            Booking.starts_at > now,
        )
        .options(selectinload(Booking.slots))
    )
    booking = await session.scalar(stmt)
    if booking is None:
        return False

    booking.status = BookingStatus.CANCELLED
    booking.cancelled_at = _utcnow()
    booking.slots.clear()  # delete-orphan удалит строки booking_slots
    await session.commit()
    return True
