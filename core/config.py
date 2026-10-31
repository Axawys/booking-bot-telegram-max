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
    # На сколько дней вперёд можно записаться.
    booking_horizon_days: int = 14
    # Минимум минут между «сейчас» и началом записи.
    booking_min_lead_minutes: int = 60

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _split_admin_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(part) for part in value.replace(" ", "").split(",") if part]
        return value

    @property
    def requires_confirmation(self) -> bool:
        return bool(self.admin_ids)

    def is_admin(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.admin_ids


settings = Settings()
