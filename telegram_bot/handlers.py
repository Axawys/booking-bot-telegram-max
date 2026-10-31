from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

router = Router(name="echo")


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        f"Привет, {message.from_user.full_name}! Отправь мне любое сообщение — я его повторю."
    )


@router.message()
async def echo(message: Message) -> None:
    try:
        await message.send_copy(chat_id=message.chat.id)
    except TypeError:
        await message.answer("Этот тип сообщения я пока не умею повторять.")
