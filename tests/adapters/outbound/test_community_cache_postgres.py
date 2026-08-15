import json
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from pydantic import HttpUrl, ValidationError

from fpl_data_relay.adapters.outbound.postgres.community_cache import (
    PostgresCommunityExtractionCacheRepository,
    _datetime,
    _entry_from_row,
    _int,
)
from fpl_data_relay.adapters.outbound.postgres.database import (
    ConnectionProtocol,
    PoolProtocol,
    PostgresDatabase,
)
from fpl_data_relay.domain.community import (
    Actionability,
    ExtractionCacheEntryDraft,
    ExtractionCacheLookup,
    SourceDocumentMetadata,
    SourceType,
    TopicCategory,
    TopicMention,
    TopicMentionBatch,
    XEngagement,
)

NOW = datetime(2026, 8, 13, 6, tzinfo=UTC)


def draft() -> ExtractionCacheEntryDraft:
    document = SourceDocumentMetadata(
        document_id="x:1",
        source_key="x-source",
        source_type=SourceType.X,
        external_id="1",
        publisher="Expert",
        title="Post",
        url=HttpUrl("https://x.com/expert/status/1"),
        published_at=NOW - timedelta(days=1),
        engagement=XEngagement(
            type=SourceType.X,
            likes=2,
            replies=1,
            reposts=0,
            quotes=0,
        ),
    )
    return ExtractionCacheEntryDraft(
        strategy_key="weekly-community-momentum-v1",
        strategy_version=1,
        source_key=document.source_key,
        source_type=document.source_type,
        document_id=document.document_id,
        external_id=document.external_id,
        content_revision="a" * 64,
        extraction_contract_hash="b" * 64,
        document=document,
        topics=TopicMentionBatch(
            topics=[
                TopicMention(
                    claim="Player is discussed.",
                    category=TopicCategory.TRANSFER_IN,
                    actionability=Actionability.HIGH,
                    mentioned_entities=["Player"],
                    document_ids=[document.document_id],
                ),
            ],
        ),
        published_at=document.published_at,
        expires_at=document.published_at + timedelta(days=8),
    )


class Connection:
    def __init__(self) -> None:
        self.rows: list[object] = []
        self.values: list[object] = []
        self.queries: list[str] = []

    def transaction(self) -> AbstractAsyncContextManager[object]:
        raise AssertionError("Cache operations use single statements.")

    async def execute(self, query: str, *arguments: object) -> str:
        del query, arguments
        return "OK"

    async def fetchrow(self, query: str, *arguments: object) -> object:
        del query, arguments
        return None

    async def fetch(self, query: str, *arguments: object) -> list[object]:
        del arguments
        self.queries.append(query)
        return self.rows

    async def fetchval(self, query: str, *arguments: object) -> object:
        del arguments
        self.queries.append(query)
        return self.values.pop(0)


class Acquire(AbstractAsyncContextManager[ConnectionProtocol]):
    def __init__(self, *, connection: Connection) -> None:
        self._connection = connection

    async def __aenter__(self) -> ConnectionProtocol:
        return cast("ConnectionProtocol", self._connection)

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback


class Pool:
    def __init__(self, *, connection: Connection) -> None:
        self._connection = connection

    def acquire(self) -> Acquire:
        return Acquire(connection=self._connection)

    async def close(self) -> None:
        return None


def row(*, json_strings: bool) -> dict[str, object]:
    item = draft()
    document: object = item.document.model_dump(mode="json")
    topics: object = item.topics.model_dump(mode="json")
    if json_strings:
        document = json.dumps(document)
        topics = json.dumps(topics)
    return {
        "id": 4,
        **item.model_dump(exclude={"document", "topics"}),
        "document": document,
        "topics": topics,
        "created_at": NOW,
    }


@pytest.mark.asyncio
async def test_cache_repository_prunes_reads_and_inserts_batches() -> None:
    connection = Connection()
    connection.values = [2, 1]
    connection.rows = [row(json_strings=True)]
    repository = PostgresCommunityExtractionCacheRepository(
        database=PostgresDatabase(
            pool=cast("PoolProtocol", Pool(connection=connection)),
        ),
    )

    assert await repository.prune_expired(as_of=NOW) == 2
    entries = await repository.get_entries(
        strategy_key=draft().strategy_key,
        strategy_version=draft().strategy_version,
        extraction_contract_hash=draft().extraction_contract_hash,
        lookups=[
            ExtractionCacheLookup(
                source_key=draft().source_key,
                document_id=draft().document_id,
                content_revision=draft().content_revision,
            ),
        ],
        as_of=NOW,
    )
    assert entries[0].topics == draft().topics
    assert await repository.insert_entries(entries=[draft()]) == 1
    assert any("jsonb_to_recordset" in query for query in connection.queries)
    assert await repository.get_entries(
        strategy_key=draft().strategy_key,
        strategy_version=1,
        extraction_contract_hash=draft().extraction_contract_hash,
        lookups=[],
        as_of=NOW,
    ) == []
    assert await repository.insert_entries(entries=[]) == 0


def test_cache_decoders_fail_fast() -> None:
    assert _datetime(NOW) == NOW
    assert _datetime(NOW.isoformat()) == NOW
    assert _int("2") == 2
    with pytest.raises(TypeError, match="datetime"):
        _datetime(1)
    with pytest.raises(TypeError, match="integer"):
        _int(None)

    corrupt = row(json_strings=False)
    corrupt_document = cast("dict[str, object]", corrupt["document"])
    corrupt_document["text"] = "Source bodies are forbidden."
    with pytest.raises(ValidationError, match="text"):
        _entry_from_row(corrupt)
