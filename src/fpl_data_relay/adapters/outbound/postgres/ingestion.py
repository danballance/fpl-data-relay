"""PostgreSQL ingestion repository adapter."""

from contextlib import AbstractAsyncContextManager

from fpl_data_relay.adapters.outbound.postgres.database import PostgresDatabase
from fpl_data_relay.domain.changes import IngestionMetadata, UpsertOutcome
from fpl_data_relay.domain.fixtures import Fixture
from fpl_data_relay.domain.live import EventLiveResponse, EventStatusResponse
from fpl_data_relay.domain.reference import BootstrapStatic, Event, Season


class PostgresIngestionRepository:
    """Expose only persistence operations required by ingestion."""

    def __init__(self, *, database: PostgresDatabase) -> None:
        self._database = database

    async def upsert_reference_snapshot(
        self,
        *,
        season: Season,
        bootstrap: BootstrapStatic,
        fixtures: list[Fixture],
        status: EventStatusResponse,
        bootstrap_metadata: IngestionMetadata,
        fixtures_metadata: IngestionMetadata,
        status_metadata: IngestionMetadata,
    ) -> list[UpsertOutcome]:
        return await self._database.upsert_reference_snapshot(
            season=season,
            bootstrap=bootstrap,
            fixtures=fixtures,
            status=status,
            bootstrap_metadata=bootstrap_metadata,
            fixtures_metadata=fixtures_metadata,
            status_metadata=status_metadata,
        )

    async def upsert_live_snapshot(
        self,
        *,
        event_id: int,
        status: EventStatusResponse,
        fixtures: list[Fixture],
        live: EventLiveResponse,
        status_metadata: IngestionMetadata,
        fixtures_metadata: IngestionMetadata,
        live_metadata: IngestionMetadata,
    ) -> list[UpsertOutcome]:
        return await self._database.upsert_live_snapshot(
            event_id=event_id,
            status=status,
            fixtures=fixtures,
            live=live,
            status_metadata=status_metadata,
            fixtures_metadata=fixtures_metadata,
            live_metadata=live_metadata,
        )

    async def upsert_bootstrap(
        self,
        *,
        season: Season,
        bootstrap: BootstrapStatic,
        metadata: IngestionMetadata,
    ) -> UpsertOutcome:
        return await self._database.upsert_bootstrap(
            season=season,
            bootstrap=bootstrap,
            metadata=metadata,
            delete_missing=True,
        )

    async def upsert_fixtures(
        self,
        *,
        fixtures: list[Fixture],
        metadata: IngestionMetadata,
    ) -> UpsertOutcome:
        return await self._database.upsert_fixtures(
            fixtures=fixtures,
            metadata=metadata,
        )

    async def upsert_event_status(
        self,
        *,
        status: EventStatusResponse,
        metadata: IngestionMetadata,
    ) -> UpsertOutcome:
        return await self._database.upsert_event_status(
            status=status,
            metadata=metadata,
        )

    async def upsert_event_live(
        self,
        *,
        event_id: int,
        live: EventLiveResponse,
        metadata: IngestionMetadata,
    ) -> UpsertOutcome:
        return await self._database.upsert_event_live(
            event_id=event_id,
            live=live,
            metadata=metadata,
        )

    async def get_current_season(self) -> Season | None:
        return await self._database.get_current_season()

    async def get_current_event(self, *, season_id: str) -> Event | None:
        return await self._database.get_current_event(season_id=season_id)

    async def get_event(self, *, season_id: str, event_id: int) -> Event | None:
        return await self._database.get_event(
            season_id=season_id,
            event_id=event_id,
        )

    async def get_fixture(
        self,
        *,
        season_id: str,
        fixture_id: int,
    ) -> Fixture | None:
        return await self._database.get_fixture(
            season_id=season_id,
            fixture_id=fixture_id,
        )

    async def maintenance_active(self) -> bool:
        return await self._database.maintenance_active()

    def ingestion_lock(self) -> AbstractAsyncContextManager[None]:
        return self._database.ingestion_lock()
