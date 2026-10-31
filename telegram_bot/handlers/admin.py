"""Админка: команды и кнопки подтверждения заявок. Доступно только ADMIN_IDS."""

import re
from datetime import date, datetime, time, timedelta

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, or_f
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from core import schedule, texts
from core.config import settings
from db import repo
from db.models import BookingStatus, Platform
from notifications.notifier import Notifier
from notifications.scheduler import BookingScheduler
from telegram_bot import keyboards as kb
from telegram_bot.callbacks import AdminAction, AdminBookingCb
from telegram_bot.filters import AccessibleMessage, IsAdmin
from telegram_bot.utils import safe_edit

router = Router(name="admin")
# Для остальных пользователей роутер «невидим»: апдейт уходит дальше по цепочке.
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

_DAY_ARG = re.compile(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?$")


# --- Команды --------------------------------------------------------------


@router.message(or_f(Command("admin"), F.text.in_({texts.BTN_ADMIN_HELP, texts.BTN_ADMIN_MODE})))
async def cmd_admin(message: Message, session: AsyncSession) -> None:
    pending = await repo.count_pending_bookings(session, schedule.now_local())
    await message.answer(
        f"{texts.ADMIN_HELP}\n\n{texts.admin_pending_count(pending)}",
        reply_markup=kb.admin_menu,
    )


@router.message(F.text == texts.BTN_CLIENT_MODE)
async def client_mode(message: Message) -> None:
    await message.answer(texts.CLIENT_MODE, reply_markup=kb.client_menu(is_admin=True))


@router.message(or_f(Command("pending"), F.text == texts.BTN_ADMIN_PENDING))
async def cmd_pending(message: Message, session: AsyncSession) -> None:
    bookings = await repo.get_pending_bookings(session, schedule.now_local())
    if not bookings:
        await message.answer(texts.ADMIN_NO_PENDING)
        return
    # Каждая заявка — отдельной карточкой со своими кнопками.
    for booking in bookings:
        await message.answer(
            texts.admin_booking_card(booking, texts.ADMIN_TITLE_PENDING),
            reply_markup=kb.admin_decision_kb(booking.id),
        )


async def _send_day(message: Message, session: AsyncSession, day: date) -> None:
    start = datetime.combine(day, time.min)
    end = start + timedelta(days=1)
    bookings = await repo.get_bookings_between(session, start, end)
    closed = await repo.get_closed_slots_between(session, start, end)
    closure = texts.day_closure(*schedule.day_state(day, closed))
    await message.answer(texts.admin_day_schedule(day, bookings, closure))


@router.message(or_f(Command("today"), F.text == texts.BTN_ADMIN_TODAY))
async def cmd_today(message: Message, session: AsyncSession) -> None:
    await _send_day(message, session, schedule.now_local().date())


@router.message(or_f(Command("tomorrow"), F.text == texts.BTN_ADMIN_TOMORROW))
async def cmd_tomorrow(message: Message, session: AsyncSession) -> None:
    await _send_day(message, session, schedule.now_local().date() + timedelta(days=1))


@router.message(Command("day"))
async def cmd_day(message: Message, command: CommandObject, session: AsyncSession) -> None:
    match = _DAY_ARG.match((command.args or "").strip())
    if not match:
        await message.answer(texts.ADMIN_DAY_USAGE)
        return
    day_, month, year = match.groups()
    try:
        day = date(int(year or schedule.now_local().year), int(month), int(day_))
    except ValueError:
        await message.answer(texts.ADMIN_DAY_USAGE)
        return
    await _send_day(message, session, day)


@router.message(or_f(Command("stats"), F.text == texts.BTN_ADMIN_STATS))
async def cmd_stats(message: Message, session: AsyncSession) -> None:
    now = schedule.now_local()
    today = datetime.combine(now.date(), time.min)
    tomorrow = today + timedelta(days=1)
    await message.answer(
        texts.admin_stats(
            today=await repo.booking_stats(session, today, tomorrow),
            week=await repo.booking_stats(session, now, now + timedelta(days=7)),
            month=await repo.booking_stats(session, now - timedelta(days=30), now),
            clients=await repo.count_clients(session),
        )
    )


# --- Подтверждение заявок -------------------------------------------------


@router.callback_query(
    AdminBookingCb.filter(F.action.in_({AdminAction.CONFIRM, AdminAction.REJECT})),
    AccessibleMessage(),
)
async def decide(
    callback: CallbackQuery,
    callback_data: AdminBookingCb,
    msg: Message,
    session: AsyncSession,
    notifier: Notifier,
    scheduler: BookingScheduler,
) -> None:
    approve = callback_data.action == AdminAction.CONFIRM
    booking = await repo.decide_booking(session, callback_data.booking_id, approve)

    if booking is None:
        # Решение уже принято (другим админом или раньше) либо запись отменил клиент.
        current = await repo.get_booking(session, callback_data.booking_id)
        if current is None:
            await callback.answer(texts.ADMIN_NOT_FOUND_ALERT, show_alert=True)
            await safe_edit(msg, msg.html_text)
            return
        await callback.answer(texts.ADMIN_ALREADY_DECIDED_ALERT, show_alert=True)
        await safe_edit(msg, texts.admin_booking_card(current, texts.ADMIN_TITLE_PENDING))
        return

    await safe_edit(
        msg,
        texts.admin_booking_card(booking, texts.ADMIN_TITLE_PENDING, callback.from_user.full_name),
    )
    await notifier.decision(booking)
    await callback.answer(texts.ADMIN_CONFIRMED_ALERT if approve else texts.ADMIN_REJECTED_ALERT)
    # Подтверждена — запланировать напоминания; отклонена — снять задачи.
    await scheduler.sync_booking(booking.id)


# --- Бан из карточки заявки -----------------------------------------------


def _card_markup(booking):
    return kb.admin_decision_kb(booking.id) if booking.status == BookingStatus.PENDING else None


@router.callback_query(
    AdminBookingCb.filter(F.action.in_({AdminAction.BAN_ASK, AdminAction.BACK})),
    AccessibleMessage(),
)
async def ban_ask_or_back(
    callback: CallbackQuery, callback_data: AdminBookingCb, msg: Message, session: AsyncSession
) -> None:
    booking = await repo.get_booking(session, callback_data.booking_id)
    if booking is None:
        await callback.answer(texts.ADMIN_NOT_FOUND_ALERT, show_alert=True)
        return
    card = texts.admin_booking_card(booking, texts.ADMIN_TITLE_PENDING)
    if callback_data.action == AdminAction.BAN_ASK:
        await safe_edit(msg, texts.admin_ban_ask(card), kb.admin_ban_confirm_kb(booking.id))
    else:
        await safe_edit(msg, card, _card_markup(booking))
    await callback.answer()


@router.callback_query(AdminBookingCb.filter(F.action == AdminAction.BAN), AccessibleMessage())
async def ban_from_card(
    callback: CallbackQuery,
    callback_data: AdminBookingCb,
    msg: Message,
    session: AsyncSession,
    scheduler: BookingScheduler,
) -> None:
    booking = await repo.get_booking(session, callback_data.booking_id)
    if booking is None:
        await callback.answer(texts.ADMIN_NOT_FOUND_ALERT, show_alert=True)
        return
    client = booking.client
    if settings.is_admin(client.external_id) and client.platform == Platform.TELEGRAM:
        await callback.answer(texts.ADMIN_CANT_BAN_ADMIN, show_alert=True)
        return
    rejected_ids = await repo.ban_user(
        session,
        client.platform,
        client.external_id,
        reason=f"заявка #{booking.id}",
        banned_by=callback.from_user.id,
        now=schedule.now_local(),
    )
    rejected = len(rejected_ids)
    booking = await repo.get_booking(session, callback_data.booking_id)
    await safe_edit(
        msg,
        texts.admin_booking_card(
            booking,
            texts.ADMIN_TITLE_PENDING,
            callback.from_user.full_name,
            note=texts.admin_ban_note(rejected),
        ),
    )
    await callback.answer(texts.admin_ban_toast(rejected))
    for booking_id in rejected_ids:
        await scheduler.sync_booking(booking_id)


# --- Команды банов --------------------------------------------------------


def _parse_user(args: str | None) -> tuple[Platform, int | None, str | None]:
    """«123 причина» — Telegram, «max 123 причина» — MAX."""
    parts = (args or "").strip().split(maxsplit=1)
    platform = Platform.TELEGRAM
    if parts and parts[0].lower() == "max":
        platform = Platform.MAX
        parts = parts[1].split(maxsplit=1) if len(parts) > 1 else []
    if not parts or not parts[0].isdigit():
        return platform, None, None
    return platform, int(parts[0]), (parts[1].strip() if len(parts) > 1 else None)


@router.message(Command("ban"))
async def cmd_ban(
    message: Message, command: CommandObject, session: AsyncSession, scheduler: BookingScheduler
) -> None:
    platform, user_id, reason = _parse_user(command.args)
    if user_id is None:
        await message.answer(texts.ADMIN_BAN_USAGE)
        return
    if platform == Platform.TELEGRAM and settings.is_admin(user_id):
        await message.answer(texts.ADMIN_CANT_BAN_ADMIN)
        return
    rejected_ids = await repo.ban_user(
        session, platform, user_id, reason, message.from_user.id, schedule.now_local()
    )
    client = await repo.get_client(session, platform, user_id)
    await message.answer(texts.admin_banned(platform, user_id, client, len(rejected_ids)))
    for booking_id in rejected_ids:
        await scheduler.sync_booking(booking_id)


@router.message(Command("unban"))
async def cmd_unban(message: Message, command: CommandObject, session: AsyncSession) -> None:
    platform, user_id, _ = _parse_user(command.args)
    if user_id is None:
        await message.answer(texts.ADMIN_UNBAN_USAGE)
        return
    if await repo.unban_user(session, platform, user_id):
        await message.answer(texts.admin_unbanned(platform, user_id))
    else:
        await message.answer(texts.admin_not_banned(platform, user_id))


@router.message(or_f(Command("banned"), F.text == texts.BTN_ADMIN_BANS))
async def cmd_banned(message: Message, session: AsyncSession) -> None:
    await message.answer(texts.admin_banned_list(await repo.list_banned(session)))
