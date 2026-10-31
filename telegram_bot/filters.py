from typing import Any

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message


class AccessibleMessage(BaseFilter):
    """Пропускает callback, только если его сообщение доступно для редактирования.

    Для сообщений старше 48 часов Telegram присылает InaccessibleMessage.
    Сообщение передаётся в хендлер аргументом `msg` с типом Message.
    """

    async def __call__(self, callback: CallbackQuery) -> bool | dict[str, Any]:
        if isinstance(callback.message, Message):
            return {"msg": callback.message}
        return False
