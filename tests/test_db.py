"""Гарантии на уровне БД: уникальный индекс слотов, триггер закрытого времени,
однократность напоминаний."""

import asyncio
from datetime import datetime, timedelta

import pytest

from db import repo
from db.models import BookingStatus, Platform
from db.session import session_factory

DAY = datetime.combine(datetime.now().date() + timedelta(days=3), datetime.min.time())


async def _setup(session):
    services = {s.name: s.id for s in await repo.get_active_services(session)}
    client = await repo.upsert_client(session, Platform.TELEGRAM, 1, "Анна")
    return client.id, services


async def test_overlapping_bookings_rejected_by_unique_index(session):
    client_id, svc = await _setup(session)
    long = await repo.create_booking(
        session, client_id, svc["Услуга за 3 000 ₽"], DAY.replace(hour=10), 30
    )
    assert len(long.slots) == 4  # 120 мин = 4 слота по 30

    with pytest.raises(repo.SlotAlreadyBookedError):
        await repo.create_booking(
            session, client_id, svc["Услуга за 500 ₽"], DAY.replace(hour=11, minute=30), 30
        )
    # Стык сразу после записи — свободен, и сессия пригодна после отката.
    await repo.create_booking(session, client_id, svc["Услуга за 500 ₽"], DAY.replace(hour=12), 30)


async def test_concurrent_inserts_only_one_wins(session):
    client_id, svc = await _setup(session)

    async def attempt():
        async with session_factory() as s:
            try:
                await repo.create_booking(
                    s, client_id, svc["Услуга за 1 000 ₽"], DAY.replace(hour=15), 30
                )
                return "ok"
            except repo.SlotAlreadyBookedError:
                return "busy"

    results = await asyncio.gather(*(attempt() for _ in range(10)))
    assert results.count("ok") == 1 and results.count("busy") == 9


async def test_cancel_frees_slots_and_checks_owner(session):
    client_id, svc = await _setup(session)
    other = (await repo.upsert_client(session, Platform.TELEGRAM, 2, "Борис")).id
    booking = await repo.create_booking(
        session, client_id, svc["Услуга за 1 000 ₽"], DAY.replace(hour=10), 30
    )
    now = datetime.now()

    assert not await repo.cancel_booking(session, booking.id, other, now)  # чужая
    assert await repo.cancel_booking(session, booking.id, client_id, now)
    assert not await repo.cancel_booking(session, booking.id, client_id, now)  # повторно
    assert await repo.get_busy_slots(session, DAY.date()) == set()


async def test_closed_slot_blocked_by_trigger_existing_kept(session):
    client_id, svc = await _setup(session)
    # id сохраняем сразу: после отката (SlotClosedError) объекты сессии expired.
    kept_id = (
        await repo.create_booking(
            session, client_id, svc["Услуга за 1 000 ₽"], DAY.replace(hour=10), 30
        )
    ).id
    await repo.close_slots(
        session, [DAY.replace(hour=h, minute=m) for h in (10, 11, 12) for m in (0, 30)]
    )

    with pytest.raises(repo.SlotClosedError):
        await repo.create_booking(
            session, client_id, svc["Услуга за 500 ₽"], DAY.replace(hour=12), 30
        )
    with pytest.raises(repo.SlotClosedError):  # частичное пересечение
        await repo.create_booking(
            session, client_id, svc["Услуга за 1 000 ₽"], DAY.replace(hour=12, minute=30), 30
        )
    assert (await repo.get_booking(session, kept_id)).status == BookingStatus.CONFIRMED

    assert not await repo.toggle_closed_slot(session, DAY.replace(hour=12))  # открыли
    await repo.create_booking(session, client_id, svc["Услуга за 500 ₽"], DAY.replace(hour=12), 30)


async def test_reminder_claimed_once(session):
    client_id, svc = await _setup(session)
    booking_id = (
        await repo.create_booking(
            session, client_id, svc["Услуга за 500 ₽"], DAY.replace(hour=10), 30
        )
    ).id
    assert await repo.claim_reminder(session, booking_id, "24h")
    assert not await repo.claim_reminder(session, booking_id, "24h")
    assert await repo.claim_reminder(session, booking_id, "2h")


async def test_ban_rejects_upcoming_bookings(session):
    client_id, svc = await _setup(session)
    booking = await repo.create_booking(
        session, client_id, svc["Услуга за 500 ₽"], DAY.replace(hour=10), 30, BookingStatus.PENDING
    )
    rejected = await repo.ban_user(session, Platform.TELEGRAM, 1, "спам", 99, datetime.now())
    assert rejected == [booking.id]
    assert await repo.is_banned(session, Platform.TELEGRAM, 1)
    assert await repo.get_busy_slots(session, DAY.date()) == set()
    assert await repo.unban_user(session, Platform.TELEGRAM, 1)
    assert not await repo.is_banned(session, Platform.TELEGRAM, 1)


async def test_booking_horizon_setting(session):
    assert await repo.get_booking_horizon(session) == 14  # значение по умолчанию из настроек
    await repo.set_booking_horizon(session, 30)
    assert await repo.get_booking_horizon(session) == 30
