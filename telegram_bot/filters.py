from typing import Any

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from core.config import settings


class AccessibleMessage(BaseFilter):
    """Пропускает callback, только если его сообщение доступно для редактирования.

    Для сообщений старше 48 часов Telegram присылает InaccessibleMessage.
    Сообщение передаётся в хендлер аргументом `msg` с типом Message.
    """

    async def __call__(self, callback: CallbackQuery) -> bool | dict[str, Any]:
        if isinstance(callback.message, Message):
            return {"msg": callback.message}
        return False


class IsAdmin(BaseFilter):
    """Пользователь указан в ADMIN_IDS."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return event.from_user is not None and event.from_user.id in settings.admin_ids
