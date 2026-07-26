"""Application service for one-shot FPL ingestion."""

import asyncio
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from fpl_data_relay.application.ports.persistence import IngestionRepository
from fpl_data_relay.application.ports.upstream import FplGateway
from fpl_data_relay.domain.changes import (
    IngestionMetadata,
    IngestionSourceKey,
    UpsertOutcome,
)
from fpl_data_relay.domain.fixtures import Fixture
from fpl_data_relay.domain.live import EventLiveResponse, EventStatusResponse
from fpl_data_relay.domain.reference import BootstrapStatic, Season
from fpl_data_relay.domain.rules import (
    derive_season,
    has_active_fixture,
    model_to_payload,
    payload_sha256,
    select_current_event_id,
)


class IngestionResult(BaseModel):
    """Summary of entity-family changes produced by one ingestion cycle."""

    model_config = ConfigDict(frozen=True)

    changed_count: int
    unchanged_count: int
    season_id: str | None
    current_event_id: int | None
    has_active_fixture: bool


class IngestionService:
    """Fetch upstream resources, validate, split, and persist FPL entities."""

    def __init__(
        self,
        *,
        client: FplGateway,
        repository: IngestionRepository,
    ) -> None:
        """Create the service from explicit upstream and persistence ports."""
        self._client = client
        self._repository = repository

    async def ingest_all_once(self) -> IngestionResult:
        """Run reference and live ingestion under one store-level lock."""
        async with self._repository.ingestion_lock():
            reference_result = await self._ingest_reference_unlocked()
            live_result = await self._ingest_live_unlocked(
                target_event_id=None,
                fixture_id=None,
            )
        return combine_results(first=reference_result, second=live_result)

    async def ingest_reference_once(self) -> IngestionResult:
        """Ingest slow-changing bootstrap and full-fixture resources."""
        async with self._repository.ingestion_lock():
            return await self._ingest_reference_unlocked()

    async def ingest_live_once(
        self,
        *,
        target_event_id: int | None,
        fixture_id: int | None,
    ) -> IngestionResult:
        """Ingest event-scoped live resources for a resolved target event."""
        async with self._repository.ingestion_lock():
            return await self._ingest_live_unlocked(
                target_event_id=target_event_id,
                fixture_id=fixture_id,
            )

    async def _ingest_reference_unlocked(self) -> IngestionResult:
        """Fetch and store reference resources while a lock is already held."""
        fetched_at = utc_now()
        bootstrap, fixtures = await asyncio.gather(
            self._client.fetch_bootstrap_static(),
            self._client.fetch_fixtures(),
        )
        return await self._persist_reference_unlocked(
            bootstrap=bootstrap,
            fixtures=fixtures,
            fetched_at=fetched_at,
        )

    async def ingest_reference_payload(
        self,
        *,
        bootstrap: BootstrapStatic,
        fixtures: list[Fixture],
        fetched_at: datetime,
    ) -> IngestionResult:
        """Persist an already collected and validated reference snapshot."""
        async with self._repository.ingestion_lock():
            return await self._persist_reference_unlocked(
                bootstrap=bootstrap,
                fixtures=fixtures,
                fetched_at=fetched_at,
            )

    async def _persist_reference_unlocked(
        self,
        *,
        bootstrap: BootstrapStatic,
        fixtures: list[Fixture],
        fetched_at: datetime,
    ) -> IngestionResult:
        """Persist reference resources while a lock is already held."""
        season = derive_season(bootstrap=bootstrap)
        current_event_id = select_current_event_id(bootstrap=bootstrap)
        outcomes = [
            await self._repository.upsert_bootstrap(
                season=season,
                bootstrap=bootstrap,
                metadata=metadata_for_payload(
                    season_id=season.id,
                    source_key=IngestionSourceKey.BOOTSTRAP,
                    event_id=None,
                    payload=model_to_payload(model=bootstrap),
                    fetched_at=fetched_at,
                ),
            ),
            await self._repository.upsert_fixtures(
                fixtures=fixtures,
                metadata=metadata_for_payload(
                    season_id=season.id,
                    source_key=IngestionSourceKey.FIXTURES,
                    event_id=None,
                    payload=model_to_payload(model=fixtures),
                    fetched_at=fetched_at,
                ),
            ),
        ]
        return result_from_outcomes(
            outcomes=outcomes,
            season_id=season.id,
            current_event_id=current_event_id,
            has_active_fixture=False,
        )

    async def _ingest_live_unlocked(
        self,
        *,
        target_event_id: int | None,
        fixture_id: int | None,
    ) -> IngestionResult:
        """Fetch and store live resources while a lock is already held."""
        season, current_event_id = await self._resolve_live_target(
            target_event_id=target_event_id,
            fixture_id=fixture_id,
        )
        fetched_at = utc_now()
        event_status, current_fixtures, event_live = await asyncio.gather(
            self._client.fetch_event_status(),
            self._client.fetch_current_fixtures(event_id=current_event_id),
            self._client.fetch_event_live(event_id=current_event_id),
        )
        return await self._persist_live_unlocked(
            season=season,
            current_event_id=current_event_id,
            event_status=event_status,
            current_fixtures=current_fixtures,
            event_live=event_live,
            fetched_at=fetched_at,
        )

    async def ingest_live_payload(
        self,
        *,
        season_id: str,
        event_id: int,
        event_status: EventStatusResponse,
        current_fixtures: list[Fixture],
        event_live: EventLiveResponse,
        fetched_at: datetime,
    ) -> IngestionResult:
        """Persist an already collected and validated live snapshot."""
        async with self._repository.ingestion_lock():
            season = await self._repository.get_current_season()
            if season is None:
                raise RuntimeError(
                    "Cannot ingest live resources before reference data exists.",
                )
            if season.id != season_id:
                raise RuntimeError(
                    f"Live payload season {season_id} does not match "
                    f"current season {season.id}.",
                )
            event = await self._repository.get_event(
                season_id=season_id,
                event_id=event_id,
            )
            if event is None:
                raise RuntimeError(
                    f"Live payload event {event_id} does not exist in "
                    f"season {season_id}.",
                )
            return await self._persist_live_unlocked(
                season=season,
                current_event_id=event_id,
                event_status=event_status,
                current_fixtures=current_fixtures,
                event_live=event_live,
                fetched_at=fetched_at,
            )

    async def _persist_live_unlocked(
        self,
        *,
        season: Season,
        current_event_id: int,
        event_status: EventStatusResponse,
        current_fixtures: list[Fixture],
        event_live: EventLiveResponse,
        fetched_at: datetime,
    ) -> IngestionResult:
        """Persist live resources while a lock is already held."""
        outcomes = [
            await self._repository.upsert_event_status(
                status=event_status,
                metadata=metadata_for_payload(
                    season_id=season.id,
                    source_key=IngestionSourceKey.EVENT_STATUS,
                    event_id=current_event_id,
                    payload=model_to_payload(model=event_status),
                    fetched_at=fetched_at,
                ),
            ),
            await self._repository.upsert_fixtures(
                fixtures=current_fixtures,
                metadata=metadata_for_payload(
                    season_id=season.id,
                    source_key=IngestionSourceKey.CURRENT_FIXTURES,
                    event_id=current_event_id,
                    payload=model_to_payload(model=current_fixtures),
                    fetched_at=fetched_at,
                ),
            ),
            await self._repository.upsert_event_live(
                event_id=current_event_id,
                live=event_live,
                metadata=metadata_for_payload(
                    season_id=season.id,
                    source_key=IngestionSourceKey.EVENT_LIVE,
                    event_id=current_event_id,
                    payload=model_to_payload(model=event_live),
                    fetched_at=fetched_at,
                ),
            ),
        ]
        return result_from_outcomes(
            outcomes=outcomes,
            season_id=season.id,
            current_event_id=current_event_id,
            has_active_fixture=has_active_fixture(fixtures=current_fixtures),
        )

    async def _resolve_live_target(
        self,
        *,
        target_event_id: int | None,
        fixture_id: int | None,
    ) -> tuple[Season, int]:
        """Resolve CLI live-ingestion target options to a season and event id."""
        if target_event_id is not None and fixture_id is not None:
            raise ValueError("Provide either target_event_id or fixture_id, not both.")
        season = await self._repository.get_current_season()
        if season is None:
            raise RuntimeError(
                "Cannot ingest live resources before reference data exists.",
            )
        if target_event_id is not None:
            return season, target_event_id
        if fixture_id is not None:
            fixture = await self._repository.get_fixture(
                season_id=season.id,
                fixture_id=fixture_id,
            )
            if fixture is None:
                raise RuntimeError(
                    f"Cannot ingest live resources for fixture {fixture_id}: "
                    "fixture does not exist. Run reference ingestion first.",
                )
            if fixture.event is None:
                raise RuntimeError(
                    f"Cannot ingest live resources for fixture {fixture_id}: "
                    "fixture has no event id.",
                )
            return season, fixture.event
        current_event = await self._repository.get_current_event(season_id=season.id)
        if current_event is None:
            raise RuntimeError(
                "Cannot ingest live resources because FPL has no current event.",
            )
        return season, current_event.id


def utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(tz=UTC)


def metadata_for_payload(
    *,
    season_id: str,
    source_key: IngestionSourceKey,
    event_id: int | None,
    payload: object,
    fetched_at: datetime,
) -> IngestionMetadata:
    """Create canonical hash metadata for a fetched upstream source."""
    return IngestionMetadata(
        season_id=season_id,
        source_key=source_key,
        event_id=event_id,
        payload_hash=payload_sha256(payload=payload),
        fetched_at=fetched_at,
        checked_at=utc_now(),
    )


def result_from_outcomes(
    *,
    outcomes: list[UpsertOutcome],
    season_id: str | None,
    current_event_id: int | None,
    has_active_fixture: bool,
) -> IngestionResult:
    """Summarize changed and unchanged writes for one ingestion phase."""
    changed_count = sum(len(outcome.change_events) for outcome in outcomes)
    unchanged_count = sum(1 for outcome in outcomes if not outcome.changed)
    return IngestionResult(
        changed_count=changed_count,
        unchanged_count=unchanged_count,
        season_id=season_id,
        current_event_id=current_event_id,
        has_active_fixture=has_active_fixture,
    )


def combine_results(
    *,
    first: IngestionResult,
    second: IngestionResult,
) -> IngestionResult:
    """Merge reference and live ingestion summaries into one result."""
    return IngestionResult(
        changed_count=first.changed_count + second.changed_count,
        unchanged_count=first.unchanged_count + second.unchanged_count,
        season_id=second.season_id if second.season_id is not None else first.season_id,
        current_event_id=second.current_event_id,
        has_active_fixture=second.has_active_fixture,
    )
