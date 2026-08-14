"""PostgreSQL persistence engine for normalised FPL relay data."""

import contextlib
import json
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from contextlib import AbstractAsyncContextManager
from datetime import UTC, date, datetime
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict, TypeAdapter

from fpl_data_relay.adapters.outbound.postgres.migrations import (
    MIGRATION_TABLE_LOOKUP_SQL,
    apply_migrations,
    migration_status,
)
from fpl_data_relay.adapters.outbound.postgres.schema import (
    ADVISORY_LOCK_ID,
)
from fpl_data_relay.application.errors import SchemaUnavailableError
from fpl_data_relay.application.ports.administration import SchemaStatus
from fpl_data_relay.domain.changes import (
    EVENT_NAMES,
    ChangeEvent,
    ChangeKind,
    EntityChange,
    EntityChangeDraft,
    EntityFamily,
    EntityFamilyDiff,
    EntitySnapshot,
    FieldChange,
    IngestionMetadata,
    IngestionSourceKey,
    IngestionSourceStatus,
    UpsertOutcome,
    diff_entity_snapshots,
)
from fpl_data_relay.domain.fixtures import (
    Fixture,
    FixtureStat,
    FixtureStatEntry,
)
from fpl_data_relay.domain.live import (
    EventLiveResponse,
    EventStatusDay,
    EventStatusResponse,
    LiveElement,
    LiveElementExplain,
    LiveElementExplainStat,
    LiveElementStats,
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
from fpl_data_relay.domain.rules import payload_sha256
from fpl_data_relay.domain.types import JsonValue

type StatScalar = int | float | str | bool | None

FIELD_CHANGES_ADAPTER = TypeAdapter(list[FieldChange])


class IngestionLockError(RuntimeError):
    """Raised when a second ingestion cycle attempts to run concurrently."""

    pass


class RowProtocol(Protocol):
    """Mapping-like row shape shared by asyncpg and Data API results."""

    def items(self) -> Iterable[tuple[str, object]]: ...


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


class NoOpTransaction(AbstractAsyncContextManager[object]):
    """Transaction façade used only inside an already-open outer transaction."""

    async def __aenter__(self) -> object:
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback


class BoundConnection:
    """Delegate SQL to one connection while suppressing nested transactions."""

    def __init__(self, *, connection: ConnectionProtocol) -> None:
        self._connection = connection

    def transaction(self) -> AbstractAsyncContextManager[object]:
        return NoOpTransaction()

    async def execute(self, query: str, *arguments: object) -> str:
        return await self._connection.execute(query, *arguments)

    async def fetchrow(self, query: str, *arguments: object) -> object:
        return await self._connection.fetchrow(query, *arguments)

    async def fetch(self, query: str, *arguments: object) -> list[object]:
        return await self._connection.fetch(query, *arguments)

    async def fetchval(self, query: str, *arguments: object) -> object:
        return await self._connection.fetchval(query, *arguments)


class BoundAcquire(AbstractAsyncContextManager[ConnectionProtocol]):
    """Yield a connection already owned by the outer persistence operation."""

    def __init__(self, *, connection: ConnectionProtocol) -> None:
        self._connection = connection

    async def __aenter__(self) -> ConnectionProtocol:
        return BoundConnection(connection=self._connection)

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback


class BoundPool:
    """Pool façade that keeps several source writes in one transaction."""

    def __init__(self, *, connection: ConnectionProtocol) -> None:
        self._connection = connection

    def acquire(self) -> ConnectionManagerProtocol:
        return BoundAcquire(connection=self._connection)

    async def close(self) -> None:
        raise RuntimeError("A transaction-bound pool cannot be closed.")


class _PostgresOperations(Protocol):
    """Persistence interface used by API and ingestion layers."""

    async def check_schema_version(self, *, expected_version: int) -> None:
        """Verify the database schema version."""
        ...

    async def schema_status(self) -> SchemaStatus:
        """Return applied and pending migration versions."""
        ...

    async def upsert_bootstrap(
        self,
        *,
        season: Season,
        bootstrap: BootstrapStatic,
        metadata: IngestionMetadata,
        delete_missing: bool,
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

    async def list_seasons(self) -> list[Season]:
        """Return all seasons."""
        ...

    async def get_current_season(self) -> Season | None:
        """Return the current season, if reference data exists."""
        ...

    async def get_season(self, *, season_id: str) -> Season | None:
        """Return one season."""
        ...

    async def get_current_event(self, *, season_id: str) -> Event | None:
        """Return the current event for one season, if reference data exists."""
        ...

    async def list_events(self, *, season_id: str) -> list[Event]:
        """Return all events."""
        ...

    async def get_event(self, *, season_id: str, event_id: int) -> Event | None:
        """Return one event."""
        ...

    async def list_phases(self, *, season_id: str) -> list[Phase]:
        """Return all phases."""
        ...

    async def list_teams(self, *, season_id: str) -> list[Team]:
        """Return all teams."""
        ...

    async def get_team(self, *, season_id: str, team_id: int) -> Team | None:
        """Return one team."""
        ...

    async def list_element_types(self, *, season_id: str) -> list[ElementType]:
        """Return all element types."""
        ...

    async def list_elements(
        self,
        *,
        season_id: str,
        after_id: int,
        limit: int,
    ) -> list[Element]:
        """Return all elements."""
        ...

    async def get_element(self, *, season_id: str, element_id: int) -> Element | None:
        """Return one element."""
        ...

    async def list_fixtures(
        self,
        *,
        season_id: str,
        event_id: int | None,
        after_id: int,
        limit: int,
    ) -> list[Fixture]:
        """Return fixtures, optionally filtered by event id."""
        ...

    async def get_fixture(self, *, season_id: str, fixture_id: int) -> Fixture | None:
        """Return one fixture with nested stat entries."""
        ...

    async def get_event_status(self, *, season_id: str) -> EventStatusResponse | None:
        """Return the latest event-status aggregate."""
        ...

    async def list_live_elements(
        self,
        *,
        season_id: str,
        event_id: int,
        after_id: int,
        limit: int,
    ) -> list[LiveElement]:
        """Return live element rows for one event."""
        ...

    async def get_live_element(
        self,
        *,
        season_id: str,
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

    async def close(self) -> None:
        """Close resources held by the store."""
        ...


class PostgresDatabase(_PostgresOperations):
    """Normalised FPL entity store implementation backed by asyncpg."""

    def __init__(self, *, pool: PoolProtocol) -> None:
        """Create a store around an asyncpg-compatible pool."""
        self._pool = pool

    @property
    def pool(self) -> PoolProtocol:
        """Expose the shared pool to narrow repository adapters."""
        return self._pool

    async def upsert_reference_snapshot(
        self,
        *,
        season: Season,
        bootstrap: BootstrapStatic,
        fixtures: list[Fixture],
        bootstrap_metadata: IngestionMetadata,
        fixtures_metadata: IngestionMetadata,
    ) -> list[UpsertOutcome]:
        """Persist a complete reference bundle in one physical transaction."""
        async with self._pool.acquire() as connection, connection.transaction():
            bound = PostgresDatabase(pool=BoundPool(connection=connection))
            bootstrap_outcome = await bound.upsert_bootstrap(
                season=season,
                bootstrap=bootstrap,
                metadata=bootstrap_metadata,
                delete_missing=False,
            )
            fixtures_outcome = await bound.upsert_fixtures(
                fixtures=fixtures,
                metadata=fixtures_metadata,
            )
            await delete_bootstrap_rows_missing_from_snapshot(
                connection=connection,
                season_id=season.id,
                bootstrap=bootstrap,
            )
        return [bootstrap_outcome, fixtures_outcome]

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
        """Persist a complete live bundle in one physical transaction."""
        async with self._pool.acquire() as connection, connection.transaction():
            bound = PostgresDatabase(pool=BoundPool(connection=connection))
            status_outcome = await bound.upsert_event_status(
                status=status,
                metadata=status_metadata,
            )
            fixtures_outcome = await bound.upsert_fixtures(
                fixtures=fixtures,
                metadata=fixtures_metadata,
            )
            live_outcome = await bound.upsert_event_live(
                event_id=event_id,
                live=live,
                metadata=live_metadata,
            )
        return [status_outcome, fixtures_outcome, live_outcome]

    async def close(self) -> None:
        """Close the underlying connection pool."""
        await self._pool.close()

    async def apply_schema(self) -> None:
        """Apply pending immutable migrations."""
        await apply_migrations(pool=self._pool)

    async def check_schema_version(self, *, expected_version: int) -> None:
        """Raise if the stored schema version differs from the expected one."""
        async with self._pool.acquire() as connection:
            migration_table = await connection.fetchval(MIGRATION_TABLE_LOOKUP_SQL)
            if migration_table is None:
                raise SchemaUnavailableError(
                    "Database migration history is not available.",
                )
            version = await connection.fetchval(
                "SELECT MAX(version) FROM relay_schema_migrations",
            )
        if version != expected_version:
            message = (
                "Database schema version mismatch: "
                f"expected {expected_version}, found {version!r}."
            )
            raise SchemaUnavailableError(message)

    async def schema_status(self) -> SchemaStatus:
        """Return validated applied and pending migration versions."""
        return await migration_status(pool=self._pool)

    async def upsert_bootstrap(
        self,
        *,
        season: Season,
        bootstrap: BootstrapStatic,
        metadata: IngestionMetadata,
        delete_missing: bool,
    ) -> UpsertOutcome:
        """Upsert bootstrap/reference rows and emit entity-family events."""
        snapshots = bootstrap_snapshots(bootstrap=bootstrap)
        async with self._pool.acquire() as connection, connection.transaction():
            comparison = await compare_source(
                connection=connection,
                metadata=metadata,
            )
            if comparison.unchanged:
                return UpsertOutcome(changed=False, change_events=[])
            if season.id != metadata.season_id:
                raise ValueError("Season id does not match ingestion metadata.")
            diffs = await compare_snapshot_families(
                connection=connection,
                season_id=metadata.season_id,
                source_event_id=metadata.event_id,
                snapshots=snapshots,
                authoritative=True,
                source_exists=comparison.exists,
            )
            await connection.execute(
                """
                UPDATE fpl_seasons
                SET is_current = false, row_hash = '', updated_at = now()
                WHERE id != $1 AND is_current = true
                """,
                season.id,
            )
            await upsert_model_row(
                connection=connection,
                table="fpl_seasons",
                key_columns=["id"],
                values=season.model_dump(),
            )
            for event in bootstrap.events:
                await upsert_model_row(
                    connection=connection,
                    table="fpl_events",
                    key_columns=["season_id", "id"],
                    values=values_for_season(
                        season_id=metadata.season_id,
                        values=event.model_dump(),
                    ),
                )
            for phase in bootstrap.phases:
                await upsert_model_row(
                    connection=connection,
                    table="fpl_phases",
                    key_columns=["season_id", "id"],
                    values=values_for_season(
                        season_id=metadata.season_id,
                        values=phase.model_dump(),
                    ),
                )
            for team in bootstrap.teams:
                await upsert_model_row(
                    connection=connection,
                    table="fpl_teams",
                    key_columns=["season_id", "id"],
                    values=values_for_season(
                        season_id=metadata.season_id,
                        values=team.model_dump(),
                    ),
                )
            for element_type in bootstrap.element_types:
                await upsert_model_row(
                    connection=connection,
                    table="fpl_element_types",
                    key_columns=["season_id", "id"],
                    values=values_for_season(
                        season_id=metadata.season_id,
                        values=element_type.model_dump(),
                    ),
                )
            for stat in bootstrap.element_stats:
                await upsert_model_row(
                    connection=connection,
                    table="fpl_element_stat_definitions",
                    key_columns=["season_id", "name"],
                    values=values_for_season(
                        season_id=metadata.season_id,
                        values=stat.model_dump(),
                    ),
                )
            for element in bootstrap.elements:
                await upsert_model_row(
                    connection=connection,
                    table="fpl_elements",
                    key_columns=["season_id", "id"],
                    values=values_for_season(
                        season_id=metadata.season_id,
                        values=element.model_dump(),
                    ),
                )
            if delete_missing:
                await delete_removed_bootstrap_rows(
                    connection=connection,
                    season_id=metadata.season_id,
                    diffs=diffs,
                )
            events = await persist_snapshot_diffs(
                connection=connection,
                snapshots=snapshots,
                diffs=diffs,
                metadata=metadata,
                authoritative=True,
            )
            await upsert_source_metadata(
                connection=connection,
                metadata=metadata,
                logically_changed=bool(events),
            )
            return UpsertOutcome(changed=True, change_events=events)

    async def upsert_fixtures(
        self,
        *,
        fixtures: list[Fixture],
        metadata: IngestionMetadata,
    ) -> UpsertOutcome:
        """Upsert fixture rows and stat entries."""
        snapshots = {
            EntityFamily.FIXTURES: [
                snapshot_for_fixture(fixture=fixture) for fixture in fixtures
            ],
        }
        authoritative = metadata.source_key is IngestionSourceKey.FIXTURES
        async with self._pool.acquire() as connection, connection.transaction():
            comparison = await compare_source(
                connection=connection,
                metadata=metadata,
            )
            if comparison.unchanged:
                return UpsertOutcome(changed=False, change_events=[])
            diffs = await compare_snapshot_families(
                connection=connection,
                season_id=metadata.season_id,
                source_event_id=metadata.event_id,
                snapshots=snapshots,
                authoritative=authoritative,
                source_exists=comparison.exists,
            )
            for fixture in fixtures:
                fixture_values = values_for_season(
                    season_id=metadata.season_id,
                    values=fixture.model_dump(exclude={"stats"}),
                )
                await upsert_model_row(
                    connection=connection,
                    table="fpl_fixtures",
                    key_columns=["season_id", "id"],
                    values=fixture_values,
                )
                await connection.execute(
                    """
                    DELETE FROM fpl_fixture_stat_entries
                    WHERE season_id = $1 AND fixture_id = $2
                    """,
                    metadata.season_id,
                    fixture.id,
                )
                await insert_fixture_stat_entries(
                    connection=connection,
                    season_id=metadata.season_id,
                    fixture=fixture,
                )
            if authoritative:
                await delete_removed_fixture_rows(
                    connection=connection,
                    season_id=metadata.season_id,
                    diff=diffs[EntityFamily.FIXTURES],
                )
            events = await persist_snapshot_diffs(
                connection=connection,
                snapshots=snapshots,
                diffs=diffs,
                metadata=metadata,
                authoritative=authoritative,
            )
            await upsert_source_metadata(
                connection=connection,
                metadata=metadata,
                logically_changed=bool(events),
            )
            return UpsertOutcome(changed=True, change_events=events)

    async def upsert_event_status(
        self,
        *,
        status: EventStatusResponse,
        metadata: IngestionMetadata,
    ) -> UpsertOutcome:
        """Upsert event-status days and response-level fields."""
        snapshots = {
            EntityFamily.EVENT_STATUS: [
                snapshot_for_event_status(status=status, event_id=metadata.event_id),
            ],
        }
        async with self._pool.acquire() as connection, connection.transaction():
            comparison = await compare_source(
                connection=connection,
                metadata=metadata,
            )
            if comparison.unchanged:
                return UpsertOutcome(changed=False, change_events=[])
            diffs = await compare_snapshot_families(
                connection=connection,
                season_id=metadata.season_id,
                source_event_id=metadata.event_id,
                snapshots=snapshots,
                authoritative=True,
                source_exists=comparison.exists,
            )
            await connection.execute(
                """
                INSERT INTO fpl_event_status (
                    season_id, leagues, payload_hash, fetched_at, checked_at
                )
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (season_id)
                DO UPDATE SET
                    leagues = EXCLUDED.leagues,
                    payload_hash = EXCLUDED.payload_hash,
                    fetched_at = EXCLUDED.fetched_at,
                    checked_at = EXCLUDED.checked_at,
                    updated_at = now()
                """,
                metadata.season_id,
                status.leagues,
                metadata.payload_hash,
                metadata.fetched_at,
                metadata.checked_at,
            )
            await connection.execute(
                "DELETE FROM fpl_event_status_days WHERE season_id = $1",
                metadata.season_id,
            )
            for day in status.status:
                await upsert_model_row(
                    connection=connection,
                    table="fpl_event_status_days",
                    key_columns=["season_id", "event", "date"],
                    values=values_for_season(
                        season_id=metadata.season_id,
                        values=day.model_dump(),
                    ),
                )
            events = await persist_snapshot_diffs(
                connection=connection,
                snapshots=snapshots,
                diffs=diffs,
                metadata=metadata,
                authoritative=True,
            )
            await upsert_source_metadata(
                connection=connection,
                metadata=metadata,
                logically_changed=bool(events),
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
        snapshots = {
            EntityFamily.EVENT_LIVE: [
                snapshot_for_live_element(
                    live_element=live_element,
                    event_id=event_id,
                )
                for live_element in live.elements
            ],
        }
        async with self._pool.acquire() as connection, connection.transaction():
            comparison = await compare_source(
                connection=connection,
                metadata=metadata,
            )
            if comparison.unchanged:
                return UpsertOutcome(changed=False, change_events=[])
            diffs = await compare_snapshot_families(
                connection=connection,
                season_id=metadata.season_id,
                source_event_id=event_id,
                snapshots=snapshots,
                authoritative=True,
                source_exists=comparison.exists,
            )
            await connection.execute(
                """
                DELETE FROM fpl_event_live_elements
                WHERE season_id = $1 AND event_id = $2
                """,
                metadata.season_id,
                event_id,
            )
            for live_element in live.elements:
                values = live_element.stats.model_dump()
                values["season_id"] = metadata.season_id
                values["event_id"] = event_id
                values["element_id"] = live_element.id
                await upsert_model_row(
                    connection=connection,
                    table="fpl_event_live_elements",
                    key_columns=["season_id", "event_id", "element_id"],
                    values=values,
                )
                await insert_live_explain_stats(
                    connection=connection,
                    season_id=metadata.season_id,
                    event_id=event_id,
                    live_element=live_element,
                )
            events = await persist_snapshot_diffs(
                connection=connection,
                snapshots=snapshots,
                diffs=diffs,
                metadata=metadata,
                authoritative=True,
            )
            await upsert_source_metadata(
                connection=connection,
                metadata=metadata,
                logically_changed=bool(events),
            )
            return UpsertOutcome(changed=True, change_events=events)

    async def list_seasons(self) -> list[Season]:
        """Return all seasons ordered by id."""
        return await self._fetch_models(
            query="SELECT * FROM fpl_seasons ORDER BY id",
            converter=season_from_row,
        )

    async def get_current_season(self) -> Season | None:
        """Return the single current season, if available."""
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM fpl_seasons WHERE is_current = true",
            )
        return None if row is None else season_from_row(row=row)

    async def get_season(self, *, season_id: str) -> Season | None:
        """Return one season by id."""
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM fpl_seasons WHERE id = $1",
                season_id,
            )
        return None if row is None else season_from_row(row=row)

    async def get_current_event(self, *, season_id: str) -> Event | None:
        """Return the single current event for a season, if available."""
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT * FROM fpl_events
                WHERE season_id = $1 AND is_current = true
                """,
                season_id,
            )
        return None if row is None else event_from_row(row=row)

    async def list_events(self, *, season_id: str) -> list[Event]:
        """Return all events ordered by id."""
        return await self._fetch_models(
            query="SELECT * FROM fpl_events WHERE season_id = $1 ORDER BY id",
            converter=event_from_row,
            arguments=(season_id,),
        )

    async def get_event(self, *, season_id: str, event_id: int) -> Event | None:
        """Return one event by id."""
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM fpl_events WHERE season_id = $1 AND id = $2",
                season_id,
                event_id,
            )
        return None if row is None else event_from_row(row=row)

    async def list_phases(self, *, season_id: str) -> list[Phase]:
        """Return all phases ordered by id."""
        return await self._fetch_models(
            query="SELECT * FROM fpl_phases WHERE season_id = $1 ORDER BY id",
            converter=phase_from_row,
            arguments=(season_id,),
        )

    async def list_teams(self, *, season_id: str) -> list[Team]:
        """Return all teams ordered by id."""
        return await self._fetch_models(
            query="SELECT * FROM fpl_teams WHERE season_id = $1 ORDER BY id",
            converter=team_from_row,
            arguments=(season_id,),
        )

    async def get_team(self, *, season_id: str, team_id: int) -> Team | None:
        """Return one team by id."""
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM fpl_teams WHERE season_id = $1 AND id = $2",
                season_id,
                team_id,
            )
        return None if row is None else team_from_row(row=row)

    async def list_element_types(self, *, season_id: str) -> list[ElementType]:
        """Return all element types ordered by id."""
        return await self._fetch_models(
            query="SELECT * FROM fpl_element_types WHERE season_id = $1 ORDER BY id",
            converter=element_type_from_row,
            arguments=(season_id,),
        )

    async def list_elements(
        self,
        *,
        season_id: str,
        after_id: int,
        limit: int,
    ) -> list[Element]:
        """Return a cursor page of elements ordered by id."""
        return await self._fetch_models(
            query="""
                SELECT * FROM fpl_elements
                WHERE season_id = $1 AND id > $2
                ORDER BY id
                LIMIT $3
            """,
            converter=element_from_row,
            arguments=(season_id, after_id, limit),
        )

    async def get_element(self, *, season_id: str, element_id: int) -> Element | None:
        """Return one element by id."""
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM fpl_elements WHERE season_id = $1 AND id = $2",
                season_id,
                element_id,
            )
        return None if row is None else element_from_row(row=row)

    async def list_fixtures(
        self,
        *,
        season_id: str,
        event_id: int | None,
        after_id: int,
        limit: int,
    ) -> list[Fixture]:
        """Return a cursor page of fixtures with nested stat entries."""
        if event_id is None:
            query = """
                SELECT * FROM fpl_fixtures
                WHERE season_id = $1 AND id > $2
                ORDER BY id
                LIMIT $3
            """
            arguments: tuple[object, ...] = (season_id, after_id, limit)
        else:
            query = """
                SELECT * FROM fpl_fixtures
                WHERE season_id = $1 AND event = $2 AND id > $3
                ORDER BY id
                LIMIT $4
            """
            arguments = (season_id, event_id, after_id, limit)
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(query, *arguments)
            fixtures = [fixture_from_row(row=row) for row in rows]
            fixture_ids = [fixture.id for fixture in fixtures]
            stats_by_fixture = await fetch_fixture_stats_for_ids(
                connection=connection,
                season_id=season_id,
                fixture_ids=fixture_ids,
            )
            for index, fixture in enumerate(fixtures):
                stats = stats_by_fixture.get(fixture.id, [])
                fixtures[index] = fixture.model_copy(update={"stats": stats})
        return fixtures

    async def get_fixture(self, *, season_id: str, fixture_id: int) -> Fixture | None:
        """Return one fixture with its nested stat entries."""
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM fpl_fixtures WHERE season_id = $1 AND id = $2",
                season_id,
                fixture_id,
            )
            if row is None:
                return None
            fixture = fixture_from_row(row=row)
            stats = await fetch_fixture_stats(
                connection=connection,
                season_id=season_id,
                fixture_id=fixture.id,
            )
        return fixture.model_copy(update={"stats": stats})

    async def get_event_status(self, *, season_id: str) -> EventStatusResponse | None:
        """Return latest event status aggregate, if ingested."""
        async with self._pool.acquire() as connection:
            status_row = await connection.fetchrow(
                "SELECT * FROM fpl_event_status WHERE season_id = $1",
                season_id,
            )
            if status_row is None:
                return None
            day_rows = await connection.fetch(
                """
                SELECT * FROM fpl_event_status_days
                WHERE season_id = $1
                ORDER BY date, event
                """,
                season_id,
            )
        status_values = row_values(row=status_row)
        return EventStatusResponse(
            leagues=optional_str(values=status_values, key="leagues"),
            status=[event_status_day_from_row(row=row) for row in day_rows],
        )

    async def list_live_elements(
        self,
        *,
        season_id: str,
        event_id: int,
        after_id: int,
        limit: int,
    ) -> list[LiveElement]:
        """Return a cursor page of live elements with explanations."""
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT * FROM fpl_event_live_elements
                WHERE season_id = $1 AND event_id = $2 AND element_id > $3
                ORDER BY element_id
                LIMIT $4
                """,
                season_id,
                event_id,
                after_id,
                limit,
            )
            return await live_elements_from_rows(
                connection=connection,
                rows=rows,
            )

    async def get_live_element(
        self,
        *,
        season_id: str,
        event_id: int,
        element_id: int,
    ) -> LiveElement | None:
        """Return one live element row."""
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT * FROM fpl_event_live_elements
                WHERE season_id = $1 AND event_id = $2 AND element_id = $3
                """,
                season_id,
                event_id,
                element_id,
            )
            if row is None:
                return None
            return await live_element_from_row(connection=connection, row=row)

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
                SELECT id, season_id, entity_family, event_name, source_key,
                       source_event_id, payload_hash, created_count, updated_count,
                       deleted_count, fetched_at, created_at
                FROM relay_change_events
                WHERE id > $1
                ORDER BY id ASC
                LIMIT $2
                """,
                after_id,
                limit,
            )
        return [change_event_from_row(row=row) for row in rows]

    async def list_recent_change_events(self, *, limit: int) -> list[ChangeEvent]:
        """Return the newest bounded change-event page."""
        return await self._fetch_change_events(
            query="""
                SELECT id, season_id, entity_family, event_name, source_key,
                       source_event_id, payload_hash, created_count, updated_count,
                       deleted_count, fetched_at, created_at
                FROM relay_change_events
                ORDER BY id DESC
                LIMIT $1
            """,
            arguments=(limit,),
        )

    async def list_change_events_before(
        self,
        *,
        before_id: int,
        limit: int,
    ) -> list[ChangeEvent]:
        """Return a newest-first historical page before an event id."""
        return await self._fetch_change_events(
            query="""
                SELECT id, season_id, entity_family, event_name, source_key,
                       source_event_id, payload_hash, created_count, updated_count,
                       deleted_count, fetched_at, created_at
                FROM relay_change_events
                WHERE id < $1
                ORDER BY id DESC
                LIMIT $2
            """,
            arguments=(before_id, limit),
        )

    async def list_entity_changes(
        self,
        *,
        change_event_id: int,
        after_id: int,
        limit: int,
    ) -> list[EntityChange]:
        """Return field-level entity changes under one family event."""
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT id, change_event_id, entity_key, entity_label, change_kind,
                       field_changes, created_at
                FROM relay_entity_changes
                WHERE change_event_id = $1 AND id > $2
                ORDER BY id
                LIMIT $3
                """,
                change_event_id,
                after_id,
                limit,
            )
        return [entity_change_from_row(row=row) for row in rows]

    async def list_ingestion_source_statuses(
        self,
        *,
        season_id: str,
    ) -> list[IngestionSourceStatus]:
        """Return latest source checks for one season."""
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT season_id, source_key, event_id, payload_hash, fetched_at,
                       checked_at, last_changed_at
                FROM relay_ingestion_sources
                WHERE season_id = $1
                ORDER BY source_key, event_id NULLS FIRST
                """,
                season_id,
            )
        return [ingestion_source_status_from_row(row=row) for row in rows]

    @contextlib.asynccontextmanager
    async def ingestion_lock(self) -> AsyncIterator[None]:
        """Hold a transaction-scoped advisory lock for one ingestion cycle."""
        async with (
            self._pool.acquire() as connection,
            connection.transaction(),
        ):
            acquired = await connection.fetchval(
                "SELECT pg_try_advisory_xact_lock($1)",
                ADVISORY_LOCK_ID,
            )
            if acquired is not True:
                raise IngestionLockError("Another ingestion cycle is already running.")
            yield

    async def _fetch_models[ModelT](
        self,
        *,
        query: str,
        converter: Callable[..., ModelT],
        arguments: tuple[object, ...] = (),
    ) -> list[ModelT]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(query, *arguments)
        return [converter(row=row) for row in rows]

    async def _fetch_change_events(
        self,
        *,
        query: str,
        arguments: tuple[object, ...],
    ) -> list[ChangeEvent]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(query, *arguments)
        return [change_event_from_row(row=row) for row in rows]


class SourceComparison(BaseModel):
    """Existing-source and hash comparison result."""

    model_config = ConfigDict(frozen=True)

    exists: bool
    unchanged: bool


async def compare_source(
    *,
    connection: ConnectionProtocol,
    metadata: IngestionMetadata,
) -> SourceComparison:
    """Compare source hashes and refresh checked time for unchanged payloads."""
    existing_hash = await connection.fetchval(
        """
        SELECT payload_hash
        FROM relay_ingestion_sources
        WHERE season_id = $1
          AND source_key = $2
          AND event_id IS NOT DISTINCT FROM $3
        """,
        metadata.season_id,
        metadata.source_key.value,
        metadata.event_id,
    )
    exists = existing_hash is not None
    if existing_hash != metadata.payload_hash:
        return SourceComparison(exists=exists, unchanged=False)
    await connection.execute(
        """
        UPDATE relay_ingestion_sources
        SET checked_at = $4, updated_at = now()
        WHERE season_id = $1
          AND source_key = $2
          AND event_id IS NOT DISTINCT FROM $3
        """,
        metadata.season_id,
        metadata.source_key.value,
        metadata.event_id,
        metadata.checked_at,
    )
    return SourceComparison(exists=True, unchanged=True)


async def upsert_source_metadata(
    *,
    connection: ConnectionProtocol,
    metadata: IngestionMetadata,
    logically_changed: bool,
) -> None:
    """Upsert latest metadata for an upstream source."""
    await connection.execute(
        """
        INSERT INTO relay_ingestion_sources (
            season_id, source_key, event_id, payload_hash, fetched_at, checked_at,
            last_changed_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (season_id, source_key, (COALESCE(event_id, 0)))
        DO UPDATE SET
            event_id = EXCLUDED.event_id,
            payload_hash = EXCLUDED.payload_hash,
            fetched_at = EXCLUDED.fetched_at,
            checked_at = EXCLUDED.checked_at,
            last_changed_at = COALESCE(
                EXCLUDED.last_changed_at,
                relay_ingestion_sources.last_changed_at
            ),
            updated_at = now()
        """,
        metadata.season_id,
        metadata.source_key.value,
        metadata.event_id,
        metadata.payload_hash,
        metadata.fetched_at,
        metadata.checked_at,
        metadata.checked_at if logically_changed else None,
    )


async def compare_snapshot_families(
    *,
    connection: ConnectionProtocol,
    season_id: str,
    source_event_id: int | None,
    snapshots: dict[EntityFamily, list[EntitySnapshot]],
    authoritative: bool,
    source_exists: bool,
) -> dict[EntityFamily, EntityFamilyDiff]:
    """Load current snapshots and calculate one pure diff per family."""
    diffs: dict[EntityFamily, EntityFamilyDiff] = {}
    for family, current in snapshots.items():
        previous = await list_entity_snapshots(
            connection=connection,
            season_id=season_id,
            family=family,
            source_event_id=(
                source_event_id if family is EntityFamily.EVENT_LIVE else None
            ),
        )
        diffs[family] = diff_entity_snapshots(
            previous=previous,
            current=current,
            authoritative=authoritative,
            baseline=not source_exists and not previous,
        )
    return diffs


async def persist_snapshot_diffs(
    *,
    connection: ConnectionProtocol,
    snapshots: dict[EntityFamily, list[EntitySnapshot]],
    diffs: dict[EntityFamily, EntityFamilyDiff],
    metadata: IngestionMetadata,
    authoritative: bool,
) -> list[ChangeEvent]:
    """Synchronize canonical snapshots and persist accurate family events."""
    events: list[ChangeEvent] = []
    for family, current in snapshots.items():
        diff = diffs[family]
        event = await insert_change_event(
            connection=connection,
            family=family,
            diff=diff,
            metadata=metadata,
        )
        if event is not None:
            await insert_entity_changes(
                connection=connection,
                change_event_id=event.id,
                changes=diff.changes,
            )
            events.append(event)
        for snapshot in current:
            await upsert_entity_snapshot(
                connection=connection,
                season_id=metadata.season_id,
                family=family,
                source_event_id=metadata.event_id,
                snapshot=snapshot,
            )
        if authoritative:
            for change in diff.changes:
                if change.kind is ChangeKind.DELETED:
                    await connection.execute(
                        """
                        DELETE FROM relay_entity_snapshots
                        WHERE season_id = $1 AND entity_family = $2
                          AND entity_key = $3
                        """,
                        metadata.season_id,
                        family.value,
                        change.entity_key,
                    )
    return events


async def insert_change_event(
    *,
    connection: ConnectionProtocol,
    family: EntityFamily,
    diff: EntityFamilyDiff,
    metadata: IngestionMetadata,
) -> ChangeEvent | None:
    """Insert one non-empty family summary."""
    if not diff.changes:
        return None
    row = await connection.fetchrow(
        """
        INSERT INTO relay_change_events (
            season_id, entity_family, event_name, source_key, source_event_id,
            payload_hash, created_count, updated_count, deleted_count, fetched_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        RETURNING id, season_id, entity_family, event_name, source_key,
                  source_event_id, payload_hash, created_count, updated_count,
                  deleted_count, fetched_at, created_at
        """,
        metadata.season_id,
        family.value,
        EVENT_NAMES[family],
        metadata.source_key.value,
        metadata.event_id,
        metadata.payload_hash,
        diff.created_count,
        diff.updated_count,
        diff.deleted_count,
        metadata.fetched_at,
    )
    if row is None:
        raise RuntimeError("Failed to insert relay change event.")
    return change_event_from_row(row=row)


async def insert_entity_changes(
    *,
    connection: ConnectionProtocol,
    change_event_id: int,
    changes: list[EntityChangeDraft],
) -> None:
    """Persist bounded JSON field changes under one family event."""
    for change in changes:
        encoded_fields = json.dumps(
            [field.model_dump(mode="json") for field in change.fields],
            separators=(",", ":"),
            sort_keys=True,
        )
        await connection.execute(
            """
            INSERT INTO relay_entity_changes (
                change_event_id, entity_key, entity_label, change_kind,
                field_changes
            )
            VALUES ($1, $2, $3, $4, CAST($5 AS jsonb))
            """,
            change_event_id,
            change.entity_key,
            change.entity_label,
            change.kind.value,
            encoded_fields,
        )


async def list_entity_snapshots(
    *,
    connection: ConnectionProtocol,
    season_id: str,
    family: EntityFamily,
    source_event_id: int | None,
) -> list[EntitySnapshot]:
    """Read canonical snapshots for one family or event-live scope."""
    if source_event_id is None:
        rows = await connection.fetch(
            """
            SELECT entity_key, entity_label, snapshot
            FROM relay_entity_snapshots
            WHERE season_id = $1 AND entity_family = $2
            ORDER BY entity_key
            """,
            season_id,
            family.value,
        )
    else:
        rows = await connection.fetch(
            """
            SELECT entity_key, entity_label, snapshot
            FROM relay_entity_snapshots
            WHERE season_id = $1 AND entity_family = $2
              AND source_event_id = $3
            ORDER BY entity_key
            """,
            season_id,
            family.value,
            source_event_id,
        )
    return [entity_snapshot_from_row(row=row) for row in rows]


async def upsert_entity_snapshot(
    *,
    connection: ConnectionProtocol,
    season_id: str,
    family: EntityFamily,
    source_event_id: int | None,
    snapshot: EntitySnapshot,
) -> None:
    """Store one canonical snapshot using JSON accepted by both executors."""
    encoded = json.dumps(snapshot.data, separators=(",", ":"), sort_keys=True)
    row_hash = payload_sha256(payload=snapshot.data)
    await connection.execute(
        """
        INSERT INTO relay_entity_snapshots (
            season_id, entity_family, source_event_id, entity_key, entity_label,
            snapshot, row_hash
        )
        VALUES ($1, $2, $3, $4, $5, CAST($6 AS jsonb), $7)
        ON CONFLICT (season_id, entity_family, entity_key)
        DO UPDATE SET
            source_event_id = EXCLUDED.source_event_id,
            entity_label = EXCLUDED.entity_label,
            snapshot = EXCLUDED.snapshot,
            row_hash = EXCLUDED.row_hash,
            updated_at = now()
        WHERE relay_entity_snapshots.row_hash IS DISTINCT FROM EXCLUDED.row_hash
           OR relay_entity_snapshots.entity_label IS DISTINCT FROM EXCLUDED.entity_label
           OR relay_entity_snapshots.source_event_id IS DISTINCT FROM
              EXCLUDED.source_event_id
        """,
        season_id,
        family.value,
        source_event_id,
        snapshot.entity_key,
        snapshot.entity_label,
        encoded,
        row_hash,
    )


async def delete_removed_bootstrap_rows(
    *,
    connection: ConnectionProtocol,
    season_id: str,
    diffs: dict[EntityFamily, EntityFamilyDiff],
) -> None:
    """Remove authoritative bootstrap entities in dependency-safe order."""
    removed_element_ids = {
        int(change.entity_key)
        for change in diffs[EntityFamily.ELEMENTS].changes
        if change.kind is ChangeKind.DELETED
    }
    removed_event_ids = {
        int(change.entity_key)
        for change in diffs[EntityFamily.EVENTS].changes
        if change.kind is ChangeKind.DELETED
    }
    await delete_bootstrap_dependents(
        connection=connection,
        season_id=season_id,
        removed_element_ids=removed_element_ids,
        removed_event_ids=removed_event_ids,
    )
    tables = (
        (EntityFamily.ELEMENTS, "fpl_elements", "id"),
        (EntityFamily.PHASES, "fpl_phases", "id"),
        (EntityFamily.ELEMENT_STATS, "fpl_element_stat_definitions", "name"),
        (EntityFamily.ELEMENT_TYPES, "fpl_element_types", "id"),
        (EntityFamily.TEAMS, "fpl_teams", "id"),
        (EntityFamily.EVENTS, "fpl_events", "id"),
    )
    for family, table, key_column in tables:
        for change in diffs[family].changes:
            if change.kind is not ChangeKind.DELETED:
                continue
            key: object = (
                change.entity_key
                if family is EntityFamily.ELEMENT_STATS
                else int(change.entity_key)
            )
            await connection.execute(
                f"DELETE FROM {table} WHERE season_id = $1 AND {key_column} = $2",
                season_id,
                key,
            )


async def delete_bootstrap_rows_missing_from_snapshot(
    *,
    connection: ConnectionProtocol,
    season_id: str,
    bootstrap: BootstrapStatic,
) -> None:
    """Delete missing reference rows after fixtures have been reconciled."""
    specifications: tuple[tuple[str, str, set[object]], ...] = (
        ("fpl_elements", "id", {element.id for element in bootstrap.elements}),
        ("fpl_phases", "id", {phase.id for phase in bootstrap.phases}),
        (
            "fpl_element_stat_definitions",
            "name",
            {stat.name for stat in bootstrap.element_stats},
        ),
        (
            "fpl_element_types",
            "id",
            {element_type.id for element_type in bootstrap.element_types},
        ),
        ("fpl_teams", "id", {team.id for team in bootstrap.teams}),
        ("fpl_events", "id", {event.id for event in bootstrap.events}),
    )
    existing_by_table: dict[str, set[object]] = {}
    for table, key_column, _desired_keys in specifications:
        rows = await connection.fetch(
            f"SELECT {key_column} FROM {table} WHERE season_id = $1",
            season_id,
        )
        existing_by_table[table] = {
            row_values(row=row)[key_column] for row in rows
        }
    await delete_bootstrap_dependents(
        connection=connection,
        season_id=season_id,
        removed_element_ids={
            cast("int", value)
            for value in existing_by_table["fpl_elements"]
            - {element.id for element in bootstrap.elements}
        },
        removed_event_ids={
            cast("int", value)
            for value in existing_by_table["fpl_events"]
            - {event.id for event in bootstrap.events}
        },
    )
    for table, key_column, desired_keys in specifications:
        existing_keys = existing_by_table[table]
        for removed_key in sorted(existing_keys - desired_keys, key=str):
            await connection.execute(
                f"DELETE FROM {table} WHERE season_id = $1 AND {key_column} = $2",
                season_id,
                removed_key,
            )


async def delete_bootstrap_dependents(
    *,
    connection: ConnectionProtocol,
    season_id: str,
    removed_element_ids: set[int],
    removed_event_ids: set[int],
) -> None:
    """Remove rows that reference authoritative bootstrap deletions."""
    for element_id in sorted(removed_element_ids):
        await connection.execute(
            """
            DELETE FROM fpl_fixture_stat_entries
            WHERE season_id = $1 AND element = $2
            """,
            season_id,
            element_id,
        )
        await connection.execute(
            """
            DELETE FROM fpl_event_live_elements
            WHERE season_id = $1 AND element_id = $2
            """,
            season_id,
            element_id,
        )
    for event_id in sorted(removed_event_ids):
        await connection.execute(
            """
            DELETE FROM fpl_event_status_days
            WHERE season_id = $1 AND event = $2
            """,
            season_id,
            event_id,
        )
        await connection.execute(
            """
            DELETE FROM fpl_event_live_elements
            WHERE season_id = $1 AND event_id = $2
            """,
            season_id,
            event_id,
        )


async def delete_removed_fixture_rows(
    *,
    connection: ConnectionProtocol,
    season_id: str,
    diff: EntityFamilyDiff,
) -> None:
    """Remove fixtures absent from the authoritative full fixture snapshot."""
    for change in diff.changes:
        if change.kind is ChangeKind.DELETED:
            await connection.execute(
                """
                DELETE FROM fpl_event_live_explain_stats
                WHERE season_id = $1 AND fixture_id = $2
                """,
                season_id,
                int(change.entity_key),
            )
            await connection.execute(
                "DELETE FROM fpl_fixtures WHERE season_id = $1 AND id = $2",
                season_id,
                int(change.entity_key),
            )


def bootstrap_snapshots(
    *,
    bootstrap: BootstrapStatic,
) -> dict[EntityFamily, list[EntitySnapshot]]:
    """Split the bootstrap aggregate into labelled canonical entities."""
    return {
        EntityFamily.EVENTS: [
            snapshot_for_model(
                entity_key=str(event.id),
                entity_label=event.name,
                model=event,
            )
            for event in bootstrap.events
        ],
        EntityFamily.PHASES: [
            snapshot_for_model(
                entity_key=str(phase.id),
                entity_label=phase.name,
                model=phase,
            )
            for phase in bootstrap.phases
        ],
        EntityFamily.TEAMS: [
            snapshot_for_model(
                entity_key=str(team.id),
                entity_label=team.name,
                model=team,
            )
            for team in bootstrap.teams
        ],
        EntityFamily.ELEMENT_TYPES: [
            snapshot_for_model(
                entity_key=str(element_type.id),
                entity_label=element_type.singular_name,
                model=element_type,
            )
            for element_type in bootstrap.element_types
        ],
        EntityFamily.ELEMENT_STATS: [
            snapshot_for_model(
                entity_key=stat.name,
                entity_label=stat.label,
                model=stat,
            )
            for stat in bootstrap.element_stats
        ],
        EntityFamily.ELEMENTS: [
            snapshot_for_model(
                entity_key=str(element.id),
                entity_label=f"{element.web_name} ({element.id})",
                model=element,
            )
            for element in bootstrap.elements
        ],
    }


def snapshot_for_fixture(*, fixture: Fixture) -> EntitySnapshot:
    """Build a labelled fixture snapshot including nested statistics."""
    return snapshot_for_model(
        entity_key=str(fixture.id),
        entity_label=(
            f"Fixture {fixture.id}: team {fixture.team_h} vs team {fixture.team_a}"
        ),
        model=fixture,
    )


def snapshot_for_event_status(
    *,
    status: EventStatusResponse,
    event_id: int | None,
) -> EntitySnapshot:
    """Represent event status as one season-level aggregate."""
    label = "Event status" if event_id is None else f"Gameweek {event_id} status"
    return snapshot_for_model(entity_key="status", entity_label=label, model=status)


def snapshot_for_live_element(
    *,
    live_element: LiveElement,
    event_id: int,
) -> EntitySnapshot:
    """Build one event-scoped live player snapshot."""
    return snapshot_for_model(
        entity_key=f"{event_id}:{live_element.id}",
        entity_label=f"Player {live_element.id} · GW {event_id}",
        model=live_element,
    )


def snapshot_for_model(
    *,
    entity_key: str,
    entity_label: str,
    model: BaseModel,
) -> EntitySnapshot:
    """Convert a known Pydantic entity to canonical JSON-compatible data."""
    data = cast("dict[str, JsonValue]", model.model_dump(mode="json"))
    return EntitySnapshot(
        entity_key=entity_key,
        entity_label=entity_label,
        data=data,
    )


def entity_snapshot_from_row(*, row: object) -> EntitySnapshot:
    """Convert a JSONB snapshot row returned by either database executor."""
    values = row_values(row=row)
    raw_snapshot = values.get("snapshot")
    if isinstance(raw_snapshot, str):
        raw_snapshot = json.loads(raw_snapshot)
    if not isinstance(raw_snapshot, dict):
        raise TypeError("Entity snapshot must be a JSON object.")
    return EntitySnapshot(
        entity_key=require_str(values=values, key="entity_key"),
        entity_label=require_str(values=values, key="entity_label"),
        data=cast("dict[str, JsonValue]", raw_snapshot),
    )


async def upsert_model_row(
    *,
    connection: ConnectionProtocol,
    table: str,
    key_columns: list[str],
    values: dict[str, object],
) -> None:
    """Upsert one model row with a deterministic row hash."""
    cleaned_values = dict(values)
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


def values_for_season(
    *,
    season_id: str,
    values: dict[str, object],
) -> dict[str, object]:
    """Return model values with the storage season key prepended."""
    return {"season_id": season_id, **values}


async def insert_fixture_stat_entries(
    *,
    connection: ConnectionProtocol,
    season_id: str,
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
                        season_id, fixture_id, identifier, side, ordinal, element,
                        value_text, value_type
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                    season_id,
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
    season_id: str,
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
                    season_id, event_id, element_id, fixture_id, identifier, ordinal,
                    points, value_text, value_type
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                season_id,
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
    season_id: str,
    fixture_id: int,
) -> list[FixtureStat]:
    """Fetch nested fixture stats for one fixture."""
    rows = await connection.fetch(
        """
        SELECT * FROM fpl_fixture_stat_entries
        WHERE season_id = $1 AND fixture_id = $2
        ORDER BY identifier, side, ordinal
        """,
        season_id,
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


async def fetch_fixture_stats_for_ids(
    *,
    connection: ConnectionProtocol,
    season_id: str,
    fixture_ids: list[int],
) -> dict[int, list[FixtureStat]]:
    """Fetch and group nested stats for a bounded fixture page."""
    if not fixture_ids:
        return {}
    rows = await connection.fetch(
        """
        SELECT * FROM fpl_fixture_stat_entries
        WHERE season_id = $1 AND fixture_id = ANY($2)
        ORDER BY fixture_id, identifier, side, ordinal
        """,
        season_id,
        fixture_ids,
    )
    grouped: dict[int, dict[str, dict[str, list[FixtureStatEntry]]]] = {}
    for row in rows:
        values = row_values(row=row)
        fixture_id = require_int(values=values, key="fixture_id")
        identifier = require_str(values=values, key="identifier")
        side = require_str(values=values, key="side")
        grouped.setdefault(fixture_id, {}).setdefault(
            identifier,
            {"a": [], "h": []},
        )[side].append(
            FixtureStatEntry(
                element=optional_int(values=values, key="element"),
                value=text_and_type_to_scalar(
                    value_text=optional_str(values=values, key="value_text"),
                    value_type=require_str(values=values, key="value_type"),
                ),
            ),
        )
    return {
        fixture_id: [
            FixtureStat(identifier=identifier, a=sides["a"], h=sides["h"])
            for identifier, sides in stats.items()
        ]
        for fixture_id, stats in grouped.items()
    }


async def live_element_from_row(
    *,
    connection: ConnectionProtocol,
    row: object,
) -> LiveElement:
    """Build a live element model from row plus explanation stats."""
    values = row_values(row=row)
    season_id = require_str(values=values, key="season_id")
    event_id = require_int(values=values, key="event_id")
    element_id = require_int(values=values, key="element_id")
    rows = await connection.fetch(
        """
        SELECT * FROM fpl_event_live_explain_stats
        WHERE season_id = $1 AND event_id = $2 AND element_id = $3
        ORDER BY fixture_id, identifier, ordinal
        """,
        season_id,
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


async def live_elements_from_rows(
    *,
    connection: ConnectionProtocol,
    rows: list[object],
) -> list[LiveElement]:
    """Build live elements using one bounded child query."""
    if not rows:
        return []
    values_by_element = [row_values(row=row) for row in rows]
    season_id = require_str(values=values_by_element[0], key="season_id")
    event_id = require_int(values=values_by_element[0], key="event_id")
    element_ids = [
        require_int(values=values, key="element_id")
        for values in values_by_element
    ]
    explain_rows = await connection.fetch(
        """
        SELECT * FROM fpl_event_live_explain_stats
        WHERE season_id = $1
          AND event_id = $2
          AND element_id = ANY($3)
        ORDER BY element_id, fixture_id, identifier, ordinal
        """,
        season_id,
        event_id,
        element_ids,
    )
    explains: dict[int, dict[int, list[LiveElementExplainStat]]] = {}
    for explain_row in explain_rows:
        explain_values = row_values(row=explain_row)
        element_id = require_int(values=explain_values, key="element_id")
        fixture_id = require_int(values=explain_values, key="fixture_id")
        explains.setdefault(element_id, {}).setdefault(fixture_id, []).append(
            LiveElementExplainStat(
                identifier=require_str(values=explain_values, key="identifier"),
                points=require_int(values=explain_values, key="points"),
                value=text_and_type_to_scalar(
                    value_text=optional_str(
                        values=explain_values,
                        key="value_text",
                    ),
                    value_type=require_str(
                        values=explain_values,
                        key="value_type",
                    ),
                ),
            ),
        )
    return [
        LiveElement(
            id=element_id,
            stats=LiveElementStats.model_validate(
                filter_row_values(values=values),
            ),
            explain=[
                LiveElementExplain(fixture=fixture_id, stats=stats)
                for fixture_id, stats in explains.get(element_id, {}).items()
            ],
        )
        for values, element_id in zip(values_by_element, element_ids, strict=True)
    ]


def change_event_from_row(*, row: object) -> ChangeEvent:
    """Convert a database row into a change-event model."""
    values = row_values(row=row)
    return ChangeEvent(
        id=require_int(values=values, key="id"),
        season_id=require_str(values=values, key="season_id"),
        entity_family=EntityFamily(
            require_str(values=values, key="entity_family"),
        ),
        event_name=require_str(values=values, key="event_name"),
        source_key=IngestionSourceKey(
            require_str(values=values, key="source_key"),
        ),
        source_event_id=optional_int(values=values, key="source_event_id"),
        payload_hash=require_str(values=values, key="payload_hash"),
        created_count=require_int(values=values, key="created_count"),
        updated_count=require_int(values=values, key="updated_count"),
        deleted_count=require_int(values=values, key="deleted_count"),
        fetched_at=require_datetime(values=values, key="fetched_at"),
        created_at=require_datetime(values=values, key="created_at"),
    )


def entity_change_from_row(*, row: object) -> EntityChange:
    """Convert a JSONB entity-change row returned by either executor."""
    values = row_values(row=row)
    raw_fields = values.get("field_changes")
    if isinstance(raw_fields, str):
        raw_fields = json.loads(raw_fields)
    if not isinstance(raw_fields, list):
        raise TypeError("Entity field changes must be a JSON array.")
    return EntityChange(
        id=require_int(values=values, key="id"),
        change_event_id=require_int(values=values, key="change_event_id"),
        entity_key=require_str(values=values, key="entity_key"),
        entity_label=require_str(values=values, key="entity_label"),
        kind=ChangeKind(require_str(values=values, key="change_kind")),
        fields=FIELD_CHANGES_ADAPTER.validate_python(raw_fields),
        created_at=require_datetime(values=values, key="created_at"),
    )


def ingestion_source_status_from_row(*, row: object) -> IngestionSourceStatus:
    """Convert a latest-source metadata row to its domain model."""
    values = row_values(row=row)
    return IngestionSourceStatus(
        season_id=require_str(values=values, key="season_id"),
        source_key=IngestionSourceKey(
            require_str(values=values, key="source_key"),
        ),
        event_id=optional_int(values=values, key="event_id"),
        payload_hash=require_str(values=values, key="payload_hash"),
        fetched_at=require_datetime(values=values, key="fetched_at"),
        checked_at=require_datetime(values=values, key="checked_at"),
        last_changed_at=optional_datetime(values=values, key="last_changed_at"),
    )


def event_from_row(*, row: object) -> Event:
    """Convert a row to Event."""
    values = filter_row_values(values=row_values(row=row))
    if "deadline_time" in values:
        values["deadline_time"] = optional_datetime(
            values=values,
            key="deadline_time",
        )
    return Event.model_validate(values)


def season_from_row(*, row: object) -> Season:
    """Convert a row to Season."""
    values = filter_row_values(values=row_values(row=row))
    values["first_deadline_time"] = require_datetime(
        values=values,
        key="first_deadline_time",
    )
    values["last_deadline_time"] = require_datetime(
        values=values,
        key="last_deadline_time",
    )
    return Season.model_validate(values)


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
    values = filter_row_values(values=row_values(row=row))
    if "news_added" in values:
        values["news_added"] = optional_datetime(values=values, key="news_added")
    return Element.model_validate(values)


def fixture_from_row(*, row: object) -> Fixture:
    """Convert a row to Fixture without nested stats."""
    values = filter_row_values(values=row_values(row=row))
    if "kickoff_time" in values:
        values["kickoff_time"] = optional_datetime(
            values=values,
            key="kickoff_time",
        )
    return Fixture.model_validate(values | {"stats": []})


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
    if isinstance(row, Mapping):
        mapping = cast("Mapping[str, object]", row)
        return {key: mapping[key] for key in mapping}
    if hasattr(row, "items"):
        mapping_like = cast("RowProtocol", row)
        return {key: value for key, value in mapping_like.items()}
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
    """Return a required database datetime normalized to aware UTC."""
    value = values[key]
    return normalize_database_datetime(value=value, key=key)


def optional_datetime(*, values: dict[str, object], key: str) -> datetime | None:
    """Return a nullable database datetime normalized to aware UTC."""
    value = values[key]
    if value is None:
        return None
    return normalize_database_datetime(value=value, key=key)


def normalize_database_datetime(*, value: object, key: str) -> datetime:
    """Restore the timezone omitted by RDS Data API formatted records."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise TypeError(
            f"Expected {key} to be datetime, found {type(value).__name__}.",
        )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def require_date(*, values: dict[str, object], key: str) -> date:
    """Return a required date field from row values."""
    value = values[key]
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise TypeError(f"Expected {key} to be date, found {type(value).__name__}.")
