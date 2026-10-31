"""Фейковый Telegram API и «пользователь в чате» для сквозных тестов.

Апдейты подаются в настоящий Dispatcher, а все запросы бота к Telegram
перехватывает FakeTelegram: можно проверить, что и кому бот отправил.
"""

import itertools
from collections.abc import Callable
from datetime import datetime
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage, TelegramMethod
from aiogram.types import InlineKeyboardButton, Message

_ids = itertools.count(1000)
_update_ids = itertools.count(1)


class FakeTelegram(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod] = []

    # Сигнатура задана базовым классом aiogram, отсюда и `timeout`.
    async def make_request(self, bot: Bot, method: TelegramMethod, timeout: int | None = None):  # noqa: ASYNC109
        self.calls.append(method)
        if isinstance(method, SendMessage | EditMessageText):
            markup = method.reply_markup
            return Message.model_validate(
                {
                    "message_id": getattr(method, "message_id", None) or next(_ids),
                    "date": int(datetime.now().timestamp()),
                    "chat": {"id": method.chat_id or 0, "type": "private"},
                    "text": method.text,
                    **(
                        {"reply_markup": markup.model_dump(exclude_none=True)}
                        if getattr(markup, "inline_keyboard", None)
                        else {}
                    ),
                },
                context={"bot": bot},
            )
        return True

    async def stream_content(self, *args: Any, **kwargs: Any):
        raise NotImplementedError

    async def close(self) -> None:
        pass

    def sent_to(self, chat_id: int, since: int = 0) -> list[SendMessage]:
        return [
            m for m in self.calls[since:] if isinstance(m, SendMessage) and m.chat_id == chat_id
        ]


def texts(calls: list[TelegramMethod]) -> list[str]:
    """Тексты отправленных/изменённых сообщений и всплывающих окон."""
    out = []
    for m in calls:
        if (
            isinstance(m, SendMessage | EditMessageText)
            or isinstance(m, AnswerCallbackQuery)
            and m.text
        ):
            out.append(m.text)
    return out


class TgUser:
    """Пользователь в личном чате с ботом (chat_id == user_id)."""

    def __init__(self, dp: Dispatcher, bot: Bot, api: FakeTelegram, user_id: int, name: str):
        self.dp, self.bot, self.api = dp, bot, api
        self.id, self.name = user_id, name
        self.inline: list[list[InlineKeyboardButton]] = []
        self.inline_message_id = 1

    def _from(self) -> dict:
        return {"id": self.id, "is_bot": False, "first_name": self.name}

    def _chat(self) -> dict:
        return {"id": self.id, "type": "private"}

    def _collect(self, start: int) -> list[TelegramMethod]:
        new = self.api.calls[start:]
        for m in new:
            if getattr(m, "chat_id", None) not in (self.id, None):
                continue
            keyboard = (
                getattr(m.reply_markup, "inline_keyboard", None)
                if hasattr(m, "reply_markup")
                else None
            )
            if keyboard:
                self.inline = keyboard
                self.inline_message_id = getattr(m, "message_id", None) or 1
        return [m for m in new if getattr(m, "chat_id", None) in (self.id, None)]

    async def send(self, text: str | None = None, contact: str | None = None) -> list:
        start = len(self.api.calls)
        msg: dict[str, Any] = {
            "message_id": next(_ids),
            "date": int(datetime.now().timestamp()),
            "chat": self._chat(),
            "from": self._from(),
        }
        if text is not None:
            msg["text"] = text
        if contact is not None:
            msg["contact"] = {"phone_number": contact, "first_name": self.name, "user_id": self.id}
        await self.dp.feed_raw_update(self.bot, {"update_id": next(_update_ids), "message": msg})
        return self._collect(start)

    async def click(self, data: str) -> list:
        start = len(self.api.calls)
        await self.dp.feed_raw_update(
            self.bot,
            {
                "update_id": next(_update_ids),
                "callback_query": {
                    "id": str(next(_ids)),
                    "from": self._from(),
                    "chat_instance": "test",
                    "data": data,
                    "message": {
                        "message_id": self.inline_message_id,
                        "date": int(datetime.now().timestamp()),
                        "chat": self._chat(),
                        "from": {"id": 42, "is_bot": True, "first_name": "bot"},
                        "text": "…",
                    },
                },
            },
        )
        return self._collect(start)

    def buttons(self) -> list[InlineKeyboardButton]:
        return [b for row in self.inline for b in row]

    def button(self, pred: Callable[[InlineKeyboardButton], bool]) -> str:
        return next(b.callback_data for b in self.buttons() if pred(b))

    def callbacks(self, prefix: str) -> list[str]:
        return [
            b.callback_data for b in self.buttons() if (b.callback_data or "").startswith(prefix)
        ]

    async def book(
        self, service: str = "Услуга за 500", slot: int = 0, phone: str = "+79001112233"
    ):
        """Полный путь записи: первая доступная дата, slot-е свободное время."""
        await self.send("Записаться")
        await self.click(self.button(lambda b: b.text.startswith(service)))
        await self.click(self.callbacks("cal:day:")[0])
        await self.click(self.callbacks("slot:")[slot])
        await self.send(phone)
        return await self.click("nav:confirm")


# --- MAX ------------------------------------------------------------------


class FakeMaxApi:
    """Подмена max_bot.api.MaxApi: запоминает вызовы вместо HTTP-запросов."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.keyboards: dict[str, list] = {}  # mid -> клавиатура
        self.texts: dict[str, str] = {}  # mid -> текст
        self.owner: dict[str, int] = {}  # mid -> user_id
        self._mids = itertools.count(1)

    async def send_message(self, user_id: int, text: str, keyboard=None) -> str:
        mid = f"mid.{next(self._mids)}"
        self.calls.append(("send", user_id, text, keyboard))
        self.keyboards[mid], self.texts[mid], self.owner[mid] = keyboard or [], text, user_id
        return mid

    async def edit_message(self, mid: str, text: str, keyboard=None) -> None:
        self.calls.append(("edit", mid, text, keyboard))
        self.keyboards[mid], self.texts[mid] = keyboard or [], text

    async def answer_callback(self, callback_id, *, text=None, keyboard=None, notification=None):
        mid = callback_id.split("|", 1)[1]  # в тестах callback_id = «N|mid»
        self.calls.append(("answer", mid, text, keyboard, notification))
        if text is not None:
            self.keyboards[mid], self.texts[mid] = keyboard or [], text

    def sent_to(self, user_id: int, since: int = 0) -> list[str]:
        return [c[2] for c in self.calls[since:] if c[0] == "send" and c[1] == user_id]


def max_texts(calls: list[tuple]) -> list[str]:
    """Тексты новых/изменённых сообщений и всплывающих уведомлений."""
    out = []
    for c in calls:
        if c[0] in ("send", "edit") or (c[0] == "answer" and c[2] is not None):
            out.append(c[2])
        if c[0] == "answer" and c[4]:
            out.append(c[4])
    return out


class MaxUser:
    """Пользователь MAX в личном диалоге с ботом."""

    def __init__(self, bot, api: FakeMaxApi, user_id: int, name: str = "Клиент"):
        self.bot, self.api, self.id = bot, api, user_id
        self.user = {"user_id": user_id, "first_name": name, "is_bot": False}

    def _messages(self) -> list[str]:
        return [mid for mid, owner in self.api.owner.items() if owner == self.id]

    def buttons(self) -> list[dict]:
        """Кнопки самого свежего сообщения с клавиатурой."""
        for mid in reversed(self._messages()):
            if self.api.keyboards.get(mid):
                return [b for row in self.api.keyboards[mid] for b in row]
        return []

    def payloads(self, prefix: str) -> list[str]:
        return [b["payload"] for b in self.buttons() if b.get("payload", "").startswith(prefix)]

    def button(self, text_prefix: str) -> str:
        return next(b["payload"] for b in self.buttons() if b["text"].startswith(text_prefix))

    async def _feed(self, update: dict) -> list[tuple]:
        start = len(self.api.calls)
        await self.bot.handle(update)
        return self.api.calls[start:]

    async def start(self) -> list[tuple]:
        return await self._feed(
            {"update_type": "bot_started", "timestamp": 0, "chat_id": self.id, "user": self.user}
        )

    async def send(self, text: str = "", vcf: str | None = None, chat_type: str = "dialog"):
        attachments = (
            [{"type": "contact", "payload": {"vcf_info": vcf, "max_info": self.user}}]
            if vcf
            else []
        )
        return await self._feed(
            {
                "update_type": "message_created",
                "timestamp": 0,
                "message": {
                    "sender": self.user,
                    "recipient": {"chat_type": chat_type, "user_id": 1},
                    "timestamp": 0,
                    "body": {
                        "mid": f"u.{next(_ids)}",
                        "seq": 1,
                        "text": text,
                        "attachments": attachments,
                    },
                },
            }
        )

    async def click(self, payload: str) -> list[tuple]:
        # Нажимаем в том сообщении, где есть такая кнопка (иначе — в последнем).
        mids = self._messages()
        mid = next(
            (
                m
                for m in reversed(mids)
                if any(
                    b.get("payload") == payload
                    for row in self.api.keyboards.get(m, [])
                    for b in row
                )
            ),
            mids[-1] if mids else "mid.0",
        )
        return await self._feed(
            {
                "update_type": "message_callback",
                "timestamp": 0,
                "callback": {
                    "timestamp": 0,
                    "callback_id": f"{next(_ids)}|{mid}",
                    "payload": payload,
                    "user": self.user,
                },
                "message": {"recipient": {"chat_type": "dialog"}, "body": {"mid": mid, "seq": 1}},
            }
        )

    async def book(
        self, service: str = "Услуга за 500", slot: int = 0, phone: str = "+79005556677"
    ):
        await self.click("menu:book")
        await self.click(self.button(service))
        await self.click(self.payloads("cal:day:")[0])
        await self.click(self.payloads("slot:")[slot])
        await self.send(
            vcf=f"BEGIN:VCARD\nVERSION:3.0\nTEL;TYPE=cell:{phone.lstrip('+')}\nEND:VCARD"
        )
        return await self.click("nav:confirm")
