from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import HttpUrl, ValidationError

from fpl_data_relay.application.community_jobs import (
    CommunityStrategyJob,
    build_strategy_jobs,
)
from fpl_data_relay.application.community_queries import CommunityQueries
from fpl_data_relay.application.community_ranking import (
    CommunityMomentumRankingPolicy,
    engagement_percentiles,
)
from fpl_data_relay.application.community_service import CommunityService
from fpl_data_relay.application.community_strategies import (
    CommunityStrategyRegistry,
    ConfiguredCommunityStrategy,
    load_strategy_registry,
)
from fpl_data_relay.application.errors import (
    CommunityModelError,
    CommunityPublicationError,
    CommunitySourceError,
)
from fpl_data_relay.application.ingestion.service import IngestionService
from fpl_data_relay.domain.community import (
    Actionability,
    AgentAnalysisRequest,
    AgentAnalysisResult,
    BlogEngagement,
    CandidateStory,
    CandidateStoryBatch,
    CommunityReport,
    CommunityReportDraft,
    CommunityReportSummary,
    CommunitySource,
    CommunityStrategyDefinition,
    EntityConfidence,
    EntityLinkCandidate,
    EntityType,
    ModelUsage,
    SourceCollectionResult,
    SourceDocument,
    SourceType,
    TopicCategory,
    XEngagement,
    XSource,
)
from tests.conftest import FakeClient, InMemoryStore

NOW = datetime(2026, 8, 13, 6, tzinfo=UTC)


def source(*, key: str = "source-one") -> XSource:
    return XSource(
        type=SourceType.X,
        key=key,
        label=key,
        user_id="123",
        username=key,
        include_replies=False,
        include_reposts=False,
        max_documents=100,
        timeout_seconds=10.0,
    )


def strategy(
    *,
    sources: list[CommunitySource],
    active: bool = True,
) -> ConfiguredCommunityStrategy:
    definition = CommunityStrategyDefinition(
        key="weekly-community-momentum-v1",
        version=1,
        active=active,
        name="Weekly momentum",
        description="Community topics",
        schedule_expression="cron(0 6 * * ? *)",
        schedule_timezone="Europe/London",
        lookback_days=7,
        target_story_count=10,
        minimum_story_count=1,
        maximum_candidate_stories=30,
        ranking_policy="community_momentum_v1",
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        extraction_prompt_version=1,
        synthesis_prompt_version=1,
        extraction_concurrency=4,
        chunk_characters=24000,
        sources=sources,
    )
    return ConfiguredCommunityStrategy(definition=definition)


def document(
    *,
    document_id: str = "x:1",
    source_key: str = "source-one",
    published_at: datetime = NOW - timedelta(hours=1),
    likes: int = 10,
) -> SourceDocument:
    return SourceDocument(
        document_id=document_id,
        source_key=source_key,
        source_type=SourceType.X,
        external_id=document_id,
        publisher=source_key,
        title="FPL post",
        url=HttpUrl(f"https://x.com/example/status/{document_id.removeprefix('x:')}"),
        published_at=published_at,
        text="Player is a popular transfer.",
        engagement=XEngagement(
            type=SourceType.X,
            likes=likes,
            replies=0,
            reposts=0,
            quotes=0,
        ),
    )


def candidate(
    *,
    headline: str = "Buy Player",
    evidence: list[str] | None = None,
    entity_id: int = 1,
    confidence: EntityConfidence = EntityConfidence.HIGH,
) -> CandidateStory:
    return CandidateStory(
        headline=headline,
        summary="The community is discussing Player as a transfer.",
        category=TopicCategory.TRANSFER_IN,
        actionability=Actionability.HIGH,
        evidence_document_ids=evidence or ["x:1"],
        entity_links=[
            EntityLinkCandidate(
                entity_type=EntityType.PLAYER,
                entity_id=entity_id,
                confidence=confidence,
            ),
        ],
    )


def analysis(*, story: CandidateStory | None = None) -> AgentAnalysisResult:
    return AgentAnalysisResult(
        candidates=CandidateStoryBatch(stories=[] if story is None else [story]),
        usage=ModelUsage(
            provider="openai",
            model="gpt-5.6-sol",
            reasoning_effort="medium",
            response_ids=["resp_1"],
            input_tokens=100,
            output_tokens=20,
        ),
    )


class FakeGateway:
    def __init__(self, *, documents: list[SourceDocument]) -> None:
        self.documents = documents
        self.failure_keys: set[str] = set()
        self.fatal_keys: set[str] = set()

    async def collect(
        self,
        *,
        source: CommunitySource,
        window_start: datetime,
        window_end: datetime,
    ) -> SourceCollectionResult:
        del window_start, window_end
        if source.key in self.fatal_keys:
            raise CommunitySourceError(
                code="x_authentication",
                fatal=True,
                detail="fatal",
            )
        if source.key in self.failure_keys:
            raise CommunitySourceError(
                code="x_fetch",
                fatal=False,
                detail="failed",
            )
        return SourceCollectionResult(
            source_key=source.key,
            documents=[
                item for item in self.documents if item.source_key == source.key
            ],
            excluded_document_count=0,
            exclusions=[],
        )

    async def close(self) -> None:
        return None


class FakeAnalyzer:
    def __init__(self, *, result: AgentAnalysisResult) -> None:
        self.result = result
        self.requests: list[AgentAnalysisRequest] = []

    async def analyze(self, *, request: AgentAnalysisRequest) -> AgentAnalysisResult:
        self.requests.append(request)
        return self.result


class MemoryReports:
    def __init__(self) -> None:
        self.reports: list[CommunityReport] = []

    async def insert_report(self, *, report: CommunityReportDraft) -> CommunityReport:
        stored = CommunityReport(id=len(self.reports) + 1, **report.model_dump())
        self.reports.append(stored)
        return stored

    async def get_report(self, *, report_id: int) -> CommunityReport | None:
        return next((item for item in self.reports if item.id == report_id), None)

    async def get_latest_report(self, *, strategy_key: str) -> CommunityReport | None:
        matches = [item for item in self.reports if item.strategy_key == strategy_key]
        return None if not matches else matches[-1]

    async def get_report_for_date(
        self,
        *,
        strategy_key: str,
        report_date: date,
    ) -> CommunityReport | None:
        return next(
            (
                item
                for item in self.reports
                if item.strategy_key == strategy_key and item.report_date == report_date
            ),
            None,
        )

    async def list_recent_reports(
        self,
        *,
        strategy_key: str,
        limit: int,
    ) -> list[CommunityReportSummary]:
        del strategy_key, limit
        return []

    async def list_reports_before(
        self,
        *,
        strategy_key: str,
        before_id: int,
        limit: int,
    ) -> list[CommunityReportSummary]:
        del strategy_key, before_id, limit
        return []


def test_strategy_manifest_is_versioned_disabled_and_fail_fast() -> None:
    registry = load_strategy_registry()
    definition = registry.require(
        strategy_key="weekly-community-momentum-v1",
    ).definition
    assert definition.active is False
    assert definition.model == "gpt-5.6-sol"
    assert registry.list_active() == []
    assert registry.get(strategy_key="missing") is None
    with pytest.raises(ValueError, match="Unknown"):
        registry.require(strategy_key="missing")
    with pytest.raises(ValueError, match="unique"):
        CommunityStrategyRegistry(
            strategies=[strategy(sources=[source()]), strategy(sources=[source()])],
        )


def test_strategy_and_job_contracts_reject_ambiguous_values() -> None:
    with pytest.raises(ValidationError, match="active strategy"):
        strategy(sources=[])
    with pytest.raises(ValidationError, match="window_end"):
        CommunityStrategyJob(
            version=1,
            kind="community_strategy",
            strategy_key="strategy",
            strategy_version=1,
            report_date=date(2026, 8, 13),
            window_start=NOW,
            window_end=NOW,
        )
    definition = strategy(sources=[source()]).definition
    with pytest.raises(ValidationError, match="IANA timezone"):
        CommunityStrategyDefinition.model_validate(
            {
                **definition.model_dump(),
                "schedule_timezone": "Not/A_Timezone",
            },
        )


def test_dispatch_builds_london_date_and_exact_seven_day_window() -> None:
    registry = CommunityStrategyRegistry(strategies=[strategy(sources=[source()])])
    scheduled = datetime(2026, 10, 25, 6, tzinfo=UTC)
    jobs = build_strategy_jobs(registry=registry, scheduled_at=scheduled)
    assert len(jobs) == 1
    assert jobs[0].report_date == date(2026, 10, 25)
    assert jobs[0].window_end - jobs[0].window_start == timedelta(days=7)
    with pytest.raises(ValueError, match="timezone-aware"):
        build_strategy_jobs(
            registry=registry,
            scheduled_at=datetime(2026, 1, 1),
        )


@pytest.mark.parametrize(
    ("scheduled_at", "expected_date"),
    [
        (datetime(2026, 1, 5, 6, tzinfo=UTC), date(2026, 1, 5)),
        (datetime(2026, 7, 5, 5, tzinfo=UTC), date(2026, 7, 5)),
    ],
)
def test_strategy_jobs_respect_london_winter_and_summer_time(
    scheduled_at: datetime,
    expected_date: date,
) -> None:
    registry = CommunityStrategyRegistry(
        strategies=[strategy(sources=[source()])],
    )
    job = build_strategy_jobs(
        registry=registry,
        scheduled_at=scheduled_at,
    )[0]
    assert job.report_date == expected_date
    assert job.window_end - job.window_start == timedelta(days=7)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"strategy_version": 2}, "version mismatch"),
        ({"report_date": date(2026, 8, 12)}, "report_date"),
        ({"window_start": NOW - timedelta(days=6)}, "lookback"),
    ],
)
async def test_service_rejects_stale_or_tampered_strategy_jobs(
    update: dict[str, object],
    message: str,
) -> None:
    registry = CommunityStrategyRegistry(
        strategies=[strategy(sources=[source()])],
    )
    service = CommunityService(
        registry=registry,
        source_gateway=FakeGateway(documents=[]),
        analyzer=FakeAnalyzer(result=analysis()),
        ranking_policy=CommunityMomentumRankingPolicy(),
        reports=MemoryReports(),
        references=InMemoryStore(),
        clock=lambda: NOW,
    )
    job = build_strategy_jobs(registry=registry, scheduled_at=NOW)[0].model_copy(
        update=update,
    )
    with pytest.raises(ValueError, match=message):
        await service.run(job=job)


def test_momentum_ranking_uses_weights_percentiles_and_deterministic_ties() -> None:
    documents = {
        "x:1": document(likes=10),
        "x:2": document(
            document_id="x:2",
            source_key="source-two",
            published_at=NOW - timedelta(days=7),
            likes=20,
        ),
        "blog:1": SourceDocument(
            document_id="blog:1",
            source_key="blog",
            source_type=SourceType.BLOG,
            external_id="article",
            publisher="Blog",
            title="Article",
            url=HttpUrl("https://example.com/article"),
            published_at=NOW - timedelta(days=3),
            text="Player",
            engagement=BlogEngagement(type=SourceType.BLOG),
        ),
    }
    assert engagement_percentiles(documents=documents) == {"x:1": 0.5, "x:2": 1.0}
    broad = candidate(headline="Broad", evidence=list(documents))
    tied = candidate(headline="Zulu")
    ranked = CommunityMomentumRankingPolicy().rank(
        candidates=[tied, candidate(headline="Alpha"), broad],
        documents=documents,
        window_start=NOW - timedelta(days=7),
        window_end=NOW,
        limit=3,
    )
    assert ranked[0].candidate.headline == "Broad"
    assert ranked[0].components.source_breadth == 21.0
    assert ranked[0].components.evidence_volume == 6.0
    assert ranked[1].candidate.headline == "Alpha"
    with pytest.raises(ValueError, match="unknown document"):
        CommunityMomentumRankingPolicy().rank(
            candidates=[candidate(evidence=["missing"])],
            documents=documents,
            window_start=NOW - timedelta(days=7),
            window_end=NOW,
            limit=1,
        )


@pytest.mark.asyncio
async def test_service_publishes_typed_snapshots_and_is_idempotent() -> None:
    store = InMemoryStore()
    await IngestionService(
        client=FakeClient(),
        repository=store,
    ).ingest_reference_once()
    reports = MemoryReports()
    gateway = FakeGateway(documents=[document()])
    story = candidate().model_copy(
        update={
            "entity_links": [
                EntityLinkCandidate(
                    entity_type=entity_type,
                    entity_id=1,
                    confidence=EntityConfidence.HIGH,
                )
                for entity_type in EntityType
            ],
        },
    )
    analyzer = FakeAnalyzer(result=analysis(story=story))
    registry = CommunityStrategyRegistry(strategies=[strategy(sources=[source()])])
    service = CommunityService(
        registry=registry,
        source_gateway=gateway,
        analyzer=analyzer,
        ranking_policy=CommunityMomentumRankingPolicy(),
        reports=reports,
        references=store,
        clock=lambda: NOW,
    )
    job = build_strategy_jobs(registry=registry, scheduled_at=NOW)[0]
    report = await service.run(job=job)
    duplicate = await service.run(job=job)
    assert duplicate.id == report.id
    assert len(analyzer.requests) == 1
    assert {item.entity_type for item in report.content.stories[0].entities} == set(
        EntityType,
    )
    assert report.content.coverage.successful_source_count == 1
    assert report.content.stories[0].rank == 1


@pytest.mark.asyncio
async def test_service_records_failures_and_rejects_model_inventions() -> None:
    store = InMemoryStore()
    await IngestionService(
        client=FakeClient(),
        repository=store,
    ).ingest_reference_once()
    first = source()
    second = source(key="source-two")
    registry = CommunityStrategyRegistry(
        strategies=[strategy(sources=[first, second])],
    )
    gateway = FakeGateway(documents=[document()])
    gateway.failure_keys.add("source-two")
    reports = MemoryReports()
    service = CommunityService(
        registry=registry,
        source_gateway=gateway,
        analyzer=FakeAnalyzer(result=analysis(story=candidate())),
        ranking_policy=CommunityMomentumRankingPolicy(),
        reports=reports,
        references=store,
        clock=lambda: NOW,
    )
    report = await service.run(
        job=build_strategy_jobs(registry=registry, scheduled_at=NOW)[0],
    )
    assert report.content.coverage.failed_sources[0].code == "x_fetch"

    invalid_service = CommunityService(
        registry=registry,
        source_gateway=FakeGateway(documents=[document()]),
        analyzer=FakeAnalyzer(result=analysis(story=candidate(entity_id=999))),
        ranking_policy=CommunityMomentumRankingPolicy(),
        reports=MemoryReports(),
        references=store,
        clock=lambda: NOW,
    )
    with pytest.raises(CommunityModelError, match="invalid player"):
        await invalid_service.run(
            job=build_strategy_jobs(registry=registry, scheduled_at=NOW)[0],
        )

    fatal_gateway = FakeGateway(documents=[document()])
    fatal_gateway.fatal_keys.add("source-two")
    fatal_service = CommunityService(
        registry=registry,
        source_gateway=fatal_gateway,
        analyzer=FakeAnalyzer(result=analysis(story=candidate())),
        ranking_policy=CommunityMomentumRankingPolicy(),
        reports=MemoryReports(),
        references=store,
        clock=lambda: NOW,
    )
    with pytest.raises(CommunitySourceError, match="fatal"):
        await fatal_service.run(
            job=build_strategy_jobs(registry=registry, scheduled_at=NOW)[0],
        )


@pytest.mark.asyncio
async def test_service_rejects_zero_documents_and_low_confidence_stories() -> None:
    store = InMemoryStore()
    await IngestionService(
        client=FakeClient(),
        repository=store,
    ).ingest_reference_once()
    registry = CommunityStrategyRegistry(strategies=[strategy(sources=[source()])])
    job = build_strategy_jobs(registry=registry, scheduled_at=NOW)[0]
    empty = CommunityService(
        registry=registry,
        source_gateway=FakeGateway(documents=[]),
        analyzer=FakeAnalyzer(result=analysis()),
        ranking_policy=CommunityMomentumRankingPolicy(),
        reports=MemoryReports(),
        references=store,
        clock=lambda: NOW,
    )
    with pytest.raises(CommunityPublicationError, match="No source documents"):
        await empty.run(job=job)
    low = CommunityService(
        registry=registry,
        source_gateway=FakeGateway(documents=[document()]),
        analyzer=FakeAnalyzer(
            result=analysis(
                story=candidate(confidence=EntityConfidence.LOW),
            ),
        ),
        ranking_policy=CommunityMomentumRankingPolicy(),
        reports=MemoryReports(),
        references=store,
        clock=lambda: NOW,
    )
    with pytest.raises(CommunityPublicationError, match="no publishable"):
        await low.run(job=job)


@pytest.mark.asyncio
async def test_queries_expose_public_metadata_without_sources() -> None:
    reports = MemoryReports()
    registry = CommunityStrategyRegistry(
        strategies=[strategy(sources=[source()], active=True)],
    )
    queries = CommunityQueries(repository=reports, registry=registry)
    assert queries.list_strategies()[0].key == "weekly-community-momentum-v1"
    assert queries.has_strategy(strategy_key="missing") is False
    assert await queries.latest(strategy_key="missing") is None
    assert await queries.get(report_id=1) is None
    assert await queries.recent(strategy_key="missing", limit=10) == []
    assert await queries.history(
        strategy_key="missing",
        before_id=2,
        limit=10,
    ) == []
