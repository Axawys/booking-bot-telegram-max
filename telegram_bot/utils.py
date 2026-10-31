from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message


async def safe_edit(msg: Message, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """edit_text, который не падает, если содержимое сообщения не изменилось."""
    try:
        await msg.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise
