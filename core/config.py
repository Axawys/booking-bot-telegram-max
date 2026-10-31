from datetime import time

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_bot_token: SecretStr

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


settings = Settings()
