from aiogram import Router

from telegram_bot.handlers import admin, admin_schedule, booking, fallback, menu, my_bookings


def setup_routers() -> list[Router]:
    # Порядок важен: fallback (эхо и устаревшие кнопки) — последним.
    return [
        admin.router,
        admin_schedule.router,
        menu.router,
        booking.router,
        my_bookings.router,
        fallback.router,
    ]
