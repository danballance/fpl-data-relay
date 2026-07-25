from contextlib import AbstractAsyncContextManager
from datetime import UTC, date, datetime
from typing import cast

import pytest

from fpl_data_relay.adapters.outbound.postgres.database import PostgresDatabase
from fpl_data_relay.domain.changes import IngestionMetadata, IngestionSourceKey
from fpl_data_relay.domain.fixtures import Fixture
from fpl_data_relay.domain.live import EventLiveResponse, EventStatusResponse
from fpl_data_relay.domain.reference import BootstrapStatic, Season


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
        self.notifications: list[str] = []

    def transaction(self) -> AbstractAsyncContextManager[object]:
        return NormalisedTransaction()

    async def execute(self, query: str, *arguments: object) -> str:
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
            self.sources[source_identity]["checked_at"] = arguments[3]
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
            }
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
            fixture_id = arguments[1]
            self.fixture_stat_entries = [
                row
                for row in self.fixture_stat_entries
                if not (
                    row["season_id"] == season_id and row["fixture_id"] == fixture_id
                )
            ]
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
            event_id = arguments[1]
            self.tables["fpl_event_live_elements"] = {
                key: row
                for key, row in self.tables["fpl_event_live_elements"].items()
                if not (
                    row["season_id"] == season_id and row["event_id"] == event_id
                )
            }
            self.live_explain_stats = [
                row
                for row in self.live_explain_stats
                if not (
                    row["season_id"] == season_id and row["event_id"] == event_id
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
        return "OK"

    async def fetchrow(
        self,
        query: str,
        *arguments: object,
    ) -> dict[str, object] | None:
        if "INSERT INTO relay_change_events" in query:
            event = {
                "id": len(self.change_events) + 1,
                "season_id": arguments[0],
                "entity_family": arguments[1],
                "event_name": arguments[2],
                "source_key": arguments[3],
                "event_id": arguments[4],
                "payload_hash": arguments[5],
                "fetched_at": arguments[6],
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
        if "FROM fpl_fixtures" in query and "ORDER BY id" in query:
            return [
                row
                for row in self.tables["fpl_fixtures"].values()
                if row["season_id"] == arguments[0]
            ]
        if "FROM fpl_fixture_stat_entries" in query:
            return [
                row
                for row in self.fixture_stat_entries
                if row["season_id"] == arguments[0]
                and row["fixture_id"] == arguments[1]
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
                if row["season_id"] == arguments[0] and row["event_id"] == arguments[1]
            ]
        if "FROM fpl_event_live_explain_stats" in query:
            return [
                row
                for row in self.live_explain_stats
                if row["season_id"] == arguments[0]
                and row["event_id"] == arguments[1]
                and row["element_id"] == arguments[2]
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
) -> IngestionMetadata:
    return IngestionMetadata(
        season_id=season_id,
        source_key=source_key,
        event_id=event_id,
        payload_hash=source_key.value.ljust(64, "a")[:64],
        fetched_at=datetime(2026, 6, 20, tzinfo=UTC),
        checked_at=datetime(2026, 6, 20, tzinfo=UTC),
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
        season=season,
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
    assert len(await store.list_elements(season_id="2025-26")) == 1
    stored_fixture = await store.get_fixture(season_id="2025-26", fixture_id=1)
    assert stored_fixture is not None
    assert stored_fixture.stats[0].h[0].value == 2
    assert len(await store.list_fixtures(season_id="2025-26", event_id=None)) == 1
    assert len(await store.list_fixtures(season_id="2025-26", event_id=1)) == 1
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
    assert len(await store.list_live_elements(season_id="2025-26", event_id=1)) == 1
    assert len(await store.list_change_events(after_id=0, limit=20)) == 9
    await store.close()
    assert pool.closed is True


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
