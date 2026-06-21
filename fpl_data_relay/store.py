"""Postgres-backed normalised FPL entity store."""

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from datetime import date, datetime
from typing import Protocol, cast

from asyncpg import Record
from pydantic import BaseModel, ConfigDict, Field, field_validator

from fpl_data_relay.fpl_models import (
    BootstrapStatic,
    Element,
    ElementType,
    Event,
    EventLiveResponse,
    EventStatusDay,
    EventStatusResponse,
    Fixture,
    FixtureStat,
    FixtureStatEntry,
    LiveElement,
    LiveElementExplain,
    LiveElementExplainStat,
    LiveElementStats,
    Phase,
    Team,
)
from fpl_data_relay.hashing import parse_json_payload, payload_sha256
from fpl_data_relay.json_types import JsonValue
from fpl_data_relay.resources import (
    EVENT_NAMES,
    EntityFamily,
    IngestionSourceKey,
    ResourceKey,
)
from fpl_data_relay.schemas import ADVISORY_LOCK_ID, NOTIFY_CHANNEL, SCHEMA_SQL

type StatScalar = int | float | str | bool | None


class SchemaError(RuntimeError):
    """Raised when the database schema does not match the application."""

    pass


class IngestionLockError(RuntimeError):
    """Raised when a second ingestion cycle attempts to run concurrently."""

    pass


class ResourceWrite(BaseModel):
    """Compatibility resource payload write model for legacy tests."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    resource_key: ResourceKey
    event_name: str
    event_id: int | None
    payload: object
    payload_hash: str
    fetched_at: datetime
    checked_at: datetime

    @field_validator("payload_hash")
    @classmethod
    def resource_payload_hash_must_be_sha256(cls, value: str) -> str:
        """Validate that stored payload hashes look like SHA-256 digests."""
        if len(value) != 64:
            raise ValueError("payload_hash must be a SHA-256 hex digest.")
        return value


class StoredResource(BaseModel):
    """Compatibility latest-source payload model for legacy tests."""

    model_config = ConfigDict(frozen=True)

    resource_key: ResourceKey
    event_id: int | None
    payload: JsonValue
    payload_hash: str
    fetched_at: datetime
    checked_at: datetime


class IngestionMetadata(BaseModel):
    """Metadata for one upstream source fetch."""

    model_config = ConfigDict(frozen=True)

    source_key: IngestionSourceKey
    event_id: int | None
    payload_hash: str
    fetched_at: datetime
    checked_at: datetime

    @field_validator("payload_hash")
    @classmethod
    def payload_hash_must_be_sha256(cls, value: str) -> str:
        """Validate that stored payload hashes look like SHA-256 digests."""
        if len(value) != 64:
            raise ValueError("payload_hash must be a SHA-256 hex digest.")
        return value


class ChangeEvent(BaseModel):
    """Metadata row describing a changed normalised entity family."""

    model_config = ConfigDict(frozen=True)

    id: int
    entity_family: EntityFamily = EntityFamily.EVENTS
    event_name: str
    source_key: IngestionSourceKey | None = None
    resource_key: ResourceKey | None = None
    event_id: int | None
    payload_hash: str
    fetched_at: datetime
    created_at: datetime

    def to_public_dict(self) -> dict[str, int | str | None]:
        """Serialize change-event metadata for the REST API."""
        return {
            "id": self.id,
            "entity_family": self.entity_family.value,
            "event_name": self.event_name,
            "source_key": None if self.source_key is None else self.source_key.value,
            "resource_key": (
                None if self.resource_key is None else self.resource_key.value
            ),
            "event_id": self.event_id,
            "payload_hash": self.payload_hash,
            "fetched_at": self.fetched_at.isoformat(),
            "created_at": self.created_at.isoformat(),
        }


class UpsertOutcome(BaseModel):
    """Result of attempting to upsert one source payload."""

    model_config = ConfigDict(frozen=True)

    changed: bool
    change_events: list[ChangeEvent] = Field(default_factory=list)
    change_event: ChangeEvent | None = None


class StoredSourceMetadata(BaseModel):
    """Latest metadata for an upstream ingestion source."""

    model_config = ConfigDict(frozen=True)

    source_key: IngestionSourceKey
    event_id: int | None
    payload_hash: str
    fetched_at: datetime
    checked_at: datetime


class ConnectionProtocol(Protocol):
    """Subset of asyncpg connection behavior used by the store."""

    def transaction(self) -> AbstractAsyncContextManager[object]:
        """Open a database transaction context."""
        ...

    async def execute(self, query: str, *arguments: object) -> str:
        """Execute a SQL command and return asyncpg status text."""
        ...

    async def fetchrow(self, query: str, *arguments: object) -> object:
        """Fetch one row from a SQL query."""
        ...

    async def fetch(self, query: str, *arguments: object) -> list[object]:
        """Fetch all rows from a SQL query."""
        ...

    async def fetchval(self, query: str, *arguments: object) -> object:
        """Fetch the first column from the first row of a SQL query."""
        ...

    async def add_listener(self, channel: str, callback: object) -> None:
        """Register a Postgres NOTIFY listener."""
        ...

    async def remove_listener(self, channel: str, callback: object) -> None:
        """Remove a Postgres NOTIFY listener."""
        ...


class ConnectionManagerProtocol(Protocol):
    """Async context manager returned by a pool acquisition."""

    async def __aenter__(self) -> ConnectionProtocol:
        """Acquire and return a connection."""
        ...

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        """Release a connection after use."""
        ...


class PoolProtocol(Protocol):
    """Subset of asyncpg pool behavior required by the store."""

    def acquire(self) -> ConnectionManagerProtocol:
        """Acquire a database connection context manager."""
        ...

    async def close(self) -> None:
        """Close the connection pool."""
        ...


class FplStore(Protocol):
    """Persistence interface used by API and ingestion layers."""

    async def apply_schema(self) -> None:
        """Create or update the database schema."""
        ...

    async def check_schema_version(self, *, expected_version: int) -> None:
        """Verify the database schema version."""
        ...

    async def upsert_bootstrap(
        self,
        *,
        bootstrap: BootstrapStatic,
        metadata: IngestionMetadata,
    ) -> UpsertOutcome:
        """Upsert bootstrap/reference entity families."""
        ...

    async def upsert_fixtures(
        self,
        *,
        fixtures: list[Fixture],
        metadata: IngestionMetadata,
    ) -> UpsertOutcome:
        """Upsert fixture rows into the shared fixture tables."""
        ...

    async def upsert_event_status(
        self,
        *,
        status: EventStatusResponse,
        metadata: IngestionMetadata,
    ) -> UpsertOutcome:
        """Upsert event-status rows."""
        ...

    async def upsert_event_live(
        self,
        *,
        event_id: int,
        live: EventLiveResponse,
        metadata: IngestionMetadata,
    ) -> UpsertOutcome:
        """Upsert live element rows for one event."""
        ...

    async def get_current_event(self) -> Event | None:
        """Return the single current event, if reference data exists."""
        ...

    async def list_events(self) -> list[Event]:
        """Return all events."""
        ...

    async def get_event(self, *, event_id: int) -> Event | None:
        """Return one event."""
        ...

    async def list_phases(self) -> list[Phase]:
        """Return all phases."""
        ...

    async def list_teams(self) -> list[Team]:
        """Return all teams."""
        ...

    async def get_team(self, *, team_id: int) -> Team | None:
        """Return one team."""
        ...

    async def list_element_types(self) -> list[ElementType]:
        """Return all element types."""
        ...

    async def list_elements(self) -> list[Element]:
        """Return all elements."""
        ...

    async def get_element(self, *, element_id: int) -> Element | None:
        """Return one element."""
        ...

    async def list_fixtures(self, *, event_id: int | None) -> list[Fixture]:
        """Return fixtures, optionally filtered by event id."""
        ...

    async def get_event_status(self) -> EventStatusResponse | None:
        """Return the latest event-status aggregate."""
        ...

    async def list_live_elements(self, *, event_id: int) -> list[LiveElement]:
        """Return live element rows for one event."""
        ...

    async def get_live_element(
        self,
        *,
        event_id: int,
        element_id: int,
    ) -> LiveElement | None:
        """Return one live element row."""
        ...

    async def list_change_events(
        self,
        *,
        after_id: int,
        limit: int,
    ) -> list[ChangeEvent]:
        """List change events with ids greater than the supplied id."""
        ...

    def ingestion_lock(self) -> AbstractAsyncContextManager[None]:
        """Acquire an exclusive lock for an ingestion cycle."""
        ...

    def watch_change_events(
        self,
        *,
        after_id: int,
        heartbeat_seconds: int,
    ) -> AsyncIterator[ChangeEvent | None]:
        """Yield new change events and heartbeat sentinels."""
        ...

    async def close(self) -> None:
        """Close resources held by the store."""
        ...


class PostgresStore(FplStore):
    """Normalised FPL entity store implementation backed by asyncpg."""

    def __init__(self, *, pool: PoolProtocol) -> None:
        """Create a store around an asyncpg-compatible pool."""
        self._pool = pool

    async def close(self) -> None:
        """Close the underlying connection pool."""
        await self._pool.close()

    async def apply_schema(self) -> None:
        """Apply the current schema SQL to the database."""
        async with self._pool.acquire() as connection:
            await connection.execute(SCHEMA_SQL)

    async def check_schema_version(self, *, expected_version: int) -> None:
        """Raise if the stored schema version differs from the expected one."""
        async with self._pool.acquire() as connection:
            version = await connection.fetchval(
                "SELECT version FROM relay_schema_version WHERE id = true",
            )
        if version != expected_version:
            message = (
                "Database schema version mismatch: "
                f"expected {expected_version}, found {version!r}."
            )
            raise SchemaError(message)

    async def upsert_bootstrap(
        self,
        *,
        bootstrap: BootstrapStatic,
        metadata: IngestionMetadata,
    ) -> UpsertOutcome:
        """Upsert bootstrap/reference rows and emit entity-family events."""
        families = [
            EntityFamily.EVENTS,
            EntityFamily.PHASES,
            EntityFamily.TEAMS,
            EntityFamily.ELEMENT_TYPES,
            EntityFamily.ELEMENT_STATS,
            EntityFamily.ELEMENTS,
        ]
        async with self._pool.acquire() as connection, connection.transaction():
            if await source_is_unchanged(connection=connection, metadata=metadata):
                return UpsertOutcome(changed=False, change_events=[])
            for event in bootstrap.events:
                await upsert_model_row(
                    connection=connection,
                    table="fpl_events",
                    key_columns=["id"],
                    values=event.model_dump(),
                )
            for phase in bootstrap.phases:
                await upsert_model_row(
                    connection=connection,
                    table="fpl_phases",
                    key_columns=["id"],
                    values=phase.model_dump(),
                )
            for team in bootstrap.teams:
                await upsert_model_row(
                    connection=connection,
                    table="fpl_teams",
                    key_columns=["id"],
                    values=team.model_dump(),
                )
            for element_type in bootstrap.element_types:
                await upsert_model_row(
                    connection=connection,
                    table="fpl_element_types",
                    key_columns=["id"],
                    values=element_type.model_dump(),
                )
            for stat in bootstrap.element_stats:
                await upsert_model_row(
                    connection=connection,
                    table="fpl_element_stat_definitions",
                    key_columns=["name"],
                    values=stat.model_dump(),
                )
            for element in bootstrap.elements:
                await upsert_model_row(
                    connection=connection,
                    table="fpl_elements",
                    key_columns=["id"],
                    values=element.model_dump(),
                )
            await upsert_source_metadata(connection=connection, metadata=metadata)
            events = await insert_change_events(
                connection=connection,
                families=families,
                metadata=metadata,
            )
            return UpsertOutcome(changed=True, change_events=events)

    async def upsert_fixtures(
        self,
        *,
        fixtures: list[Fixture],
        metadata: IngestionMetadata,
    ) -> UpsertOutcome:
        """Upsert fixture rows and stat entries."""
        async with self._pool.acquire() as connection, connection.transaction():
            if await source_is_unchanged(connection=connection, metadata=metadata):
                return UpsertOutcome(changed=False, change_events=[])
            for fixture in fixtures:
                fixture_values = fixture.model_dump(exclude={"stats"})
                await upsert_model_row(
                    connection=connection,
                    table="fpl_fixtures",
                    key_columns=["id"],
                    values=fixture_values,
                )
                await connection.execute(
                    "DELETE FROM fpl_fixture_stat_entries WHERE fixture_id = $1",
                    fixture.id,
                )
                await insert_fixture_stat_entries(
                    connection=connection,
                    fixture=fixture,
                )
            await upsert_source_metadata(connection=connection, metadata=metadata)
            events = await insert_change_events(
                connection=connection,
                families=[EntityFamily.FIXTURES],
                metadata=metadata,
            )
            return UpsertOutcome(changed=True, change_events=events)

    async def upsert_event_status(
        self,
        *,
        status: EventStatusResponse,
        metadata: IngestionMetadata,
    ) -> UpsertOutcome:
        """Upsert event-status days and response-level fields."""
        async with self._pool.acquire() as connection, connection.transaction():
            if await source_is_unchanged(connection=connection, metadata=metadata):
                return UpsertOutcome(changed=False, change_events=[])
            await connection.execute(
                """
                INSERT INTO fpl_event_status (
                    id, leagues, payload_hash, fetched_at, checked_at
                )
                VALUES (true, $1, $2, $3, $4)
                ON CONFLICT (id)
                DO UPDATE SET
                    leagues = EXCLUDED.leagues,
                    payload_hash = EXCLUDED.payload_hash,
                    fetched_at = EXCLUDED.fetched_at,
                    checked_at = EXCLUDED.checked_at,
                    updated_at = now()
                """,
                status.leagues,
                metadata.payload_hash,
                metadata.fetched_at,
                metadata.checked_at,
            )
            for day in status.status:
                await upsert_model_row(
                    connection=connection,
                    table="fpl_event_status_days",
                    key_columns=["event", "date"],
                    values=day.model_dump(),
                )
            await upsert_source_metadata(connection=connection, metadata=metadata)
            events = await insert_change_events(
                connection=connection,
                families=[EntityFamily.EVENT_STATUS],
                metadata=metadata,
            )
            return UpsertOutcome(changed=True, change_events=events)

    async def upsert_event_live(
        self,
        *,
        event_id: int,
        live: EventLiveResponse,
        metadata: IngestionMetadata,
    ) -> UpsertOutcome:
        """Upsert live element rows for one event."""
        async with self._pool.acquire() as connection, connection.transaction():
            if await source_is_unchanged(connection=connection, metadata=metadata):
                return UpsertOutcome(changed=False, change_events=[])
            await connection.execute(
                "DELETE FROM fpl_event_live_elements WHERE event_id = $1",
                event_id,
            )
            for live_element in live.elements:
                values = live_element.stats.model_dump()
                values["event_id"] = event_id
                values["element_id"] = live_element.id
                await upsert_model_row(
                    connection=connection,
                    table="fpl_event_live_elements",
                    key_columns=["event_id", "element_id"],
                    values=values,
                )
                await insert_live_explain_stats(
                    connection=connection,
                    event_id=event_id,
                    live_element=live_element,
                )
            await upsert_source_metadata(connection=connection, metadata=metadata)
            events = await insert_change_events(
                connection=connection,
                families=[EntityFamily.EVENT_LIVE],
                metadata=metadata,
            )
            return UpsertOutcome(changed=True, change_events=events)

    async def get_current_event(self) -> Event | None:
        """Return the single current event, if available."""
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM fpl_events WHERE is_current = true",
            )
        return None if row is None else event_from_row(row=row)

    async def list_events(self) -> list[Event]:
        """Return all events ordered by id."""
        return await self._fetch_models(
            query="SELECT * FROM fpl_events ORDER BY id",
            converter=event_from_row,
        )

    async def get_event(self, *, event_id: int) -> Event | None:
        """Return one event by id."""
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM fpl_events WHERE id = $1",
                event_id,
            )
        return None if row is None else event_from_row(row=row)

    async def list_phases(self) -> list[Phase]:
        """Return all phases ordered by id."""
        return await self._fetch_models(
            query="SELECT * FROM fpl_phases ORDER BY id",
            converter=phase_from_row,
        )

    async def list_teams(self) -> list[Team]:
        """Return all teams ordered by id."""
        return await self._fetch_models(
            query="SELECT * FROM fpl_teams ORDER BY id",
            converter=team_from_row,
        )

    async def get_team(self, *, team_id: int) -> Team | None:
        """Return one team by id."""
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM fpl_teams WHERE id = $1",
                team_id,
            )
        return None if row is None else team_from_row(row=row)

    async def list_element_types(self) -> list[ElementType]:
        """Return all element types ordered by id."""
        return await self._fetch_models(
            query="SELECT * FROM fpl_element_types ORDER BY id",
            converter=element_type_from_row,
        )

    async def list_elements(self) -> list[Element]:
        """Return all elements ordered by id."""
        return await self._fetch_models(
            query="SELECT * FROM fpl_elements ORDER BY id",
            converter=element_from_row,
        )

    async def get_element(self, *, element_id: int) -> Element | None:
        """Return one element by id."""
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM fpl_elements WHERE id = $1",
                element_id,
            )
        return None if row is None else element_from_row(row=row)

    async def list_fixtures(self, *, event_id: int | None) -> list[Fixture]:
        """Return fixtures with their nested stat entries."""
        if event_id is None:
            query = "SELECT * FROM fpl_fixtures ORDER BY id"
            arguments: tuple[object, ...] = ()
        else:
            query = "SELECT * FROM fpl_fixtures WHERE event = $1 ORDER BY id"
            arguments = (event_id,)
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(query, *arguments)
            fixtures = [fixture_from_row(row=row) for row in rows]
            for index, fixture in enumerate(fixtures):
                stats = await fetch_fixture_stats(
                    connection=connection,
                    fixture_id=fixture.id,
                )
                fixtures[index] = fixture.model_copy(update={"stats": stats})
        return fixtures

    async def get_event_status(self) -> EventStatusResponse | None:
        """Return latest event status aggregate, if ingested."""
        async with self._pool.acquire() as connection:
            status_row = await connection.fetchrow("SELECT * FROM fpl_event_status")
            if status_row is None:
                return None
            day_rows = await connection.fetch(
                "SELECT * FROM fpl_event_status_days ORDER BY date, event",
            )
        status_values = row_values(row=status_row)
        return EventStatusResponse(
            leagues=optional_str(values=status_values, key="leagues"),
            status=[event_status_day_from_row(row=row) for row in day_rows],
        )

    async def list_live_elements(self, *, event_id: int) -> list[LiveElement]:
        """Return live elements with explanations for one event."""
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT * FROM fpl_event_live_elements
                WHERE event_id = $1
                ORDER BY element_id
                """,
                event_id,
            )
            return [
                await live_element_from_row(
                    connection=connection,
                    row=row,
                )
                for row in rows
            ]

    async def get_live_element(
        self,
        *,
        event_id: int,
        element_id: int,
    ) -> LiveElement | None:
        """Return one live element row."""
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT * FROM fpl_event_live_elements
                WHERE event_id = $1 AND element_id = $2
                """,
                event_id,
                element_id,
            )
            if row is None:
                return None
            return await live_element_from_row(connection=connection, row=row)

    async def get_resource(
        self,
        *,
        resource_key: ResourceKey,
    ) -> StoredResource | None:
        """Compatibility method returning an opaque legacy resource payload."""
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT resource_key, event_id, payload::text, payload_hash,
                       fetched_at, checked_at
                FROM relay_resources
                WHERE resource_key = $1
                """,
                resource_key.value,
            )
        return None if row is None else stored_resource_from_row(row=row)

    async def upsert_resource(self, *, resource: ResourceWrite) -> UpsertOutcome:
        """Compatibility method for legacy opaque-resource tests."""
        payload_text = json.dumps(resource.payload, separators=(",", ":"))
        async with self._pool.acquire() as connection, connection.transaction():
            existing_hash = await connection.fetchval(
                """
                SELECT payload_hash
                FROM relay_resources
                WHERE resource_key = $1
                """,
                resource.resource_key.value,
            )
            if existing_hash == resource.payload_hash:
                await connection.execute(
                    """
                    UPDATE relay_resources
                    SET checked_at = $2, updated_at = now()
                    WHERE resource_key = $1
                    """,
                    resource.resource_key.value,
                    resource.checked_at,
                )
                return UpsertOutcome(changed=False)
            await connection.execute(
                """
                INSERT INTO relay_resources (
                    resource_key, event_id, payload, payload_hash,
                    fetched_at, checked_at
                )
                VALUES ($1, $2, $3::jsonb, $4, $5, $6)
                ON CONFLICT (resource_key)
                DO UPDATE SET
                    event_id = EXCLUDED.event_id,
                    payload = EXCLUDED.payload,
                    payload_hash = EXCLUDED.payload_hash,
                    fetched_at = EXCLUDED.fetched_at,
                    checked_at = EXCLUDED.checked_at,
                    updated_at = now()
                """,
                resource.resource_key.value,
                resource.event_id,
                payload_text,
                resource.payload_hash,
                resource.fetched_at,
                resource.checked_at,
            )
            row = await connection.fetchrow(
                """
                INSERT INTO relay_change_events (
                    resource_key, event_name, event_id, payload_hash, fetched_at
                )
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id, resource_key, event_name, event_id, payload_hash,
                          fetched_at, created_at
                """,
                resource.resource_key.value,
                resource.event_name,
                resource.event_id,
                resource.payload_hash,
                resource.fetched_at,
            )
            if row is None:
                raise RuntimeError("Failed to insert relay change event.")
            event = change_event_from_row(row=row)
            await connection.fetchval(
                "SELECT pg_notify($1, $2)",
                NOTIFY_CHANNEL,
                str(event.id),
            )
            return UpsertOutcome(
                changed=True,
                change_events=[event],
                change_event=event,
            )

    async def list_change_events(
        self,
        *,
        after_id: int,
        limit: int,
    ) -> list[ChangeEvent]:
        """Return ordered change events after the supplied event id."""
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT id, entity_family, event_name, source_key, event_id,
                       payload_hash, fetched_at, created_at
                FROM relay_change_events
                WHERE id > $1
                ORDER BY id ASC
                LIMIT $2
                """,
                after_id,
                limit,
            )
        return [change_event_from_row(row=row) for row in rows]

    @contextlib.asynccontextmanager
    async def ingestion_lock(self) -> AsyncIterator[None]:
        """Hold the Postgres advisory lock for one ingestion cycle."""
        async with self._pool.acquire() as connection:
            acquired = await connection.fetchval(
                "SELECT pg_try_advisory_lock($1)",
                ADVISORY_LOCK_ID,
            )
            if acquired is not True:
                raise IngestionLockError("Another ingestion cycle is already running.")
            try:
                yield
            finally:
                await connection.fetchval(
                    "SELECT pg_advisory_unlock($1)",
                    ADVISORY_LOCK_ID,
                )

    async def watch_change_events(
        self,
        *,
        after_id: int,
        heartbeat_seconds: int,
    ) -> AsyncIterator[ChangeEvent | None]:
        """Watch stored events via polling plus Postgres notifications."""
        current_id = after_id
        queue: asyncio.Queue[int] = asyncio.Queue()

        def listener(
            connection: object,
            process_id: int,
            channel: str,
            payload: str,
        ) -> None:
            """Queue notified change-event ids for the stream loop."""
            del connection, process_id, channel
            queue.put_nowait(int(payload))

        async with self._pool.acquire() as connection:
            await connection.add_listener(NOTIFY_CHANNEL, listener)
            try:
                while True:
                    events = await self.list_change_events(
                        after_id=current_id,
                        limit=100,
                    )
                    for event in events:
                        current_id = event.id
                        yield event
                    try:
                        await asyncio.wait_for(
                            queue.get(),
                            timeout=heartbeat_seconds,
                        )
                    except TimeoutError:
                        yield None
            finally:
                await connection.remove_listener(NOTIFY_CHANNEL, listener)

    async def _fetch_models[ModelT](
        self,
        *,
        query: str,
        converter: Callable[..., ModelT],
    ) -> list[ModelT]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(query)
        return [converter(row=row) for row in rows]


async def source_is_unchanged(
    *,
    connection: ConnectionProtocol,
    metadata: IngestionMetadata,
) -> bool:
    """Return whether source hash is unchanged, updating checked_at when it is."""
    existing_hash = await connection.fetchval(
        "SELECT payload_hash FROM relay_ingestion_sources WHERE source_key = $1",
        metadata.source_key.value,
    )
    if existing_hash != metadata.payload_hash:
        return False
    await connection.execute(
        """
        UPDATE relay_ingestion_sources
        SET checked_at = $2, updated_at = now()
        WHERE source_key = $1
        """,
        metadata.source_key.value,
        metadata.checked_at,
    )
    return True


async def upsert_source_metadata(
    *,
    connection: ConnectionProtocol,
    metadata: IngestionMetadata,
) -> None:
    """Upsert latest metadata for an upstream source."""
    await connection.execute(
        """
        INSERT INTO relay_ingestion_sources (
            source_key, event_id, payload_hash, fetched_at, checked_at
        )
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (source_key)
        DO UPDATE SET
            event_id = EXCLUDED.event_id,
            payload_hash = EXCLUDED.payload_hash,
            fetched_at = EXCLUDED.fetched_at,
            checked_at = EXCLUDED.checked_at,
            updated_at = now()
        """,
        metadata.source_key.value,
        metadata.event_id,
        metadata.payload_hash,
        metadata.fetched_at,
        metadata.checked_at,
    )


async def insert_change_events(
    *,
    connection: ConnectionProtocol,
    families: list[EntityFamily],
    metadata: IngestionMetadata,
) -> list[ChangeEvent]:
    """Insert and notify change events for entity families."""
    events: list[ChangeEvent] = []
    for family in families:
        row = await connection.fetchrow(
            """
            INSERT INTO relay_change_events (
                entity_family, event_name, source_key, event_id,
                payload_hash, fetched_at
            )
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id, entity_family, event_name, source_key, event_id,
                      payload_hash, fetched_at, created_at
            """,
            family.value,
            EVENT_NAMES[family],
            metadata.source_key.value,
            metadata.event_id,
            metadata.payload_hash,
            metadata.fetched_at,
        )
        if row is None:
            raise RuntimeError("Failed to insert relay change event.")
        event = change_event_from_row(row=row)
        events.append(event)
        await connection.fetchval(
            "SELECT pg_notify($1, $2)",
            NOTIFY_CHANNEL,
            str(event.id),
        )
    return events


async def upsert_model_row(
    *,
    connection: ConnectionProtocol,
    table: str,
    key_columns: list[str],
    values: dict[str, object],
) -> None:
    """Upsert one model row with a deterministic row hash."""
    cleaned_values = {key: value for key, value in values.items() if value is not None}
    cleaned_values["row_hash"] = payload_sha256(
        payload={
            key: hashable_value(value=value)
            for key, value in cleaned_values.items()
        },
    )
    columns = list(cleaned_values)
    placeholders = [f"${index}" for index in range(1, len(columns) + 1)]
    update_columns = [column for column in columns if column not in key_columns]
    assignments = [f"{column} = EXCLUDED.{column}" for column in update_columns]
    assignments.append("updated_at = now()")
    conflict_columns = ", ".join(key_columns)
    query = f"""
        INSERT INTO {table} ({", ".join(columns)})
        VALUES ({", ".join(placeholders)})
        ON CONFLICT ({conflict_columns})
        DO UPDATE SET {", ".join(assignments)}
        WHERE {table}.row_hash IS DISTINCT FROM EXCLUDED.row_hash
    """
    await connection.execute(query, *[cleaned_values[column] for column in columns])


async def insert_fixture_stat_entries(
    *,
    connection: ConnectionProtocol,
    fixture: Fixture,
) -> None:
    """Insert normalised fixture stat entries for one fixture."""
    for stat in fixture.stats:
        for side, entries in (("a", stat.a), ("h", stat.h)):
            for ordinal, entry in enumerate(entries):
                value_text, value_type = scalar_to_text_and_type(value=entry.value)
                await connection.execute(
                    """
                    INSERT INTO fpl_fixture_stat_entries (
                        fixture_id, identifier, side, ordinal, element,
                        value_text, value_type
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                    fixture.id,
                    stat.identifier,
                    side,
                    ordinal,
                    entry.element,
                    value_text,
                    value_type,
                )


async def insert_live_explain_stats(
    *,
    connection: ConnectionProtocol,
    event_id: int,
    live_element: LiveElement,
) -> None:
    """Insert normalised live explain stat rows for one live element."""
    for explain in live_element.explain:
        for ordinal, stat in enumerate(explain.stats):
            value_text, value_type = scalar_to_text_and_type(value=stat.value)
            await connection.execute(
                """
                INSERT INTO fpl_event_live_explain_stats (
                    event_id, element_id, fixture_id, identifier, ordinal,
                    points, value_text, value_type
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                event_id,
                live_element.id,
                explain.fixture,
                stat.identifier,
                ordinal,
                stat.points,
                value_text,
                value_type,
            )


def hashable_value(*, value: object) -> JsonValue:
    """Convert DB-bound scalar values into canonical JSON-compatible values."""
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, list):
        return [hashable_value(value=item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    raise TypeError(f"Unsupported row hash value type: {type(value).__name__}.")


def scalar_to_text_and_type(*, value: object) -> tuple[str | None, str]:
    """Store scalar stat values as text plus type metadata."""
    if value is None:
        return None, "null"
    if isinstance(value, bool):
        return str(value).lower(), "bool"
    if isinstance(value, int):
        return str(value), "int"
    if isinstance(value, float):
        return repr(value), "float"
    if isinstance(value, str):
        return value, "str"
    raise TypeError(f"Unsupported stat scalar value type: {type(value).__name__}.")


def text_and_type_to_scalar(*, value_text: str | None, value_type: str) -> StatScalar:
    """Restore scalar stat values from text plus type metadata."""
    if value_type == "null":
        return None
    if value_text is None:
        raise TypeError("Non-null scalar stat value cannot have null text.")
    if value_type == "bool":
        return value_text == "true"
    if value_type == "int":
        return int(value_text)
    if value_type == "float":
        return float(value_text)
    if value_type == "str":
        return value_text
    raise TypeError(f"Unsupported stat value_type: {value_type}.")


async def fetch_fixture_stats(
    *,
    connection: ConnectionProtocol,
    fixture_id: int,
) -> list[FixtureStat]:
    """Fetch nested fixture stats for one fixture."""
    rows = await connection.fetch(
        """
        SELECT * FROM fpl_fixture_stat_entries
        WHERE fixture_id = $1
        ORDER BY identifier, side, ordinal
        """,
        fixture_id,
    )
    grouped: dict[str, dict[str, list[FixtureStatEntry]]] = {}
    for row in rows:
        values = row_values(row=row)
        identifier = require_str(values=values, key="identifier")
        side = require_str(values=values, key="side")
        grouped.setdefault(identifier, {"a": [], "h": []})[side].append(
            FixtureStatEntry(
                element=optional_int(values=values, key="element"),
                value=text_and_type_to_scalar(
                    value_text=optional_str(values=values, key="value_text"),
                    value_type=require_str(values=values, key="value_type"),
                ),
            ),
        )
    return [
        FixtureStat(identifier=identifier, a=sides["a"], h=sides["h"])
        for identifier, sides in grouped.items()
    ]


async def live_element_from_row(
    *,
    connection: ConnectionProtocol,
    row: object,
) -> LiveElement:
    """Build a live element model from row plus explanation stats."""
    values = row_values(row=row)
    event_id = require_int(values=values, key="event_id")
    element_id = require_int(values=values, key="element_id")
    rows = await connection.fetch(
        """
        SELECT * FROM fpl_event_live_explain_stats
        WHERE event_id = $1 AND element_id = $2
        ORDER BY fixture_id, identifier, ordinal
        """,
        event_id,
        element_id,
    )
    explains: dict[int, list[LiveElementExplainStat]] = {}
    for explain_row in rows:
        explain_values = row_values(row=explain_row)
        fixture_id = require_int(values=explain_values, key="fixture_id")
        explains.setdefault(fixture_id, []).append(
            LiveElementExplainStat(
                identifier=require_str(values=explain_values, key="identifier"),
                points=require_int(values=explain_values, key="points"),
                value=text_and_type_to_scalar(
                    value_text=optional_str(values=explain_values, key="value_text"),
                    value_type=require_str(values=explain_values, key="value_type"),
                ),
            ),
        )
    return LiveElement(
        id=element_id,
        stats=LiveElementStats.model_validate(filter_row_values(values=values)),
        explain=[
            LiveElementExplain(fixture=fixture_id, stats=stats)
            for fixture_id, stats in explains.items()
        ],
    )


def stored_resource_from_row(*, row: object) -> StoredResource:
    """Convert a legacy resource database row into a stored-resource model."""
    values = row_values(row=row)
    return StoredResource(
        resource_key=ResourceKey(require_str(values=values, key="resource_key")),
        event_id=optional_int(values=values, key="event_id"),
        payload=parse_json_payload(payload=require_str(values=values, key="payload")),
        payload_hash=require_str(values=values, key="payload_hash"),
        fetched_at=require_datetime(values=values, key="fetched_at"),
        checked_at=require_datetime(values=values, key="checked_at"),
    )


def change_event_from_row(*, row: object) -> ChangeEvent:
    """Convert a database row into a change-event model."""
    values = row_values(row=row)
    source_key = values.get("source_key")
    resource_key = values.get("resource_key")
    entity_family_value = values.get("entity_family")
    entity_family = (
        EntityFamily.EVENTS
        if entity_family_value is None
        else EntityFamily(cast("str", entity_family_value))
    )
    return ChangeEvent(
        id=require_int(values=values, key="id"),
        entity_family=entity_family,
        event_name=require_str(values=values, key="event_name"),
        source_key=(
            None if source_key is None else IngestionSourceKey(cast("str", source_key))
        ),
        resource_key=(
            None if resource_key is None else ResourceKey(cast("str", resource_key))
        ),
        event_id=optional_int(values=values, key="event_id"),
        payload_hash=require_str(values=values, key="payload_hash"),
        fetched_at=require_datetime(values=values, key="fetched_at"),
        created_at=require_datetime(values=values, key="created_at"),
    )


def event_from_row(*, row: object) -> Event:
    """Convert a row to Event."""
    return Event.model_validate(filter_row_values(values=row_values(row=row)))


def phase_from_row(*, row: object) -> Phase:
    """Convert a row to Phase."""
    return Phase.model_validate(filter_row_values(values=row_values(row=row)))


def team_from_row(*, row: object) -> Team:
    """Convert a row to Team."""
    return Team.model_validate(filter_row_values(values=row_values(row=row)))


def element_type_from_row(*, row: object) -> ElementType:
    """Convert a row to ElementType."""
    return ElementType.model_validate(filter_row_values(values=row_values(row=row)))


def element_from_row(*, row: object) -> Element:
    """Convert a row to Element."""
    return Element.model_validate(filter_row_values(values=row_values(row=row)))


def fixture_from_row(*, row: object) -> Fixture:
    """Convert a row to Fixture without nested stats."""
    return Fixture.model_validate(
        filter_row_values(values=row_values(row=row)) | {"stats": []},
    )


def event_status_day_from_row(*, row: object) -> EventStatusDay:
    """Convert a row to EventStatusDay."""
    return EventStatusDay.model_validate(filter_row_values(values=row_values(row=row)))


def filter_row_values(*, values: dict[str, object]) -> dict[str, object]:
    """Remove storage-only columns from database row values."""
    return {
        key: value
        for key, value in values.items()
        if key not in {"row_hash", "updated_at"}
    }


def row_values(*, row: object) -> dict[str, object]:
    """Normalize a mapping-like database row into a plain dictionary."""
    if isinstance(row, Record):
        return {key: value for key, value in row.items()}
    if isinstance(row, Mapping):
        mapping = cast("Mapping[str, object]", row)
        return {key: mapping[key] for key in mapping}
    raise TypeError(f"Unsupported database row type: {type(row).__name__}")


def require_str(*, values: dict[str, object], key: str) -> str:
    """Return a required string field from row values."""
    value = values[key]
    if isinstance(value, str):
        return value
    raise TypeError(f"Expected {key} to be str, found {type(value).__name__}.")


def optional_str(*, values: dict[str, object], key: str) -> str | None:
    """Return a nullable string field from row values."""
    value = values[key]
    if value is None or isinstance(value, str):
        return value
    raise TypeError(f"Expected {key} to be str or None, found {type(value).__name__}.")


def require_int(*, values: dict[str, object], key: str) -> int:
    """Return a required integer field from row values."""
    value = values[key]
    if isinstance(value, int):
        return value
    raise TypeError(f"Expected {key} to be int, found {type(value).__name__}.")


def optional_int(*, values: dict[str, object], key: str) -> int | None:
    """Return a nullable integer field from row values."""
    value = values[key]
    if value is None or isinstance(value, int):
        return value
    raise TypeError(f"Expected {key} to be int or None, found {type(value).__name__}.")


def require_datetime(*, values: dict[str, object], key: str) -> datetime:
    """Return a required datetime field from row values."""
    value = values[key]
    if isinstance(value, datetime):
        return value
    raise TypeError(f"Expected {key} to be datetime, found {type(value).__name__}.")


def require_date(*, values: dict[str, object], key: str) -> date:
    """Return a required date field from row values."""
    value = values[key]
    if isinstance(value, date):
        return value
    raise TypeError(f"Expected {key} to be date, found {type(value).__name__}.")


ResourceStore = FplStore
