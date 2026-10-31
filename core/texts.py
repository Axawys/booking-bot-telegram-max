"""Тексты бота. Общие для Telegram и MAX, разметка — HTML."""

from collections.abc import Iterable
from datetime import date, datetime, timedelta
from html import escape

from db.models import Service

# --- Кнопки ---------------------------------------------------------------

BTN_BOOK = "Записаться"
BTN_MY_BOOKINGS = "Мои записи"
BTN_SERVICES = "Услуги и цены"
BTN_CONTACTS = "Контакты"

BTN_SEND_PHONE = "📱 Отправить номер"
BTN_CANCEL = "Отмена"
BTN_BACK = "⬅ Назад"
BTN_CANCEL_INLINE = "✖ Отмена"
BTN_CONFIRM = "✅ Подтвердить"
BTN_CHANGE_TIME = "⬅ Изменить время"


def btn_use_saved_phone(phone: str) -> str:
    return f"Использовать {phone}"


# --- Даты, деньги ---------------------------------------------------------

MONTHS = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]
MONTHS_GEN = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
WEEKDAYS_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def format_date(day: date) -> str:
    """5 ноября, четверг"""
    return f"{day.day} {MONTHS_GEN[day.month - 1]}, {WEEKDAYS[day.weekday()]}"


def format_duration(minutes: int) -> str:
    hours, mins = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} ч")
    if mins:
        parts.append(f"{mins} мин")
    return " ".join(parts)


def rub(amount: int) -> str:
    """1500 -> '1 500 ₽' (неразрывные пробелы, чтобы цена не переносилась)."""
    return f"{amount:,}".replace(",", " ") + " ₽"


# --- Главное меню ---------------------------------------------------------

ADDRESS = "г. Москва, ул. Примерная, д. 1"
WORK_HOURS = "ежедневно с 10:00 до 21:00"
PHONE = "+7 (900) 000-00-00"

START = (
    "👋 Привет, {name}!\n\n"
    "Я — <b>бот для онлайн-записи клиентов</b>. Помогаю салонам красоты, "
    "мастерам, студиям, клиникам и другим сервисным бизнесам принимать записи "
    "24/7 — без звонков и долгой переписки.\n\n"
    "<b>Что я умею:</b>\n"
    "💼 Показывать список услуг с ценами и длительностью\n"
    "📅 Подбирать свободные дату и время\n"
    "✍️ Записывать клиента в пару нажатий\n"
    "🔔 Напоминать о визите заранее\n"
    "🔄 Переносить и отменять записи\n"
    "📲 Сразу уведомлять владельца о новой записи\n\n"
    "Работаю в <b>Telegram</b> и <b>MAX</b>.\n\n"
    "<i>Это демо-версия для портфолио: часть функций пока в разработке.</i>\n\n"
    "Выбери нужный пункт в меню ниже 👇"
)

MY_BOOKINGS = (
    "📋 <b>Мои записи</b>\n\n"
    "<i>Просмотр, перенос и отмена записей — в разработке.</i>"
)


def services_list(services: Iterable[Service]) -> str:
    lines = [
        f"• {escape(s.name)} — <b>{rub(s.price)}</b> · {format_duration(s.duration_minutes)}"
        for s in services
    ]
    if not lines:
        return "💼 <b>Услуги и цены</b>\n\nСписок услуг пока пуст."
    return (
        "💼 <b>Услуги и цены</b>\n\n"
        + "\n".join(lines)
        + "\n\n<i>Цены демонстрационные.</i>"
    )


CONTACTS = (
    "📍 <b>Контакты</b>\n\n"
    f"🏠 Адрес: {ADDRESS}\n"
    f"🕘 Режим работы: {WORK_HOURS}\n"
    f"📞 Телефон: {PHONE}\n\n"
    "<i>Данные демонстрационные.</i>"
)

UNSUPPORTED = "Этот тип сообщения я пока не умею повторять."
STALE_BUTTON = "Эта кнопка устарела. Начните запись заново — «Записаться»."
BACK_TO_MENU = "Главное меню 👇"

# --- Запись ---------------------------------------------------------------

BOOK_NO_SERVICES = "Сейчас нет доступных услуг для записи."
BOOK_CHOOSE_SERVICE = "✍️ <b>Запись</b> · шаг 1 из 4\n\nВыберите услугу:"


def book_choose_date(service_name: str) -> str:
    return (
        "📅 <b>Запись</b> · шаг 2 из 4\n\n"
        f"Услуга: <b>{escape(service_name)}</b>\n"
        "Выберите дату. Дни без свободного времени отмечены «·»."
    )


def book_choose_time(service_name: str, day: date, notice: str | None = None) -> str:
    text = (
        "🕒 <b>Запись</b> · шаг 3 из 4\n\n"
        f"Услуга: <b>{escape(service_name)}</b>\n"
        f"Дата: <b>{format_date(day)}</b>\n"
        "Выберите время начала:"
    )
    return f"{notice}\n\n{text}" if notice else text


def book_picked(service_name: str, starts_at: datetime) -> str:
    return (
        "🕒 <b>Запись</b> · шаг 3 из 4\n\n"
        f"Услуга: <b>{escape(service_name)}</b>\n"
        f"Дата и время: <b>{format_date(starts_at.date())}, {starts_at:%H:%M}</b> ✔"
    )


BOOK_ASK_PHONE = (
    "📞 <b>Запись</b> · шаг 4 из 4\n\n"
    "Оставьте номер телефона для связи: нажмите «📱 Отправить номер» "
    "или напишите его сообщением, например <code>+7 900 123-45-67</code>."
)
BOOK_PHONE_INVALID = (
    "Не похоже на номер телефона 🤔\n"
    "Напишите номер в формате <code>+7 900 123-45-67</code> или нажмите «📱 Отправить номер»."
)
BOOK_PHONE_ACCEPTED = "👍 Номер принят."

BOOK_NO_SLOTS_ALERT = "На этот день свободного времени нет. Выберите другую дату."
BOOK_DAY_UNAVAILABLE_ALERT = "На эту дату запись недоступна."
BOOK_SLOT_GONE_ALERT = "Это время уже недоступно. Выберите другое."
BOOK_SLOT_TAKEN = "😔 <b>Это время только что заняли.</b> Выберите другое:"
BOOK_TOO_LATE = "⏰ <b>Это время уже недоступно для записи.</b> Выберите другое:"
BOOK_CANCELLED = "✖ Запись отменена. Можно начать заново в любой момент."
BOOK_ERROR = "Не удалось оформить запись: услуга больше недоступна. Начните заново."


def _booking_details(
    service_name: str, price: int, duration: int, starts_at: datetime, phone: str
) -> str:
    ends_at = starts_at + timedelta(minutes=duration)
    return (
        f"💼 Услуга: <b>{escape(service_name)}</b>\n"
        f"📅 Дата: <b>{format_date(starts_at.date())}</b>\n"
        f"🕒 Время: <b>{starts_at:%H:%M}–{ends_at:%H:%M}</b> ({format_duration(duration)})\n"
        f"💰 Стоимость: <b>{rub(price)}</b>\n"
        f"📞 Телефон: <b>{escape(phone)}</b>"
    )


def book_summary(**details) -> str:
    return "📝 <b>Проверьте запись</b>\n\n" + _booking_details(**details)


def book_success(**details) -> str:
    return (
        "✅ <b>Вы записаны!</b>\n\n"
        + _booking_details(**details)
        + f"\n\n📍 Ждём вас по адресу: {ADDRESS}.\n"
        f"Если планы изменятся — позвоните нам: {PHONE}."
    )
