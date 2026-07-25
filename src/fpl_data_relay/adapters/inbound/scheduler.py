"""Polling scheduler inbound adapter."""

import asyncio
import logging

from fpl_data_relay.application.ingestion.service import IngestionResult
from fpl_data_relay.application.ports.inbound import IngestionRunner

LOGGER = logging.getLogger(__name__)


class RelayScheduler:
    """Schedule reference and live ingestion cycles at separate intervals."""

    def __init__(
        self,
        *,
        ingestion_service: IngestionRunner,
        reference_poll_seconds: int,
        live_poll_seconds: int,
        idle_poll_seconds: int,
    ) -> None:
        self._ingestion_service = ingestion_service
        self._reference_poll_seconds = reference_poll_seconds
        self._live_poll_seconds = live_poll_seconds
        self._idle_poll_seconds = idle_poll_seconds
        self._stop_event = asyncio.Event()

    def start(self) -> asyncio.Task[None]:
        """Start the scheduler loop in a background task."""
        return asyncio.create_task(self.run())

    async def stop(self, *, task: asyncio.Task[None]) -> None:
        """Signal the scheduler to stop and wait for its task to exit."""
        self._stop_event.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return

    async def run(self) -> None:
        """Run ingestion cycles until stopped."""
        next_reference_at = 0.0
        next_live_at = 0.0
        while not self._stop_event.is_set():
            loop_time = asyncio.get_running_loop().time()
            if loop_time >= next_reference_at:
                await self._run_reference_cycle()
                next_reference_at = (
                    asyncio.get_running_loop().time() + self._reference_poll_seconds
                )
            if loop_time >= next_live_at:
                live_result = await self._run_live_cycle()
                live_interval = (
                    self._live_poll_seconds
                    if live_result.has_active_fixture
                    else self._idle_poll_seconds
                )
                next_live_at = asyncio.get_running_loop().time() + live_interval
            sleep_seconds = max(0.1, min(next_reference_at, next_live_at) - loop_time)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=sleep_seconds,
                )
            except TimeoutError:
                continue

    async def _run_reference_cycle(self) -> None:
        """Log a failed cycle while keeping the scheduler alive."""
        try:
            await self._ingestion_service.ingest_reference_once()
        except Exception:
            LOGGER.exception("Reference ingestion cycle failed.")

    async def _run_live_cycle(self) -> IngestionResult:
        """Return an idle result after a failed live cycle."""
        try:
            return await self._ingestion_service.ingest_live_once(
                target_event_id=None,
                fixture_id=None,
            )
        except Exception:
            LOGGER.exception("Live ingestion cycle failed.")
            return IngestionResult(
                changed_count=0,
                unchanged_count=0,
                season_id=None,
                current_event_id=None,
                has_active_fixture=False,
            )
