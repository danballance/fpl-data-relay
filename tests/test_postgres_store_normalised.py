from contextlib import AbstractAsyncContextManager
from datetime import UTC, date, datetime
from typing import cast

import pytest

from fpl_data_relay.fpl_models import (
    BootstrapStatic,
    EventLiveResponse,
    EventStatusResponse,
    Fixture,
)
from fpl_data_relay.resources import IngestionSourceKey
from fpl_data_relay.store import IngestionMetadata, PostgresStore


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
        self.sources: dict[str, dict[str, object]] = {}
        self.tables: dict[str, dict[object, dict[str, object]]] = {
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
        self.notifications: list[str] = []

    def transaction(self) -> AbstractAsyncContextManager[object]:
        return NormalisedTransaction()

    async def execute(self, query: str, *arguments: object) -> str:
        if "UPDATE relay_ingestion_sources" in query:
            source_key = cast("str", arguments[0])
            self.sources[source_key]["checked_at"] = arguments[1]
            return "UPDATE 1"
        if "INSERT INTO relay_ingestion_sources" in query:
            source_key = cast("str", arguments[0])
            self.sources[source_key] = {
                "source_key": source_key,
                "event_id": arguments[1],
                "payload_hash": arguments[2],
                "fetched_at": arguments[3],
                "checked_at": arguments[4],
            }
            return "INSERT 0 1"
        if "INSERT INTO fpl_event_status (" in query:
            self.event_status = {
                "id": True,
                "leagues": arguments[0],
                "payload_hash": arguments[1],
                "fetched_at": arguments[2],
                "checked_at": arguments[3],
            }
            return "INSERT 0 1"
        if "DELETE FROM fpl_fixture_stat_entries" in query:
            fixture_id = arguments[0]
            self.fixture_stat_entries = [
                row
                for row in self.fixture_stat_entries
                if row["fixture_id"] != fixture_id
            ]
            return "DELETE"
        if "INSERT INTO fpl_fixture_stat_entries" in query:
            self.fixture_stat_entries.append(
                {
                    "fixture_id": arguments[0],
                    "identifier": arguments[1],
                    "side": arguments[2],
                    "ordinal": arguments[3],
                    "element": arguments[4],
                    "value_text": arguments[5],
                    "value_type": arguments[6],
                },
            )
            return "INSERT 0 1"
        if "DELETE FROM fpl_event_live_elements" in query:
            event_id = arguments[0]
            self.tables["fpl_event_live_elements"] = {
                key: row
                for key, row in self.tables["fpl_event_live_elements"].items()
                if row["event_id"] != event_id
            }
            self.live_explain_stats = [
                row for row in self.live_explain_stats if row["event_id"] != event_id
            ]
            return "DELETE"
        if "INSERT INTO fpl_event_live_explain_stats" in query:
            self.live_explain_stats.append(
                {
                    "event_id": arguments[0],
                    "element_id": arguments[1],
                    "fixture_id": arguments[2],
                    "identifier": arguments[3],
                    "ordinal": arguments[4],
                    "points": arguments[5],
                    "value_text": arguments[6],
                    "value_type": arguments[7],
                },
            )
            return "INSERT 0 1"
        if "INSERT INTO fpl_" in query:
            self._record_model_row(query=query, arguments=arguments)
            return "INSERT 0 1"
        return "OK"

    async def fetchrow(
        self,
        query: str,
        *arguments: object,
    ) -> dict[str, object] | None:
        if "INSERT INTO relay_change_events" in query:
            event = {
                "id": len(self.change_events) + 1,
                "entity_family": arguments[0],
                "event_name": arguments[1],
                "source_key": arguments[2],
                "event_id": arguments[3],
                "payload_hash": arguments[4],
                "fetched_at": arguments[5],
                "created_at": datetime.now(tz=UTC),
            }
            self.change_events.append(event)
            return event
        if "FROM fpl_events WHERE is_current" in query:
            return next(
                (
                    row
                    for row in self.tables["fpl_events"].values()
                    if row["is_current"] is True
                ),
                None,
            )
        if "FROM fpl_events WHERE id" in query:
            return self.tables["fpl_events"].get(arguments[0])
        if "FROM fpl_teams WHERE id" in query:
            return self.tables["fpl_teams"].get(arguments[0])
        if "FROM fpl_elements WHERE id" in query:
            return self.tables["fpl_elements"].get(arguments[0])
        if "FROM fpl_fixtures WHERE id" in query:
            return self.tables["fpl_fixtures"].get(arguments[0])
        if "FROM fpl_event_status" in query:
            return self.event_status
        if "FROM fpl_event_live_elements" in query:
            return self.tables["fpl_event_live_elements"].get(
                (arguments[0], arguments[1]),
            )
        return None

    async def fetch(self, query: str, *arguments: object) -> list[object]:
        if "FROM fpl_events ORDER BY" in query:
            return list(self.tables["fpl_events"].values())
        if "FROM fpl_phases" in query:
            return list(self.tables["fpl_phases"].values())
        if "FROM fpl_teams ORDER BY" in query:
            return list(self.tables["fpl_teams"].values())
        if "FROM fpl_element_types" in query:
            return list(self.tables["fpl_element_types"].values())
        if "FROM fpl_elements ORDER BY" in query:
            return list(self.tables["fpl_elements"].values())
        if "FROM fpl_fixtures WHERE event" in query:
            return [
                row
                for row in self.tables["fpl_fixtures"].values()
                if row["event"] == arguments[0]
            ]
        if "FROM fpl_fixtures ORDER BY" in query:
            return list(self.tables["fpl_fixtures"].values())
        if "FROM fpl_fixture_stat_entries" in query:
            return [
                row
                for row in self.fixture_stat_entries
                if row["fixture_id"] == arguments[0]
            ]
        if "FROM fpl_event_status_days" in query:
            return list(self.tables["fpl_event_status_days"].values())
        if "FROM fpl_event_live_elements" in query:
            return [
                row
                for row in self.tables["fpl_event_live_elements"].values()
                if row["event_id"] == arguments[0]
            ]
        if "FROM fpl_event_live_explain_stats" in query:
            return [
                row
                for row in self.live_explain_stats
                if row["event_id"] == arguments[0]
                and row["element_id"] == arguments[1]
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
        return []

    async def fetchval(self, query: str, *arguments: object) -> object:
        if "SELECT payload_hash" in query:
            source = self.sources.get(cast("str", arguments[0]))
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
        return row["name"]
    if table == "fpl_event_status_days":
        return (row["event"], row["date"])
    if table == "fpl_event_live_elements":
        return (row["event_id"], row["element_id"])
    return row["id"]


def metadata(
    *,
    source_key: IngestionSourceKey,
    event_id: int | None,
) -> IngestionMetadata:
    return IngestionMetadata(
        source_key=source_key,
        event_id=event_id,
        payload_hash=source_key.value.ljust(64, "a")[:64],
        fetched_at=datetime(2026, 6, 20, tzinfo=UTC),
        checked_at=datetime(2026, 6, 20, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_postgres_store_normalised_upsert_and_read_paths() -> None:
    pool = NormalisedPool()
    store = PostgresStore(pool=pool)
    bootstrap = BootstrapStatic.model_validate(
        {
            "events": [{"id": 1, "name": "Gameweek 1", "is_current": True}],
            "phases": [{"id": 1, "name": "Phase", "start_event": 1, "stop_event": 2}],
            "teams": [{"id": 1, "name": "Team", "short_name": "TST"}],
            "element_types": [{"id": 1, "singular_name": "Goalkeeper"}],
            "element_stats": [{"label": "Minutes", "name": "minutes"}],
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
                    "leagues_updated": True,
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
        bootstrap=bootstrap,
        metadata=metadata(source_key=IngestionSourceKey.BOOTSTRAP, event_id=None),
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
    current_event = await store.get_current_event()
    assert current_event is not None
    assert current_event.id == 1
    event = await store.get_event(event_id=1)
    assert event is not None
    assert event.name == "Gameweek 1"
    assert len(await store.list_events()) == 1
    assert len(await store.list_phases()) == 1
    team = await store.get_team(team_id=1)
    assert team is not None
    assert team.short_name == "TST"
    assert len(await store.list_teams()) == 1
    assert len(await store.list_element_types()) == 1
    element = await store.get_element(element_id=1)
    assert element is not None
    assert element.web_name == "Player"
    assert len(await store.list_elements()) == 1
    stored_fixture = await store.get_fixture(fixture_id=1)
    assert stored_fixture is not None
    assert stored_fixture.stats[0].h[0].value == 2
    assert len(await store.list_fixtures(event_id=None)) == 1
    assert len(await store.list_fixtures(event_id=1)) == 1
    stored_status = await store.get_event_status()
    assert stored_status is not None
    assert stored_status.leagues == "updated"
    live_element = await store.get_live_element(event_id=1, element_id=1)
    assert live_element is not None
    assert live_element.stats.total_points == 8
    assert len(await store.list_live_elements(event_id=1)) == 1
    assert len(await store.list_change_events(after_id=0, limit=20)) == 9
    await store.close()
    assert pool.closed is True


@pytest.mark.asyncio
async def test_postgres_store_normalised_missing_reads_return_none() -> None:
    store = PostgresStore(pool=NormalisedPool())
    assert await store.get_event(event_id=999) is None
    assert await store.get_team(team_id=999) is None
    assert await store.get_element(element_id=999) is None
    assert await store.get_fixture(fixture_id=999) is None
    assert await store.get_event_status() is None
    assert await store.get_live_element(event_id=1, element_id=999) is None
