"""Narrow persistence ports owned by the application layer."""

from contextlib import AbstractAsyncContextManager
from datetime import date, datetime
from typing import Protocol

from fpl_data_relay.domain.changes import (
    ChangeEvent,
    EntityChange,
    IngestionMetadata,
    IngestionSourceStatus,
    UpsertOutcome,
)
from fpl_data_relay.domain.community import (
    CommunityReport,
    CommunityReportDraft,
    CommunityReportSummary,
    ExtractionCacheEntry,
    ExtractionCacheEntryDraft,
    ExtractionCacheLookup,
)
from fpl_data_relay.domain.fixtures import Fixture
from fpl_data_relay.domain.live import (
    EventLiveResponse,
    EventStatusResponse,
    LiveElement,
)
from fpl_data_relay.domain.reference import (
    BootstrapStatic,
    Element,
    ElementType,
    Event,
    Phase,
    Season,
    Team,
)


class IngestionRepository(Protocol):
    """Writes and target lookups required by ingestion."""

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
        """Persist one complete reference bundle atomically."""
        ...

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
        """Persist one complete live bundle atomically."""
        ...

    async def upsert_bootstrap(
        self,
        *,
        season: Season,
        bootstrap: BootstrapStatic,
        metadata: IngestionMetadata,
    ) -> UpsertOutcome:
        """Persist a bootstrap source transactionally."""
        ...

    async def upsert_fixtures(
        self,
        *,
        fixtures: list[Fixture],
        metadata: IngestionMetadata,
    ) -> UpsertOutcome:
        """Persist a fixture source transactionally."""
        ...

    async def upsert_event_status(
        self,
        *,
        status: EventStatusResponse,
        metadata: IngestionMetadata,
    ) -> UpsertOutcome:
        """Persist event-status data transactionally."""
        ...

    async def upsert_event_live(
        self,
        *,
        event_id: int,
        live: EventLiveResponse,
        metadata: IngestionMetadata,
    ) -> UpsertOutcome:
        """Persist event-live data transactionally."""
        ...

    async def get_current_season(self) -> Season | None:
        """Return the current season."""
        ...

    async def get_current_event(self, *, season_id: str) -> Event | None:
        """Return the current event for a season."""
        ...

    async def get_event(self, *, season_id: str, event_id: int) -> Event | None:
        """Return one event for live-job metadata validation."""
        ...

    async def get_fixture(
        self,
        *,
        season_id: str,
        fixture_id: int,
    ) -> Fixture | None:
        """Return one fixture."""
        ...

    def ingestion_lock(self) -> AbstractAsyncContextManager[None]:
        """Acquire the process-wide ingestion lock."""
        ...


class ReferenceRepository(Protocol):
    """Reference-data read operations."""

    async def list_seasons(self) -> list[Season]: ...

    async def get_current_season(self) -> Season | None: ...

    async def get_season(self, *, season_id: str) -> Season | None: ...

    async def get_current_event(self, *, season_id: str) -> Event | None: ...

    async def list_events(self, *, season_id: str) -> list[Event]: ...

    async def get_event(self, *, season_id: str, event_id: int) -> Event | None: ...

    async def list_phases(self, *, season_id: str) -> list[Phase]: ...

    async def list_teams(self, *, season_id: str) -> list[Team]: ...

    async def get_team(self, *, season_id: str, team_id: int) -> Team | None: ...

    async def list_element_types(self, *, season_id: str) -> list[ElementType]: ...

    async def list_elements(
        self,
        *,
        season_id: str,
        after_id: int,
        limit: int,
    ) -> list[Element]: ...

    async def get_element(
        self,
        *,
        season_id: str,
        element_id: int,
    ) -> Element | None: ...

    async def list_fixtures(
        self,
        *,
        season_id: str,
        event_id: int | None,
        after_id: int,
        limit: int,
    ) -> list[Fixture]: ...

    async def get_fixture(
        self,
        *,
        season_id: str,
        fixture_id: int,
    ) -> Fixture | None: ...


class LiveRepository(Protocol):
    """Live-data read operations."""

    async def get_event_status(
        self,
        *,
        season_id: str,
    ) -> EventStatusResponse | None: ...

    async def list_live_elements(
        self,
        *,
        season_id: str,
        event_id: int,
        after_id: int,
        limit: int,
    ) -> list[LiveElement]: ...

    async def get_live_element(
        self,
        *,
        season_id: str,
        event_id: int,
        element_id: int,
    ) -> LiveElement | None: ...


class ChangeEventRepository(Protocol):
    """Change-event replay operations."""

    async def list_change_events(
        self,
        *,
        after_id: int,
        limit: int,
    ) -> list[ChangeEvent]: ...

    async def list_recent_change_events(self, *, limit: int) -> list[ChangeEvent]: ...

    async def list_change_events_before(
        self,
        *,
        before_id: int,
        limit: int,
    ) -> list[ChangeEvent]: ...

    async def list_entity_changes(
        self,
        *,
        change_event_id: int,
        after_id: int,
        limit: int,
    ) -> list[EntityChange]: ...

    async def list_ingestion_source_statuses(
        self,
        *,
        season_id: str,
    ) -> list[IngestionSourceStatus]: ...


class CommunityReportRepository(Protocol):
    """Insert-only reports and bounded report history reads."""

    async def insert_report(
        self,
        *,
        report: CommunityReportDraft,
    ) -> CommunityReport: ...

    async def get_report(self, *, report_id: int) -> CommunityReport | None: ...

    async def get_latest_report(
        self,
        *,
        strategy_key: str,
    ) -> CommunityReport | None: ...

    async def get_report_for_date(
        self,
        *,
        strategy_key: str,
        report_date: date,
    ) -> CommunityReport | None: ...

    async def list_recent_reports(
        self,
        *,
        strategy_key: str,
        limit: int,
    ) -> list[CommunityReportSummary]: ...

    async def list_reports_before(
        self,
        *,
        strategy_key: str,
        before_id: int,
        limit: int,
    ) -> list[CommunityReportSummary]: ...


class CommunityExtractionCacheRepository(Protocol):
    """Exact structured extraction lookups and bounded operational retention."""

    async def prune_expired(self, *, as_of: datetime) -> int: ...

    async def get_entries(
        self,
        *,
        strategy_key: str,
        strategy_version: int,
        extraction_contract_hash: str,
        lookups: list[ExtractionCacheLookup],
        as_of: datetime,
    ) -> list[ExtractionCacheEntry]: ...

    async def insert_entries(
        self,
        *,
        entries: list[ExtractionCacheEntryDraft],
    ) -> int: ...
