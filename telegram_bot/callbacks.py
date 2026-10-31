from enum import StrEnum

from aiogram.filters.callback_data import CallbackData

# Кнопки-заглушки календаря (заголовок, дни недели, недоступные дни).
NOOP = "noop"


class ServiceCb(CallbackData, prefix="svc"):
    id: int


class CalendarAction(StrEnum):
    NAV = "nav"
    DAY = "day"


class CalendarCb(CallbackData, prefix="cal"):
    action: CalendarAction
    year: int
    month: int
    day: int = 0


class SlotCb(CallbackData, prefix="slot"):
    hour: int
    minute: int


class NavAction(StrEnum):
    BACK = "back"
    CANCEL = "cancel"
    CONFIRM = "confirm"


class NavCb(CallbackData, prefix="nav"):
    action: NavAction


class MyBookingAction(StrEnum):
    LIST = "list"
    ASK = "ask"
    CANCEL = "cancel"


class MyBookingCb(CallbackData, prefix="my"):
    action: MyBookingAction
    booking_id: int = 0
