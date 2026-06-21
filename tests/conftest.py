from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import cast

import pytest

from fpl_data_relay.json_types import JsonValue
from fpl_data_relay.resources import EVENT_NAMES, EntityFamily, ResourceKey
from fpl_data_relay.schemas import SCHEMA_VERSION
from fpl_data_relay.store import (
    ChangeEvent,
    IngestionLockError,
    IngestionMetadata,
    ResourceWrite,
    StoredResource,
    UpsertOutcome,
)
from fpl_data_relay.upstream_models import (
    BootstrapStatic,
    Element,
    ElementType,
    Event,
    EventLiveResponse,
    EventStatusResponse,
    Fixture,
    LiveElement,
    Phase,
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
        self.resources: dict[ResourceKey, StoredResource] = {}
        self.source_hashes: dict[ResourceKey, str] = {}
        self.events: list[ChangeEvent] = []
        self.fpl_events: list[Event] = []
        self.phases: list[Phase] = []
        self.teams: list[Team] = []
        self.element_types: list[ElementType] = []
        self.elements: list[Element] = []
        self.fixtures: dict[int, Fixture] = {}
        self.event_status: EventStatusResponse | None = None
        self.live_elements: dict[int, EventLiveResponse] = {}
        self.locked = False
        self.schema_applied = False
        self.closed = False

    async def apply_schema(self) -> None:
        self.schema_applied = True

    async def check_schema_version(self, *, expected_version: int) -> None:
        if expected_version != SCHEMA_VERSION:
            raise RuntimeError("Unexpected schema version.")

    async def upsert_bootstrap(
        self,
        *,
        bootstrap: BootstrapStatic,
        metadata: IngestionMetadata,
    ) -> UpsertOutcome:
        self.fpl_events = bootstrap.events
        self.phases = bootstrap.phases
        self.teams = bootstrap.teams
        self.element_types = bootstrap.element_types
        self.elements = bootstrap.elements
        return self._record_source(
            metadata=metadata,
            families=[
                EntityFamily.EVENTS,
                EntityFamily.PHASES,
                EntityFamily.TEAMS,
                EntityFamily.ELEMENT_TYPES,
                EntityFamily.ELEMENT_STATS,
                EntityFamily.ELEMENTS,
            ],
        )

    async def upsert_fixtures(
        self,
        *,
        fixtures: list[Fixture],
        metadata: IngestionMetadata,
    ) -> UpsertOutcome:
        for fixture in fixtures:
            self.fixtures[fixture.id] = fixture
        return self._record_source(
            metadata=metadata,
            families=[EntityFamily.FIXTURES],
        )

    async def upsert_event_status(
        self,
        *,
        status: EventStatusResponse,
        metadata: IngestionMetadata,
    ) -> UpsertOutcome:
        self.event_status = status
        return self._record_source(
            metadata=metadata,
            families=[EntityFamily.EVENT_STATUS],
        )

    async def upsert_event_live(
        self,
        *,
        event_id: int,
        live: EventLiveResponse,
        metadata: IngestionMetadata,
    ) -> UpsertOutcome:
        self.live_elements[event_id] = live
        return self._record_source(
            metadata=metadata,
            families=[EntityFamily.EVENT_LIVE],
        )

    async def get_current_event(self) -> Event | None:
        current_events = [event for event in self.fpl_events if event.is_current]
        if len(current_events) != 1:
            return None
        return current_events[0]

    async def list_events(self) -> list[Event]:
        return self.fpl_events

    async def get_event(self, *, event_id: int) -> Event | None:
        return next((event for event in self.fpl_events if event.id == event_id), None)

    async def list_phases(self) -> list[Phase]:
        return self.phases

    async def list_teams(self) -> list[Team]:
        return self.teams

    async def get_team(self, *, team_id: int) -> Team | None:
        return next((team for team in self.teams if team.id == team_id), None)

    async def list_element_types(self) -> list[ElementType]:
        return self.element_types

    async def list_elements(self) -> list[Element]:
        return self.elements

    async def get_element(self, *, element_id: int) -> Element | None:
        return next(
            (element for element in self.elements if element.id == element_id),
            None,
        )

    async def list_fixtures(self, *, event_id: int | None) -> list[Fixture]:
        fixtures = list(self.fixtures.values())
        if event_id is None:
            return fixtures
        return [fixture for fixture in fixtures if fixture.event == event_id]

    async def get_fixture(self, *, fixture_id: int) -> Fixture | None:
        return self.fixtures.get(fixture_id)

    async def get_event_status(self) -> EventStatusResponse | None:
        return self.event_status

    async def list_live_elements(self, *, event_id: int) -> list[LiveElement]:
        response = self.live_elements.get(event_id)
        return [] if response is None else response.elements

    async def get_live_element(
        self,
        *,
        event_id: int,
        element_id: int,
    ) -> LiveElement | None:
        elements = await self.list_live_elements(event_id=event_id)
        return next((element for element in elements if element.id == element_id), None)

    async def get_resource(
        self,
        *,
        resource_key: ResourceKey,
    ) -> StoredResource | None:
        return self.resources.get(resource_key)

    async def list_change_events(
        self,
        *,
        after_id: int,
        limit: int,
    ) -> list[ChangeEvent]:
        return [event for event in self.events if event.id > after_id][:limit]

    async def upsert_resource(self, *, resource: ResourceWrite) -> UpsertOutcome:
        existing = self.resources.get(resource.resource_key)
        if existing is not None and existing.payload_hash == resource.payload_hash:
            self.resources[resource.resource_key] = StoredResource(
                resource_key=existing.resource_key,
                event_id=existing.event_id,
                payload=existing.payload,
                payload_hash=existing.payload_hash,
                fetched_at=existing.fetched_at,
                checked_at=resource.checked_at,
            )
            return UpsertOutcome(changed=False, change_event=None)
        stored = StoredResource(
            resource_key=resource.resource_key,
            event_id=resource.event_id,
            payload=cast("JsonValue", resource.payload),
            payload_hash=resource.payload_hash,
            fetched_at=resource.fetched_at,
            checked_at=resource.checked_at,
        )
        self.resources[resource.resource_key] = stored
        event = ChangeEvent(
            id=len(self.events) + 1,
            resource_key=resource.resource_key,
            event_name=resource.event_name,
            event_id=resource.event_id,
            payload_hash=resource.payload_hash,
            fetched_at=resource.fetched_at,
            created_at=datetime.now(tz=UTC),
        )
        self.events.append(event)
        return UpsertOutcome(changed=True, change_event=event)

    def _record_source(
        self,
        *,
        metadata: IngestionMetadata,
        families: list[EntityFamily],
    ) -> UpsertOutcome:
        resource_key = ResourceKey(metadata.source_key.value)
        existing_hash = self.source_hashes.get(resource_key)
        if existing_hash == metadata.payload_hash:
            return UpsertOutcome(changed=False, change_events=[])
        self.source_hashes[resource_key] = metadata.payload_hash
        self.resources[resource_key] = StoredResource(
            resource_key=resource_key,
            event_id=metadata.event_id,
            payload=cast("JsonValue", {"source_key": resource_key.value}),
            payload_hash=metadata.payload_hash,
            fetched_at=metadata.fetched_at,
            checked_at=metadata.checked_at,
        )
        events: list[ChangeEvent] = []
        for family in families:
            event = ChangeEvent(
                id=len(self.events) + 1,
                entity_family=family,
                event_name=EVENT_NAMES[family],
                source_key=metadata.source_key,
                resource_key=resource_key,
                event_id=metadata.event_id,
                payload_hash=metadata.payload_hash,
                fetched_at=metadata.fetched_at,
                created_at=datetime.now(tz=UTC),
            )
            self.events.append(event)
            events.append(event)
        return UpsertOutcome(
            changed=True,
            change_events=events,
            change_event=events[0],
        )

    def ingestion_lock(self) -> FakeLock:
        return FakeLock(store=self)

    async def watch_change_events(
        self,
        *,
        after_id: int,
        heartbeat_seconds: int,
    ) -> AsyncIterator[ChangeEvent | None]:
        del heartbeat_seconds
        for event in await self.list_change_events(after_id=after_id, limit=100):
            yield event
        yield None

    async def close(self) -> None:
        self.closed = True


class FakeClient:
    def __init__(self) -> None:
        self.bootstrap_current_id = 1
        self.fixture_started = True
        self.current_fixture_event_ids: list[int] = []
        self.live_event_ids: list[int] = []
        self.closed = False

    async def close(self) -> None:
        self.closed = True

    async def fetch_bootstrap_static(self) -> BootstrapStatic:
        return BootstrapStatic.model_validate(
            bootstrap_payload(current_ids=[self.bootstrap_current_id]),
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
        return EventStatusResponse.model_validate(
            {
                "status": [
                    {
                        "event": self.bootstrap_current_id,
                        "bonus_added": False,
                        "date": "2026-06-20",
                        "leagues_updated": False,
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
                "is_current": event_id in current_ids,
                "unknown_event_field": "kept",
            }
            for event_id in [1, 2]
        ],
        "teams": [{"id": 1, "name": "Team", "short_name": "TST"}],
        "elements": [
            {
                "id": 1,
                "first_name": "First",
                "second_name": "Second",
                "web_name": "Player",
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


def resource_write(
    *,
    resource_key: ResourceKey,
    payload_hash: str,
) -> ResourceWrite:
    timestamp = datetime(2026, 6, 20, tzinfo=UTC)
    return ResourceWrite(
        resource_key=resource_key,
        event_name=EVENT_NAMES[resource_key],
        event_id=None,
        payload={"resource": resource_key.value},
        payload_hash=payload_hash,
        fetched_at=timestamp,
        checked_at=timestamp,
    )


class FakePostgresConnection:
    def __init__(self, *, pool: FakePostgresPool) -> None:
        self.pool = pool

    def transaction(self) -> FakePostgresTransaction:
        return FakePostgresTransaction()

    async def execute(self, query: str, *arguments: object) -> str:
        if "relay_schema_version" in query and "CREATE TABLE" in query:
            self.pool.schema_version = SCHEMA_VERSION
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
        del query
        after_id = cast("int", arguments[0])
        limit = cast("int", arguments[1])
        events = [
            event
            for event in self.pool.events
            if cast("int", event["id"]) > after_id
        ][:limit]
        return cast("list[object]", events)

    async def fetchval(self, query: str, *arguments: object) -> object:
        if "SELECT version" in query:
            return self.pool.schema_version
        if "SELECT payload_hash" in query:
            resource = self.pool.resources.get(str(arguments[0]))
            return None if resource is None else resource["payload_hash"]
        if "pg_try_advisory_lock" in query:
            if self.pool.locked:
                return False
            self.pool.locked = True
            return True
        if "pg_advisory_unlock" in query:
            self.pool.locked = False
            return True
        if "pg_notify" in query:
            payload = str(arguments[1])
            self.pool.notifications.append(payload)
            for listener in self.pool.listeners:
                listener(self, 0, str(arguments[0]), payload)
            return None
        return None

    async def add_listener(self, channel: str, callback: object) -> None:
        del channel
        typed_callback = callback
        if not callable(typed_callback):
            raise TypeError("listener callback must be callable")
        self.pool.listeners.append(
            cast("Callable[[object, int, str, str], None]", typed_callback),
        )

    async def remove_listener(self, channel: str, callback: object) -> None:
        del channel
        self.pool.listeners.remove(
            cast("Callable[[object, int, str, str], None]", callback),
        )


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
    async def __aenter__(self) -> object:
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback


class FakePostgresPool:
    def __init__(self) -> None:
        self.schema_version: int | None = None
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
