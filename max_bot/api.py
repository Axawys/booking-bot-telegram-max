"""Тонкий асинхронный клиент MAX Bot API (https://dev.max.ru/docs-api).

Своя реализация вместо сторонней обёртки: API — простой REST, а нам нужны
полтора десятка вызовов. Схема: https://github.com/max-messenger/api-schema

TLS: platform-api2.max.ru подписан Russian Trusted Root CA (НУЦ Минцифры),
которого нет в стандартном наборе сертификатов. Корневой сертификат лежит
в max_bot/certs и добавляется к системным — только для этого клиента;
проверка сертификатов не отключается.
"""

import asyncio
import json as jsonlib
import logging
import ssl
from pathlib import Path
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

CA_FILE = Path(__file__).parent / "certs" / "russian_trusted_root_ca.pem"

# Inline-клавиатура: строки кнопок, каждая кнопка — dict из схемы Button.
Keyboard = list[list[dict[str, Any]]]


class MaxApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"MAX API {status}: {message}")
        self.status = status


def _keyboard_attachments(keyboard: Keyboard | None) -> list[dict[str, Any]]:
    if not keyboard:
        return []
    return [{"type": "inline_keyboard", "payload": {"buttons": keyboard}}]


def message_body(text: str, keyboard: Keyboard | None = None) -> dict[str, Any]:
    """NewMessageBody. Пустой attachments убирает старую клавиатуру при правке."""
    return {"text": text, "format": "html", "attachments": _keyboard_attachments(keyboard)}


class MaxApi:
    def __init__(self, token: str, base_url: str, ca_file: Path = CA_FILE) -> None:
        self.base_url = base_url.rstrip("/")
        self._headers = {"Authorization": token}
        self._ca_file = ca_file
        self._session: aiohttp.ClientSession | None = None

    async def _http(self) -> aiohttp.ClientSession:
        # Сессия создаётся лениво: ей нужен работающий event loop.
        if self._session is None or self._session.closed:
            context = ssl.create_default_context()
            context.load_verify_locations(cafile=self._ca_file)
            self._session = aiohttp.ClientSession(
                headers=self._headers, connector=aiohttp.TCPConnector(ssl=context)
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        http_timeout: float = 30,
    ) -> dict[str, Any]:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        session = await self._http()
        for attempt in range(3):
            async with session.request(
                method,
                f"{self.base_url}{path}",
                params=params,
                json=json,
                timeout=aiohttp.ClientTimeout(total=http_timeout),
            ) as response:
                if response.status == 429 and attempt < 2:
                    # Лимит: 2 сообщения в секунду на чат.
                    await asyncio.sleep(1 + attempt)
                    continue
                raw = await response.text()
                try:
                    data = jsonlib.loads(raw) if raw else {}
                except ValueError:  # например, HTML-страница ошибки прокси
                    data = {"raw": raw[:300]}
                if response.status >= 400:
                    raise MaxApiError(response.status, str(data))
                if isinstance(data, dict) and data.get("success") is False:
                    log.warning("MAX API %s %s: %s", method, path, data.get("message"))
                return data if isinstance(data, dict) else {}
        raise MaxApiError(429, "rate limit")

    # --- Методы -----------------------------------------------------------

    async def get_me(self) -> dict[str, Any]:
        return await self.request("GET", "/me")

    async def get_updates(
        self, marker: int | None, wait_seconds: int = 30, limit: int = 100
    ) -> dict[str, Any]:
        """Long polling: сервер держит запрос до wait_seconds, пока нет событий.
        Возвращает {"updates": [...], "marker": int}."""
        return await self.request(
            "GET",
            "/updates",
            params={"marker": marker, "timeout": wait_seconds, "limit": limit},
            http_timeout=wait_seconds + 15,
        )

    async def send_message(self, user_id: int, text: str, keyboard: Keyboard | None = None) -> str:
        """Сообщение в личный диалог. Возвращает mid нового сообщения."""
        data = await self.request(
            "POST", "/messages", params={"user_id": user_id}, json=message_body(text, keyboard)
        )
        return data["message"]["body"]["mid"]

    async def edit_message(self, mid: str, text: str, keyboard: Keyboard | None = None) -> None:
        await self.request(
            "PUT", "/messages", params={"message_id": mid}, json=message_body(text, keyboard)
        )

    async def answer_callback(
        self,
        callback_id: str,
        *,
        text: str | None = None,
        keyboard: Keyboard | None = None,
        notification: str | None = None,
    ) -> None:
        """Ответ на нажатие кнопки. С text — заодно меняет сообщение с кнопкой."""
        body: dict[str, Any] = {}
        if text is not None:
            body["message"] = message_body(text, keyboard)
        if notification is not None:
            body["notification"] = notification
        await self.request("POST", "/answers", params={"callback_id": callback_id}, json=body)

    async def set_commands(self, commands: list[tuple[str, str]]) -> None:
        await self.request(
            "PATCH",
            "/me/commands",
            json={"commands": [{"name": n, "description": d} for n, d in commands]},
        )
