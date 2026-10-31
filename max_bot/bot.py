"""Обработчики MAX-бота поверх общего core.

Клиентская часть та же, что в Telegram: меню, запись (услуга → дата → время →
телефон → подтверждение), «Мои записи» с отменой. Что показать и можно ли
записаться, решает core.booking_service; здесь — только кнопки и сообщения MAX.
Админка остаётся в Telegram: заявки из MAX приходят админам туда же.

Отличия MAX от Telegram, которые учтены здесь:
* нет reply-клавиатуры — главное меню тоже inline-кнопки;
* ответ на нажатие кнопки (POST /answers) может сразу изменить сообщение;
* контакт приходит вложением `contact` с vCard внутри.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from datetime import time as dtime
from html import escape
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core import booking_service as service
from core import schedule, texts
from core.phone import normalize_phone, phone_from_vcard
from core.ratelimit import RateLimiter, Verdict
from db import repo
from db.models import BookingStatus, Platform
from max_bot import fsm
from max_bot import keyboards as kb
from max_bot.api import Keyboard, MaxApi
from notifications.notifier import Notifier

log = logging.getLogger(__name__)


def _full_name(user: dict[str, Any]) -> str:
    parts = [user.get("first_name") or "", user.get("last_name") or ""]
    return " ".join(p for p in parts if p).strip() or "Клиент"


@dataclass
class Ctx:
    """Одно входящее событие: кто, откуда и как отвечать."""

    api: MaxApi
    session: AsyncSession
    user_id: int
    name: str
    callback_id: str | None = None
    mid: str | None = None  # сообщение с нажатой кнопкой
    answered: bool = field(default=False)

    async def show(self, text: str, keyboard: Keyboard | None = None) -> None:
        """Показать экран: в ответ на кнопку — изменить её сообщение, иначе — новое."""
        if self.callback_id and not self.answered:
            self.answered = True
            await self.api.answer_callback(self.callback_id, text=text, keyboard=keyboard)
        elif self.mid:
            await self.api.edit_message(self.mid, text, keyboard)
        else:
            await self.send(text, keyboard)

    async def send(self, text: str, keyboard: Keyboard | None = None) -> str:
        return await self.api.send_message(self.user_id, text, keyboard)

    async def alert(self, text: str) -> None:
        if self.callback_id and not self.answered:
            self.answered = True
            await self.api.answer_callback(self.callback_id, notification=text)
        else:
            await self.send(text)

    async def finish(self) -> None:
        """На каждое нажатие кнопки нужно ответить, иначе она «зависнет»."""
        if self.callback_id and not self.answered:
            self.answered = True
            await self.api.answer_callback(self.callback_id)


class MaxBot:
    def __init__(
        self,
        api: MaxApi,
        notifier: Notifier,
        session_factory: async_sessionmaker[AsyncSession],
        limiter: RateLimiter,
        ban_notice_interval: float = 3600,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.api = api
        self.notifier = notifier
        self.session_factory = session_factory
        self.limiter = limiter
        self.fsm = fsm.MemoryFSM()
        self.ban_notice_interval = ban_notice_interval
        self.clock = clock
        self._ban_notice: dict[int, float] = {}

    # --- Вход -------------------------------------------------------------

    async def handle(self, update: dict[str, Any]) -> None:
        kind = update.get("update_type")
        callback = update.get("callback") if kind == "message_callback" else None
        message = update.get("message")

        if kind == "bot_started":
            user = update.get("user")
        elif kind == "message_created":
            if (message or {}).get("recipient", {}).get("chat_type") != "dialog":
                return  # бот работает только в личных диалогах
            user = message.get("sender")
        elif callback is not None:
            user = callback.get("user")
        else:
            return
        if not user or user.get("is_bot"):
            return

        user_id = user["user_id"]
        callback_id = callback["callback_id"] if callback else None
        mid = ((message or {}).get("body") or {}).get("mid") if callback else None

        # Антифлуд — до любых запросов к БД.
        verdict = self.limiter.hit(user_id)
        if verdict is not Verdict.ALLOW:
            if verdict is Verdict.WARN:
                log.warning("MAX: флуд от пользователя %s", user_id)
                text = texts.throttled(int(self.limiter.cooldown))
                if callback_id:
                    await self.api.answer_callback(callback_id, notification=text)
                else:
                    await self.api.send_message(user_id, text)
            elif callback_id:
                await self.api.answer_callback(callback_id)
            return

        async with self.session_factory() as session:
            ctx = Ctx(self.api, session, user_id, _full_name(user), callback_id, mid)
            if await repo.is_banned(session, Platform.MAX, user_id):
                await self._banned(ctx)
                return
            try:
                if kind == "bot_started":
                    await self.start(ctx)
                elif kind == "message_created":
                    await self.on_message(ctx, message)
                else:
                    await self.on_callback(ctx, callback.get("payload") or "")
            finally:
                await ctx.finish()

    async def _banned(self, ctx: Ctx) -> None:
        now = self.clock()
        last = self._ban_notice.get(ctx.user_id)
        if last is None or now - last >= self.ban_notice_interval:
            self._ban_notice[ctx.user_id] = now
            await ctx.alert(texts.BANNED)
        await ctx.finish()

    # --- Сообщения --------------------------------------------------------

    async def on_message(self, ctx: Ctx, message: dict[str, Any]) -> None:
        body = message.get("body") or {}
        text = (body.get("text") or "").strip()
        command = text.split()[0].lower() if text.startswith("/") else None

        if command in ("/start", "/menu"):
            await self.start(ctx)
        elif command == "/cancel":
            self.fsm.clear(ctx.user_id)
            await ctx.send(texts.BOOK_CANCELLED, kb.main_menu())
        elif self.fsm.state(ctx.user_id) == fsm.PHONE and command is None:
            await self.on_phone(ctx, text, body.get("attachments") or [])
        elif command is not None:
            await ctx.send(texts.UNKNOWN_COMMAND)
        else:
            await ctx.send(texts.BACK_TO_MENU, kb.main_menu())

    async def start(self, ctx: Ctx) -> None:
        self.fsm.clear(ctx.user_id)
        await ctx.send(texts.START.format(name=escape(ctx.name)), kb.main_menu())

    # --- Кнопки -----------------------------------------------------------

    async def on_callback(self, ctx: Ctx, payload: str) -> None:
        parts = payload.split(":")
        state = self.fsm.state(ctx.user_id)
        match parts:
            case ["noop"]:
                pass
            case ["menu"]:
                await ctx.send(texts.BACK_TO_MENU, kb.main_menu())
            case ["menu", "book"]:
                await self.start_booking(ctx)
            case ["menu", "my"]:
                await ctx.send(*await self._my_list(ctx))
            case ["menu", "services"]:
                items = await repo.get_active_services(ctx.session)
                await ctx.send(texts.services_list(items), kb.back_to_menu())
            case ["menu", "contacts"]:
                await ctx.send(texts.CONTACTS, kb.back_to_menu())
            case ["nav", "cancel"]:
                self.fsm.clear(ctx.user_id)
                await ctx.show(texts.BOOK_CANCELLED, kb.main_menu())
            case ["svc", sid] if state == fsm.SERVICE:
                await self.pick_service(ctx, int(sid))
            case ["cal", "nav", y, m] if state == fsm.DATE:
                await self.show_calendar(ctx, int(y), int(m))
            case ["cal", "day", y, m, d] if state == fsm.DATE:
                await self.pick_day(ctx, date(int(y), int(m), int(d)))
            case ["slot", h, m] if state == fsm.TIME:
                await self.pick_slot(ctx, dtime(int(h), int(m)))
            case ["phone", "saved"] if state == fsm.PHONE:
                saved = self.fsm.data(ctx.user_id).get("saved_phone")
                if saved:
                    await self.accept_phone(ctx, saved)
            case ["nav", "back"] if state == fsm.DATE:
                await self.show_services(ctx)
            case ["nav", "back"] if state == fsm.TIME:
                day = date.fromisoformat(self.fsm.data(ctx.user_id)["day"])
                await self.show_calendar(ctx, day.year, day.month)
            case ["nav", "back"] if state == fsm.CONFIRM:
                await self.back_to_times(ctx)
            case ["nav", "confirm"] if state == fsm.CONFIRM:
                await self.confirm(ctx)
            case ["my", "list"]:
                await ctx.show(*await self._my_list(ctx))
            case ["my", "ask", bid]:
                await self.my_ask(ctx, int(bid))
            case ["my", "cancel", bid]:
                await self.my_cancel(ctx, int(bid))
            case _:
                await ctx.alert(texts.STALE_BUTTON)

    # --- Запись -----------------------------------------------------------

    async def start_booking(self, ctx: Ctx) -> None:
        self.fsm.clear(ctx.user_id)
        if error := await service.limit_error(ctx.session, Platform.MAX, ctx.user_id):
            await ctx.send(error, kb.back_to_menu())
            return
        await self.show_services(ctx, new_message=True)

    async def show_services(self, ctx: Ctx, new_message: bool = False) -> None:
        items = await repo.get_active_services(ctx.session)
        if not items:
            self.fsm.clear(ctx.user_id)
            await ctx.send(texts.BOOK_NO_SERVICES, kb.back_to_menu())
            return
        self.fsm.set_state(ctx.user_id, fsm.SERVICE)
        if new_message:
            await ctx.send(texts.BOOK_CHOOSE_SERVICE, kb.services(items))
        else:
            await ctx.show(texts.BOOK_CHOOSE_SERVICE, kb.services(items))

    async def pick_service(self, ctx: Ctx, service_id: int) -> None:
        item = await repo.get_service(ctx.session, service_id)
        if item is None or not item.is_active:
            await ctx.alert(texts.BOOK_ERROR)
            return
        self.fsm.update(
            ctx.user_id,
            service_id=item.id,
            service_name=item.name,
            price=item.price,
            duration=item.duration_minutes,
        )
        await self.show_calendar(ctx)

    async def show_calendar(
        self, ctx: Ctx, year: int | None = None, month: int | None = None, notice: str | None = None
    ) -> None:
        data = self.fsm.data(ctx.user_id)
        view = await service.month_view(ctx.session, data["duration"], year, month)
        self.fsm.set_state(ctx.user_id, fsm.DATE)
        await ctx.show(texts.book_choose_date(data["service_name"], notice), kb.month(view))

    async def show_times(self, ctx: Ctx, day: date, notice: str | None = None) -> bool:
        data = self.fsm.data(ctx.user_id)
        starts = await service.free_starts(ctx.session, day, data["duration"])
        if not starts:
            return False
        self.fsm.update(ctx.user_id, day=day.isoformat())
        self.fsm.set_state(ctx.user_id, fsm.TIME)
        await ctx.show(texts.book_choose_time(data["service_name"], day, notice), kb.slots(starts))
        return True

    async def back_to_times(self, ctx: Ctx, notice: str | None = None) -> None:
        day = date.fromisoformat(self.fsm.data(ctx.user_id)["day"])
        if not await self.show_times(ctx, day, notice):
            await self.show_calendar(ctx, day.year, day.month)

    async def pick_day(self, ctx: Ctx, day: date) -> None:
        if not await service.is_day_open(ctx.session, day):
            await ctx.alert(texts.BOOK_DAY_UNAVAILABLE_ALERT)
            return
        if not await self.show_times(ctx, day):
            await ctx.alert(texts.BOOK_NO_SLOTS_ALERT)

    async def pick_slot(self, ctx: Ctx, at: dtime) -> None:
        data = self.fsm.data(ctx.user_id)
        day = date.fromisoformat(data["day"])
        starts_at = datetime.combine(day, at)
        if starts_at not in await service.free_starts(ctx.session, day, data["duration"]):
            await ctx.alert(texts.BOOK_SLOT_GONE_ALERT)
            return
        self.fsm.update(ctx.user_id, starts_at=starts_at.isoformat())

        # Телефон уже указан в этой записи (клиент вернулся «Изменить время»).
        if data.get("phone"):
            await self.show_summary(ctx)
            return

        await ctx.show(texts.book_picked(data["service_name"], starts_at))
        client = await repo.get_client(ctx.session, Platform.MAX, ctx.user_id)
        saved_phone = client.phone if client else None
        self.fsm.set_state(ctx.user_id, fsm.PHONE)
        phone_mid = await ctx.send(texts.BOOK_ASK_PHONE, kb.phone(saved_phone))
        self.fsm.update(ctx.user_id, saved_phone=saved_phone, phone_mid=phone_mid)

    async def on_phone(self, ctx: Ctx, text: str, attachments: list[dict[str, Any]]) -> None:
        phone = None
        for attachment in attachments:
            if attachment.get("type") == "contact":
                phone = phone_from_vcard((attachment.get("payload") or {}).get("vcf_info"))
                break
        else:
            phone = normalize_phone(text) if text else None
        if phone is None:
            await ctx.send(texts.BOOK_PHONE_INVALID)
            return
        await self.accept_phone(ctx, phone)

    async def accept_phone(self, ctx: Ctx, phone: str) -> None:
        self.fsm.update(ctx.user_id, phone=phone)
        # Убрать кнопки «Отправить номер» с сообщения-запроса.
        if phone_mid := self.fsm.data(ctx.user_id).get("phone_mid"):
            await self.api.edit_message(phone_mid, texts.BOOK_PHONE_ACCEPTED)
        await ctx.finish()
        ctx.mid = None  # итог записи — новым сообщением
        await self.show_summary(ctx)

    async def show_summary(self, ctx: Ctx) -> None:
        self.fsm.set_state(ctx.user_id, fsm.CONFIRM)
        await ctx.show(texts.book_summary(**_details(self.fsm.data(ctx.user_id))), kb.confirm())

    async def confirm(self, ctx: Ctx) -> None:
        data = self.fsm.data(ctx.user_id)
        result = await service.place_booking(
            ctx.session,
            platform=Platform.MAX,
            user_id=ctx.user_id,
            full_name=ctx.name,
            service_id=data["service_id"],
            starts_at=datetime.fromisoformat(data["starts_at"]),
            phone=data["phone"],
        )
        match result.outcome:
            case service.Outcome.CREATED:
                self.fsm.clear(ctx.user_id)
                details = _details(data)
                if result.status == BookingStatus.PENDING:
                    await ctx.show(texts.book_pending(**details), kb.main_menu())
                else:
                    await ctx.show(texts.book_success(**details), kb.main_menu())
                # Админы — в Telegram. Задачи (напоминания, истечение заявки)
                # планировщик Telegram-сервиса подхватит при сверке с БД.
                await self.notifier.new_booking(
                    await repo.get_booking(ctx.session, result.booking_id)
                )
            case service.Outcome.TOO_LATE:
                await self.back_to_times(ctx, texts.BOOK_TOO_LATE)
            case service.Outcome.SLOT_TAKEN:
                await self.back_to_times(ctx, texts.BOOK_SLOT_TAKEN)
            case service.Outcome.SLOT_CLOSED:
                await self.back_to_times(ctx, texts.BOOK_SLOT_CLOSED)
            case service.Outcome.OUT_OF_HORIZON:
                await self.show_calendar(ctx, notice=texts.BOOK_OUT_OF_HORIZON)
            case service.Outcome.LIMIT | service.Outcome.SERVICE_GONE:
                self.fsm.clear(ctx.user_id)
                await ctx.show(result.message or texts.BOOK_ERROR, kb.main_menu())

    # --- Мои записи -------------------------------------------------------

    async def _client_id(self, ctx: Ctx) -> int | None:
        client = await repo.get_client(ctx.session, Platform.MAX, ctx.user_id)
        return client.id if client else None

    async def _my_list(self, ctx: Ctx, notice: str | None = None) -> tuple[str, Keyboard]:
        client_id = await self._client_id(ctx)
        bookings = (
            await repo.get_upcoming_bookings(ctx.session, client_id, schedule.now_local())
            if client_id
            else []
        )
        keyboard = kb.my_bookings(bookings) if bookings else kb.back_to_menu()
        return texts.my_bookings_list(bookings, notice), keyboard

    async def my_ask(self, ctx: Ctx, booking_id: int) -> None:
        client_id = await self._client_id(ctx)
        booking = (
            await repo.get_upcoming_booking(
                ctx.session, booking_id, client_id, schedule.now_local()
            )
            if client_id
            else None
        )
        if booking is None:
            await ctx.alert(texts.MY_BOOKING_NOT_FOUND_ALERT)
            return
        await ctx.show(texts.my_booking_cancel_ask(booking), kb.cancel_ask(booking.id))

    async def my_cancel(self, ctx: Ctx, booking_id: int) -> None:
        client_id = await self._client_id(ctx)
        now = schedule.now_local()
        booking = (
            await repo.get_upcoming_booking(ctx.session, booking_id, client_id, now)
            if client_id
            else None
        )
        notice = texts.my_booking_cancelled(booking) if booking else None
        if booking is None or not await repo.cancel_booking(
            ctx.session, booking_id, client_id, now
        ):
            await ctx.alert(texts.MY_BOOKING_NOT_FOUND_ALERT)
            return
        await ctx.show(*await self._my_list(ctx, notice))
        await self.notifier.cancelled_by_client(await repo.get_booking(ctx.session, booking_id))


def _details(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "service_name": data["service_name"],
        "price": data["price"],
        "duration": data["duration"],
        "starts_at": datetime.fromisoformat(data["starts_at"]),
        "phone": data["phone"],
    }
