"""MAX-бот: те же сценарии поверх общего core, плюс взаимодействие с Telegram
(админы в Telegram, гонка клиентов разных мессенджеров за один слот)."""

import json

import pytest
from aiohttp import web
from sqlalchemy import text

from core import texts as core_texts
from core.config import settings
from core.phone import phone_from_vcard
from core.ratelimit import RateLimiter
from db.session import session_factory
from max_bot.api import MaxApi, MaxApiError
from max_bot.bot import MaxBot
from notifications.scheduler import BookingScheduler
from tests.helpers import MaxUser, max_texts, texts


async def _q(sql: str):
    async with session_factory() as s:
        return (await s.execute(text(sql))).all()


async def _last_booking_id() -> int:
    return (await _q("select max(id) from bookings"))[0][0]


async def test_start_and_menu(max_user, max_api):
    u = max_user(501, "Анна")
    r = await u.start()
    assert "Привет, Анна" in max_texts(r)[0]
    assert {b["payload"] for b in u.buttons()} == {
        "menu:book",
        "menu:my",
        "menu:services",
        "menu:contacts",
    }

    r = await u.click("menu:services")
    assert "Услуга за 500 ₽" in max_texts(r)[0]
    r = await u.click("menu:contacts")
    assert "Контакты" in max_texts(r)[0]
    assert max_texts(await u.send("/nope")) == ["Неизвестная команда. Главное меню — /start"]


async def test_full_flow_with_contact_and_admin_card_in_telegram(max_user, api):
    u = max_user(502, "Анна")
    await u.click("menu:book")
    await u.click(u.button("Услуга за 500"))
    await u.click(u.payloads("cal:day:")[0])
    r = await u.click(u.payloads("slot:")[0])
    assert any("шаг 4 из 4" in t for t in max_texts(r))
    assert u.buttons()[0] == {"type": "request_contact", "text": "📱 Отправить номер"}

    r = await u.send("не номер")
    assert "Не похоже на номер" in max_texts(r)[0]
    r = await u.send(vcf="BEGIN:VCARD\nVERSION:3.0\nTEL;TYPE=cell:79005556677\nEND:VCARD")
    assert any("Проверьте запись" in t and "+79005556677" in t for t in max_texts(r))

    mark = len(api.calls)
    r = await u.click("nav:confirm")
    assert any("Заявка отправлена" in t for t in max_texts(r))
    card = api.sent_to(99, mark)[0]  # админ — в Telegram
    assert "Новая заявка" in card.text and "Анна (MAX)" in card.text
    assert "max 502" in card.text  # ID для /ban max …
    assert await _q(
        "select c.platform, b.status from bookings b join clients c on c.id = b.client_id"
    ) == [("max", "pending")]


async def test_saved_phone_button_on_second_booking(max_user):
    u = max_user(503)
    await u.book(slot=0, phone="+79001230000")
    await u.click("menu:book")
    await u.click(u.button("Услуга за 500"))
    await u.click(u.payloads("cal:day:")[0])
    await u.click(u.payloads("slot:")[2])
    assert "phone:saved" in [b.get("payload") for b in u.buttons()]
    r = await u.click("phone:saved")
    assert any("+79001230000" in t for t in max_texts(r))


async def test_telegram_and_max_clients_race_for_one_slot(user, max_user):
    tg, mx = user(1, "Telegram"), max_user(504, "MAX")
    # Оба доходят до подтверждения одного и того же времени.
    await tg.send("Записаться")
    await tg.click(tg.button(lambda b: b.text.startswith("Услуга за 3 000")))
    await tg.click(tg.callbacks("cal:day:")[0])
    await tg.click(tg.callbacks("slot:")[0])
    await tg.send("+79001112233")

    await mx.click("menu:book")
    await mx.click(mx.button("Услуга за 3 000"))
    await mx.click(mx.payloads("cal:day:")[0])
    await mx.click(mx.payloads("slot:")[0])
    await mx.send("+79005556677")

    await tg.click("nav:confirm")
    r = await mx.click("nav:confirm")
    assert any("только что заняли" in t for t in max_texts(r))
    assert await _q("select count(*) from bookings") == [(1,)]


async def test_admin_decision_reaches_max_client(max_user, user, max_api):
    mx, admin = max_user(505), user(99)
    await mx.book()
    bid = await _last_booking_id()
    mark = len(max_api.calls)
    await admin.click(f"adm:confirm:{bid}")
    assert any("Ваша запись подтверждена" in t for t in max_api.sent_to(505, mark))


async def test_my_bookings_cancel_notifies_admins(max_user, api):
    u = max_user(506)
    await u.book()
    r = await u.click("menu:my")
    assert "ожидает подтверждения" in max_texts(r)[0]
    ask = u.payloads("my:ask:")[0]
    await u.click(ask)
    mark = len(api.calls)
    r = await u.click(ask.replace(":ask:", ":cancel:"))
    assert "Запись на" in max_texts(r)[0] and "отменена" in max_texts(r)[0]
    assert "Клиент отменил запись" in api.sent_to(99, mark)[0].text

    other = max_user(507)
    r = await other.click(ask.replace(":ask:", ":cancel:"))  # чужая/отменённая
    assert "Запись не найдена" in max_texts(r)[0]


async def test_ban_max_user_from_telegram(max_user, user):
    mx, admin = max_user(508), user(99)
    await mx.book()
    r = await admin.send("/ban max 508 спам")
    assert "забанен" in texts(r)[0] and "/unban max 508" in texts(r)[0]
    assert await _q("select status from bookings") == [("rejected",)]

    assert max_texts(await mx.start()) == ["⛔ Доступ к боту ограничен администратором."]
    assert max_texts(await mx.click("menu:book")) == []  # дальше — тишина
    assert "разбанен" in texts(await admin.send("/unban max 508"))[0]
    assert "шаг 1 из 4" in max_texts(await mx.click("menu:book"))[0]


async def test_stale_buttons_group_chats_and_back_navigation(max_user):
    u = max_user(509)
    r = await u.click("slot:10:0")
    assert max_texts(r) == [core_texts.STALE_BUTTON]
    assert await u.send("привет", chat_type="chat") == []  # группы игнорируем

    await u.click("menu:book")
    await u.click(u.button("Услуга за 500"))
    r = await u.click("nav:back")
    assert "шаг 1 из 4" in max_texts(r)[0]
    r = await u.click("nav:cancel")
    assert "Запись отменена" in max_texts(r)[0]


async def test_reminder_to_max_client_has_cancel_button(max_user, max_api, notifier, monkeypatch):
    monkeypatch.setattr(settings, "admin_ids", [])  # без админов запись сразу подтверждена
    u = max_user(510)
    r = await u.book()
    assert any("Вы записаны" in t for t in max_texts(r))
    bid = await _last_booking_id()

    sched = BookingScheduler(notifier, session_factory)
    mark = len(max_api.calls)
    await sched._remind(bid, 24)
    reminder = [c for c in max_api.calls[mark:] if c[0] == "send"]
    assert len(reminder) == 1 and "Напоминаем" in reminder[0][2]
    assert reminder[0][3] == [
        [{"type": "callback", "text": "✖ Отменить запись", "payload": f"my:ask:{bid}"}]
    ]


async def test_scheduler_picks_up_max_bookings_on_resync(max_user, scheduler):
    # Запись создана «в другом процессе» (MAX-бот) — планировщик о ней не знает...
    await max_user(511).book()
    bid = await _last_booking_id()
    assert f"expire:{bid}" not in {j.id for j in scheduler.scheduler.get_jobs()}
    # ...пока не сверится с БД.
    await scheduler.reconcile()
    assert f"expire:{bid}" in {j.id for j in scheduler.scheduler.get_jobs()}


async def test_throttling(max_api, notifier):
    now = [0.0]
    bot = MaxBot(max_api, notifier, session_factory, RateLimiter(3, 10, 30, clock=lambda: now[0]))
    u = MaxUser(bot, max_api, 512)
    replies = [max_texts(await u.send("привет")) for _ in range(5)]
    assert all(r == ["Главное меню 👇"] for r in replies[:3])
    assert "Подождите 30 сек" in replies[3][0] and replies[4] == []


def test_phone_from_vcard():
    vcf = "BEGIN:VCARD\nVERSION:3.0\nFN:Анна\nTEL;TYPE=cell:79001234567\nEND:VCARD"
    assert phone_from_vcard(vcf) == "+79001234567"
    assert phone_from_vcard("TEL:8 900 111-22-33") == "+79001112233"
    assert phone_from_vcard(None) is None and phone_from_vcard("FN:Анна") is None


# --- HTTP-контракт клиента MAX API -----------------------------------------


@pytest.fixture
async def max_server():
    """Локальный HTTP-сервер, изображающий platform-api2.max.ru."""
    requests: list[dict] = []
    state = {"rate_limited": 1}

    async def record(request: web.Request) -> web.Response:
        body = await request.text()
        requests.append(
            {
                "method": request.method,
                "path": request.path,
                "query": dict(request.query),
                "auth": request.headers.get("Authorization"),
                "json": json.loads(body) if body else None,
            }
        )
        if request.path == "/messages" and request.method == "POST":
            if state["rate_limited"]:
                state["rate_limited"] -= 1
                return web.json_response({"code": "too.many.requests"}, status=429)
            return web.json_response({"message": {"body": {"mid": "mid.42", "seq": 1}}})
        if request.path == "/me":
            return web.json_response({"code": "verify.token"}, status=401)
        return web.json_response({"success": True})

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", record)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}", requests
    await runner.cleanup()


async def test_max_api_http_contract(max_server):
    base_url, requests = max_server
    api = MaxApi("secret-token", base_url)
    try:
        keyboard = [[{"type": "callback", "text": "OK", "payload": "ok"}]]
        assert await api.send_message(77, "<b>Привет</b>", keyboard) == "mid.42"
        await api.answer_callback("cb-1", text="Изменено")
        await api.edit_message("mid.42", "Без кнопок")
        with pytest.raises(MaxApiError) as error:
            await api.get_me()
        assert error.value.status == 401
    finally:
        await api.close()

    send = [r for r in requests if r["method"] == "POST" and r["path"] == "/messages"]
    assert len(send) == 2  # первый ответ — 429, клиент повторил запрос
    assert send[-1]["auth"] == "secret-token"  # токен в заголовке, без «Bearer»
    assert send[-1]["query"] == {"user_id": "77"}
    assert send[-1]["json"] == {
        "text": "<b>Привет</b>",
        "format": "html",
        "attachments": [{"type": "inline_keyboard", "payload": {"buttons": keyboard}}],
    }
    answer = next(r for r in requests if r["path"] == "/answers")
    assert answer["query"] == {"callback_id": "cb-1"}
    assert answer["json"]["message"]["attachments"] == []  # клавиатура убирается
    edit = next(r for r in requests if r["method"] == "PUT")
    assert edit["query"] == {"message_id": "mid.42"}


def test_bundled_ca_is_loadable():
    import ssl

    from max_bot.api import CA_FILE

    context = ssl.create_default_context()
    context.load_verify_locations(cafile=CA_FILE)
    subjects = [dict(x[0] for x in c["subject"]) for c in context.get_ca_certs()]
    assert {"commonName": "Russian Trusted Root CA"}.items() <= next(
        s for s in subjects if s.get("commonName") == "Russian Trusted Root CA"
    ).items()
