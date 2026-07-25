from collections.abc import Callable
from typing import cast

import pytest
from asyncpg import Record
from asyncpg.protocol import protocol

from fpl_data_relay.adapters.outbound.postgres.database import (
    IngestionLockError,
    PostgresDatabase,
    row_values,
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
