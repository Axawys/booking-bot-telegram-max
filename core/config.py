from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_bot_token: SecretStr

    # Путь относительный к рабочей директории (в контейнере — /app/data/bot.db).
    database_url: str = "sqlite+aiosqlite:///data/bot.db"
    # Шаг сетки расписания. Длительность каждой услуги должна быть ему кратна.
    slot_minutes: int = 30


settings = Settings()
