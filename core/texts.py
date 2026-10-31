"""Тексты бота. Общие для Telegram и MAX, разметка — HTML."""

from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta
from html import escape

from db.models import Booking, BookingStatus, Client, Platform, Service

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


STATUS_LABELS = {
    BookingStatus.PENDING: "⏳ ожидает подтверждения",
    BookingStatus.CONFIRMED: "✅ подтверждена",
    BookingStatus.CANCELLED: "🚫 отменена клиентом",
    BookingStatus.REJECTED: "❌ отклонена",
}


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



def services_list(services: Iterable[Service]) -> str:
    lines = [
        f"• <b>{escape(s.name)}</b> · {format_duration(s.duration_minutes)}"
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
UNKNOWN_COMMAND = "Неизвестная команда. Главное меню — /start"
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
        "Если планы изменятся, отменить запись можно в разделе «Мои записи»."
    )


def book_pending(**details) -> str:
    return (
        "⏳ <b>Заявка отправлена!</b>\n\n"
        + _booking_details(**details)
        + "\n\nВремя забронировано за вами. Администратор подтвердит запись, "
        "и я сразу пришлю уведомление.\n"
        "Статус и отмена — в разделе «Мои записи»."
    )


# --- Мои записи -----------------------------------------------------------

BTN_CANCEL_BOOKING_YES = "✖ Да, отменить"
BTN_KEEP_BOOKING = "⬅ Нет, оставить"

MY_BOOKING_CANCELLED_ALERT = "Запись отменена"
MY_BOOKING_NOT_FOUND_ALERT = "Запись не найдена: она уже отменена или уже началась."


def _when(booking: Booking) -> str:
    return (
        f"{format_date(booking.starts_at.date())}, "
        f"{booking.starts_at:%H:%M}–{booking.ends_at:%H:%M}"
    )


def btn_cancel_booking(booking: Booking) -> str:
    starts_at = booking.starts_at
    return (
        f"✖ {starts_at.day} {MONTHS_GEN[starts_at.month - 1]}, {starts_at:%H:%M}"
        f" · {booking.service.name}"
    )


def my_bookings_list(bookings: Sequence[Booking], notice: str | None = None) -> str:
    head = f"{notice}\n\n" if notice else ""
    if not bookings:
        return (
            head + "📋 <b>Мои записи</b>\n\n"
            "У вас нет предстоящих записей.\n"
            "Чтобы записаться, нажмите «Записаться» в меню."
        )
    items = [
        f"{i}. <b>{_when(b)}</b>\n    {escape(b.service.name)} · {STATUS_LABELS[b.status]}"
        for i, b in enumerate(bookings, start=1)
    ]
    return (
        head + "📋 <b>Мои записи</b>\n\n"
        + "\n\n".join(items)
        + "\n\nЧтобы отменить запись, нажмите на неё ниже."
    )


def my_booking_cancel_ask(booking: Booking) -> str:
    return (
        "❓ <b>Отменить запись?</b>\n\n"
        f"💼 Услуга: <b>{escape(booking.service.name)}</b>\n"
        f"📅 Когда: <b>{_when(booking)}</b>"
    )


def my_booking_cancelled(booking: Booking) -> str:
    return f"✅ Запись на <b>{_when(booking)}</b> отменена."


# --- Уведомления клиенту --------------------------------------------------


def _booking_short(booking: Booking) -> str:
    return (
        f"💼 Услуга: <b>{escape(booking.service.name)}</b>\n"
        f"📅 Когда: <b>{_when(booking)}</b>"
    )


def client_booking_confirmed(booking: Booking) -> str:
    return (
        "✅ <b>Ваша запись подтверждена!</b>\n\n"
        + _booking_short(booking)
        + f"\n\n📍 Ждём вас по адресу: {ADDRESS}."
    )


def client_booking_rejected(booking: Booking) -> str:
    return (
        "❌ <b>К сожалению, запись отклонена.</b>\n\n"
        + _booking_short(booking)
        + "\n\nВремя освободилось. Выберите другое через «Записаться» "
        f"или позвоните нам: {PHONE}."
    )


# --- Администратор --------------------------------------------------------

BTN_ADMIN_CONFIRM = "✅ Подтвердить"
BTN_ADMIN_REJECT = "❌ Отклонить"

ADMIN_CONFIRMED_ALERT = "Запись подтверждена, клиент уведомлён"
ADMIN_REJECTED_ALERT = "Запись отклонена, клиент уведомлён"
ADMIN_ALREADY_DECIDED_ALERT = "По этой заявке уже принято решение."
ADMIN_NOT_FOUND_ALERT = "Запись не найдена."

ADMIN_HELP = (
    "🛠 <b>Панель администратора</b>\n\n"
    "/pending — заявки, ожидающие подтверждения\n"
    "/today — расписание на сегодня\n"
    "/tomorrow — расписание на завтра\n"
    "/day <code>ДД.ММ</code> — расписание на дату\n"
    "/stats — статистика\n"
    "/admin — эта справка\n\n"
    "Новые заявки приходят сюда автоматически, с кнопками подтверждения."
)
ADMIN_NO_PENDING = "✨ Заявок, ожидающих подтверждения, нет."
ADMIN_DAY_USAGE = "Укажите дату: <code>/day 05.11</code> или <code>/day 05.11.2026</code>"


def admin_pending_count(count: int) -> str:
    if not count:
        return ADMIN_NO_PENDING
    return f"⏳ Ожидают подтверждения: <b>{count}</b> — /pending"


def client_mention(client: Client) -> str:
    name = escape(client.full_name)
    if client.platform == Platform.TELEGRAM:
        return f'<a href="tg://user?id={client.external_id}">{name}</a>'
    return f"{name} (MAX)"


def admin_booking_card(booking: Booking, title: str, decided_by: str | None = None) -> str:
    duration = booking.service.duration_minutes
    text = (
        f"{title} <b>#{booking.id}</b>\n\n"
        f"👤 Клиент: {client_mention(booking.client)}\n"
        f"📞 Телефон: {escape(booking.client.phone or '—')}\n"
        f"💼 Услуга: <b>{escape(booking.service.name)}</b>\n"
        f"📅 Дата: <b>{format_date(booking.starts_at.date())}</b>\n"
        f"🕒 Время: <b>{booking.starts_at:%H:%M}–{booking.ends_at:%H:%M}</b> "
        f"({format_duration(duration)})\n"
        f"💰 Стоимость: <b>{rub(booking.service.price)}</b>\n\n"
        f"Статус: <b>{STATUS_LABELS[booking.status]}</b>"
    )
    if decided_by:
        text += f" · {escape(decided_by)}"
    return text


ADMIN_TITLE_NEW = "🆕 Новая заявка"
ADMIN_TITLE_NEW_CONFIRMED = "🆕 Новая запись"
ADMIN_TITLE_PENDING = "📋 Заявка"
ADMIN_TITLE_CANCELLED = "🚫 Клиент отменил запись"


def admin_day_schedule(day: date, bookings: Sequence[Booking]) -> str:
    head = f"📅 <b>Расписание на {format_date(day)}</b>\n\n"
    if not bookings:
        return head + "Записей нет."
    lines = []
    for b in bookings:
        icon = "⏳" if b.status == BookingStatus.PENDING else "✅"
        lines.append(
            f"{icon} <b>{b.starts_at:%H:%M}–{b.ends_at:%H:%M}</b> · {escape(b.service.name)}\n"
            f"     {client_mention(b.client)}, {escape(b.client.phone or '—')} · #{b.id}"
        )
    total = sum(b.service.price for b in bookings)
    return (
        head
        + "\n".join(lines)
        + f"\n\nВсего: <b>{len(bookings)}</b> · на сумму <b>{rub(total)}</b>\n"
        "✅ подтверждена · ⏳ ждёт подтверждения"
    )


def _stats_line(stats: dict, statuses: Iterable[BookingStatus]) -> tuple[int, int]:
    count = sum(stats.get(s, (0, 0))[0] for s in statuses)
    total = sum(stats.get(s, (0, 0))[1] for s in statuses)
    return count, total


def admin_stats(today: dict, week: dict, month: dict, clients: int) -> str:
    def active(stats: dict) -> str:
        confirmed = stats.get(BookingStatus.CONFIRMED, (0, 0))[0]
        pending = stats.get(BookingStatus.PENDING, (0, 0))[0]
        _, total = _stats_line(stats, (BookingStatus.CONFIRMED, BookingStatus.PENDING))
        return f"✅ {confirmed} · ⏳ {pending} · на сумму <b>{rub(total)}</b>"

    month_done, month_total = _stats_line(month, (BookingStatus.CONFIRMED,))
    return (
        "📊 <b>Статистика</b>\n\n"
        f"<b>Сегодня:</b> {active(today)}\n"
        f"<b>Ближайшие 7 дней:</b> {active(week)}\n\n"
        "<b>Последние 30 дней:</b>\n"
        f"• визитов: {month_done} на сумму <b>{rub(month_total)}</b>\n"
        f"• отменено клиентами: {month.get(BookingStatus.CANCELLED, (0, 0))[0]}\n"
        f"• отклонено: {month.get(BookingStatus.REJECTED, (0, 0))[0]}\n\n"
        f"👥 Клиентов в базе: <b>{clients}</b>\n\n"
        "<i>Суммы считаются по текущим ценам услуг.</i>"
    )
