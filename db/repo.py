"""Запросы к БД. Функции, которые что-то меняют, сами делают commit."""

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.config import settings
from core.schedule import iter_slot_starts
from db.models import (
    ACTIVE_STATUSES,
    AppSetting,
    BannedUser,
    Booking,
    BookingSlot,
    BookingStatus,
    Client,
    ClosedSlot,
    Platform,
    Service,
)


class SlotUnavailableError(Exception):
    """Время нельзя занять — так решила БД при вставке слотов записи."""


class SlotAlreadyBookedError(SlotUnavailableError):
    """Хотя бы один слот уже занят: сработал уникальный индекс booking_slots."""


class SlotClosedError(SlotUnavailableError):
    """Хотя бы один слот закрыт админом: сработал триггер trg_booking_slots_not_closed."""


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
    """Начала всех занятых слотов за день."""
    day_start = datetime.combine(day, time.min)
    return await get_busy_slots_between(session, day_start, day_start + timedelta(days=1))


async def get_closed_slots_between(
    session: AsyncSession, start: datetime, end: datetime
) -> set[datetime]:
    """Слоты, закрытые админом, в полуинтервале [start, end)."""
    stmt = select(ClosedSlot.starts_at).where(
        ClosedSlot.starts_at >= start, ClosedSlot.starts_at < end
    )
    return set((await session.scalars(stmt)).all())


async def get_unavailable_slots_between(
    session: AsyncSession, start: datetime, end: datetime
) -> set[datetime]:
    """Занятые записями и закрытые админом слоты — то, что нельзя предложить клиенту."""
    busy = await get_busy_slots_between(session, start, end)
    return busy | await get_closed_slots_between(session, start, end)


async def get_unavailable_slots(session: AsyncSession, day: date) -> set[datetime]:
    day_start = datetime.combine(day, time.min)
    return await get_unavailable_slots_between(session, day_start, day_start + timedelta(days=1))


async def create_booking(
    session: AsyncSession,
    client_id: int,
    service_id: int,
    starts_at: datetime,
    slot_minutes: int,
    status: BookingStatus = BookingStatus.CONFIRMED,
) -> Booking:
    """Создать запись и занять её слоты одной транзакцией.

    Проверка «свободно ли время» в коде здесь намеренно не делается: два клиента
    могут одновременно увидеть слот свободным. Гарантию даёт уникальный индекс
    booking_slots.starts_at — второй INSERT упадёт с IntegrityError.
    Закрытое админом время так же отсекает триггер на booking_slots.

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
        status=status,
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
        if "slot_closed" in str(exc.orig):
            raise SlotClosedError from exc
        raise
    return booking


async def get_upcoming_bookings(
    session: AsyncSession, client_id: int, now: datetime
) -> Sequence[Booking]:
    stmt = (
        select(Booking)
        .where(
            Booking.client_id == client_id,
            Booking.status.in_(ACTIVE_STATUSES),
            Booking.starts_at >= now,
        )
        .options(selectinload(Booking.service))
        .order_by(Booking.starts_at)
    )
    return (await session.scalars(stmt)).all()


async def get_upcoming_booking(
    session: AsyncSession, booking_id: int, client_id: int, now: datetime
) -> Booking | None:
    """Предстоящая активная запись, только если она принадлежит клиенту."""
    stmt = (
        select(Booking)
        .where(
            Booking.id == booking_id,
            Booking.client_id == client_id,
            Booking.status.in_(ACTIVE_STATUSES),
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
            Booking.status.in_(ACTIVE_STATUSES),
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


# --- Администрирование ----------------------------------------------------


async def get_booking(session: AsyncSession, booking_id: int) -> Booking | None:
    """Запись с клиентом и услугой — для уведомлений и карточек админа."""
    stmt = (
        select(Booking)
        .where(Booking.id == booking_id)
        .options(selectinload(Booking.client), selectinload(Booking.service))
        # Объект мог остаться в identity map со старым статусом.
        .execution_options(populate_existing=True)
    )
    return await session.scalar(stmt)


async def decide_booking(session: AsyncSession, booking_id: int, approve: bool) -> Booking | None:
    """Подтвердить или отклонить заявку. None — если она уже не ждёт решения.

    Условие status = pending в самом запросе: если два админа нажмут кнопки
    одновременно, решение примет только первый.
    """
    stmt = (
        select(Booking)
        .where(Booking.id == booking_id, Booking.status == BookingStatus.PENDING)
        .options(
            selectinload(Booking.client),
            selectinload(Booking.service),
            selectinload(Booking.slots),
        )
    )
    booking = await session.scalar(stmt)
    if booking is None:
        return None

    if approve:
        booking.status = BookingStatus.CONFIRMED
    else:
        booking.status = BookingStatus.REJECTED
        booking.cancelled_at = _utcnow()
        booking.slots.clear()  # время снова свободно
    await session.commit()
    return booking


async def get_bookings_between(
    session: AsyncSession, start: datetime, end: datetime
) -> Sequence[Booking]:
    """Активные записи, начинающиеся в [start, end) — расписание для админа."""
    stmt = (
        select(Booking)
        .where(
            Booking.status.in_(ACTIVE_STATUSES),
            Booking.starts_at >= start,
            Booking.starts_at < end,
        )
        .options(selectinload(Booking.client), selectinload(Booking.service))
        .order_by(Booking.starts_at)
    )
    return (await session.scalars(stmt)).all()


async def get_pending_bookings(
    session: AsyncSession, now: datetime, limit: int = 20
) -> Sequence[Booking]:
    stmt = (
        select(Booking)
        .where(Booking.status == BookingStatus.PENDING, Booking.starts_at > now)
        .options(selectinload(Booking.client), selectinload(Booking.service))
        .order_by(Booking.starts_at)
        .limit(limit)
    )
    return (await session.scalars(stmt)).all()


async def count_pending_bookings(session: AsyncSession, now: datetime) -> int:
    stmt = select(func.count()).where(
        Booking.status == BookingStatus.PENDING, Booking.starts_at > now
    )
    return await session.scalar(stmt)


async def booking_stats(
    session: AsyncSession, start: datetime, end: datetime
) -> dict[BookingStatus, tuple[int, int]]:
    """{статус: (кол-во записей, сумма цен услуг)} для записей в [start, end)."""
    stmt = (
        select(Booking.status, func.count(), func.coalesce(func.sum(Service.price), 0))
        .join(Service)
        .where(Booking.starts_at >= start, Booking.starts_at < end)
        .group_by(Booking.status)
    )
    return {status: (count, total) for status, count, total in await session.execute(stmt)}


async def count_clients(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(Client))


# --- Закрытие времени админом ---------------------------------------------


async def close_slots(session: AsyncSession, starts: Sequence[datetime]) -> None:
    """Закрыть слоты для новых записей. Уже закрытые пропускаются."""
    if starts:
        stmt = sqlite_insert(ClosedSlot).on_conflict_do_nothing(
            index_elements=[ClosedSlot.starts_at]
        )
        await session.execute(stmt, [{"starts_at": s} for s in starts])
    await session.commit()


async def open_slots(session: AsyncSession, starts: Sequence[datetime]) -> None:
    if starts:
        await session.execute(delete(ClosedSlot).where(ClosedSlot.starts_at.in_(starts)))
    await session.commit()


async def open_slots_between(session: AsyncSession, start: datetime, end: datetime) -> None:
    """Открыть всё закрытое время в полуинтервале [start, end)."""
    await session.execute(
        delete(ClosedSlot).where(ClosedSlot.starts_at >= start, ClosedSlot.starts_at < end)
    )
    await session.commit()


async def toggle_closed_slot(session: AsyncSession, starts_at: datetime) -> bool:
    """Переключить слот. True — если теперь он закрыт."""
    result = await session.execute(delete(ClosedSlot).where(ClosedSlot.starts_at == starts_at))
    closed = result.rowcount == 0
    if closed:
        session.add(ClosedSlot(starts_at=starts_at))
    await session.commit()
    return closed


async def get_slot_booking(session: AsyncSession, starts_at: datetime) -> Booking | None:
    """Активная запись, которая занимает слот."""
    stmt = (
        select(Booking)
        .join(BookingSlot)
        .where(BookingSlot.starts_at == starts_at)
        .options(selectinload(Booking.client), selectinload(Booking.service))
    )
    return await session.scalar(stmt)


# --- Настройки ------------------------------------------------------------

HORIZON_KEY = "booking_horizon_days"


async def get_setting(session: AsyncSession, key: str) -> str | None:
    return await session.scalar(select(AppSetting.value).where(AppSetting.key == key))


async def set_setting(session: AsyncSession, key: str, value: str) -> None:
    stmt = (
        sqlite_insert(AppSetting)
        .values(key=key, value=value)
        .on_conflict_do_update(
            index_elements=[AppSetting.key],
            set_={"value": value, "updated_at": func.current_timestamp()},
        )
    )
    await session.execute(stmt)
    await session.commit()


async def get_booking_horizon(session: AsyncSession) -> int:
    """На сколько дней вперёд открыта запись: значение админа или из .env."""
    value = await get_setting(session, HORIZON_KEY)
    return int(value) if value is not None else settings.booking_horizon_days


async def set_booking_horizon(session: AsyncSession, days: int) -> None:
    await set_setting(session, HORIZON_KEY, str(days))


# --- Антиспам: лимиты записей ---------------------------------------------


async def count_active_bookings(session: AsyncSession, client_id: int, now: datetime) -> int:
    stmt = select(func.count()).where(
        Booking.client_id == client_id,
        Booking.status.in_(ACTIVE_STATUSES),
        Booking.starts_at > now,
    )
    return await session.scalar(stmt)


async def count_recent_bookings(session: AsyncSession, client_id: int, hours: int = 24) -> int:
    """Сколько записей клиент создал за последние hours часов, включая отменённые."""
    since = _utcnow() - timedelta(hours=hours)
    stmt = select(func.count()).where(Booking.client_id == client_id, Booking.created_at >= since)
    return await session.scalar(stmt)


# --- Баны -----------------------------------------------------------------


async def is_banned(session: AsyncSession, platform: Platform, external_id: int) -> bool:
    stmt = select(BannedUser.external_id).where(
        BannedUser.platform == platform, BannedUser.external_id == external_id
    )
    return await session.scalar(stmt) is not None


async def ban_user(
    session: AsyncSession,
    platform: Platform,
    external_id: int,
    reason: str | None,
    banned_by: int,
    now: datetime,
) -> int:
    """Забанить и отклонить все предстоящие записи пользователя (слоты освобождаются).

    Возвращает число отклонённых записей. Повторный бан обновляет причину.
    """
    reason = reason[:255] if reason else None
    stmt = (
        sqlite_insert(BannedUser)
        .values(platform=platform, external_id=external_id, reason=reason, banned_by=banned_by)
        .on_conflict_do_update(
            index_elements=[BannedUser.platform, BannedUser.external_id],
            set_={"reason": reason, "banned_by": banned_by},
        )
    )
    await session.execute(stmt)

    bookings = (
        await session.scalars(
            select(Booking)
            .join(Client)
            .where(
                Client.platform == platform,
                Client.external_id == external_id,
                Booking.status.in_(ACTIVE_STATUSES),
                Booking.starts_at > now,
            )
            .options(selectinload(Booking.slots))
        )
    ).all()
    for booking in bookings:
        booking.status = BookingStatus.REJECTED
        booking.cancelled_at = _utcnow()
        booking.slots.clear()
    await session.commit()
    return len(bookings)


async def unban_user(session: AsyncSession, platform: Platform, external_id: int) -> bool:
    result = await session.execute(
        delete(BannedUser).where(
            BannedUser.platform == platform, BannedUser.external_id == external_id
        )
    )
    await session.commit()
    return result.rowcount > 0


async def list_banned(
    session: AsyncSession, limit: int = 50
) -> Sequence[tuple[BannedUser, Client | None]]:
    stmt = (
        select(BannedUser, Client)
        .outerjoin(
            Client,
            (Client.platform == BannedUser.platform)
            & (Client.external_id == BannedUser.external_id),
        )
        .order_by(BannedUser.created_at.desc())
        .limit(limit)
    )
    return (await session.execute(stmt)).tuples().all()
