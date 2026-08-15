"""PostgreSQL persistence for short-lived structured extraction cache rows."""

import json
from collections.abc import Mapping
from datetime import datetime
from typing import cast

from fpl_data_relay.adapters.outbound.postgres.database import PostgresDatabase
from fpl_data_relay.domain.community import (
    ExtractionCacheEntry,
    ExtractionCacheEntryDraft,
    ExtractionCacheLookup,
    SourceDocumentMetadata,
    SourceType,
    TopicMentionBatch,
)

CACHE_COLUMNS = """
    id,
    strategy_key,
    strategy_version,
    source_key,
    source_type,
    document_id,
    external_id,
    content_revision,
    extraction_contract_hash,
    document,
    topics,
    published_at,
    expires_at,
    created_at
"""


class PostgresCommunityExtractionCacheRepository:
    """Read exact revisions, insert immutable results, and prune expired rows."""

    def __init__(self, *, database: PostgresDatabase) -> None:
        self._database = database

    async def prune_expired(self, *, as_of: datetime) -> int:
        query = """
            WITH deleted AS (
                DELETE FROM relay_community_extraction_cache
                WHERE expires_at <= $1
                RETURNING 1
            )
            SELECT count(*)::bigint FROM deleted
        """
        async with self._database.pool.acquire() as connection:
            value = await connection.fetchval(query, as_of)
        return _int(value)

    async def get_entries(
        self,
        *,
        strategy_key: str,
        strategy_version: int,
        extraction_contract_hash: str,
        lookups: list[ExtractionCacheLookup],
        as_of: datetime,
    ) -> list[ExtractionCacheEntry]:
        if not lookups:
            return []
        query = f"""
            WITH requested AS (
                SELECT *
                FROM jsonb_to_recordset($5::jsonb) AS item(
                    source_key text,
                    document_id text,
                    content_revision text
                )
            )
            SELECT {CACHE_COLUMNS}
            FROM relay_community_extraction_cache AS cache
            JOIN requested USING (source_key, document_id, content_revision)
            WHERE cache.strategy_key = $1
              AND cache.strategy_version = $2
              AND cache.extraction_contract_hash = $3
              AND cache.expires_at > $4
            ORDER BY cache.id
        """
        payload = json.dumps(
            [item.model_dump(mode="json") for item in lookups],
            separators=(",", ":"),
            sort_keys=True,
        )
        async with self._database.pool.acquire() as connection:
            rows = await connection.fetch(
                query,
                strategy_key,
                strategy_version,
                extraction_contract_hash,
                as_of,
                payload,
            )
        return [_entry_from_row(row) for row in rows]

    async def insert_entries(
        self,
        *,
        entries: list[ExtractionCacheEntryDraft],
    ) -> int:
        if not entries:
            return 0
        query = """
            WITH supplied AS (
                SELECT *
                FROM jsonb_to_recordset($1::jsonb) AS item(
                    strategy_key text,
                    strategy_version integer,
                    source_key text,
                    source_type text,
                    document_id text,
                    external_id text,
                    content_revision text,
                    extraction_contract_hash text,
                    document jsonb,
                    topics jsonb,
                    published_at timestamptz,
                    expires_at timestamptz
                )
            ), inserted AS (
                INSERT INTO relay_community_extraction_cache (
                    strategy_key,
                    strategy_version,
                    source_key,
                    source_type,
                    document_id,
                    external_id,
                    content_revision,
                    extraction_contract_hash,
                    document,
                    topics,
                    published_at,
                    expires_at
                )
                SELECT
                    strategy_key,
                    strategy_version,
                    source_key,
                    source_type,
                    document_id,
                    external_id,
                    content_revision,
                    extraction_contract_hash,
                    document,
                    topics,
                    published_at,
                    expires_at
                FROM supplied
                ON CONFLICT (
                    strategy_key,
                    strategy_version,
                    source_key,
                    document_id,
                    content_revision,
                    extraction_contract_hash
                ) DO NOTHING
                RETURNING 1
            )
            SELECT count(*)::bigint FROM inserted
        """
        payload = json.dumps(
            [entry.model_dump(mode="json") for entry in entries],
            separators=(",", ":"),
            sort_keys=True,
        )
        async with self._database.pool.acquire() as connection:
            value = await connection.fetchval(query, payload)
        return _int(value)


def _entry_from_row(row: object) -> ExtractionCacheEntry:
    item = _mapping(row)
    return ExtractionCacheEntry(
        id=_int(item["id"]),
        strategy_key=str(item["strategy_key"]),
        strategy_version=_int(item["strategy_version"]),
        source_key=str(item["source_key"]),
        source_type=SourceType(str(item["source_type"])),
        document_id=str(item["document_id"]),
        external_id=str(item["external_id"]),
        content_revision=str(item["content_revision"]),
        extraction_contract_hash=str(item["extraction_contract_hash"]),
        document=_document(item["document"]),
        topics=_topics(item["topics"]),
        published_at=_datetime(item["published_at"]),
        expires_at=_datetime(item["expires_at"]),
        created_at=_datetime(item["created_at"]),
    )


def _mapping(row: object) -> Mapping[str, object]:
    return cast("Mapping[str, object]", row)


def _document(value: object) -> SourceDocumentMetadata:
    if isinstance(value, str):
        return SourceDocumentMetadata.model_validate_json(value)
    return SourceDocumentMetadata.model_validate(value)


def _topics(value: object) -> TopicMentionBatch:
    if isinstance(value, str):
        return TopicMentionBatch.model_validate_json(value)
    return TopicMentionBatch.model_validate(value)


def _datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise TypeError(f"Expected datetime, received {type(value).__name__}.")


def _int(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise TypeError(f"Expected integer, received {type(value).__name__}.")
