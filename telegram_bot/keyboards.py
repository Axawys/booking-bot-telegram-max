from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from core import texts

main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=texts.BTN_BOOK), KeyboardButton(text=texts.BTN_MY_BOOKINGS)],
        [KeyboardButton(text=texts.BTN_SERVICES), KeyboardButton(text=texts.BTN_CONTACTS)],
    ],
    resize_keyboard=True,
    is_persistent=True,
    input_field_placeholder="Выберите пункт меню",
)
