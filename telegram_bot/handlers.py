from aiogram import F, Router, html
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Message

from core import texts
from telegram_bot.keyboards import main_menu

router = Router(name="main")


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    name = html.quote(message.from_user.full_name) if message.from_user else "друг"
    await message.answer(
        texts.START.format(name=name),
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu,
    )


@router.message(F.text == texts.BTN_BOOK)
async def book(message: Message) -> None:
    await message.answer(texts.BOOK, parse_mode=ParseMode.HTML)


@router.message(F.text == texts.BTN_MY_BOOKINGS)
async def my_bookings(message: Message) -> None:
    await message.answer(texts.MY_BOOKINGS, parse_mode=ParseMode.HTML)


@router.message(F.text == texts.BTN_SERVICES)
async def services(message: Message) -> None:
    await message.answer(texts.SERVICES, parse_mode=ParseMode.HTML)


@router.message(F.text == texts.BTN_CONTACTS)
async def contacts(message: Message) -> None:
    await message.answer(texts.CONTACTS, parse_mode=ParseMode.HTML)


@router.message()
async def echo(message: Message) -> None:
    try:
        await message.send_copy(chat_id=message.chat.id)
    except TypeError:
        await message.answer(texts.UNSUPPORTED)
