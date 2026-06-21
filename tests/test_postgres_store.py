from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

import pytest
from asyncpg import Record
from asyncpg.protocol import protocol

from fpl_data_relay.resources import ResourceKey
from fpl_data_relay.schemas import SCHEMA_VERSION
from fpl_data_relay.store import (
    IngestionLockError,
    PostgresStore,
    SchemaError,
    row_values,
)
from tests.conftest import FakePostgresPool, resource_write


@pytest.mark.asyncio
async def test_postgres_store_applies_and_checks_schema() -> None:
    pool = FakePostgresPool()
    store = PostgresStore(pool=pool)
    await store.apply_schema()
    await store.check_schema_version(expected_version=SCHEMA_VERSION)
    await store.close()
    assert pool.schema_version == SCHEMA_VERSION
    assert pool.closed is True


@pytest.mark.asyncio
async def test_postgres_store_fails_on_schema_mismatch() -> None:
    pool = FakePostgresPool()
    pool.schema_version = 999
    store = PostgresStore(pool=pool)
    with pytest.raises(SchemaError, match="schema version mismatch"):
        await store.check_schema_version(expected_version=SCHEMA_VERSION)


@pytest.mark.asyncio
async def test_postgres_store_returns_none_for_missing_resource() -> None:
    store = PostgresStore(pool=FakePostgresPool())
    missing = await store.get_resource(resource_key=ResourceKey.BOOTSTRAP)
    assert missing is None


@pytest.mark.asyncio
async def test_postgres_store_upsert_get_list_and_notify() -> None:
    pool = FakePostgresPool()
    store = PostgresStore(pool=pool)
    resource = resource_write(resource_key=ResourceKey.BOOTSTRAP, payload_hash="a" * 64)
    outcome = await store.upsert_resource(resource=resource)
    stored = await store.get_resource(resource_key=ResourceKey.BOOTSTRAP)
    events = await store.list_change_events(after_id=0, limit=10)
    assert outcome.changed is True
    assert outcome.change_event is not None
    assert stored is not None
    assert stored.payload == {"resource": "bootstrap-static"}
    assert len(events) == 1
    assert pool.notifications == ["1"]


@pytest.mark.asyncio
async def test_postgres_store_updates_checked_at_for_same_hash() -> None:
    pool = FakePostgresPool()
    store = PostgresStore(pool=pool)
    first = resource_write(resource_key=ResourceKey.EVENT_STATUS, payload_hash="a" * 64)
    await store.upsert_resource(resource=first)
    second = first.model_copy(
        update={"checked_at": datetime(2026, 6, 21, tzinfo=UTC)},
    )
    outcome = await store.upsert_resource(resource=second)
    assert outcome.changed is False
    assert len(pool.events) == 1
    assert pool.resources["event-status"]["checked_at"] == second.checked_at


@pytest.mark.asyncio
async def test_postgres_store_advisory_lock_rejects_overlap() -> None:
    pool = FakePostgresPool()
    store = PostgresStore(pool=pool)
    async with store.ingestion_lock():
        with pytest.raises(IngestionLockError):
            async with store.ingestion_lock():
                pass
    assert pool.locked is False


@pytest.mark.asyncio
async def test_postgres_store_watch_replays_then_heartbeats() -> None:
    pool = FakePostgresPool()
    store = PostgresStore(pool=pool)
    resource = resource_write(
        resource_key=ResourceKey.EVENT_LIVE,
        payload_hash="a" * 64,
    )
    await store.upsert_resource(resource=resource)
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
