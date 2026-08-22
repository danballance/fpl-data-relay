import json
from contextlib import AbstractAsyncContextManager
from datetime import UTC, date, datetime
from typing import cast

import pytest

from fpl_data_relay.adapters.outbound.postgres.database import (
    IngestionLockError,
    PostgresDatabase,
)
from fpl_data_relay.domain.changes import (
    EntityFamily,
    IngestionMetadata,
    IngestionSourceKey,
)
from fpl_data_relay.domain.fixtures import Fixture
from fpl_data_relay.domain.live import (
    EventLiveResponse,
    EventStatusPoints,
    EventStatusResponse,
)
from fpl_data_relay.domain.reference import BootstrapStatic, Season
from fpl_data_relay.domain.rules import derive_season
from tests.conftest import FakeClient


class NormalisedTransaction:
    async def __aenter__(self) -> object:
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback


class NormalisedConnection:
    def __init__(self) -> None:
        self.sources: dict[tuple[str, str, int | None], dict[str, object]] = {}
        self.tables: dict[str, dict[object, dict[str, object]]] = {
            "fpl_seasons": {},
            "fpl_events": {},
            "fpl_phases": {},
            "fpl_teams": {},
            "fpl_element_types": {},
            "fpl_element_stat_definitions": {},
            "fpl_elements": {},
            "fpl_fixtures": {},
            "fpl_event_status_days": {},
            "fpl_event_live_elements": {},
        }
        self.fixture_stat_entries: list[dict[str, object]] = []
        self.live_explain_stats: list[dict[str, object]] = []
        self.event_status: dict[str, object] | None = None
        self.change_events: list[dict[str, object]] = []
        self.entity_changes: list[dict[str, object]] = []
        self.entity_snapshots: dict[
            tuple[str, str, str],
            dict[str, object],
        ] = {}
        self.notifications: list[str] = []
        self.rebaseline_audits: list[dict[str, object]] = []
        self.advisory_lock_available = True
        self.transaction_calls = 0

    def transaction(self) -> AbstractAsyncContextManager[object]:
        self.transaction_calls += 1
        return NormalisedTransaction()

    async def execute(self, query: str, *arguments: object) -> str:
        if "DELETE FROM relay_change_events WHERE season_id" in query:
            removed_ids = {
                cast("int", event["id"])
                for event in self.change_events
                if event["season_id"] == arguments[0]
            }
            self.change_events = [
                event
                for event in self.change_events
                if event["season_id"] != arguments[0]
            ]
            self.entity_changes = [
                change
                for change in self.entity_changes
                if change["change_event_id"] not in removed_ids
            ]
            return "DELETE"
        if "DELETE FROM relay_entity_snapshots WHERE season_id = $1" in query:
            self.entity_snapshots = {
                key: row
                for key, row in self.entity_snapshots.items()
                if key[0] != arguments[0]
            }
            return "DELETE"
        if "UPDATE fpl_seasons" in query and "is_current = false" in query:
            current_season_id = cast("str", arguments[0])
            for season_id, season in self.tables["fpl_seasons"].items():
                if season_id != current_season_id:
                    season["is_current"] = False
                    season["row_hash"] = ""
            return "UPDATE"
        if "UPDATE relay_ingestion_sources" in query:
            source_identity = (
                cast("str", arguments[0]),
                cast("str", arguments[1]),
                cast("int | None", arguments[2]),
            )
            self.sources[source_identity]["fetched_at"] = arguments[3]
            self.sources[source_identity]["checked_at"] = arguments[4]
            return "UPDATE 1"
        if "INSERT INTO relay_ingestion_sources" in query:
            source_key = cast("str", arguments[1])
            source_identity = (
                cast("str", arguments[0]),
                source_key,
                cast("int | None", arguments[2]),
            )
            self.sources[source_identity] = {
                "season_id": arguments[0],
                "source_key": source_key,
                "event_id": arguments[2],
                "payload_hash": arguments[3],
                "fetched_at": arguments[4],
                "checked_at": arguments[5],
                "last_changed_at": arguments[6],
            }
            return "INSERT 0 1"
        if "INSERT INTO relay_entity_snapshots" in query:
            key = (
                cast("str", arguments[0]),
                cast("str", arguments[1]),
                cast("str", arguments[3]),
            )
            self.entity_snapshots[key] = {
                "season_id": arguments[0],
                "entity_family": arguments[1],
                "source_event_id": arguments[2],
                "entity_key": arguments[3],
                "entity_label": arguments[4],
                "snapshot": json.loads(cast("str", arguments[5])),
                "row_hash": arguments[6],
            }
            return "INSERT 0 1"
        if "DELETE FROM relay_entity_snapshots" in query:
            self.entity_snapshots.pop(
                (
                    cast("str", arguments[0]),
                    cast("str", arguments[1]),
                    cast("str", arguments[2]),
                ),
                None,
            )
            return "DELETE 1"
        if "INSERT INTO relay_entity_changes" in query:
            self.entity_changes.append(
                {
                    "id": len(self.entity_changes) + 1,
                    "change_event_id": arguments[0],
                    "entity_key": arguments[1],
                    "entity_label": arguments[2],
                    "change_kind": arguments[3],
                    "field_changes": json.loads(cast("str", arguments[4])),
                    "created_at": datetime.now(tz=UTC),
                },
            )
            return "INSERT 0 1"
        if "INSERT INTO fpl_event_status (" in query:
            self.event_status = {
                "season_id": arguments[0],
                "leagues": arguments[1],
                "payload_hash": arguments[2],
                "fetched_at": arguments[3],
                "checked_at": arguments[4],
            }
            return "INSERT 0 1"
        if "DELETE FROM fpl_fixture_stat_entries" in query:
            season_id = arguments[0]
            target_id = arguments[1]
            target_column = "element" if "element = $2" in query else "fixture_id"
            self.fixture_stat_entries = [
                row
                for row in self.fixture_stat_entries
                if not (
                    row["season_id"] == season_id
                    and row[target_column] == target_id
                )
            ]
            return "DELETE"
        if "DELETE FROM fpl_event_status_days" in query:
            self.tables["fpl_event_status_days"] = {
                key: row
                for key, row in self.tables["fpl_event_status_days"].items()
                if row["season_id"] != arguments[0]
                or (len(arguments) == 2 and row["event"] != arguments[1])
            }
            return "DELETE"
        if "INSERT INTO fpl_fixture_stat_entries" in query:
            self.fixture_stat_entries.append(
                {
                    "season_id": arguments[0],
                    "fixture_id": arguments[1],
                    "identifier": arguments[2],
                    "side": arguments[3],
                    "ordinal": arguments[4],
                    "element": arguments[5],
                    "value_text": arguments[6],
                    "value_type": arguments[7],
                },
            )
            return "INSERT 0 1"
        if "DELETE FROM fpl_event_live_elements" in query:
            season_id = arguments[0]
            target_id = arguments[1]
            target_column = (
                "element_id" if "element_id = $2" in query else "event_id"
            )
            self.tables["fpl_event_live_elements"] = {
                key: row
                for key, row in self.tables["fpl_event_live_elements"].items()
                if not (
                    row["season_id"] == season_id
                    and row[target_column] == target_id
                )
            }
            self.live_explain_stats = [
                row
                for row in self.live_explain_stats
                if not (
                    row["season_id"] == season_id
                    and row[target_column] == target_id
                )
            ]
            return "DELETE"
        if "DELETE FROM fpl_event_live_explain_stats" in query:
            self.live_explain_stats = [
                row
                for row in self.live_explain_stats
                if not (
                    row["season_id"] == arguments[0]
                    and row["fixture_id"] == arguments[1]
                )
            ]
            return "DELETE"
        if "INSERT INTO fpl_event_live_explain_stats" in query:
            self.live_explain_stats.append(
                {
                    "season_id": arguments[0],
                    "event_id": arguments[1],
                    "element_id": arguments[2],
                    "fixture_id": arguments[3],
                    "identifier": arguments[4],
                    "ordinal": arguments[5],
                    "points": arguments[6],
                    "value_text": arguments[7],
                    "value_type": arguments[8],
                },
            )
            return "INSERT 0 1"
        if "INSERT INTO fpl_" in query:
            self._record_model_row(query=query, arguments=arguments)
            return "INSERT 0 1"
        if query.strip().startswith("DELETE FROM fpl_") and "WHERE season_id" in query:
            table = query.strip().split()[2]
            retained: dict[object, dict[str, object]] = {}
            for key, row in self.tables[table].items():
                key_matches = (
                    arguments[1] in key
                    if isinstance(key, tuple)
                    else key == arguments[1]
                )
                if row["season_id"] != arguments[0] or not key_matches:
                    retained[key] = row
            self.tables[table] = retained
            return "DELETE 1"
        return "OK"

    async def fetchrow(
        self,
        query: str,
        *arguments: object,
    ) -> dict[str, object] | None:
        if "INSERT INTO relay_change_feed_rebaselines" in query:
            audit = {
                "id": len(self.rebaseline_audits) + 1,
                "season_id": arguments[0],
                "reason": arguments[1],
                "change_events_deleted": arguments[2],
                "entity_changes_deleted": arguments[3],
                "snapshots_rebuilt": arguments[4],
                "created_at": datetime.now(tz=UTC),
            }
            self.rebaseline_audits.append(audit)
            return audit
        if "FROM relay_ingestion_sources" in query:
            source_identity = (
                cast("str", arguments[0]),
                cast("str", arguments[1]),
                cast("int | None", arguments[2]),
            )
            return self.sources.get(source_identity)
        if "INSERT INTO relay_change_events" in query:
            event = {
                "id": len(self.change_events) + 1,
                "season_id": arguments[0],
                "entity_family": arguments[1],
                "event_name": arguments[2],
                "source_key": arguments[3],
                "source_event_id": arguments[4],
                "payload_hash": arguments[5],
                "created_count": arguments[6],
                "updated_count": arguments[7],
                "deleted_count": arguments[8],
                "fetched_at": arguments[9],
                "created_at": datetime.now(tz=UTC),
            }
            self.change_events.append(event)
            return event
        if "FROM fpl_seasons WHERE is_current" in query:
            return next(
                (
                    row
                    for row in self.tables["fpl_seasons"].values()
                    if row["is_current"] is True
                ),
                None,
            )
        if "FROM fpl_seasons WHERE id" in query:
            return self.tables["fpl_seasons"].get(arguments[0])
        if "FROM fpl_events" in query and "is_current" in query:
            return next(
                (
                    row
                    for row in self.tables["fpl_events"].values()
                    if row["season_id"] == arguments[0] and row["is_current"] is True
                ),
                None,
            )
        if "FROM fpl_events" in query and "id = $2" in query:
            return self.tables["fpl_events"].get((arguments[0], arguments[1]))
        if "FROM fpl_teams" in query and "id = $2" in query:
            return self.tables["fpl_teams"].get((arguments[0], arguments[1]))
        if "FROM fpl_elements" in query and "id = $2" in query:
            return self.tables["fpl_elements"].get((arguments[0], arguments[1]))
        if "FROM fpl_fixtures" in query and "id = $2" in query:
            return self.tables["fpl_fixtures"].get((arguments[0], arguments[1]))
        if "FROM fpl_event_status" in query:
            if self.event_status is None:
                return None
            if self.event_status["season_id"] != arguments[0]:
                return None
            return self.event_status
        if "FROM fpl_event_live_elements" in query:
            return self.tables["fpl_event_live_elements"].get(
                (arguments[0], arguments[1], arguments[2]),
            )
        return None

    async def fetch(self, query: str, *arguments: object) -> list[object]:
        compact_query = " ".join(query.split())
        if compact_query == "SELECT * FROM fpl_seasons WHERE is_current = true":
            return [
                row
                for row in self.tables["fpl_seasons"].values()
                if row["is_current"] is True
            ]
        if compact_query.startswith("SELECT id FROM fpl_"):
            table = compact_query.split()[3]
            return [
                {"id": row["id"]}
                for row in self.tables[table].values()
                if row["season_id"] == arguments[0]
            ]
        if compact_query.startswith(
            "SELECT name FROM fpl_element_stat_definitions",
        ):
            return [
                {"name": row["name"]}
                for row in self.tables["fpl_element_stat_definitions"].values()
                if row["season_id"] == arguments[0]
            ]
        if "FROM fpl_element_stat_definitions" in query:
            return [
                row
                for row in self.tables["fpl_element_stat_definitions"].values()
                if row["season_id"] == arguments[0]
            ]
        if "FROM fpl_seasons ORDER BY" in query:
            return list(self.tables["fpl_seasons"].values())
        if "FROM fpl_events" in query and "ORDER BY id" in query:
            return [
                row
                for row in self.tables["fpl_events"].values()
                if row["season_id"] == arguments[0]
            ]
        if "FROM fpl_phases" in query:
            return [
                row
                for row in self.tables["fpl_phases"].values()
                if row["season_id"] == arguments[0]
            ]
        if "FROM fpl_teams" in query and "ORDER BY id" in query:
            return [
                row
                for row in self.tables["fpl_teams"].values()
                if row["season_id"] == arguments[0]
            ]
        if "FROM fpl_element_types" in query and "ORDER BY id" in query:
            return [
                row
                for row in self.tables["fpl_element_types"].values()
                if row["season_id"] == arguments[0]
            ]
        if "FROM fpl_elements" in query and "ORDER BY id" in query:
            return [
                row
                for row in self.tables["fpl_elements"].values()
                if row["season_id"] == arguments[0]
            ]
        if "FROM fpl_fixtures" in query and "event = $2" in query:
            return [
                row
                for row in self.tables["fpl_fixtures"].values()
                if row["season_id"] == arguments[0] and row["event"] == arguments[1]
            ]
        if "FROM fpl_fixtures" in query and "id = ANY($2)" in query:
            fixture_ids = cast("list[int]", arguments[1])
            return [
                row
                for row in self.tables["fpl_fixtures"].values()
                if row["season_id"] == arguments[0]
                and row["id"] in fixture_ids
            ]
        if "FROM fpl_fixtures" in query and "ORDER BY id" in query:
            return [
                row
                for row in self.tables["fpl_fixtures"].values()
                if row["season_id"] == arguments[0]
            ]
        if "FROM fpl_fixture_stat_entries" in query:
            fixture_ids = (
                cast("list[int]", arguments[1])
                if "ANY($2)" in query
                else [cast("int", arguments[1])]
            )
            return [
                row
                for row in self.fixture_stat_entries
                if row["season_id"] == arguments[0]
                and row["fixture_id"] in fixture_ids
            ]
        if "FROM fpl_event_status_days" in query:
            return [
                row
                for row in self.tables["fpl_event_status_days"].values()
                if row["season_id"] == arguments[0]
            ]
        if "FROM fpl_event_live_elements" in query:
            return [
                row
                for row in self.tables["fpl_event_live_elements"].values()
                if row["season_id"] == arguments[0]
                and (
                    "event_id = $2" not in query
                    or row["event_id"] == arguments[1]
                )
            ]
        if "FROM fpl_event_live_explain_stats" in query:
            element_ids = (
                cast("list[int]", arguments[2])
                if "ANY($3)" in query
                else [cast("int", arguments[2])]
            )
            return [
                row
                for row in self.live_explain_stats
                if row["season_id"] == arguments[0]
                and row["event_id"] == arguments[1]
                and row["element_id"] in element_ids
            ]
        if "FROM relay_change_events" in query:
            after_id = cast("int", arguments[0])
            limit = cast("int", arguments[1])
            events = [
                event
                for event in self.change_events
                if cast("int", event["id"]) > after_id
            ][:limit]
            return cast("list[object]", events)
        if "FROM relay_entity_snapshots" in query:
            rows = [
                row
                for (season_id, family, _), row in self.entity_snapshots.items()
                if season_id == arguments[0] and family == arguments[1]
            ]
            if "source_event_id = $3" in query:
                rows = [
                    row for row in rows if row["source_event_id"] == arguments[2]
                ]
            return cast("list[object]", rows)
        if "FROM relay_entity_changes" in query:
            return cast(
                "list[object]",
                [
                    change
                    for change in self.entity_changes
                    if change["change_event_id"] == arguments[0]
                    and cast("int", change["id"]) > cast("int", arguments[1])
                ][: cast("int", arguments[2])],
            )
        if "FROM relay_ingestion_sources" in query:
            return cast(
                "list[object]",
                [
                    source
                    for source in self.sources.values()
                    if source["season_id"] == arguments[0]
                ],
            )
        return []

    async def fetchval(self, query: str, *arguments: object) -> object:
        if "pg_try_advisory_xact_lock" in query:
            return self.advisory_lock_available
        if "COUNT(*) FROM relay_change_events" in query:
            return sum(
                event["season_id"] == arguments[0]
                for event in self.change_events
            )
        if "COUNT(*)" in query and "relay_entity_changes" in query:
            event_ids = {
                event["id"]
                for event in self.change_events
                if event["season_id"] == arguments[0]
            }
            return sum(
                change["change_event_id"] in event_ids
                for change in self.entity_changes
            )
        if "SELECT payload_hash" in query:
            source_identity = (
                cast("str", arguments[0]),
                cast("str", arguments[1]),
                cast("int | None", arguments[2]),
            )
            source = self.sources.get(source_identity)
            return None if source is None else source["payload_hash"]
        if "pg_notify" in query:
            self.notifications.append(cast("str", arguments[1]))
        return None

    async def add_listener(self, channel: str, callback: object) -> None:
        del channel, callback

    async def remove_listener(self, channel: str, callback: object) -> None:
        del channel, callback

    def _record_model_row(self, *, query: str, arguments: tuple[object, ...]) -> None:
        table = query.split("INSERT INTO ", 1)[1].split(" (", 1)[0]
        column_text = query.split("(", 1)[1].split(")", 1)[0]
        columns = [column.strip() for column in column_text.split(",")]
        row = dict(zip(columns, arguments, strict=True))
        self.tables[table][table_key(table=table, row=row)] = row


class NormalisedAcquire:
    def __init__(self, *, connection: NormalisedConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> NormalisedConnection:
        return self._connection

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback


class NormalisedPool:
    def __init__(self) -> None:
        self.connection = NormalisedConnection()
        self.closed = False

    def acquire(self) -> NormalisedAcquire:
        return NormalisedAcquire(connection=self.connection)

    async def close(self) -> None:
        self.closed = True


def table_key(*, table: str, row: dict[str, object]) -> object:
    if table == "fpl_element_stat_definitions":
        return (row["season_id"], row["name"])
    if table == "fpl_event_status_days":
        return (row["season_id"], row["event"], row["date"])
    if table == "fpl_event_live_elements":
        return (row["season_id"], row["event_id"], row["element_id"])
    if table == "fpl_seasons":
        return row["id"]
    if table.startswith("fpl_"):
        return (row["season_id"], row["id"])
    return row["id"]


def metadata(
    *,
    season_id: str = "2025-26",
    source_key: IngestionSourceKey,
    event_id: int | None,
    hash_seed: str | None = None,
    fetched_minute: int = 0,
) -> IngestionMetadata:
    seed = source_key.value if hash_seed is None else hash_seed
    return IngestionMetadata(
        season_id=season_id,
        source_key=source_key,
        event_id=event_id,
        payload_hash=seed.ljust(64, "a")[:64],
        fetched_at=datetime(2026, 6, 20, 0, fetched_minute, tzinfo=UTC),
        checked_at=datetime(2026, 6, 20, 0, fetched_minute, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_postgres_store_persists_reference_and_live_bundles_atomically() -> None:
    pool = NormalisedPool()
    store = PostgresDatabase(pool=pool)
    client = FakeClient()
    bootstrap = await client.fetch_bootstrap_static()
    season = derive_season(bootstrap=bootstrap)
    reference_outcomes = await store.upsert_reference_snapshot(
        season=season,
        bootstrap=bootstrap,
        fixtures=await client.fetch_fixtures(),
        status=await client.fetch_event_status(),
        bootstrap_metadata=metadata(
            source_key=IngestionSourceKey.BOOTSTRAP,
            event_id=None,
        ),
        fixtures_metadata=metadata(
            source_key=IngestionSourceKey.FIXTURES,
            event_id=None,
        ),
        status_metadata=metadata(
            source_key=IngestionSourceKey.EVENT_STATUS,
            event_id=1,
        ),
    )

    assert len(reference_outcomes) == 3
    assert pool.connection.transaction_calls == 1
    live_outcomes = await store.upsert_live_snapshot(
        event_id=1,
        status=await client.fetch_event_status(),
        fixtures=await client.fetch_current_fixtures(event_id=1),
        live=await client.fetch_event_live(event_id=1),
        status_metadata=metadata(
            source_key=IngestionSourceKey.EVENT_STATUS,
            event_id=1,
        ),
        fixtures_metadata=metadata(
            source_key=IngestionSourceKey.CURRENT_FIXTURES,
            event_id=1,
        ),
        live_metadata=metadata(
            source_key=IngestionSourceKey.EVENT_LIVE,
            event_id=1,
        ),
    )

    assert len(live_outcomes) == 3
    assert pool.connection.transaction_calls == 2
    assert await store.get_event_status(season_id=season.id) is not None
    assert (
        await store.get_live_element(
            season_id=season.id,
            event_id=1,
            element_id=1,
        )
        is not None
    )


@pytest.mark.asyncio
async def test_postgres_store_normalised_upsert_and_read_paths() -> None:
    pool = NormalisedPool()
    store = PostgresDatabase(pool=pool)
    season = Season(
        id="2025-26",
        start_year=2025,
        end_year=2026,
        first_deadline_time=datetime(2025, 8, 15, 17, 30, tzinfo=UTC),
        last_deadline_time=datetime(2026, 5, 24, 13, 30, tzinfo=UTC),
        is_current=True,
    )
    bootstrap = BootstrapStatic.model_validate(
        {
            "events": [
                {"id": 1, "name": "Gameweek 1", "is_current": True},
                {"id": 2, "name": "Gameweek 2", "is_current": False},
            ],
            "phases": [{"id": 1, "name": "Phase", "start_event": 1, "stop_event": 2}],
            "teams": [
                {"id": 1, "name": "Team", "short_name": "TST"},
                {"id": 2, "name": "Other Team", "short_name": "OTH"},
            ],
            "element_types": [{"id": 1, "singular_name": "Goalkeeper"}],
            "element_stats": [{"label": "Minutes", "name": "minutes"}],
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
        },
    )
    fixture = Fixture.model_validate(
        {
            "id": 1,
            "event": 1,
            "team_h": 1,
            "team_a": 2,
            "started": True,
            "finished": False,
            "stats": [
                {
                    "identifier": "goals_scored",
                    "h": [{"element": 1, "value": 2}],
                    "a": [{"element": None, "value": None}],
                },
            ],
        },
    )
    status = EventStatusResponse.model_validate(
        {
            "leagues": "updated",
            "status": [
                {
                    "event": 1,
                    "bonus_added": False,
                    "date": date(2026, 6, 20),
                    "points": "r",
                },
            ],
        },
    )
    live = EventLiveResponse.model_validate(
        {
            "elements": [
                {
                    "id": 1,
                    "stats": {"total_points": 8},
                    "explain": [
                        {
                            "fixture": 1,
                            "stats": [
                                {"identifier": "minutes", "points": 2, "value": 90},
                            ],
                        },
                    ],
                },
            ],
        },
    )

    bootstrap_outcome = await store.upsert_bootstrap(
        season=season,
        bootstrap=bootstrap,
        metadata=metadata(source_key=IngestionSourceKey.BOOTSTRAP, event_id=None),
        delete_missing=True,
    )
    fixture_outcome = await store.upsert_fixtures(
        fixtures=[fixture],
        metadata=metadata(source_key=IngestionSourceKey.FIXTURES, event_id=None),
    )
    status_outcome = await store.upsert_event_status(
        status=status,
        metadata=metadata(source_key=IngestionSourceKey.EVENT_STATUS, event_id=1),
    )
    live_outcome = await store.upsert_event_live(
        event_id=1,
        live=live,
        metadata=metadata(source_key=IngestionSourceKey.EVENT_LIVE, event_id=1),
    )
    unchanged = await store.upsert_event_live(
        event_id=1,
        live=live,
        metadata=metadata(source_key=IngestionSourceKey.EVENT_LIVE, event_id=1),
    )

    assert bootstrap_outcome.changed is True
    assert fixture_outcome.changed is True
    assert status_outcome.changed is True
    assert live_outcome.changed is True
    assert unchanged.changed is False
    seasons = await store.list_seasons()
    assert seasons[0].id == "2025-26"
    current_season = await store.get_current_season()
    assert current_season is not None
    assert current_season.id == "2025-26"
    assert await store.get_season(season_id="2025-26") == current_season
    current_event = await store.get_current_event(season_id="2025-26")
    assert current_event is not None
    assert current_event.id == 1
    event = await store.get_event(season_id="2025-26", event_id=1)
    assert event is not None
    assert event.name == "Gameweek 1"
    assert len(await store.list_events(season_id="2025-26")) == 2
    assert len(await store.list_phases(season_id="2025-26")) == 1
    team = await store.get_team(season_id="2025-26", team_id=1)
    assert team is not None
    assert team.short_name == "TST"
    assert len(await store.list_teams(season_id="2025-26")) == 2
    assert len(await store.list_element_types(season_id="2025-26")) == 1
    element = await store.get_element(season_id="2025-26", element_id=1)
    assert element is not None
    assert element.web_name == "Player"
    assert element.photo == "1.jpg"
    assert len(
        await store.list_elements(
            season_id="2025-26",
            after_id=0,
            limit=100,
        ),
    ) == 1
    stored_fixture = await store.get_fixture(season_id="2025-26", fixture_id=1)
    assert stored_fixture is not None
    assert stored_fixture.stats[0].h[0].value == 2
    assert len(
        await store.list_fixtures(
            season_id="2025-26",
            event_id=None,
            after_id=0,
            limit=100,
        ),
    ) == 1
    assert len(
        await store.list_fixtures(
            season_id="2025-26",
            event_id=1,
            after_id=0,
            limit=100,
        ),
    ) == 1
    stored_status = await store.get_event_status(season_id="2025-26")
    assert stored_status is not None
    assert stored_status.leagues == "updated"
    live_element = await store.get_live_element(
        season_id="2025-26",
        event_id=1,
        element_id=1,
    )
    assert live_element is not None
    assert live_element.stats.total_points == 8
    assert len(
        await store.list_live_elements(
            season_id="2025-26",
            event_id=1,
            after_id=0,
            limit=100,
        ),
    ) == 1
    assert await store.list_change_events(after_id=0, limit=20) == []
    fixture_deletion = await store.upsert_fixtures(
        fixtures=[],
        metadata=metadata(
            source_key=IngestionSourceKey.FIXTURES,
            event_id=None,
            hash_seed="fixtures-removed",
            fetched_minute=1,
        ),
    )
    assert fixture_deletion.change_events[0].deleted_count == 1
    assert await store.get_fixture(season_id="2025-26", fixture_id=1) is None
    assert pool.connection.live_explain_stats == []
    await store.close()
    assert pool.closed is True


@pytest.mark.asyncio
async def test_postgres_store_emits_only_real_player_fields_and_deletions() -> None:
    pool = NormalisedPool()
    store = PostgresDatabase(pool=pool)
    season = Season(
        id="2025-26",
        start_year=2025,
        end_year=2026,
        first_deadline_time=datetime(2025, 8, 15, 17, 30, tzinfo=UTC),
        last_deadline_time=datetime(2026, 5, 24, 13, 30, tzinfo=UTC),
        is_current=True,
    )
    initial = BootstrapStatic.model_validate(
        {
            "events": [{"id": 1, "name": "Gameweek 1", "is_current": True}],
            "teams": [
                {"id": 1, "name": "Team", "short_name": "TST"},
            ],
            "element_types": [{"id": 1, "singular_name": "Goalkeeper"}],
            "elements": [
                {
                    "id": 1,
                    "first_name": "First",
                    "second_name": "Second",
                    "web_name": "Player",
                    "team": 1,
                    "element_type": 1,
                    "now_cost": 50,
                    "news": "Injured",
                },
            ],
        },
    )
    baseline = await store.upsert_bootstrap(
        season=season,
        bootstrap=initial,
        metadata=metadata(
            source_key=IngestionSourceKey.BOOTSTRAP,
            event_id=None,
            hash_seed="baseline",
        ),
        delete_missing=True,
    )
    changed = initial.model_copy(
        update={
            "elements": [
                initial.elements[0].model_copy(
                    update={"now_cost": 51, "news": None},
                ),
            ],
        },
    )
    update = await store.upsert_bootstrap(
        season=season,
        bootstrap=changed,
        metadata=metadata(
            source_key=IngestionSourceKey.BOOTSTRAP,
                event_id=None,
                hash_seed="changed",
                fetched_minute=1,
        ),
        delete_missing=True,
    )

    assert baseline.change_events == []
    assert len(update.change_events) == 1
    event = update.change_events[0]
    assert event.entity_family.value == "elements"
    assert event.updated_count == 1
    details = await store.list_entity_changes(
        change_event_id=event.id,
        after_id=0,
        limit=100,
    )
    assert len(details) == 1
    assert [field.field for field in details[0].fields] == ["news", "now_cost"]
    assert details[0].fields[0].after.value is None
    stored = await store.get_element(season_id=season.id, element_id=1)
    assert stored is not None
    assert stored.news is None
    assert stored.now_cost == 51

    pool.connection.fixture_stat_entries.append(
        {
            "season_id": season.id,
            "fixture_id": 10,
            "identifier": "goals_scored",
            "side": "h",
            "ordinal": 0,
            "element": 1,
            "value_text": "1",
            "value_type": "int",
        },
    )
    pool.connection.tables["fpl_event_live_elements"][(season.id, 1, 1)] = {
        "season_id": season.id,
        "event_id": 1,
        "element_id": 1,
    }
    pool.connection.live_explain_stats.append(
        {
            "season_id": season.id,
            "event_id": 1,
            "element_id": 1,
            "fixture_id": 10,
        },
    )
    removed = changed.model_copy(update={"elements": []})
    deletion = await store.upsert_bootstrap(
        season=season,
        bootstrap=removed,
        metadata=metadata(
            source_key=IngestionSourceKey.BOOTSTRAP,
            event_id=None,
            hash_seed="removed",
            fetched_minute=2,
        ),
        delete_missing=True,
    )
    assert deletion.change_events[0].deleted_count == 1
    assert await store.get_element(season_id=season.id, element_id=1) is None
    assert pool.connection.fixture_stat_entries == []
    assert pool.connection.tables["fpl_event_live_elements"] == {}
    assert pool.connection.live_explain_stats == []


@pytest.mark.asyncio
async def test_postgres_store_keeps_repeated_upstream_ids_per_season() -> None:
    pool = NormalisedPool()
    store = PostgresDatabase(pool=pool)
    seasons = [
        Season(
            id="2025-26",
            start_year=2025,
            end_year=2026,
            first_deadline_time=datetime(2025, 8, 15, 17, 30, tzinfo=UTC),
            last_deadline_time=datetime(2026, 5, 24, 13, 30, tzinfo=UTC),
            is_current=False,
        ),
        Season(
            id="2026-27",
            start_year=2026,
            end_year=2027,
            first_deadline_time=datetime(2026, 8, 15, 17, 30, tzinfo=UTC),
            last_deadline_time=datetime(2027, 5, 24, 13, 30, tzinfo=UTC),
            is_current=True,
        ),
    ]
    for season in seasons:
        bootstrap = BootstrapStatic.model_validate(
            {
                "events": [
                    {
                        "id": 1,
                        "name": f"{season.id} Gameweek 1",
                        "is_current": True,
                    },
                ],
                "teams": [
                    {
                        "id": 1,
                        "name": f"{season.id} Team",
                        "short_name": "TST",
                    },
                ],
                "element_types": [{"id": 1, "singular_name": "Goalkeeper"}],
                "elements": [
                    {
                        "id": 1,
                        "first_name": "First",
                        "second_name": season.id,
                        "web_name": f"{season.id} Player",
                        "team": 1,
                        "element_type": 1,
                    },
                ],
            },
        )
        outcome = await store.upsert_bootstrap(
            season=season,
            bootstrap=bootstrap,
            metadata=metadata(
                season_id=season.id,
                source_key=IngestionSourceKey.BOOTSTRAP,
                event_id=None,
            ),
            delete_missing=True,
        )
        assert outcome.changed is True

    old_event = await store.get_event(season_id="2025-26", event_id=1)
    new_event = await store.get_event(season_id="2026-27", event_id=1)
    assert old_event is not None
    assert new_event is not None
    assert old_event.name == "2025-26 Gameweek 1"
    assert new_event.name == "2026-27 Gameweek 1"
    current_season = await store.get_current_season()
    assert current_season is not None
    assert current_season.id == "2026-27"


@pytest.mark.asyncio
async def test_postgres_store_normalised_missing_reads_return_none() -> None:
    store = PostgresDatabase(pool=NormalisedPool())
    assert await store.get_season(season_id="2099-00") is None
    assert await store.get_event(season_id="2025-26", event_id=999) is None
    assert await store.get_team(season_id="2025-26", team_id=999) is None
    assert await store.get_element(season_id="2025-26", element_id=999) is None
    assert await store.get_fixture(season_id="2025-26", fixture_id=999) is None
    assert await store.get_event_status(season_id="2025-26") is None
    assert (
        await store.get_live_element(
            season_id="2025-26",
            event_id=1,
            element_id=999,
        )
        is None
    )


@pytest.mark.asyncio
async def test_source_freshness_rejects_older_and_conflicting_payloads() -> None:
    pool = NormalisedPool()
    store = PostgresDatabase(pool=pool)
    fresh_status = EventStatusResponse.model_validate(
        {
            "status": [
                {
                    "event": 1,
                    "bonus_added": False,
                    "date": "2026-06-20",
                    "points": "l",
                },
            ],
        },
    )
    stale_status = fresh_status.model_copy(
        update={
            "status": [
                fresh_status.status[0].model_copy(
                    update={"points": EventStatusPoints.NOT_STARTED},
                ),
            ],
        },
    )
    fresh_metadata = metadata(
        source_key=IngestionSourceKey.EVENT_STATUS,
        event_id=1,
        hash_seed="fresh",
        fetched_minute=2,
    )
    stale_metadata = metadata(
        source_key=IngestionSourceKey.EVENT_STATUS,
        event_id=1,
        hash_seed="stale",
        fetched_minute=1,
    )

    await store.upsert_event_status(status=fresh_status, metadata=fresh_metadata)
    stale = await store.upsert_event_status(
        status=stale_status,
        metadata=stale_metadata,
    )
    stored = await store.get_event_status(season_id="2025-26")
    assert stale.changed is False
    assert stored is not None
    assert stored.status[0].points == "l"
    with pytest.raises(ValueError, match="conflicting payloads"):
        await store.upsert_event_status(
            status=stale_status,
            metadata=stale_metadata.model_copy(
                update={"fetched_at": fresh_metadata.fetched_at},
            ),
        )


@pytest.mark.asyncio
async def test_full_fixtures_cannot_regress_live_owned_fields() -> None:
    store = PostgresDatabase(pool=NormalisedPool())
    kickoff = datetime(2026, 6, 20, tzinfo=UTC)
    live_fixture = Fixture(
        id=1,
        event=1,
        kickoff_time=kickoff,
        started=True,
        finished=False,
        finished_provisional=True,
        minutes=90,
        team_h=1,
        team_a=2,
        team_h_score=3,
        team_a_score=0,
    )
    await store.upsert_fixtures(
        fixtures=[live_fixture],
        metadata=metadata(
            source_key=IngestionSourceKey.CURRENT_FIXTURES,
            event_id=1,
            fetched_minute=5,
        ),
    )
    await store.upsert_fixtures(
        fixtures=[
            live_fixture.model_copy(
                update={
                    "started": False,
                    "finished_provisional": False,
                    "minutes": 0,
                    "team_h_score": 2,
                },
            ),
        ],
        metadata=metadata(
            source_key=IngestionSourceKey.FIXTURES,
            event_id=None,
            fetched_minute=6,
        ),
    )
    stored = await store.get_fixture(season_id="2025-26", fixture_id=1)
    assert stored is not None
    assert stored.started is True
    assert stored.finished_provisional is True
    assert stored.minutes == 90
    assert stored.team_h_score == 3


@pytest.mark.asyncio
async def test_rebaseline_rebuilds_current_snapshots_and_preserves_sources() -> None:
    pool = NormalisedPool()
    store = PostgresDatabase(pool=pool)
    client = FakeClient()
    bootstrap = await client.fetch_bootstrap_static()
    season = derive_season(bootstrap=bootstrap)
    await store.upsert_reference_snapshot(
        season=season,
        bootstrap=bootstrap,
        fixtures=await client.fetch_fixtures(),
        status=await client.fetch_event_status(),
        bootstrap_metadata=metadata(
            source_key=IngestionSourceKey.BOOTSTRAP,
            event_id=None,
        ),
        fixtures_metadata=metadata(
            source_key=IngestionSourceKey.FIXTURES,
            event_id=None,
        ),
        status_metadata=metadata(
            source_key=IngestionSourceKey.EVENT_STATUS,
            event_id=1,
        ),
    )
    await store.upsert_event_live(
        event_id=1,
        live=await client.fetch_event_live(event_id=1),
        metadata=metadata(
            source_key=IngestionSourceKey.EVENT_LIVE,
            event_id=1,
        ),
    )
    connection = pool.connection
    connection.change_events.append(
        {
            "id": 99,
            "season_id": season.id,
            "entity_family": "fixtures",
        },
    )
    connection.entity_changes.append(
        {"id": 100, "change_event_id": 99},
    )
    connection.entity_snapshots[("other-season", "fixtures", "7")] = {
        "season_id": "other-season",
        "entity_family": "fixtures",
        "source_event_id": None,
        "entity_key": "7",
        "entity_label": "Other fixture",
        "snapshot": {"id": 7},
        "row_hash": "a" * 64,
    }
    source_count = len(connection.sources)
    normalized_counts = {
        table: len(rows) for table, rows in connection.tables.items()
    }

    result = await store.rebaseline_current_change_feed(reason="season repair")
    rebuilt_count = len(
        [key for key in connection.entity_snapshots if key[0] == season.id],
    )
    assert result.season_id == season.id
    assert result.change_events_deleted == 1
    assert result.entity_changes_deleted == 1
    assert result.snapshots_rebuilt == rebuilt_count
    assert rebuilt_count > 0
    assert len(connection.sources) == source_count
    assert {
        table: len(rows) for table, rows in connection.tables.items()
    } == normalized_counts
    assert ("other-season", "fixtures", "7") in connection.entity_snapshots

    repeated = await store.rebaseline_current_change_feed(reason="repeat repair")
    assert repeated.id == 2
    assert repeated.change_events_deleted == 0
    assert repeated.snapshots_rebuilt == result.snapshots_rebuilt

    identical = await store.upsert_reference_snapshot(
        season=season,
        bootstrap=bootstrap,
        fixtures=await client.fetch_fixtures(),
        status=await client.fetch_event_status(),
        bootstrap_metadata=metadata(
            source_key=IngestionSourceKey.BOOTSTRAP,
            event_id=None,
        ),
        fixtures_metadata=metadata(
            source_key=IngestionSourceKey.FIXTURES,
            event_id=None,
        ),
        status_metadata=metadata(
            source_key=IngestionSourceKey.EVENT_STATUS,
            event_id=1,
        ),
    )
    assert all(not outcome.change_events for outcome in identical)

    newer_bootstrap = bootstrap.model_copy(
        update={
            "elements": [
                bootstrap.elements[0].model_copy(update={"web_name": "New Ada"}),
                *bootstrap.elements[1:],
            ],
        },
    )
    newer = await store.upsert_bootstrap(
        season=season,
        bootstrap=newer_bootstrap,
        metadata=metadata(
            source_key=IngestionSourceKey.BOOTSTRAP,
            event_id=None,
            hash_seed="newer-bootstrap",
            fetched_minute=1,
        ),
        delete_missing=True,
    )
    assert len(newer.change_events) == 1
    assert newer.change_events[0].entity_family is EntityFamily.ELEMENTS
    assert newer.change_events[0].updated_count == 1

    stale = await store.upsert_bootstrap(
        season=season,
        bootstrap=bootstrap,
        metadata=metadata(
            source_key=IngestionSourceKey.BOOTSTRAP,
            event_id=None,
        ),
        delete_missing=True,
    )
    stored_element = await store.get_element(
        season_id=season.id,
        element_id=bootstrap.elements[0].id,
    )
    assert stale.changed is False
    assert stored_element is not None
    assert stored_element.web_name == "New Ada"


@pytest.mark.asyncio
async def test_rebaseline_fails_without_current_season_or_ingestion_lock() -> None:
    pool = NormalisedPool()
    store = PostgresDatabase(pool=pool)
    with pytest.raises(RuntimeError, match="exactly one current"):
        await store.rebaseline_current_change_feed(reason="missing season")
    pool.connection.advisory_lock_available = False
    with pytest.raises(IngestionLockError):
        await store.rebaseline_current_change_feed(reason="busy")

    ambiguous_pool = NormalisedPool()
    ambiguous_store = PostgresDatabase(pool=ambiguous_pool)
    season = derive_season(
        bootstrap=await FakeClient().fetch_bootstrap_static(),
    )
    ambiguous_pool.connection.tables["fpl_seasons"] = {
        season.id: season.model_dump(),
        "other-current": season.model_copy(
            update={"id": "other-current"},
        ).model_dump(),
    }
    with pytest.raises(RuntimeError, match="exactly one current"):
        await ambiguous_store.rebaseline_current_change_feed(
            reason="ambiguous season",
        )
