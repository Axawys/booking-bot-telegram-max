"""Антифлуд без привязки к мессенджеру: Telegram-middleware и MAX-бот
используют один и тот же счётчик."""

import enum
import time
from collections import deque
from collections.abc import Callable


class Verdict(enum.Enum):
    ALLOW = "allow"
    WARN = "warn"  # лимит только что превышен — предупредить один раз
    DROP = "drop"  # пользователь «остывает» — молча отбросить


class RateLimiter:
    """Не больше `limit` событий за `window` секунд, иначе пауза `cooldown` секунд."""

    CLEANUP_EVERY = 1000

    def __init__(
        self,
        limit: int,
        window: float,
        cooldown: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit, self.window, self.cooldown, self.clock = limit, window, cooldown, clock
        self.hits: dict[int, deque[float]] = {}
        self.blocked_until: dict[int, float] = {}
        self._calls = 0

    def hit(self, user_id: int) -> Verdict:
        now = self.clock()
        self._maybe_cleanup(now)

        until = self.blocked_until.get(user_id)
        if until is not None:
            if now < until:
                return Verdict.DROP
            del self.blocked_until[user_id]

        hits = self.hits.setdefault(user_id, deque())
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        hits.append(now)

        if len(hits) > self.limit:
            self.blocked_until[user_id] = now + self.cooldown
            hits.clear()
            return Verdict.WARN
        return Verdict.ALLOW

    def _maybe_cleanup(self, now: float) -> None:
        self._calls += 1
        if self._calls % self.CLEANUP_EVERY:
            return
        for user_id in [u for u, h in self.hits.items() if not h or h[-1] <= now - self.window]:
            del self.hits[user_id]
        for user_id in [u for u, t in self.blocked_until.items() if t <= now]:
            del self.blocked_until[user_id]
