import asyncio

import pytest

from fpl_data_relay.application.request_pacing import EvenlySpacedRequestPacer


class FakeTime:
    def __init__(self) -> None:
        self.current = 0.0
        self.started_at: list[float] = []
        self.sleep_delays: list[float] = []
        self.oversleep_seconds = 0.0

    def monotonic(self) -> float:
        return self.current

    async def sleep(self, seconds: float) -> None:
        self.sleep_delays.append(seconds)
        self.current += seconds + self.oversleep_seconds
        await asyncio.sleep(0)

    async def request(self, *, pacer: EvenlySpacedRequestPacer) -> None:
        await pacer.wait()
        self.started_at.append(self.current)


def pacer(
    *,
    fake_time: FakeTime,
    requests_per_second: int,
) -> EvenlySpacedRequestPacer:
    return EvenlySpacedRequestPacer(
        requests_per_second=requests_per_second,
        monotonic_clock=fake_time.monotonic,
        sleeper=fake_time.sleep,
    )


@pytest.mark.asyncio
async def test_pacer_starts_first_immediately_and_spaces_concurrent_callers() -> None:
    fake_time = FakeTime()
    limiter = pacer(fake_time=fake_time, requests_per_second=8)

    await asyncio.gather(
        *(fake_time.request(pacer=limiter) for _ in range(17)),
    )

    assert fake_time.started_at == pytest.approx(
        [index * 0.125 for index in range(17)],
    )
    for window_start in fake_time.started_at:
        assert sum(
            window_start <= started_at < window_start + 1
            for started_at in fake_time.started_at
        ) <= 8


@pytest.mark.asyncio
async def test_pacer_uses_actual_time_after_oversleep_without_bursting() -> None:
    fake_time = FakeTime()
    fake_time.oversleep_seconds = 0.5
    limiter = pacer(fake_time=fake_time, requests_per_second=8)

    await asyncio.gather(
        fake_time.request(pacer=limiter),
        fake_time.request(pacer=limiter),
        fake_time.request(pacer=limiter),
    )

    assert fake_time.started_at == pytest.approx([0.0, 0.625, 1.25])
    assert fake_time.sleep_delays == pytest.approx([0.125, 0.125])


@pytest.mark.asyncio
async def test_cancelled_waiter_releases_lock_without_reserving_slot() -> None:
    first_sleep_started = asyncio.Event()
    release_sleep = asyncio.Event()
    current = 0.0

    def monotonic() -> float:
        return current

    async def sleep(seconds: float) -> None:
        nonlocal current
        first_sleep_started.set()
        await release_sleep.wait()
        current += seconds

    limiter = EvenlySpacedRequestPacer(
        requests_per_second=8,
        monotonic_clock=monotonic,
        sleeper=sleep,
    )
    await limiter.wait()
    cancelled = asyncio.create_task(limiter.wait())
    await first_sleep_started.wait()
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled

    current = 0.125
    await limiter.wait()


@pytest.mark.parametrize("requests_per_second", [0, -1])
def test_pacer_rejects_non_positive_rates(requests_per_second: int) -> None:
    fake_time = FakeTime()
    with pytest.raises(ValueError, match="positive"):
        EvenlySpacedRequestPacer(
            requests_per_second=requests_per_second,
            monotonic_clock=fake_time.monotonic,
            sleeper=fake_time.sleep,
        )
