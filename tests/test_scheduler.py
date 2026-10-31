"""Планировщик: задачи пересоздаются из БД, напоминания не дублируются."""

from datetime import timedelta

import pytest
from sqlalchemy import text

from core import schedule
from db import repo
from db.models import BookingStatus, Platform
from db.session import session_factory
from telegram_bot.scheduler import BookingScheduler


def _align(dt):
    dt = dt.replace(second=0, microsecond=0)
    extra = dt.minute % 30
    return dt + timedelta(minutes=30 - extra) if extra else dt


def _job_ids(sched: BookingScheduler) -> set[str]:
    return {j.id for j in sched.scheduler.get_jobs()}


@pytest.fixture
async def bookings(session):
    """Записи в разных состояниях относительно «сейчас»."""
    base = _align(schedule.now_local())
    svc = (await repo.get_active_services(session))[0].id
    cid = (await repo.upsert_client(session, Platform.TELEGRAM, 1, "Анна")).id
    c, p = BookingStatus.CONFIRMED, BookingStatus.PENDING

    async def mk(hours, status, minutes=0):
        delta = timedelta(hours=hours, minutes=minutes)
        return (await repo.create_booking(session, cid, svc, base + delta, 30, status)).id

    ids = {
        "far": await mk(30, c),  # оба напоминания в будущем
        "missed_recent": await mk(23, c, 30),  # 24h пропущено < часа назад
        "missed_old": await mk(20, c),  # 24h пропущено 4 часа назад
        "pending": await mk(48, p),
        "cancelled": await mk(60, c),
    }
    await repo.cancel_booking(session, ids["cancelled"], cid, schedule.now_local())
    return ids


async def _restored(bot) -> BookingScheduler:
    sched = BookingScheduler(bot, session_factory)
    sched.scheduler.start(paused=True)
    await sched.restore()
    return sched


async def test_restore_builds_jobs_from_db(bot, bookings):
    sched = await _restored(bot)
    jobs = _job_ids(sched)
    sched.shutdown()

    b = bookings
    assert {f"remind:{b['far']}:24h", f"remind:{b['far']}:2h"} <= jobs
    assert f"remind:{b['missed_recent']}:24h" in jobs  # догоняем после простоя
    assert f"remind:{b['missed_old']}:24h" not in jobs  # слишком поздно
    assert f"remind:{b['missed_old']}:2h" in jobs
    assert f"expire:{b['pending']}" in jobs
    assert not any(f":{b['cancelled']}" in j for j in jobs)


async def test_restart_rebuilds_same_jobs(bot, bookings):
    first = await _restored(bot)
    jobs_before = _job_ids(first)
    first.shutdown()  # «перезапуск бота»
    second = await _restored(bot)
    assert _job_ids(second) == jobs_before
    second.shutdown()


async def test_reminder_sent_once_and_skipped_for_cancelled(bot, api, bookings):
    sched = await _restored(bot)
    await sched._remind(bookings["far"], 24)
    await sched._remind(bookings["far"], 24)
    await sched._remind(bookings["cancelled"], 24)
    sent = api.sent_to(1)
    assert len(sent) == 1 and "Напоминаем" in sent[0].text
    # После «перезапуска» отправленное напоминание не планируется снова.
    sched.shutdown()
    again = await _restored(bot)
    assert f"remind:{bookings['far']}:24h" not in _job_ids(again)
    again.shutdown()


async def test_pending_expires_only_once(bot, api, bookings):
    sched = await _restored(bot)
    await sched._expire(bookings["pending"])
    await sched._expire(bookings["pending"])
    await sched._expire(bookings["far"])  # подтверждённая — не трогаем
    sched.shutdown()
    assert len(api.sent_to(1)) == 1 and "не была подтверждена" in api.sent_to(1)[0].text
    async with session_factory() as s:
        statuses = dict((await s.execute(text("select id, status from bookings"))).all())
    assert statuses[bookings["pending"]] == "rejected"
    assert statuses[bookings["far"]] == "confirmed"


async def test_jobs_follow_booking_changes(user, scheduler):
    client, admin = user(1), user(99)
    await client.book()
    async with session_factory() as s:
        bid = (await s.execute(text("select max(id) from bookings"))).scalar()
    mine = lambda: {j for j in _job_ids(scheduler) if j.split(":")[1] == str(bid)}  # noqa: E731

    assert mine() == {f"expire:{bid}"}
    await admin.click(f"adm:confirm:{bid}")
    assert f"expire:{bid}" not in mine() and any(j.startswith("remind:") for j in mine())
    await client.send("Мои записи")
    ask = client.callbacks("my:ask:")[0]
    await client.click(ask)
    await client.click(ask.replace(":ask:", ":cancel:"))
    assert mine() == set()
