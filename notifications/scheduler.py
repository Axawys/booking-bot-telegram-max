"""Отложенные задачи на APScheduler.

* напоминания клиенту перед визитом (settings.reminder_hours);
* автоотклонение заявки, если админ не ответил за settings.pending_ttl_hours;
* утренняя сводка для админов (settings.admin_digest_time).

Задачи живут только в памяти. Источник правды — БД:

* при старте restore() читает предстоящие записи и пересоздаёт задачи;
* после изменения записи в этом процессе обработчик вызывает sync_booking();
* раз в settings.scheduler_resync_seconds reconcile() сверяет задачи с БД —
  так планировщик узнаёт о записях из другого процесса (MAX-бота).

Задачи при срабатывании перечитывают запись из БД, поэтому устаревшая задача
ничего не сломает. Повторную отправку напоминания исключает первичный ключ
sent_reminders. Планировщик один на систему — он работает в Telegram-сервисе.
"""

import contextlib
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core import schedule, texts
from core.config import settings
from db import repo
from db.models import Booking, BookingStatus
from notifications.notifier import Notifier

log = logging.getLogger(__name__)

# Пропущенную задачу «на сейчас» запускаем с небольшой задержкой.
_RUN_SOON = timedelta(seconds=2)
# Служебные задачи — не про конкретную запись, сверка их не трогает.
_SERVICE_JOBS = {"digest", "resync"}


def _reminder_kind(hours: int) -> str:
    return f"{hours}h"


class BookingScheduler:
    def __init__(
        self, notifier: Notifier, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self.notifier = notifier
        self.session_factory = session_factory
        self.tz = ZoneInfo(settings.timezone)
        self.scheduler = AsyncIOScheduler(timezone=self.tz)

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
        if settings.scheduler_resync_seconds > 0:
            self.scheduler.add_job(
                self.reconcile,
                IntervalTrigger(seconds=settings.scheduler_resync_seconds),
                id="resync",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
        await self.restore()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    async def restore(self) -> int:
        """При старте: пересоздать задачи по БД. Напоминание, время которого
        пришлось на простой бота, отправляется, если опоздание не больше
        reminder_grace_minutes."""
        count = await self.reconcile(grace=timedelta(minutes=settings.reminder_grace_minutes))
        log.info("Планировщик: восстановлено задач — %s", count)
        return count

    async def reconcile(self, grace: timedelta | None = None) -> int:
        """Привести задачи всех записей к состоянию БД: добавить недостающие,
        убрать лишние. Возвращает число задач по записям.

        По умолчанию догоняет только напоминания, пропущенные за последние два
        интервала сверки, — например, если запись создали в MAX-боте прямо
        перед временем напоминания.
        """
        if grace is None:
            grace = timedelta(seconds=2 * settings.scheduler_resync_seconds)
        now = schedule.now_local()
        async with self.session_factory() as session:
            bookings = await repo.get_schedulable_bookings(session, now)
            sent = await repo.get_sent_reminders(session, [b.id for b in bookings])

        desired: dict[str, tuple] = {}
        for booking in bookings:
            for job in self._desired_jobs(booking, sent.get(booking.id, set()), now, grace):
                desired[job[0]] = job

        for job in self.scheduler.get_jobs():
            if job.id not in _SERVICE_JOBS and job.id not in desired:
                job.remove()
        for job_id, run_at, soon, func, args in desired.values():
            existing = self.scheduler.get_job(job_id)
            if existing is not None and (soon or self._same_time(existing, run_at)):
                continue  # уже запланирована
            self._add(job_id, run_at, func, *args)
        return len(desired)

    async def sync_booking(self, booking_id: int) -> None:
        """Сразу привести задачи одной записи к её текущему состоянию в БД."""
        now = schedule.now_local()
        async with self.session_factory() as session:
            booking = await repo.get_booking(session, booking_id)
            sent = await repo.get_sent_reminders(session, [booking_id])
        self._remove_jobs(booking_id)
        if booking is not None:
            for job_id, run_at, _, func, args in self._desired_jobs(
                booking, sent.get(booking_id, set()), now, grace=timedelta(0)
            ):
                self._add(job_id, run_at, func, *args)

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

    def _same_time(self, job, run_at: datetime) -> bool:
        planned = getattr(job.trigger, "run_date", None)
        return planned is not None and planned == run_at.replace(tzinfo=self.tz)

    def _desired_jobs(self, booking: Booking, sent: set[str], now: datetime, grace: timedelta):
        """Задачи, которые должны быть у записи: (id, когда, «на сейчас», функция, аргументы).

        grace — насколько опоздавшее напоминание ещё стоит отправить.
        """
        if booking.starts_at <= now:
            return

        if booking.status == BookingStatus.PENDING:
            created = schedule.utc_to_local(booking.created_at)
            expire_at = min(
                created + timedelta(hours=settings.pending_ttl_hours), booking.starts_at
            )
            soon = expire_at <= now
            run_at = now + _RUN_SOON if soon else expire_at
            yield f"expire:{booking.id}", run_at, soon, self._expire, (booking.id,)

        elif booking.status == BookingStatus.CONFIRMED:
            for hours in settings.reminder_hours:
                kind = _reminder_kind(hours)
                if kind in sent:
                    continue
                run_at = booking.starts_at - timedelta(hours=hours)
                soon = run_at <= now
                if soon:
                    if now - run_at > grace:
                        continue  # время прошло: напоминание уже неактуально
                    run_at = now + _RUN_SOON
                yield (
                    f"remind:{booking.id}:{kind}",
                    run_at,
                    soon,
                    self._remind,
                    (booking.id, hours),
                )

    # --- Задачи -----------------------------------------------------------

    async def _remind(self, booking_id: int, hours: int) -> None:
        async with self.session_factory() as session:
            booking = await repo.get_booking(session, booking_id)
            if (
                booking is None
                or booking.status != BookingStatus.CONFIRMED
                or booking.starts_at <= schedule.now_local()
            ):
                return
            if not await repo.claim_reminder(session, booking_id, _reminder_kind(hours)):
                return  # уже отправляли
            # claim_reminder сделал commit — перечитываем связи заново.
            booking = await repo.get_booking(session, booking_id)
        if await self.notifier.reminder(booking):
            log.info("Напоминание %sh по записи #%s отправлено", hours, booking_id)

    async def _expire(self, booking_id: int) -> None:
        async with self.session_factory() as session:
            # decide_booking меняет статус, только если заявка всё ещё ждёт решения.
            booking = await repo.decide_booking(session, booking_id, approve=False)
        if booking is None:
            return
        log.info("Заявка #%s истекла без решения и отклонена", booking_id)
        await self.notifier.expired(booking)

    async def _send_digest(self) -> None:
        today = schedule.now_local().date()
        start = datetime.combine(today, datetime.min.time())
        async with self.session_factory() as session:
            bookings = await repo.get_bookings_between(session, start, start + timedelta(days=1))
            closed = await repo.get_closed_slots_between(session, start, start + timedelta(days=1))
            pending = await repo.count_pending_bookings(session, schedule.now_local())
        closure = texts.day_closure(*schedule.day_state(today, closed))
        await self.notifier.admins(
            texts.admin_digest(
                texts.admin_day_schedule(today, bookings, closure),
                texts.admin_pending_count(pending),
            ),
        )
