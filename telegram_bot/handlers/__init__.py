from aiogram import Router

from telegram_bot.handlers import booking, fallback, menu


def setup_routers() -> list[Router]:
    # Порядок важен: fallback (эхо и устаревшие кнопки) — последним.
    return [menu.router, booking.router, fallback.router]
