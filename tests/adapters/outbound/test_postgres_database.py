from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

import pytest
from asyncpg import Record
from asyncpg.protocol import protocol

from fpl_data_relay.adapters.outbound.postgres.database import (
    IngestionLockError,
    PostgresDatabase,
    SchemaError,
    row_values,
)
from fpl_data_relay.application.database import SCHEMA_VERSION
from fpl_data_relay.domain.changes import EntityFamily, IngestionSourceKey
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
    with pytest.raises(SchemaError, match="schema version mismatch"):
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


@pytest.mark.asyncio
async def test_postgres_store_watch_replays_then_heartbeats() -> None:
    pool = FakePostgresPool()
    store = PostgresDatabase(pool=pool)
    timestamp = datetime(2026, 6, 20, tzinfo=UTC)
    pool.events.append(
        {
            "id": 1,
            "season_id": "2025-26",
            "entity_family": EntityFamily.EVENT_LIVE.value,
            "event_name": "event_live.updated",
            "source_key": IngestionSourceKey.EVENT_LIVE.value,
            "resource_key": IngestionSourceKey.EVENT_LIVE.value,
            "event_id": 1,
            "payload_hash": "a" * 64,
            "fetched_at": timestamp,
            "created_at": timestamp,
        },
    )
    events = []
    async for event in store.watch_change_events(after_id=0, heartbeat_seconds=1):
        events.append(event)
        if len(events) == 2:
            break
    assert events[0] is not None
    assert events[1] is None


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
