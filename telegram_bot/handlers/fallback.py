from aiogram import Router
from aiogram.types import CallbackQuery, Message

from core import texts

router = Router(name="fallback")


@router.callback_query()
async def stale_callback(callback: CallbackQuery) -> None:
    """Кнопки старых сообщений, для которых больше нет подходящего состояния."""
    await callback.answer(texts.STALE_BUTTON, show_alert=True)


@router.message()
async def echo(message: Message) -> None:
    try:
        await message.send_copy(chat_id=message.chat.id)
    except TypeError:
        await message.answer(texts.UNSUPPORTED)
