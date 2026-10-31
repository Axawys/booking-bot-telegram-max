from datetime import time
from typing import Annotated

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_bot_token: SecretStr

    # Telegram ID администраторов через запятую: ADMIN_IDS=123,456.
    # Если список пуст, записи подтверждаются сразу, без участия админа.
    admin_ids: Annotated[list[int], NoDecode] = []

    # Путь относительный к рабочей директории (в контейнере — /app/data/bot.db).
    database_url: str = "sqlite+aiosqlite:///data/bot.db"

    # --- Расписание ---
    # Часовой пояс бизнеса: время записей хранится и показывается в нём.
    timezone: str = "Europe/Moscow"
    work_start: time = time(10, 0)
    work_end: time = time(21, 0)
    # Шаг сетки расписания. Длительность каждой услуги должна быть ему кратна.
    slot_minutes: int = 30
    # На сколько дней вперёд можно записаться — значение по умолчанию.
    # Админ меняет его в боте командой /horizon (хранится в БД).
    booking_horizon_days: int = 14
    # Минимум минут между «сейчас» и началом записи.
    booking_min_lead_minutes: int = 60

    # --- Антиспам (на админов не действует) ---
    # Не больше rate_limit_messages действий за rate_limit_window_seconds,
    # иначе пауза rate_limit_cooldown_seconds.
    rate_limit_messages: int = 20
    rate_limit_window_seconds: int = 10
    rate_limit_cooldown_seconds: int = 30
    # Лимиты записей на одного клиента.
    max_active_bookings: int = 3
    max_bookings_per_day: int = 5

    # --- Планировщик ---
    # За сколько часов до визита напоминать клиенту: REMINDER_HOURS=24,2.
    reminder_hours: Annotated[list[int], NoDecode] = [24, 2]
    # Напоминание, время которого пришлось на простой бота, отправляется
    # после старта, если опоздание не больше этого числа минут.
    reminder_grace_minutes: int = 60
    # Сколько часов заявка ждёт решения админа, потом отклоняется автоматически.
    pending_ttl_hours: int = 12
    # Время утренней сводки для админов; пусто — не отправлять.
    admin_digest_time: time | None = time(9, 0)

    @field_validator("admin_ids", "reminder_hours", mode="before")
    @classmethod
    def _split_ints(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(part) for part in value.replace(" ", "").split(",") if part]
        return value

    @field_validator("admin_digest_time", mode="before")
    @classmethod
    def _empty_time_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @property
    def requires_confirmation(self) -> bool:
        return bool(self.admin_ids)

    def is_admin(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.admin_ids


settings = Settings()
