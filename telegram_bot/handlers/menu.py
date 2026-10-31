from aiogram import F, Router, html
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from core import schedule, texts
from core.config import settings
from db import repo
from telegram_bot.keyboards import admin_menu, client_menu

router = Router(name="menu")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    name = html.quote(message.from_user.full_name) if message.from_user else "друг"
    if settings.is_admin(message.from_user.id):
        pending = await repo.count_pending_bookings(session, schedule.now_local())
        await message.answer(
            texts.admin_start(name, texts.admin_pending_count(pending)), reply_markup=admin_menu
        )
        return
    await message.answer(texts.START.format(name=name), reply_markup=client_menu())


@router.message(F.text == texts.BTN_SERVICES)
async def services(message: Message, session: AsyncSession) -> None:
    items = await repo.get_active_services(session)
    await message.answer(texts.services_list(items))


@router.message(F.text == texts.BTN_CONTACTS)
async def contacts(message: Message) -> None:
    await message.answer(texts.CONTACTS)
