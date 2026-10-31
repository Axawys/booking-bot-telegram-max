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
