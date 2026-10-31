from aiogram.fsm.state import State, StatesGroup


class BookingForm(StatesGroup):
    service = State()
    date = State()
    time = State()
    phone = State()
    confirm = State()
