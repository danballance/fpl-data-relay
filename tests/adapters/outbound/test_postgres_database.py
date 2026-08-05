from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

import pytest
from asyncpg import Record
from asyncpg.protocol import protocol

from fpl_data_relay.adapters.outbound.postgres.database import (
    IngestionLockError,
    PostgresDatabase,
    element_from_row,
    event_from_row,
    fixture_from_row,
    row_values,
    season_from_row,
)
from fpl_data_relay.application.database import SCHEMA_VERSION
from fpl_data_relay.application.errors import SchemaUnavailableError
from tests.conftest import FakePostgresPool


@pytest.mark.asyncio
async def test_postgres_store_applies_and_checks_schema() -> None:
    pool = FakePostgresPool()
    store = PostgresDatabase(pool=pool)
    await store.apply_schema()
    await store.check_schema_version(expected_version=SCHEMA_VERSION)
    await store.close()
    assert pool.schema_version == SCHEMA_VERSION
    assert pool.closed is True


@pytest.mark.asyncio
async def test_postgres_store_fails_on_schema_mismatch() -> None:
    pool = FakePostgresPool()
    pool.schema_version = 999
    store = PostgresDatabase(pool=pool)
    with pytest.raises(SchemaUnavailableError, match="schema version mismatch"):
        await store.check_schema_version(expected_version=SCHEMA_VERSION)


@pytest.mark.asyncio
async def test_postgres_store_advisory_lock_rejects_overlap() -> None:
    pool = FakePostgresPool()
    store = PostgresDatabase(pool=pool)
    async with store.ingestion_lock():
        with pytest.raises(IngestionLockError):
            async with store.ingestion_lock():
                pass
    assert pool.locked is False


def test_row_values_rejects_unknown_row_shape() -> None:
    with pytest.raises(TypeError, match="Unsupported database row type"):
        row_values(row=object())


def test_row_values_accepts_asyncpg_record() -> None:
    create_record = cast(
        "Callable[[dict[str, int], tuple[object, ...]], Record]",
        vars(protocol)["_create_record"],
    )
    row = create_record({"id": 0, "resource_key": 1}, (1, "bootstrap"))
    assert row_values(row=row) == {"id": 1, "resource_key": "bootstrap"}


def test_database_row_mappers_restore_rds_timestamps_as_aware_utc() -> None:
    timestamp = "2026-08-21 17:30:00"
    expected = datetime(2026, 8, 21, 17, 30, tzinfo=UTC)

    season = season_from_row(
        row={
            "id": "2026-27",
            "start_year": 2026,
            "end_year": 2027,
            "first_deadline_time": timestamp,
            "last_deadline_time": "2027-05-30 13:30:00",
            "is_current": True,
        },
    )
    event = event_from_row(
        row={"id": 1, "name": "Gameweek 1", "deadline_time": timestamp},
    )
    element = element_from_row(
        row={
            "id": 1,
            "first_name": "Ada",
            "second_name": "Lovelace",
            "web_name": "Lovelace",
            "team": 1,
            "element_type": 1,
            "news_added": timestamp,
        },
    )
    fixture = fixture_from_row(
        row={
            "id": 1,
            "event": 1,
            "finished": False,
            "kickoff_time": timestamp,
            "started": False,
            "team_a": 1,
            "team_h": 2,
        },
    )

    assert season.first_deadline_time == expected
    assert season.last_deadline_time == datetime(
        2027,
        5,
        30,
        13,
        30,
        tzinfo=UTC,
    )
    assert event.deadline_time == expected
    assert element.news_added == expected
    assert fixture.kickoff_time == expected
