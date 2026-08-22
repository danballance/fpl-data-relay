from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

import pytest

from fpl_data_relay.adapters.outbound.postgres.database import (
    IngestionLockError,
    bootstrap_snapshots,
    snapshot_for_event_status,
    snapshot_for_fixture,
    snapshot_for_live_element,
)
from fpl_data_relay.application.database import SCHEMA_VERSION
from fpl_data_relay.application.ports.administration import SchemaStatus
from fpl_data_relay.domain.changes import (
    EVENT_NAMES,
    ChangeEvent,
    EntityChange,
    EntityFamily,
    EntitySnapshot,
    IngestionMetadata,
    IngestionSourceKey,
    IngestionSourceStatus,
    UpsertOutcome,
    diff_entity_snapshots,
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


class FakeLock:
    def __init__(self, *, store: InMemoryStore) -> None:
        self._store = store

    async def __aenter__(self) -> None:
        if self._store.locked:
            raise IngestionLockError("Another ingestion cycle is already running.")
        self._store.locked = True

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        self._store.locked = False


class InMemoryStore:
    def __init__(self) -> None:
        self.source_hashes: dict[
            tuple[str, IngestionSourceKey, int | None],
            str,
        ] = {}
        self.events: list[ChangeEvent] = []
        self.entity_changes: list[EntityChange] = []
        self.snapshots: dict[
            tuple[str, EntityFamily, str],
            EntitySnapshot,
        ] = {}
        self.source_statuses: dict[
            tuple[str, IngestionSourceKey, int | None],
            IngestionSourceStatus,
        ] = {}
        self.seasons: dict[str, Season] = {}
        self.fpl_events: dict[str, list[Event]] = {}
        self.phases: dict[str, list[Phase]] = {}
        self.teams: dict[str, list[Team]] = {}
        self.element_types: dict[str, list[ElementType]] = {}
        self.elements: dict[str, list[Element]] = {}
        self.fixtures: dict[tuple[str, int], Fixture] = {}
        self.event_status: dict[str, EventStatusResponse] = {}
        self.live_elements: dict[tuple[str, int], EventLiveResponse] = {}
        self.locked = False
        self.schema_applied = False
        self.closed = False

    async def apply_schema(self) -> None:
        self.schema_applied = True

    async def check_schema_version(self, *, expected_version: int) -> None:
        if expected_version != SCHEMA_VERSION:
            raise RuntimeError("Unexpected schema version.")

    async def schema_status(self) -> SchemaStatus:
        return SchemaStatus(
            applied_versions=[SCHEMA_VERSION],
            pending_versions=[],
        )

    async def upsert_bootstrap(
        self,
        *,
        season: Season,
        bootstrap: BootstrapStatic,
        metadata: IngestionMetadata,
    ) -> UpsertOutcome:
        if season.id != metadata.season_id:
            raise ValueError("Season id does not match ingestion metadata.")
        if season.is_current:
            self.seasons = {
                season_id: existing.model_copy(update={"is_current": False})
                for season_id, existing in self.seasons.items()
            }
        self.seasons[season.id] = season
        self.fpl_events[season.id] = bootstrap.events
        self.phases[season.id] = bootstrap.phases
        self.teams[season.id] = bootstrap.teams
        self.element_types[season.id] = bootstrap.element_types
        self.elements[season.id] = bootstrap.elements
        return self._record_source(
            metadata=metadata,
            snapshots=bootstrap_snapshots(bootstrap=bootstrap),
            authoritative=True,
        )

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
        return [
            await self.upsert_bootstrap(
                season=season,
                bootstrap=bootstrap,
                metadata=bootstrap_metadata,
            ),
            await self.upsert_fixtures(
                fixtures=fixtures,
                metadata=fixtures_metadata,
            ),
            await self.upsert_event_status(
                status=status,
                metadata=status_metadata,
            ),
        ]

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
        return [
            await self.upsert_event_status(
                status=status,
                metadata=status_metadata,
            ),
            await self.upsert_fixtures(
                fixtures=fixtures,
                metadata=fixtures_metadata,
            ),
            await self.upsert_event_live(
                event_id=event_id,
                live=live,
                metadata=live_metadata,
            ),
        ]

    async def upsert_fixtures(
        self,
        *,
        fixtures: list[Fixture],
        metadata: IngestionMetadata,
    ) -> UpsertOutcome:
        for fixture in fixtures:
            self.fixtures[(metadata.season_id, fixture.id)] = fixture
        return self._record_source(
            metadata=metadata,
            snapshots={
                EntityFamily.FIXTURES: [
                    snapshot_for_fixture(fixture=fixture) for fixture in fixtures
                ],
            },
            authoritative=metadata.source_key is IngestionSourceKey.FIXTURES,
        )

    async def upsert_event_status(
        self,
        *,
        status: EventStatusResponse,
        metadata: IngestionMetadata,
    ) -> UpsertOutcome:
        self.event_status[metadata.season_id] = status
        return self._record_source(
            metadata=metadata,
            snapshots={
                EntityFamily.EVENT_STATUS: [
                    snapshot_for_event_status(
                        status=status,
                        event_id=metadata.event_id,
                    ),
                ],
            },
            authoritative=True,
        )

    async def upsert_event_live(
        self,
        *,
        event_id: int,
        live: EventLiveResponse,
        metadata: IngestionMetadata,
    ) -> UpsertOutcome:
        self.live_elements[(metadata.season_id, event_id)] = live
        return self._record_source(
            metadata=metadata,
            snapshots={
                EntityFamily.EVENT_LIVE: [
                    snapshot_for_live_element(
                        live_element=element,
                        event_id=event_id,
                    )
                    for element in live.elements
                ],
            },
            authoritative=True,
        )

    async def list_seasons(self) -> list[Season]:
        return sorted(self.seasons.values(), key=lambda season: season.id)

    async def get_current_season(self) -> Season | None:
        current_seasons = [
            season for season in self.seasons.values() if season.is_current
        ]
        if len(current_seasons) != 1:
            return None
        return current_seasons[0]

    async def get_season(self, *, season_id: str) -> Season | None:
        return self.seasons.get(season_id)

    async def get_current_event(self, *, season_id: str) -> Event | None:
        current_events = [
            event for event in self.fpl_events.get(season_id, []) if event.is_current
        ]
        if len(current_events) != 1:
            return None
        return current_events[0]

    async def list_events(self, *, season_id: str) -> list[Event]:
        return self.fpl_events.get(season_id, [])

    async def get_event(self, *, season_id: str, event_id: int) -> Event | None:
        return next(
            (
                event
                for event in self.fpl_events.get(season_id, [])
                if event.id == event_id
            ),
            None,
        )

    async def list_phases(self, *, season_id: str) -> list[Phase]:
        return self.phases.get(season_id, [])

    async def list_teams(self, *, season_id: str) -> list[Team]:
        return self.teams.get(season_id, [])

    async def get_team(self, *, season_id: str, team_id: int) -> Team | None:
        return next(
            (team for team in self.teams.get(season_id, []) if team.id == team_id),
            None,
        )

    async def list_element_types(self, *, season_id: str) -> list[ElementType]:
        return self.element_types.get(season_id, [])

    async def list_elements(
        self,
        *,
        season_id: str,
        after_id: int,
        limit: int,
    ) -> list[Element]:
        return [
            element
            for element in self.elements.get(season_id, [])
            if element.id > after_id
        ][:limit]

    async def get_element(self, *, season_id: str, element_id: int) -> Element | None:
        return next(
            (
                element
                for element in self.elements.get(season_id, [])
                if element.id == element_id
            ),
            None,
        )

    async def list_fixtures(
        self,
        *,
        season_id: str,
        event_id: int | None,
        after_id: int,
        limit: int,
    ) -> list[Fixture]:
        fixtures = [
            fixture
            for (fixture_season_id, _), fixture in self.fixtures.items()
            if fixture_season_id == season_id
        ]
        filtered = (
            fixtures
            if event_id is None
            else [fixture for fixture in fixtures if fixture.event == event_id]
        )
        return [fixture for fixture in filtered if fixture.id > after_id][:limit]

    async def get_fixture(self, *, season_id: str, fixture_id: int) -> Fixture | None:
        return self.fixtures.get((season_id, fixture_id))

    async def get_event_status(self, *, season_id: str) -> EventStatusResponse | None:
        return self.event_status.get(season_id)

    async def list_live_elements(
        self,
        *,
        season_id: str,
        event_id: int,
        after_id: int,
        limit: int,
    ) -> list[LiveElement]:
        response = self.live_elements.get((season_id, event_id))
        if response is None:
            return []
        return [
            element for element in response.elements if element.id > after_id
        ][:limit]

    async def get_live_element(
        self,
        *,
        season_id: str,
        event_id: int,
        element_id: int,
    ) -> LiveElement | None:
        elements = await self.list_live_elements(
            season_id=season_id,
            event_id=event_id,
            after_id=0,
            limit=200,
        )
        return next((element for element in elements if element.id == element_id), None)

    async def list_change_events(
        self,
        *,
        after_id: int,
        limit: int,
    ) -> list[ChangeEvent]:
        return [event for event in self.events if event.id > after_id][:limit]

    async def list_recent_change_events(self, *, limit: int) -> list[ChangeEvent]:
        return sorted(self.events, key=lambda event: event.id, reverse=True)[:limit]

    async def list_change_events_before(
        self,
        *,
        before_id: int,
        limit: int,
    ) -> list[ChangeEvent]:
        return sorted(
            [event for event in self.events if event.id < before_id],
            key=lambda event: event.id,
            reverse=True,
        )[:limit]

    async def list_entity_changes(
        self,
        *,
        change_event_id: int,
        after_id: int,
        limit: int,
    ) -> list[EntityChange]:
        return [
            change
            for change in self.entity_changes
            if change.change_event_id == change_event_id and change.id > after_id
        ][:limit]

    async def list_ingestion_source_statuses(
        self,
        *,
        season_id: str,
    ) -> list[IngestionSourceStatus]:
        return [
            status
            for status in self.source_statuses.values()
            if status.season_id == season_id
        ]

    def _record_source(
        self,
        *,
        metadata: IngestionMetadata,
        snapshots: dict[EntityFamily, list[EntitySnapshot]],
        authoritative: bool,
    ) -> UpsertOutcome:
        source_identity = (
            metadata.season_id,
            metadata.source_key,
            metadata.event_id,
        )
        existing_hash = self.source_hashes.get(source_identity)
        if existing_hash == metadata.payload_hash:
            existing = self.source_statuses[source_identity]
            self.source_statuses[source_identity] = existing.model_copy(
                update={"checked_at": metadata.checked_at},
            )
            return UpsertOutcome(changed=False, change_events=[])
        source_exists = source_identity in self.source_hashes
        self.source_hashes[source_identity] = metadata.payload_hash
        events: list[ChangeEvent] = []
        for family, current in snapshots.items():
            previous = [
                snapshot
                for (season_id, snapshot_family, _), snapshot in self.snapshots.items()
                if season_id == metadata.season_id and snapshot_family is family
                and (
                    family is not EntityFamily.EVENT_LIVE
                    or snapshot.entity_key.startswith(f"{metadata.event_id}:")
                )
            ]
            diff = diff_entity_snapshots(
                previous=previous,
                current=current,
                authoritative=authoritative,
                baseline=not source_exists and not previous,
            )
            if not diff.changes:
                for snapshot in current:
                    identity = (metadata.season_id, family, snapshot.entity_key)
                    self.snapshots[identity] = snapshot
                continue
            event = ChangeEvent(
                id=len(self.events) + 1,
                season_id=metadata.season_id,
                entity_family=family,
                event_name=EVENT_NAMES[family],
                source_key=metadata.source_key,
                source_event_id=metadata.event_id,
                payload_hash=metadata.payload_hash,
                created_count=diff.created_count,
                updated_count=diff.updated_count,
                deleted_count=diff.deleted_count,
                fetched_at=metadata.fetched_at,
                created_at=datetime.now(tz=UTC),
            )
            self.events.append(event)
            events.append(event)
            for draft in diff.changes:
                self.entity_changes.append(
                    EntityChange(
                        id=len(self.entity_changes) + 1,
                        change_event_id=event.id,
                        entity_key=draft.entity_key,
                        entity_label=draft.entity_label,
                        kind=draft.kind,
                        fields=draft.fields,
                        created_at=event.created_at,
                    ),
                )
                if authoritative and draft.kind.value == "deleted":
                    self.snapshots.pop(
                        (metadata.season_id, family, draft.entity_key),
                        None,
                    )
            for snapshot in current:
                identity = (metadata.season_id, family, snapshot.entity_key)
                self.snapshots[identity] = snapshot
        prior_status = self.source_statuses.get(source_identity)
        self.source_statuses[source_identity] = IngestionSourceStatus(
            season_id=metadata.season_id,
            source_key=metadata.source_key,
            event_id=metadata.event_id,
            payload_hash=metadata.payload_hash,
            fetched_at=metadata.fetched_at,
            checked_at=metadata.checked_at,
            last_changed_at=(
                metadata.checked_at
                if events
                else (
                    None if prior_status is None else prior_status.last_changed_at
                )
            ),
        )
        return UpsertOutcome(
            changed=True,
            change_events=events,
        )

    def ingestion_lock(self) -> FakeLock:
        return FakeLock(store=self)

    async def close(self) -> None:
        self.closed = True


class FakeClient:
    def __init__(self) -> None:
        self.bootstrap_current_id: int | None = 1
        self.fixture_started = True
        self.current_fixture_event_ids: list[int] = []
        self.live_event_ids: list[int] = []
        self.closed = False

    async def close(self) -> None:
        self.closed = True

    async def fetch_bootstrap_static(self) -> BootstrapStatic:
        current_ids = (
            []
            if self.bootstrap_current_id is None
            else [self.bootstrap_current_id]
        )
        return BootstrapStatic.model_validate(
            bootstrap_payload(current_ids=current_ids),
        )

    async def fetch_fixtures(self) -> list[Fixture]:
        return [
            Fixture.model_validate(
                fixture_payload(
                    fixture_id=1,
                    event=1,
                    started=False,
                    finished=False,
                ),
            ),
        ]

    async def fetch_current_fixtures(self, *, event_id: int) -> list[Fixture]:
        self.current_fixture_event_ids.append(event_id)
        return [
            Fixture.model_validate(
                fixture_payload(
                    fixture_id=event_id,
                    event=event_id,
                    started=self.fixture_started,
                    finished=False,
                ),
            ),
        ]

    async def fetch_event_status(self) -> EventStatusResponse:
        if self.bootstrap_current_id is None:
            return EventStatusResponse(status=[])
        return EventStatusResponse.model_validate(
            {
                "status": [
                    {
                        "event": self.bootstrap_current_id,
                        "bonus_added": False,
                        "date": "2026-06-20",
                        "points": "",
                    },
                ],
            },
        )

    async def fetch_event_live(self, *, event_id: int) -> EventLiveResponse:
        self.live_event_ids.append(event_id)
        return EventLiveResponse.model_validate(
            {
                "elements": [
                    {
                        "id": event_id,
                        "stats": {"total_points": 4},
                        "explain": [
                            {
                                "fixture": event_id,
                                "stats": [{"identifier": "minutes", "points": 2}],
                            },
                        ],
                    },
                ],
            },
        )


def bootstrap_payload(*, current_ids: list[int]) -> dict[str, object]:
    return {
        "events": [
            {
                "id": event_id,
                "name": f"Gameweek {event_id}",
                "deadline_time": (
                    "2025-08-15T17:30:00Z"
                    if event_id == 1
                    else "2026-05-24T13:30:00Z"
                ),
                "is_current": event_id in current_ids,
                "unknown_event_field": "kept",
            }
            for event_id in [1, 2]
        ],
        "teams": [
            {"id": 1, "name": "Team", "short_name": "TST"},
            {"id": 2, "name": "Other Team", "short_name": "OTH"},
        ],
        "elements": [
            {
                "id": 1,
                "first_name": "First",
                "second_name": "Second",
                "web_name": "Player",
                "photo": "1.jpg",
                "team": 1,
                "element_type": 1,
            },
        ],
        "element_types": [
            {"id": 1, "singular_name": "Goalkeeper", "plural_name": "Goalkeepers"},
        ],
    }


def fixture_payload(
    *,
    fixture_id: int,
    event: int | None,
    started: bool,
    finished: bool,
) -> dict[str, object]:
    return {
        "id": fixture_id,
        "event": event,
        "team_h": 1,
        "team_a": 2,
        "started": started,
        "finished": finished,
        "unknown_fixture_field": "kept",
    }


class FakePostgresConnection:
    def __init__(self, *, pool: FakePostgresPool) -> None:
        self.pool = pool

    def transaction(self) -> FakePostgresTransaction:
        return FakePostgresTransaction(pool=self.pool)

    async def execute(self, query: str, *arguments: object) -> str:
        if "INSERT INTO relay_schema_migrations" in query:
            self.pool.applied_migrations.append(
                {
                    "version": arguments[0],
                    "name": arguments[1],
                    "checksum": arguments[2],
                },
            )
            self.pool.schema_version = cast("int", arguments[0])
            return "CREATE TABLE"
        if "UPDATE relay_resources" in query:
            resource_key = str(arguments[0])
            checked_at = arguments[1]
            self.pool.resources[resource_key]["checked_at"] = checked_at
            return "UPDATE 1"
        if "INSERT INTO relay_resources" in query:
            resource_key = str(arguments[0])
            self.pool.resources[resource_key] = {
                "resource_key": resource_key,
                "event_id": arguments[1],
                "payload": arguments[2],
                "payload_hash": arguments[3],
                "fetched_at": arguments[4],
                "checked_at": arguments[5],
            }
            return "INSERT 0 1"
        return "OK"

    async def fetchrow(
        self,
        query: str,
        *arguments: object,
    ) -> dict[str, object] | None:
        if "FROM relay_resources" in query:
            return self.pool.resources.get(str(arguments[0]))
        if "INSERT INTO relay_change_events" in query:
            event = {
                "id": len(self.pool.events) + 1,
                "resource_key": arguments[0],
                "event_name": arguments[1],
                "event_id": arguments[2],
                "payload_hash": arguments[3],
                "fetched_at": arguments[4],
                "created_at": datetime.now(tz=UTC),
            }
            self.pool.events.append(event)
            return event
        return None

    async def fetch(self, query: str, *arguments: object) -> list[object]:
        if "FROM relay_schema_migrations" in query:
            return cast("list[object]", self.pool.applied_migrations)
        after_id = cast("int", arguments[0])
        limit = cast("int", arguments[1])
        events = [
            event
            for event in self.pool.events
            if cast("int", event["id"]) > after_id
        ][:limit]
        return cast("list[object]", events)

    async def fetchval(self, query: str, *arguments: object) -> object:
        if "to_regclass" in query:
            return (
                "relay_schema_migrations"
                if self.pool.applied_migrations
                or self.pool.schema_version is not None
                else None
            )
        if "SELECT MAX(version)" in query:
            return self.pool.schema_version
        if "SELECT payload_hash" in query:
            resource = self.pool.resources.get(str(arguments[0]))
            return None if resource is None else resource["payload_hash"]
        if "pg_try_advisory_xact_lock" in query:
            if self.pool.locked:
                return False
            self.pool.locked = True
            return True
        return None


class FakePostgresAcquire:
    def __init__(self, *, connection: FakePostgresConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> FakePostgresConnection:
        return self._connection

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback


class FakePostgresTransaction:
    def __init__(self, *, pool: FakePostgresPool) -> None:
        self._pool = pool

    async def __aenter__(self) -> object:
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback
        self._pool.locked = False


class FakePostgresPool:
    def __init__(self) -> None:
        self.schema_version: int | None = None
        self.applied_migrations: list[dict[str, object]] = []
        self.resources: dict[str, dict[str, object]] = {}
        self.events: list[dict[str, object]] = []
        self.notifications: list[str] = []
        self.listeners: list[Callable[[object, int, str, str], None]] = []
        self.locked = False
        self.closed = False
        self.connection = FakePostgresConnection(pool=self)

    def acquire(self) -> FakePostgresAcquire:
        return FakePostgresAcquire(connection=self.connection)

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def in_memory_store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def fake_client() -> FakeClient:
    return FakeClient()
