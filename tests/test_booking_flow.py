"""Сквозной сценарий записи клиента через настоящий Dispatcher."""

from sqlalchemy import text

from core.config import settings
from db.session import session_factory
from tests.helpers import texts


async def _bookings():
    async with session_factory() as s:
        return (await s.execute(text("select status from bookings order by id"))).scalars().all()


async def test_full_flow_creates_pending_booking_and_notifies_admins(user, api):
    client = user(1, "Анна")
    r = await client.send("Записаться")
    assert "шаг 1 из 4" in texts(r)[0]

    await client.click(client.button(lambda b: b.text.startswith("Услуга за 500")))
    assert client.callbacks("cal:day:"), "в календаре должны быть доступные дни"
    await client.click(client.callbacks("cal:day:")[0])
    await client.click(client.callbacks("slot:")[0])

    r = await client.send("привет")
    assert "Не похоже на номер" in texts(r)[0]
    r = await client.send("8 (900) 111-22-33")
    assert any("Проверьте запись" in t and "+79001112233" in t for t in texts(r))

    mark = len(api.calls)
    r = await client.click("nav:confirm")
    assert any("Заявка отправлена" in t for t in texts(r))
    assert await _bookings() == ["pending"]
    for admin_id in settings.admin_ids:
        card = api.sent_to(admin_id, mark)[0]
        assert "Новая заявка" in card.text
        assert [b.text for row in card.reply_markup.inline_keyboard for b in row] == [
            "✅ Подтвердить",
            "❌ Отклонить",
            "⛔ Бан",
        ]


async def test_auto_confirm_without_admins(user, monkeypatch):
    monkeypatch.setattr(settings, "admin_ids", [])
    r = await user(1).book()
    assert any("Вы записаны" in t for t in texts(r))
    assert await _bookings() == ["confirmed"]


async def test_same_slot_race_second_client_rejected_by_db(user):
    a, b = user(1, "Анна"), user(2, "Борис")
    # Оба доходят до подтверждения одного и того же времени.
    for u in (a, b):
        await u.send("Записаться")
        await u.click(u.button(lambda x: x.text.startswith("Услуга за 3 000")))
        await u.click(u.callbacks("cal:day:")[0])
        await u.click(u.callbacks("slot:")[0])
        await u.send("+79001112233")
    taken = a.callbacks("slot:") or None  # клавиатура A уже сменилась на подтверждение
    assert taken is None

    await a.click("nav:confirm")
    r = await b.click("nav:confirm")
    assert any("только что заняли" in t for t in texts(r))
    assert await _bookings() == ["pending"]


async def test_back_cancel_and_stale_buttons(user):
    client = user(1)
    await client.send("Записаться")
    await client.click(client.button(lambda b: b.text.startswith("Услуга за 500")))
    r = await client.click("nav:back")
    assert "шаг 1 из 4" in texts(r)[0]
    r = await client.click("nav:cancel")
    assert "Запись отменена" in texts(r)[0]
    r = await client.click("cal:nav:2026:12:0")
    assert "кнопка устарела" in texts(r)[0]


async def test_unknown_command_is_not_echoed(user):
    r = await user(1).send("/whatever")
    assert texts(r) == ["Неизвестная команда. Главное меню — /start"]
