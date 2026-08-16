"""Asynchronous admission pacing for rate-limited providers."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

type MonotonicClock = Callable[[], float]
type AsyncSleeper = Callable[[float], Awaitable[None]]


class RequestPacer(Protocol):
    """Admission control for one asynchronous provider request."""

    async def wait(self) -> None: ...


class EvenlySpacedRequestPacer:
    """Start concurrent requests at a fixed maximum rate without bursts."""

    def __init__(
        self,
        *,
        requests_per_second: int,
        monotonic_clock: MonotonicClock,
        sleeper: AsyncSleeper,
    ) -> None:
        if requests_per_second < 1:
            raise ValueError("requests_per_second must be positive.")
        self._interval_seconds = 1 / requests_per_second
        self._monotonic_clock = monotonic_clock
        self._sleeper = sleeper
        self._next_request_at = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        """Wait until the next evenly spaced request start is available."""
        async with self._lock:
            now = self._monotonic_clock()
            delay = max(0.0, self._next_request_at - now)
            if delay > 0:
                await self._sleeper(delay)
            started_at = self._monotonic_clock()
            self._next_request_at = started_at + self._interval_seconds
