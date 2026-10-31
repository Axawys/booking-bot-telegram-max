"""Админка: подтверждение заявок, «Мои записи», баны, закрытие времени, горизонт."""

from datetime import timedelta

from sqlalchemy import text

from core import schedule
from db.session import session_factory
from tests.helpers import texts


async def _q(sql: str):
    async with session_factory() as s:
        return (await s.execute(text(sql))).all()


async def _last_booking_id() -> int:
    return (await _q("select max(id) from bookings"))[0][0]


async def test_confirm_notifies_client_and_second_admin_is_too_late(user, api):
    client, admin1, admin2 = user(1), user(99), user(98)
    await client.book()
    bid = await _last_booking_id()

    mark = len(api.calls)
    r = await admin1.click(f"adm:confirm:{bid}")
    assert "подтверждена" in texts(r)[0]
    assert "Ваша запись подтверждена" in api.sent_to(1, mark)[0].text

    r = await admin2.click(f"adm:reject:{bid}")
    assert "уже принято решение" in texts(r)[0]
    assert await _q(f"select status from bookings where id={bid}") == [("confirmed",)]


async def test_reject_frees_slots(user, api):
    client, admin = user(1), user(99)
    await client.book()
    bid = await _last_booking_id()
    mark = len(api.calls)
    await admin.click(f"adm:reject:{bid}")
    assert "отклонена" in api.sent_to(1, mark)[0].text
    assert await _q(f"select count(*) from booking_slots where booking_id={bid}") == [(0,)]


async def test_my_bookings_cancel_and_forged_cancel(user, api):
    anna, boris = user(1, "Анна"), user(2, "Борис")
    await anna.book()
    bid = await _last_booking_id()

    r = await boris.click(f"my:cancel:{bid}")
    assert "Запись не найдена" in texts(r)[0]

    await anna.send("Мои записи")
    ask = anna.callbacks("my:ask:")[0]
    await anna.click(ask)
    mark = len(api.calls)
    r = await anna.click(ask.replace(":ask:", ":cancel:"))
    assert "Запись отменена" in texts(r)[0]
    assert "Клиент отменил запись" in api.sent_to(99, mark)[0].text
    assert await _q(f"select status from bookings where id={bid}") == [("cancelled",)]


async def test_non_admin_has_no_access(user):
    client = user(1)
    r = await client.send("/admin")
    assert "Неизвестная команда" in texts(r)[0]
    r = await client.click("adm:confirm:1")
    assert "кнопка устарела" in texts(r)[0]


async def test_admin_menu_and_commands(admin):
    r = await admin.send("/start")
    menu = r[-1].reply_markup.keyboard
    assert menu[0][0].text == "⏳ Заявки"
    for cmd in ("/admin", "/pending", "/today", "/tomorrow", "/stats", "/banned", "/schedule"):
        assert texts(await admin.send(cmd)), cmd
    assert "Укажите дату" in texts(await admin.send("/day 31.02"))[0]


async def test_ban_from_card_and_banned_user_is_ignored(user, api):
    spammer, admin = user(3, "Спамер"), user(99)
    await spammer.book()
    bid = await _last_booking_id()

    await admin.click(f"adm:ban_ask:{bid}")
    assert [b.text for b in admin.buttons()] == ["⛔ Да, забанить", "⬅ Назад"]
    r = await admin.click(f"adm:ban:{bid}")
    assert "Пользователь забанен" in texts(r)[0]
    assert await _q(f"select status from bookings where id={bid}") == [("rejected",)]

    r = await spammer.send("/start")
    assert texts(r) == ["⛔ Доступ к боту ограничен администратором."]
    assert texts(await spammer.send("ещё")) == []  # дальше — тишина

    assert "разбанен" in texts(await admin.send("/unban 3"))[0]
    assert "шаг 1 из 4" in texts(await spammer.send("Записаться"))[0]


async def test_cant_ban_admin(admin):
    assert texts(await admin.send("/ban 98")) == ["Нельзя забанить администратора."]


async def test_close_period_hides_days_and_keeps_bookings(user, admin):
    client = user(1)
    await client.book()
    bid = await _last_booking_id()
    day = (await _q(f"select date(starts_at) from bookings where id={bid}"))[0][0]
    d = f"{day[8:10]}.{day[5:7]}"

    r = await admin.send(f"/close {d}")
    assert "Записи в этом периоде сохранены (1)" in texts(r)[0]
    assert await _q(f"select status from bookings where id={bid}") == [("pending",)]

    other = user(2)
    await other.send("Записаться")
    await other.click(other.button(lambda b: b.text.startswith("Услуга за 500")))
    y, m, dd = day.split("-")
    assert f"cal:day:{int(y)}:{int(m)}:{int(dd)}" not in other.callbacks("cal:day:")

    await admin.send(f"/open {d} 13:00-14:00")
    await other.send("Записаться")
    await other.click(other.button(lambda b: b.text.startswith("Услуга за 500")))
    target = f"cal:day:{int(y)}:{int(m)}:{int(dd)}"
    if target in other.callbacks("cal:day:"):  # день мог уже закончиться
        await other.click(target)
        assert {b.text for b in other.buttons() if ":" in b.text} <= {"13:00", "13:30"}


async def test_horizon_limits_client_calendar(user, admin):
    r = await admin.send("/horizon 0")
    assert "только на сегодня" in texts(r)[0]
    assert "от 0 до 365" in texts(await admin.send("/horizon 400"))[0]

    client = user(1)
    await client.send("Записаться")
    await client.click(client.button(lambda b: b.text.startswith("Услуга за 500")))
    today = schedule.now_local().date()
    allowed = {f"cal:day:{today.year}:{today.month}:{today.day}"}
    assert set(client.callbacks("cal:day:")) <= allowed

    await admin.send("/horizon 3")
    await client.send("Записаться")
    await client.click(client.button(lambda b: b.text.startswith("Услуга за 500")))
    last = today + timedelta(days=3)
    days = [tuple(map(int, c.split(":")[2:])) for c in client.callbacks("cal:day:")]
    assert max(days) <= (last.year, last.month, last.day)
