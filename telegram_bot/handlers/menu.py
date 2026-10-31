from aiogram import F, Router, html
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from core import texts
from db import repo
from telegram_bot.keyboards import main_menu

router = Router(name="menu")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    name = html.quote(message.from_user.full_name) if message.from_user else "друг"
    await message.answer(texts.START.format(name=name), reply_markup=main_menu)


@router.message(F.text == texts.BTN_SERVICES)
async def services(message: Message, session: AsyncSession) -> None:
    items = await repo.get_active_services(session)
    await message.answer(texts.services_list(items))


@router.message(F.text == texts.BTN_CONTACTS)
async def contacts(message: Message) -> None:
    await message.answer(texts.CONTACTS)
