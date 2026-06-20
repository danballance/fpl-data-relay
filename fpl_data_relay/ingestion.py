import asyncio
import logging
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from fpl_data_relay.fpl_client import model_to_payload
from fpl_data_relay.hashing import payload_sha256
from fpl_data_relay.resources import EVENT_NAMES, ResourceKey
from fpl_data_relay.store import ResourceStore, ResourceWrite, UpsertOutcome
from fpl_data_relay.upstream_models import (
    BootstrapStatic,
    EventLiveResponse,
    EventStatusResponse,
    Fixture,
)

LOGGER = logging.getLogger(__name__)


class FplApiClient(Protocol):
    async def close(self) -> None: ...

    async def fetch_bootstrap_static(self) -> BootstrapStatic: ...

    async def fetch_fixtures(self) -> list[Fixture]: ...

    async def fetch_current_fixtures(self, *, event_id: int) -> list[Fixture]: ...

    async def fetch_event_status(self) -> EventStatusResponse: ...

    async def fetch_event_live(self, *, event_id: int) -> EventLiveResponse: ...


class IngestionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    changed_count: int
    unchanged_count: int
    current_event_id: int | None
    has_active_fixture: bool


class IngestionService:
    def __init__(self, *, client: FplApiClient, store: ResourceStore) -> None:
        self._client = client
        self._store = store

    async def close(self) -> None:
        await self._client.close()

    async def ingest_all_once(self) -> IngestionResult:
        async with self._store.ingestion_lock():
            reference_result = await self._ingest_reference_unlocked()
            live_result = await self._ingest_live_unlocked()
        return combine_results(first=reference_result, second=live_result)

    async def ingest_reference_once(self) -> IngestionResult:
        async with self._store.ingestion_lock():
            return await self._ingest_reference_unlocked()

    async def ingest_live_once(self) -> IngestionResult:
        async with self._store.ingestion_lock():
            return await self._ingest_live_unlocked()

    async def _ingest_reference_unlocked(self) -> IngestionResult:
        fetched_at = utc_now()
        bootstrap, fixtures = await asyncio.gather(
            self._client.fetch_bootstrap_static(),
            self._client.fetch_fixtures(),
        )
        current_event_id = select_current_event_id(bootstrap=bootstrap)
        outcomes = [
            await self._upsert(
                resource_key=ResourceKey.BOOTSTRAP,
                payload=model_to_payload(model=bootstrap),
                event_id=None,
                fetched_at=fetched_at,
            ),
            await self._upsert(
                resource_key=ResourceKey.FIXTURES,
                payload=model_to_payload(model=fixtures),
                event_id=None,
                fetched_at=fetched_at,
            ),
        ]
        return result_from_outcomes(
            outcomes=outcomes,
            current_event_id=current_event_id,
            has_active_fixture=False,
        )

    async def _ingest_live_unlocked(self) -> IngestionResult:
        bootstrap_resource = await self._store.get_resource(
            resource_key=ResourceKey.BOOTSTRAP,
        )
        if bootstrap_resource is None:
            raise RuntimeError("Cannot ingest live resources before bootstrap exists.")
        bootstrap = BootstrapStatic.model_validate(bootstrap_resource.payload)
        current_event_id = select_current_event_id(bootstrap=bootstrap)
        fetched_at = utc_now()
        event_status, current_fixtures, event_live = await asyncio.gather(
            self._client.fetch_event_status(),
            self._client.fetch_current_fixtures(event_id=current_event_id),
            self._client.fetch_event_live(event_id=current_event_id),
        )
        outcomes = [
            await self._upsert(
                resource_key=ResourceKey.EVENT_STATUS,
                payload=model_to_payload(model=event_status),
                event_id=current_event_id,
                fetched_at=fetched_at,
            ),
            await self._upsert(
                resource_key=ResourceKey.CURRENT_FIXTURES,
                payload=model_to_payload(model=current_fixtures),
                event_id=current_event_id,
                fetched_at=fetched_at,
            ),
            await self._upsert(
                resource_key=ResourceKey.EVENT_LIVE,
                payload=model_to_payload(model=event_live),
                event_id=current_event_id,
                fetched_at=fetched_at,
            ),
        ]
        return result_from_outcomes(
            outcomes=outcomes,
            current_event_id=current_event_id,
            has_active_fixture=has_active_fixture(fixtures=current_fixtures),
        )

    async def _upsert(
        self,
        *,
        resource_key: ResourceKey,
        payload: object,
        event_id: int | None,
        fetched_at: datetime,
    ) -> UpsertOutcome:
        payload_hash = payload_sha256(payload=payload)
        write = ResourceWrite(
            resource_key=resource_key,
            event_name=EVENT_NAMES[resource_key],
            event_id=event_id,
            payload=payload,
            payload_hash=payload_hash,
            fetched_at=fetched_at,
            checked_at=utc_now(),
        )
        return await self._store.upsert_resource(resource=write)


class IngestionRunner(Protocol):
    async def ingest_reference_once(self) -> IngestionResult: ...

    async def ingest_live_once(self) -> IngestionResult: ...


class RelayScheduler:
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
        return asyncio.create_task(self.run())

    async def stop(self, *, task: asyncio.Task[None]) -> None:
        self._stop_event.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return

    async def run(self) -> None:
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
        try:
            await self._ingestion_service.ingest_reference_once()
        except Exception:
            LOGGER.exception("Reference ingestion cycle failed.")

    async def _run_live_cycle(self) -> IngestionResult:
        try:
            return await self._ingestion_service.ingest_live_once()
        except Exception:
            LOGGER.exception("Live ingestion cycle failed.")
            return IngestionResult(
                changed_count=0,
                unchanged_count=0,
                current_event_id=None,
                has_active_fixture=False,
            )


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def select_current_event_id(*, bootstrap: BootstrapStatic) -> int:
    current_events = [event for event in bootstrap.events if event.is_current]
    if len(current_events) != 1:
        count = len(current_events)
        raise ValueError(f"Expected exactly one current FPL event, found {count}.")
    return current_events[0].id


def has_active_fixture(*, fixtures: list[Fixture]) -> bool:
    return any(fixture.started and not fixture.finished for fixture in fixtures)


def result_from_outcomes(
    *,
    outcomes: list[UpsertOutcome],
    current_event_id: int | None,
    has_active_fixture: bool,
) -> IngestionResult:
    changed_count = sum(1 for outcome in outcomes if outcome.changed)
    unchanged_count = len(outcomes) - changed_count
    return IngestionResult(
        changed_count=changed_count,
        unchanged_count=unchanged_count,
        current_event_id=current_event_id,
        has_active_fixture=has_active_fixture,
    )


def combine_results(
    *,
    first: IngestionResult,
    second: IngestionResult,
) -> IngestionResult:
    return IngestionResult(
        changed_count=first.changed_count + second.changed_count,
        unchanged_count=first.unchanged_count + second.unchanged_count,
        current_event_id=second.current_event_id,
        has_active_fixture=second.has_active_fixture,
    )
