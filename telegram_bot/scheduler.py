"""Отложенные задачи на APScheduler.

* напоминания клиенту перед визитом (settings.reminder_hours);
* автоотклонение заявки, если админ не ответил за settings.pending_ttl_hours;
* утренняя сводка для админов (settings.admin_digest_time).

Задачи живут только в памяти. Источник правды — БД: при старте restore()
читает предстоящие записи и пересоздаёт задачи, а после любого изменения
записи обработчики вызывают sync_booking(). Сами задачи при срабатывании
перечитывают запись из БД, поэтому устаревшая задача ничего не сломает.
Повторную отправку напоминания исключает первичный ключ sent_reminders.
"""

import contextlib
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core import schedule, texts
from core.config import settings
from db import repo
from db.models import Booking, BookingStatus, Platform
from telegram_bot import keyboards as kb
from telegram_bot import notify

log = logging.getLogger(__name__)

# Пропущенную задачу «на сейчас» запускаем с небольшой задержкой.
_RUN_SOON = timedelta(seconds=2)


def _reminder_kind(hours: int) -> str:
    return f"{hours}h"


class BookingScheduler:
    def __init__(self, bot: Bot, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.bot = bot
        self.session_factory = session_factory
        self.scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.timezone))

    # --- Жизненный цикл ---------------------------------------------------

    async def start(self) -> None:
        self.scheduler.start()
        if settings.admin_digest_time and settings.admin_ids:
            t = settings.admin_digest_time
            self.scheduler.add_job(
                self._send_digest,
                CronTrigger(hour=t.hour, minute=t.minute),
                id="digest",
                replace_existing=True,
            )
        await self.restore()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    async def restore(self) -> int:
        """Пересоздать задачи по данным БД. Возвращает число запланированных задач."""
        now = schedule.now_local()
        async with self.session_factory() as session:
            bookings = await repo.get_schedulable_bookings(session, now)
            sent = await repo.get_sent_reminders(session, [b.id for b in bookings])
        for booking in bookings:
            self._plan(booking, sent.get(booking.id, set()), now, catch_up=True)
        count = len([j for j in self.scheduler.get_jobs() if j.id != "digest"])
        log.info("Планировщик: восстановлено задач — %s (записей — %s)", count, len(bookings))
        return count

    async def sync_booking(self, booking_id: int) -> None:
        """Привести задачи записи в соответствие с её текущим состоянием в БД."""
        now = schedule.now_local()
        async with self.session_factory() as session:
            booking = await repo.get_booking(session, booking_id)
            sent = await repo.get_sent_reminders(session, [booking_id])
        self._remove_jobs(booking_id)
        if booking is not None:
            self._plan(booking, sent.get(booking_id, set()), now, catch_up=False)

    # --- Планирование -----------------------------------------------------

    def _job_ids(self, booking_id: int) -> list[str]:
        ids = [f"expire:{booking_id}"]
        ids += [f"remind:{booking_id}:{_reminder_kind(h)}" for h in settings.reminder_hours]
        return ids

    def _remove_jobs(self, booking_id: int) -> None:
        for job_id in self._job_ids(booking_id):
            with contextlib.suppress(JobLookupError):
                self.scheduler.remove_job(job_id)

    def _add(self, job_id: str, run_at: datetime, func, *args) -> None:
        self.scheduler.add_job(
            func, DateTrigger(run_date=run_at), args=args, id=job_id, replace_existing=True
        )

    def _plan(self, booking: Booking, sent: set[str], now: datetime, catch_up: bool) -> None:
        """catch_up=True (при старте): напомнить, если время напоминания пришлось
        на простой бота и прошло не больше reminder_grace_minutes."""
        if booking.starts_at <= now:
            return

        if booking.status == BookingStatus.PENDING:
            created = schedule.utc_to_local(booking.created_at)
            expire_at = min(
                created + timedelta(hours=settings.pending_ttl_hours), booking.starts_at
            )
            self._add(
                f"expire:{booking.id}", max(expire_at, now + _RUN_SOON), self._expire, booking.id
            )

        elif booking.status == BookingStatus.CONFIRMED:
            grace = timedelta(minutes=settings.reminder_grace_minutes)
            for hours in settings.reminder_hours:
                kind = _reminder_kind(hours)
                if kind in sent:
                    continue
                run_at = booking.starts_at - timedelta(hours=hours)
                if run_at <= now:
                    if not (catch_up and now - run_at <= grace):
                        continue  # время прошло: напоминание уже неактуально
                    run_at = now + _RUN_SOON
                self._add(f"remind:{booking.id}:{kind}", run_at, self._remind, booking.id, hours)

    # --- Задачи -----------------------------------------------------------

    async def _remind(self, booking_id: int, hours: int) -> None:
        async with self.session_factory() as session:
            booking = await repo.get_booking(session, booking_id)
            if (
                booking is None
                or booking.status != BookingStatus.CONFIRMED
                or booking.starts_at <= schedule.now_local()
                or booking.client.platform != Platform.TELEGRAM
            ):
                return
            if not await repo.claim_reminder(session, booking_id, _reminder_kind(hours)):
                return  # уже отправляли
            # claim_reminder сделал commit — перечитываем связи заново.
            booking = await repo.get_booking(session, booking_id)
        await notify.send(
            self.bot,
            booking.client.external_id,
            texts.client_reminder(booking),
            kb.reminder_kb(booking_id),
        )
        log.info("Напоминание %sh по записи #%s отправлено", hours, booking_id)

    async def _expire(self, booking_id: int) -> None:
        async with self.session_factory() as session:
            # decide_booking меняет статус, только если заявка всё ещё ждёт решения.
            booking = await repo.decide_booking(session, booking_id, approve=False)
        if booking is None:
            return
        log.info("Заявка #%s истекла без решения и отклонена", booking_id)
        if booking.client.platform == Platform.TELEGRAM:
            await notify.send(
                self.bot, booking.client.external_id, texts.client_booking_expired(booking)
            )
        await notify.notify_admins(
            self.bot, texts.admin_booking_card(booking, texts.ADMIN_TITLE_EXPIRED)
        )

    async def _send_digest(self) -> None:
        today = schedule.now_local().date()
        start = datetime.combine(today, datetime.min.time())
        async with self.session_factory() as session:
            bookings = await repo.get_bookings_between(session, start, start + timedelta(days=1))
            closed = await repo.get_closed_slots_between(session, start, start + timedelta(days=1))
            pending = await repo.count_pending_bookings(session, schedule.now_local())
        closure = texts.day_closure(*schedule.day_state(today, closed))
        await notify.notify_admins(
            self.bot,
            texts.admin_digest(
                texts.admin_day_schedule(today, bookings, closure),
                texts.admin_pending_count(pending),
            ),
        )
