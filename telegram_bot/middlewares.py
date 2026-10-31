import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import ReplyKeyboardRemove, TelegramObject, Update, User
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core import texts
from core.config import settings
from core.ratelimit import RateLimiter, Verdict
from db import repo
from db.models import Platform

log = logging.getLogger(__name__)

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


def _user(data: dict[str, Any]) -> User | None:
    # Заполняет встроенный UserContextMiddleware aiogram.
    return data.get("event_from_user")


class DbSessionMiddleware(BaseMiddleware):
    """Открывает сессию БД на каждый апдейт и передаёт её в хендлер как `session`."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        async with self.session_factory() as session:
            data["session"] = session
            return await handler(event, data)


class ThrottlingMiddleware(BaseMiddleware):
    """Антифлуд: не больше `limit` апдейтов за `window` секунд от одного пользователя.

    При превышении пользователь «остывает» `cooldown` секунд: апдейты
    отбрасываются, предупреждение отправляется один раз. Счётчики в памяти —
    флуд отсекается до любых запросов к БД. Админов не ограничивает.
    Сам счётчик общий с MAX-ботом: core.ratelimit.RateLimiter.
    """

    def __init__(
        self,
        limit: int,
        window: float,
        cooldown: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limiter = RateLimiter(limit, window, cooldown, clock)

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        user = _user(data)
        if user is None or settings.is_admin(user.id):
            return await handler(event, data)

        match self.limiter.hit(user.id):
            case Verdict.ALLOW:
                return await handler(event, data)
            case Verdict.WARN:
                log.warning("Флуд от пользователя %s: пауза %s с", user.id, self.limiter.cooldown)
                await _reply(event, texts.throttled(int(self.limiter.cooldown)))
            case Verdict.DROP:
                await _stop_spinner(event)
        return None


class BanMiddleware(BaseMiddleware):
    """Забаненным бот не отвечает, кроме короткого уведомления раз в `notice_interval` секунд.

    Регистрируется после DbSessionMiddleware: берёт сессию из data["session"].
    """

    def __init__(
        self, notice_interval: float = 3600, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.notice_interval, self.clock = notice_interval, clock
        self.last_notice: dict[int, float] = {}

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        user = _user(data)
        if user is None or settings.is_admin(user.id):
            return await handler(event, data)
        if not await repo.is_banned(data["session"], Platform.TELEGRAM, user.id):
            return await handler(event, data)

        now = self.clock()
        last = self.last_notice.get(user.id)
        if last is None or now - last >= self.notice_interval:
            self.last_notice[user.id] = now
            await _reply(event, texts.BANNED, remove_keyboard=True)
        else:
            await _stop_spinner(event)
        return None


async def _reply(event: TelegramObject, text: str, remove_keyboard: bool = False) -> None:
    if not isinstance(event, Update):
        return
    if event.callback_query:
        await event.callback_query.answer(text, show_alert=True)
    elif event.message:
        markup = ReplyKeyboardRemove() if remove_keyboard else None
        await event.message.answer(text, reply_markup=markup)


async def _stop_spinner(event: TelegramObject) -> None:
    """Кнопка без ответа «крутится» у пользователя — отвечаем пустым answer()."""
    if isinstance(event, Update) and event.callback_query:
        await event.callback_query.answer()
