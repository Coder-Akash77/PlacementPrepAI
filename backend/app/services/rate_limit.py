"""Process-local quotas reset on restart; Render must run one instance and worker."""

import logging
import math
import random
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, TypeVar

from google.genai.errors import APIError

logger = logging.getLogger(__name__)

T = TypeVar("T")
MAX_ATTEMPTS = 3


class RateLimitExceeded(RuntimeError):
    def __init__(self, retry_after: int, message: str):
        super().__init__(message)
        self.retry_after = max(1, retry_after)


class ProviderTemporarilyUnavailable(RuntimeError):
    """Raised when bounded retries for a transient provider error are exhausted."""


@dataclass
class _RequestEvent:
    timestamp: float
    tokens: int


class RollingRateLimiter:
    def __init__(
        self,
        *,
        requests_per_minute: int,
        requests_per_day: int,
        tokens_per_minute: int | None = None,
        request_window_seconds: float = 60,
        day_window_seconds: float = 86400,
        max_wait_seconds: float = 30,
        max_queued_requests: int = 32,
        min_request_interval: float = 0,
    ):
        self.requests_per_minute = requests_per_minute
        self.requests_per_day = requests_per_day
        self.tokens_per_minute = tokens_per_minute
        self.request_window_seconds = request_window_seconds
        self.day_window_seconds = day_window_seconds
        self.max_wait_seconds = max_wait_seconds
        self.max_queued_requests = max_queued_requests
        self.min_request_interval = min_request_interval
        self._events: deque[_RequestEvent] = deque()
        self._waiters: deque[object] = deque()
        self._condition = threading.Condition()

    def _prune(self, now: float) -> None:
        while (
            self._events
            and self._events[0].timestamp < now - self.day_window_seconds
        ):
            self._events.popleft()

    def _next_available(self, now: float, tokens: int) -> float:
        self._prune(now)
        minute_events = [
            event
            for event in self._events
            if event.timestamp >= now - self.request_window_seconds
        ]
        next_time = now

        if len(minute_events) >= self.requests_per_minute:
            next_time = max(
                next_time,
                minute_events[0].timestamp + self.request_window_seconds + 1e-6,
            )
        if len(self._events) >= self.requests_per_day:
            next_time = max(
                next_time,
                self._events[0].timestamp + self.day_window_seconds + 1e-6,
            )
        if self._events and self.min_request_interval:
            next_time = max(
                next_time,
                self._events[-1].timestamp + self.min_request_interval,
            )

        if self.tokens_per_minute is not None:
            token_events = [
                event for event in minute_events if event.tokens
            ]
            total_tokens = sum(event.tokens for event in token_events) + tokens
            if tokens > self.tokens_per_minute:
                raise RateLimitExceeded(
                    60,
                    "This embedding request exceeds the per-minute token limit.",
                )
            if total_tokens > self.tokens_per_minute:
                remaining = total_tokens - self.tokens_per_minute
                for event in token_events:
                    remaining -= event.tokens
                    if remaining <= 0:
                        next_time = max(
                            next_time,
                            event.timestamp + self.request_window_seconds + 1e-6,
                        )
                        break

        return next_time

    def acquire(self, tokens: int = 0) -> None:
        started = time.monotonic()
        deadline = started + self.max_wait_seconds
        ticket = object()

        with self._condition:
            if len(self._waiters) >= self.max_queued_requests:
                available = self._next_available(started, tokens)
                raise RateLimitExceeded(
                    max(1, math.ceil(available - started)),
                    "AI request capacity is busy. Please retry shortly.",
                )

            self._waiters.append(ticket)
            try:
                while True:
                    now = time.monotonic()
                    available = self._next_available(now, tokens)
                    if self._waiters[0] is ticket and available <= now:
                        self._waiters.popleft()
                        self._events.append(_RequestEvent(now, tokens))
                        self._condition.notify_all()
                        return

                    remaining = deadline - now
                    if remaining <= 0:
                        raise RateLimitExceeded(
                            max(1, math.ceil(available - now)),
                            "AI request capacity is busy. Please retry shortly.",
                        )

                    until_available = max(0, available - now)
                    self._condition.wait(
                        timeout=min(remaining, max(0.01, until_available))
                    )
            finally:
                if ticket in self._waiters:
                    self._waiters.remove(ticket)
                    self._condition.notify_all()


generation_limiter = RollingRateLimiter(
    requests_per_minute=15,
    requests_per_day=500,
    max_wait_seconds=30,
    max_queued_requests=32,
    min_request_interval=60 / 15 + 0.01,
)

embedding_limiter = RollingRateLimiter(
    requests_per_minute=100,
    requests_per_day=1000,
    tokens_per_minute=30_000,
    max_wait_seconds=30,
    max_queued_requests=32,
    min_request_interval=60 / 100 + 0.01,
)


class PerUserRateLimiter:
    """Rejects (never queues) requests from a user who exceeds their own quota."""

    def __init__(
        self,
        *,
        requests_per_minute: int,
        requests_per_day: int,
        max_tracked_users: int = 10_000,
    ):
        self.requests_per_minute = requests_per_minute
        self.requests_per_day = requests_per_day
        self.max_tracked_users = max_tracked_users
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._events.clear()

    def check(self, user_id: str) -> None:
        now = time.monotonic()
        with self._lock:
            events = self._events.setdefault(user_id, deque())
            while events and events[0] <= now - 86400:
                events.popleft()
            if len(events) >= self.requests_per_day:
                raise RateLimitExceeded(
                    math.ceil(events[0] + 86400 - now),
                    "Daily AI request limit reached. Please try again later.",
                )
            recent = [moment for moment in events if moment > now - 60]
            if len(recent) >= self.requests_per_minute:
                raise RateLimitExceeded(
                    math.ceil(recent[0] + 60 - now),
                    "Too many AI requests. Please wait a moment and try again.",
                )
            events.append(now)
            if len(self._events) > self.max_tracked_users:
                self._evict_idle_users(now)

    def _evict_idle_users(self, now: float) -> None:
        for key in [
            key
            for key, events in self._events.items()
            if not events or events[-1] <= now - 86400
        ]:
            del self._events[key]


# Per signed-in user, on top of the process-wide limiters above.
user_limiter = PerUserRateLimiter(requests_per_minute=10, requests_per_day=200)


def _retry_after(error: APIError) -> int:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    value = headers.get("Retry-After") if headers is not None else None
    try:
        return max(1, min(86400, math.ceil(float(value))))
    except (TypeError, ValueError):
        return 60


def call_with_rate_limit(
    operation: Callable[[], T],
    limiter: RollingRateLimiter,
    *,
    tokens: int = 0,
) -> T:
    for attempt in range(MAX_ATTEMPTS):
        limiter.acquire(tokens=tokens)
        try:
            return operation()
        except APIError as exc:
            code = getattr(exc, "code", None)
            status = getattr(exc, "status", None)
            if code == 429:
                logger.warning(
                    "Gemini request rate limited (error_type=%s status_code=429 status=%s)",
                    type(exc).__name__,
                    status or "unknown",
                )
                raise RateLimitExceeded(
                    _retry_after(exc),
                    "Gemini request quota reached. Please retry after the indicated delay.",
                ) from exc
            if code not in {503, 504}:
                raise

            logger.warning(
                "Gemini transient failure (error_type=%s status_code=%s status=%s attempt=%s)",
                type(exc).__name__,
                code,
                status or "unknown",
                attempt + 1,
            )
            if attempt + 1 == MAX_ATTEMPTS:
                raise ProviderTemporarilyUnavailable(
                    "Gemini is temporarily unavailable. Please try again."
                ) from exc
            delay = min(2.0, 0.25 * (2**attempt))
            time.sleep(random.uniform(delay * 0.8, delay * 1.2))

    raise AssertionError("unreachable")
