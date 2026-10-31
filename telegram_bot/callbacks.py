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


class AdminAction(StrEnum):
    CONFIRM = "confirm"
    REJECT = "reject"
    BAN_ASK = "ban_ask"
    BAN = "ban"
    BACK = "back"


class AdminBookingCb(CallbackData, prefix="adm"):
    action: AdminAction
    booking_id: int


class AdminCalAction(StrEnum):
    NAV = "nav"
    DAY = "day"


class AdminCalCb(CallbackData, prefix="acal"):
    action: AdminCalAction
    year: int
    month: int
    day: int = 0


class AdminDayAction(StrEnum):
    TOGGLE = "toggle"
    CLOSE_DAY = "close"
    OPEN_DAY = "open"
    BOOKED = "booked"


class AdminDayCb(CallbackData, prefix="aday"):
    action: AdminDayAction
    year: int
    month: int
    day: int
    hour: int = 0
    minute: int = 0


class HorizonAction(StrEnum):
    SHOW = "show"
    SET = "set"


class HorizonCb(CallbackData, prefix="hz"):
    action: HorizonAction
    days: int = 0
