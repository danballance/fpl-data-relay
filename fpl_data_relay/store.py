import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict, field_validator

from fpl_data_relay.hashing import parse_json_payload
from fpl_data_relay.json_types import JsonValue
from fpl_data_relay.resources import ResourceKey
from fpl_data_relay.schemas import ADVISORY_LOCK_ID, NOTIFY_CHANNEL, SCHEMA_SQL


class SchemaError(RuntimeError):
    pass


class IngestionLockError(RuntimeError):
    pass


class ResourceWrite(BaseModel):
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
    def payload_hash_must_be_sha256(cls, value: str) -> str:
        if len(value) != 64:
            raise ValueError("payload_hash must be a SHA-256 hex digest.")
        return value


class StoredResource(BaseModel):
    model_config = ConfigDict(frozen=True)

    resource_key: ResourceKey
    event_id: int | None
    payload: JsonValue
    payload_hash: str
    fetched_at: datetime
    checked_at: datetime


class ChangeEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    resource_key: ResourceKey
    event_name: str
    event_id: int | None
    payload_hash: str
    fetched_at: datetime
    created_at: datetime

    def to_public_dict(self) -> dict[str, int | str | None]:
        return {
            "id": self.id,
            "resource_key": self.resource_key.value,
            "event_name": self.event_name,
            "event_id": self.event_id,
            "payload_hash": self.payload_hash,
            "fetched_at": self.fetched_at.isoformat(),
            "created_at": self.created_at.isoformat(),
        }


class UpsertOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    changed: bool
    change_event: ChangeEvent | None


class ConnectionProtocol(Protocol):
    def transaction(self) -> AbstractAsyncContextManager[object]: ...

    async def execute(self, query: str, *arguments: object) -> str: ...

    async def fetchrow(self, query: str, *arguments: object) -> object: ...

    async def fetch(self, query: str, *arguments: object) -> list[object]: ...

    async def fetchval(self, query: str, *arguments: object) -> object: ...

    async def add_listener(self, channel: str, callback: object) -> None: ...

    async def remove_listener(self, channel: str, callback: object) -> None: ...


class ConnectionManagerProtocol(Protocol):
    async def __aenter__(self) -> ConnectionProtocol: ...

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None: ...


class PoolProtocol(Protocol):
    def acquire(self) -> ConnectionManagerProtocol: ...

    async def close(self) -> None: ...


class ResourceStore(Protocol):
    async def apply_schema(self) -> None: ...

    async def check_schema_version(self, *, expected_version: int) -> None: ...

    async def get_resource(
        self,
        *,
        resource_key: ResourceKey,
    ) -> StoredResource | None: ...

    async def list_change_events(
        self,
        *,
        after_id: int,
        limit: int,
    ) -> list[ChangeEvent]: ...

    async def upsert_resource(self, *, resource: ResourceWrite) -> UpsertOutcome: ...

    def ingestion_lock(self) -> AbstractAsyncContextManager[None]: ...

    def watch_change_events(
        self,
        *,
        after_id: int,
        heartbeat_seconds: int,
    ) -> AsyncIterator[ChangeEvent | None]: ...

    async def close(self) -> None: ...


class PostgresStore:
    def __init__(self, *, pool: PoolProtocol) -> None:
        self._pool = pool

    async def close(self) -> None:
        await self._pool.close()

    async def apply_schema(self) -> None:
        async with self._pool.acquire() as connection:
            await connection.execute(SCHEMA_SQL)

    async def check_schema_version(self, *, expected_version: int) -> None:
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

    async def get_resource(
        self,
        *,
        resource_key: ResourceKey,
    ) -> StoredResource | None:
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
        if row is None:
            return None
        return stored_resource_from_row(row=row)

    async def list_change_events(
        self,
        *,
        after_id: int,
        limit: int,
    ) -> list[ChangeEvent]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT id, resource_key, event_name, event_id, payload_hash,
                       fetched_at, created_at
                FROM relay_change_events
                WHERE id > $1
                ORDER BY id ASC
                LIMIT $2
                """,
                after_id,
                limit,
            )
        return [change_event_from_row(row=row) for row in rows]

    async def upsert_resource(self, *, resource: ResourceWrite) -> UpsertOutcome:
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
                return UpsertOutcome(changed=False, change_event=None)

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
            change_event = change_event_from_row(row=row)
            await connection.fetchval(
                "SELECT pg_notify($1, $2)",
                NOTIFY_CHANNEL,
                str(change_event.id),
            )
            return UpsertOutcome(changed=True, change_event=change_event)

    @contextlib.asynccontextmanager
    async def ingestion_lock(self) -> AsyncIterator[None]:
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
        current_id = after_id
        queue: asyncio.Queue[int] = asyncio.Queue()

        def listener(
            connection: object,
            process_id: int,
            channel: str,
            payload: str,
        ) -> None:
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


def stored_resource_from_row(*, row: object) -> StoredResource:
    values = row_values(row=row)
    return StoredResource(
        resource_key=ResourceKey(require_str(values=values, key="resource_key")),
        event_id=require_optional_int(values=values, key="event_id"),
        payload=parse_json_payload(payload=require_str(values=values, key="payload")),
        payload_hash=require_str(values=values, key="payload_hash"),
        fetched_at=require_datetime(values=values, key="fetched_at"),
        checked_at=require_datetime(values=values, key="checked_at"),
    )


def change_event_from_row(*, row: object) -> ChangeEvent:
    values = row_values(row=row)
    return ChangeEvent(
        id=require_int(values=values, key="id"),
        resource_key=ResourceKey(require_str(values=values, key="resource_key")),
        event_name=require_str(values=values, key="event_name"),
        event_id=require_optional_int(values=values, key="event_id"),
        payload_hash=require_str(values=values, key="payload_hash"),
        fetched_at=require_datetime(values=values, key="fetched_at"),
        created_at=require_datetime(values=values, key="created_at"),
    )


def row_values(*, row: object) -> dict[str, object]:
    if isinstance(row, Mapping):
        mapping = cast("Mapping[str, object]", row)
        return {key: mapping[key] for key in mapping}
    raise TypeError(f"Unsupported database row type: {type(row).__name__}")


def require_str(*, values: dict[str, object], key: str) -> str:
    value = values[key]
    if isinstance(value, str):
        return value
    raise TypeError(f"Expected {key} to be str, found {type(value).__name__}.")


def require_int(*, values: dict[str, object], key: str) -> int:
    value = values[key]
    if isinstance(value, int):
        return value
    raise TypeError(f"Expected {key} to be int, found {type(value).__name__}.")


def require_optional_int(*, values: dict[str, object], key: str) -> int | None:
    value = values[key]
    if value is None or isinstance(value, int):
        return value
    raise TypeError(f"Expected {key} to be int or None, found {type(value).__name__}.")


def require_datetime(*, values: dict[str, object], key: str) -> datetime:
    value = values[key]
    if isinstance(value, datetime):
        return value
    raise TypeError(f"Expected {key} to be datetime, found {type(value).__name__}.")
