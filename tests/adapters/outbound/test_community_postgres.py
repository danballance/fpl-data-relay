import json
from contextlib import AbstractAsyncContextManager
from datetime import UTC, date, datetime, timedelta
from typing import cast

import pytest
from pydantic import HttpUrl

from fpl_data_relay.adapters.outbound.postgres.community import (
    PostgresCommunityReportRepository,
    _content,
    _date,
    _datetime,
)
from fpl_data_relay.adapters.outbound.postgres.database import (
    ConnectionProtocol,
    PoolProtocol,
    PostgresDatabase,
)
from fpl_data_relay.domain.community import (
    BlogEngagement,
    CollectionCoverage,
    CommunityReportContent,
    CommunityReportDraft,
    CommunityStory,
    EntityType,
    EvidenceReference,
    ExtractionCacheUsage,
    ModelUsage,
    MomentumComponents,
    SourceType,
    TeamReference,
    TeamSnapshot,
    TopicCategory,
)

NOW = datetime(2026, 8, 13, 6, tzinfo=UTC)


def content() -> CommunityReportContent:
    return CommunityReportContent(
        strategy_name="Weekly",
        strategy_description="Topics",
        ranking_policy="community_momentum_v1",
        extraction_prompt_version=1,
        synthesis_prompt_version=1,
        target_story_count=10,
        coverage=CollectionCoverage(
            configured_source_count=1,
            successful_source_count=1,
            failed_sources=[],
            excluded_document_count=0,
            exclusions=[],
            x_document_count=0,
            youtube_document_count=0,
            blog_document_count=1,
        ),
        extraction_cache=ExtractionCacheUsage(
            eligible_document_count=1,
            hit_count=0,
            miss_count=1,
            write_count=1,
            expired_entry_count=0,
        ),
        model_usage=ModelUsage(
            provider="openai",
            model="gpt-5.6-sol",
            reasoning_effort="medium",
            response_ids=["resp"],
            input_tokens=10,
            output_tokens=5,
        ),
        stories=[
            CommunityStory(
                rank=1,
                headline="Team discussion",
                summary="The community discussed Team.",
                category=TopicCategory.TEAM,
                momentum_score=20,
                momentum_components=MomentumComponents(
                    source_breadth=7,
                    evidence_volume=2,
                    engagement=0,
                    recency=5,
                    actionability=6,
                ),
                evidence=[
                    EvidenceReference(
                        document_id="blog:1",
                        source_key="blog",
                        source_type=SourceType.BLOG,
                        publisher="Blog",
                        title="Article",
                        url=HttpUrl("https://example.com/article"),
                        published_at=NOW,
                        engagement=BlogEngagement(type=SourceType.BLOG),
                    ),
                ],
                entities=[
                    TeamReference(
                        entity_type=EntityType.TEAM,
                        season_id="2026-27",
                        entity_id=1,
                        display_name="Team",
                        snapshot=TeamSnapshot(
                            name="Team",
                            short_name="TST",
                            strength=3,
                            strength_overall_home=1100,
                            strength_overall_away=1000,
                            strength_attack_home=1000,
                            strength_attack_away=900,
                            strength_defence_home=1000,
                            strength_defence_away=900,
                        ),
                    ),
                ],
            ),
        ],
    )


def draft() -> CommunityReportDraft:
    return CommunityReportDraft(
        strategy_key="weekly-community-momentum-v1",
        strategy_version=1,
        report_date=date(2026, 8, 13),
        season_id="2026-27",
        as_of_event_id=1,
        window_start=NOW - timedelta(days=7),
        window_end=NOW,
        generated_at=NOW,
        content=content(),
    )


class Connection:
    def __init__(self) -> None:
        self.row: dict[str, object] | None = None
        self.rows: list[object] = []
        self.insert_conflict = False

    def transaction(self) -> AbstractAsyncContextManager[object]:
        raise AssertionError("Community reads do not open a transaction.")

    async def execute(self, query: str, *arguments: object) -> str:
        del query, arguments
        return "OK"

    async def fetchrow(self, query: str, *arguments: object) -> object:
        del arguments
        if "INSERT INTO" in query and self.insert_conflict:
            return None
        return self.row

    async def fetch(self, query: str, *arguments: object) -> list[object]:
        del query, arguments
        return self.rows

    async def fetchval(self, query: str, *arguments: object) -> object:
        del query, arguments
        return None


class Acquire(AbstractAsyncContextManager[ConnectionProtocol]):
    def __init__(self, *, connection: Connection) -> None:
        self.connection = connection

    async def __aenter__(self) -> ConnectionProtocol:
        return cast("ConnectionProtocol", self.connection)

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback


class Pool:
    def __init__(self, *, connection: Connection) -> None:
        self.connection = connection

    def acquire(self) -> Acquire:
        return Acquire(connection=self.connection)

    async def close(self) -> None:
        return None


def row(*, json_string: bool = False) -> dict[str, object]:
    report = draft()
    payload: object = report.content.model_dump(mode="json")
    if json_string:
        payload = json.dumps(payload)
    return {
        "id": 7,
        "strategy_key": report.strategy_key,
        "strategy_version": report.strategy_version,
        "report_date": report.report_date.isoformat(),
        "season_id": report.season_id,
        "as_of_event_id": report.as_of_event_id,
        "window_start": report.window_start.isoformat(),
        "window_end": report.window_end.isoformat(),
        "generated_at": report.generated_at.isoformat(),
        "content": payload,
        "story_count": 1,
        "successful_source_count": 1,
        "failed_source_count": 0,
    }


@pytest.mark.asyncio
async def test_report_repository_inserts_reads_and_pages_both_executors() -> None:
    connection = Connection()
    connection.row = row(json_string=True)
    database = PostgresDatabase(
        pool=cast("PoolProtocol", Pool(connection=connection)),
    )
    repository = PostgresCommunityReportRepository(database=database)
    inserted = await repository.insert_report(report=draft())
    assert inserted.id == 7
    assert await repository.get_report(report_id=7) == inserted
    assert await repository.get_latest_report(
        strategy_key=inserted.strategy_key,
    ) == inserted
    assert await repository.get_report_for_date(
        strategy_key=inserted.strategy_key,
        report_date=inserted.report_date,
    ) == inserted
    connection.rows = [row()]
    recent = await repository.list_recent_reports(
        strategy_key=inserted.strategy_key,
        limit=10,
    )
    history = await repository.list_reports_before(
        strategy_key=inserted.strategy_key,
        before_id=8,
        limit=10,
    )
    assert recent == history
    assert recent[0].story_count == 1


@pytest.mark.asyncio
async def test_report_repository_returns_conflict_and_detects_impossible_state(
) -> None:
    connection = Connection()
    connection.row = row()
    connection.insert_conflict = True
    database = PostgresDatabase(
        pool=cast("PoolProtocol", Pool(connection=connection)),
    )
    repository = PostgresCommunityReportRepository(database=database)
    assert (await repository.insert_report(report=draft())).id == 7
    connection.row = None
    assert await repository.get_report(report_id=999) is None
    with pytest.raises(RuntimeError, match="conflicted"):
        await repository.insert_report(report=draft())


def test_repository_value_decoders_fail_fast() -> None:
    assert _date(NOW) == NOW.date()
    assert _date(NOW.date()) == NOW.date()
    assert _datetime(NOW) == NOW
    assert _content(content().model_dump(mode="json")) == content()
    with pytest.raises(TypeError, match="date value"):
        _date(1)
    with pytest.raises(TypeError, match="datetime value"):
        _datetime(1)
