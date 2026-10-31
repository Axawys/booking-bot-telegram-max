"""Антифлуд и лимиты записей."""

from datetime import datetime

from aiogram.types import Update, User

from core.config import settings
from telegram_bot.middlewares import ThrottlingMiddleware
from tests.helpers import texts


def _message_update(bot, user_id: int) -> Update:
    return Update.model_validate(
        {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "date": int(datetime.now().timestamp()),
                "chat": {"id": user_id, "type": "private"},
                "from": {"id": user_id, "is_bot": False, "first_name": "U"},
                "text": "spam",
            },
        },
        context={"bot": bot},
    )


async def test_throttling(bot, api):
    now = [0.0]
    mw = ThrottlingMiddleware(limit=5, window=10, cooldown=30, clock=lambda: now[0])
    handled = []

    async def handler(event, data):
        handled.append(event)

    async def send(user_id: int):
        data = {"event_from_user": User(id=user_id, is_bot=False, first_name="U")}
        await mw(handler, _message_update(bot, user_id), data)

    for _ in range(7):
        await send(1)
    assert len(handled) == 5  # 6-е и 7-е отброшены
    warnings = [m.text for m in api.sent_to(1)]
    assert len(warnings) == 1 and "Подождите 30 сек" in warnings[0]

    now[0] += 31
    await send(1)
    assert len(handled) == 6  # пауза закончилась

    for _ in range(10):
        await send(99)  # админа не ограничиваем
    assert len(handled) == 16


async def test_booking_limits(user, monkeypatch):
    monkeypatch.setattr(settings, "max_active_bookings", 2)
    monkeypatch.setattr(settings, "max_bookings_per_day", 3)
    client = user(1)
    await client.book(slot=0)
    await client.book(slot=3)
    r = await client.send("Записаться")
    assert "2 активные записи" in texts(r)[0]

    await client.send("Мои записи")
    ask = client.callbacks("my:ask:")[0]
    await client.click(ask)
    await client.click(ask.replace(":ask:", ":cancel:"))
    await client.book(slot=6)  # 3-я за сутки — ещё можно

    await client.send("Мои записи")
    ask = client.callbacks("my:ask:")[0]
    await client.click(ask)
    await client.click(ask.replace(":ask:", ":cancel:"))
    r = await client.send("Записаться")
    assert "Слишком много записей за сутки" in texts(r)[0]


async def test_admin_has_no_booking_limits(admin, monkeypatch):
    monkeypatch.setattr(settings, "max_active_bookings", 1)
    await admin.book(slot=0)
    r = await admin.book(slot=3)
    assert any("Заявка отправлена" in t for t in texts(r))
