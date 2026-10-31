from aiogram import Router

from telegram_bot.handlers import booking, fallback, menu, my_bookings


def setup_routers() -> list[Router]:
    # Порядок важен: fallback (эхо и устаревшие кнопки) — последним.
    return [menu.router, booking.router, my_bookings.router, fallback.router]
