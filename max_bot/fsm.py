"""Состояние диалога с пользователем (FSM) в памяти — как MemoryStorage в aiogram.

После перезапуска MAX-бота начатое оформление записи сбрасывается; сами
записи хранятся в БД и не теряются.
"""

from typing import Any

# Шаги оформления записи — те же, что в Telegram (telegram_bot/states.py).
SERVICE, DATE, TIME, PHONE, CONFIRM = "service", "date", "time", "phone", "confirm"


class MemoryFSM:
    def __init__(self) -> None:
        self._states: dict[int, str] = {}
        self._data: dict[int, dict[str, Any]] = {}

    def state(self, user_id: int) -> str | None:
        return self._states.get(user_id)

    def set_state(self, user_id: int, state: str) -> None:
        self._states[user_id] = state

    def data(self, user_id: int) -> dict[str, Any]:
        return dict(self._data.get(user_id, {}))

    def update(self, user_id: int, **values: Any) -> None:
        self._data.setdefault(user_id, {}).update(values)

    def clear(self, user_id: int) -> None:
        self._states.pop(user_id, None)
        self._data.pop(user_id, None)
