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
from fpl_data_relay.application.community_service import (
    CommunityService,
    _combine_usage,
    _extraction_contract_hash,
    _validated_cache_hits,
    _validated_extractions,
)
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
    AgentExtractionRequest,
    AgentExtractionResult,
    AgentSynthesisRequest,
    AgentSynthesisResult,
    BlogDiscoveredDocument,
    BlogEngagement,
    BlogSource,
    CandidateStory,
    CandidateStoryBatch,
    CommunityReport,
    CommunityReportDraft,
    CommunityReportSummary,
    CommunitySource,
    CommunityStrategyDefinition,
    DiscoveredDocument,
    DocumentExtraction,
    EntityConfidence,
    EntityLinkCandidate,
    EntityType,
    ExtractionCacheEntry,
    ExtractionCacheEntryDraft,
    ExtractionCacheLookup,
    ModelUsage,
    SourceDiscoveryResult,
    SourceDocument,
    SourceDocumentMetadata,
    SourceMaterializationResult,
    SourceType,
    TopicCategory,
    TopicMention,
    TopicMentionBatch,
    XDiscoveredDocument,
    XEngagement,
    XSource,
    YouTubeDiscoveredDocument,
    YouTubeEngagement,
    YouTubeSource,
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


def youtube_source() -> YouTubeSource:
    return YouTubeSource(
        type=SourceType.YOUTUBE,
        key="youtube-one",
        label="YouTube one",
        channel_id="UC123",
        max_videos=10,
        timeout_seconds=10.0,
        transcript_language="en",
        transcript_mode="native",
        transcript_poll_seconds=1.0,
        transcript_timeout_seconds=30.0,
    )


def blog_source() -> BlogSource:
    return BlogSource(
        type=SourceType.BLOG,
        key="blog-one",
        label="Blog one",
        feed_url=HttpUrl("https://feed.example/rss"),
        allowed_article_hosts=["blog.example"],
        max_articles=10,
        timeout_seconds=10.0,
        max_response_bytes=100_000,
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
        extraction_cache_retention_days=8,
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


def synthesis(*, story: CandidateStory | None = None) -> AgentSynthesisResult:
    return AgentSynthesisResult(
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
        self.revision = "a" * 64
        self.materialization_calls: list[list[str]] = []

    async def discover(
        self,
        *,
        source: CommunitySource,
        window_start: datetime,
        window_end: datetime,
    ) -> SourceDiscoveryResult:
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
        return SourceDiscoveryResult(
            source_key=source.key,
            documents=[
                XDiscoveredDocument(
                    **item.model_dump(exclude={"text"}),
                    content_revision=self.revision,
                    transient_text=item.text,
                )
                for item in self.documents
                if item.source_key == source.key
            ],
            excluded_document_count=0,
            exclusions=[],
        )

    async def materialize(
        self,
        *,
        source: CommunitySource,
        documents: list[DiscoveredDocument],
    ) -> SourceMaterializationResult:
        requested = {item.document_id for item in documents}
        self.materialization_calls.append(sorted(requested))
        return SourceMaterializationResult(
            source_key=source.key,
            documents=[
                item
                for item in self.documents
                if item.source_key == source.key and item.document_id in requested
            ],
            excluded_document_count=0,
            exclusions=[],
        )

    async def close(self) -> None:
        return None


class FakeAnalyzer:
    def __init__(self, *, result: AgentSynthesisResult) -> None:
        self.result = result
        self.extraction_requests: list[AgentExtractionRequest] = []
        self.synthesis_requests: list[AgentSynthesisRequest] = []
        self.empty_document_ids: set[str] = set()

    async def extract(
        self,
        *,
        request: AgentExtractionRequest,
    ) -> AgentExtractionResult:
        self.extraction_requests.append(request)
        return AgentExtractionResult(
            documents=[
                DocumentExtraction(
                    document_id=item.document_id,
                    topics=TopicMentionBatch(
                        topics=[]
                        if item.document_id in self.empty_document_ids
                        else [
                            TopicMention(
                                claim="Player is a popular transfer.",
                                category=TopicCategory.TRANSFER_IN,
                                actionability=Actionability.HIGH,
                                mentioned_entities=["Player"],
                                document_ids=[item.document_id],
                            ),
                        ],
                    ),
                )
                for item in request.documents
            ],
            usage=ModelUsage(
                provider="openai",
                model=request.model,
                reasoning_effort=request.reasoning_effort,
                response_ids=["resp_extract"],
                input_tokens=50,
                output_tokens=10,
            ),
        )

    async def synthesize(
        self,
        *,
        request: AgentSynthesisRequest,
    ) -> AgentSynthesisResult:
        self.synthesis_requests.append(request)
        return self.result


class MediaGateway:
    """Discovery fixture proving expensive materialization is miss-only."""

    def __init__(self) -> None:
        self.revisions = {
            "youtube:video-1": "a" * 64,
            "blog:article-1": "b" * 64,
        }
        self.youtube_views = 100
        self.materialization_calls: list[str] = []

    async def discover(
        self,
        *,
        source: CommunitySource,
        window_start: datetime,
        window_end: datetime,
    ) -> SourceDiscoveryResult:
        del window_start, window_end
        if isinstance(source, YouTubeSource):
            document: DiscoveredDocument = YouTubeDiscoveredDocument(
                document_id="youtube:video-1",
                source_key=source.key,
                source_type=source.type,
                external_id="video-1",
                publisher=source.label,
                title="Video",
                url=HttpUrl("https://www.youtube.com/watch?v=video-1"),
                published_at=NOW - timedelta(hours=1),
                engagement=YouTubeEngagement(
                    type=SourceType.YOUTUBE,
                    views=self.youtube_views,
                    likes=10,
                    comments=2,
                ),
                content_revision=self.revisions["youtube:video-1"],
            )
        elif isinstance(source, BlogSource):
            document = BlogDiscoveredDocument(
                document_id="blog:article-1",
                source_key=source.key,
                source_type=source.type,
                external_id="article-1",
                publisher=source.label,
                title="Article",
                url=HttpUrl("https://blog.example/article"),
                published_at=NOW - timedelta(hours=2),
                engagement=BlogEngagement(type=SourceType.BLOG),
                content_revision=self.revisions["blog:article-1"],
            )
        else:
            raise AssertionError("MediaGateway supports YouTube and blogs only.")
        return SourceDiscoveryResult(
            source_key=source.key,
            documents=[document],
            excluded_document_count=0,
            exclusions=[],
        )

    async def materialize(
        self,
        *,
        source: CommunitySource,
        documents: list[DiscoveredDocument],
    ) -> SourceMaterializationResult:
        materialized: list[SourceDocument] = []
        for discovered in documents:
            self.materialization_calls.append(discovered.document_id)
            materialized.append(
                SourceDocument.model_validate(
                    {
                        **discovered.model_dump(
                            exclude={"content_revision", "transient_text"},
                        ),
                        "text": f"Player discussion in {discovered.document_id}",
                    },
                ),
            )
        return SourceMaterializationResult(
            source_key=source.key,
            documents=materialized,
            excluded_document_count=0,
            exclusions=[],
        )

    async def close(self) -> None:
        return None


class MemoryCache:
    def __init__(self) -> None:
        self.entries: list[ExtractionCacheEntry] = []

    async def prune_expired(self, *, as_of: datetime) -> int:
        current = [item for item in self.entries if item.expires_at > as_of]
        deleted = len(self.entries) - len(current)
        self.entries = current
        return deleted

    async def get_entries(
        self,
        *,
        strategy_key: str,
        strategy_version: int,
        extraction_contract_hash: str,
        lookups: list[ExtractionCacheLookup],
        as_of: datetime,
    ) -> list[ExtractionCacheEntry]:
        keys = {
            (item.source_key, item.document_id, item.content_revision)
            for item in lookups
        }
        return [
            item
            for item in self.entries
            if item.strategy_key == strategy_key
            and item.strategy_version == strategy_version
            and item.extraction_contract_hash == extraction_contract_hash
            and item.expires_at > as_of
            and (item.source_key, item.document_id, item.content_revision) in keys
        ]

    async def insert_entries(
        self,
        *,
        entries: list[ExtractionCacheEntryDraft],
    ) -> int:
        for entry in entries:
            self.entries.append(
                ExtractionCacheEntry(
                    id=len(self.entries) + 1,
                    created_at=NOW,
                    **entry.model_dump(),
                ),
            )
        return len(entries)


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
    for changed, message in (
        ({"minimum_story_count": 10, "target_story_count": 1}, "minimum"),
        ({"maximum_candidate_stories": 1}, "maximum"),
        ({"extraction_cache_retention_days": 7}, "retention"),
        ({"sources": [source(), source()]}, "source keys"),
    ):
        with pytest.raises(ValidationError, match=message):
            CommunityStrategyDefinition.model_validate(
                {**definition.model_dump(), **changed},
            )
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
        analyzer=FakeAnalyzer(result=synthesis()),
        ranking_policy=CommunityMomentumRankingPolicy(),
        reports=MemoryReports(),
        extraction_cache=MemoryCache(),
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
    analyzer = FakeAnalyzer(result=synthesis(story=story))
    registry = CommunityStrategyRegistry(strategies=[strategy(sources=[source()])])
    service = CommunityService(
        registry=registry,
        source_gateway=gateway,
        analyzer=analyzer,
        ranking_policy=CommunityMomentumRankingPolicy(),
        reports=reports,
        extraction_cache=MemoryCache(),
        references=store,
        clock=lambda: NOW,
    )
    job = build_strategy_jobs(registry=registry, scheduled_at=NOW)[0]
    report = await service.run(job=job)
    duplicate = await service.run(job=job)
    assert duplicate.id == report.id
    assert len(analyzer.extraction_requests) == 1
    assert len(analyzer.synthesis_requests) == 1
    assert {item.entity_type for item in report.content.stories[0].entities} == set(
        EntityType,
    )
    assert report.content.coverage.successful_source_count == 1
    assert report.content.stories[0].rank == 1


@pytest.mark.asyncio
async def test_service_reuses_extraction_on_the_next_daily_report() -> None:
    store = InMemoryStore()
    await IngestionService(
        client=FakeClient(),
        repository=store,
    ).ingest_reference_once()
    registry = CommunityStrategyRegistry(strategies=[strategy(sources=[source()])])
    reports = MemoryReports()
    cache = MemoryCache()
    gateway = FakeGateway(documents=[document()])
    analyzer = FakeAnalyzer(result=synthesis(story=candidate()))
    service = CommunityService(
        registry=registry,
        source_gateway=gateway,
        analyzer=analyzer,
        ranking_policy=CommunityMomentumRankingPolicy(),
        reports=reports,
        extraction_cache=cache,
        references=store,
        clock=lambda: NOW + timedelta(days=3),
    )

    first = await service.run(
        job=build_strategy_jobs(registry=registry, scheduled_at=NOW)[0],
    )
    second = await service.run(
        job=build_strategy_jobs(
            registry=registry,
            scheduled_at=NOW + timedelta(days=1),
        )[0],
    )

    assert first.content.extraction_cache.miss_count == 1
    assert second.content.extraction_cache.hit_count == 1
    assert second.content.extraction_cache.write_count == 0
    assert gateway.materialization_calls == [["x:1"]]
    assert len(analyzer.extraction_requests) == 1
    assert len(analyzer.synthesis_requests) == 2
    assert second.content.model_usage.response_ids == ["resp_1"]

    gateway.revision = "b" * 64
    third = await service.run(
        job=build_strategy_jobs(
            registry=registry,
            scheduled_at=NOW + timedelta(days=2),
        )[0],
    )
    assert third.content.extraction_cache.miss_count == 1
    assert len(analyzer.extraction_requests) == 2


@pytest.mark.asyncio
async def test_service_skips_expensive_media_on_hits_and_refreshes_engagement() -> None:
    store = InMemoryStore()
    await IngestionService(
        client=FakeClient(),
        repository=store,
    ).ingest_reference_once()
    registry = CommunityStrategyRegistry(
        strategies=[strategy(sources=[youtube_source(), blog_source()])],
    )
    current_time = [NOW]
    gateway = MediaGateway()
    analyzer = FakeAnalyzer(
        result=synthesis(
            story=candidate(
                evidence=["youtube:video-1", "blog:article-1"],
            ),
        ),
    )
    service = CommunityService(
        registry=registry,
        source_gateway=gateway,
        analyzer=analyzer,
        ranking_policy=CommunityMomentumRankingPolicy(),
        reports=MemoryReports(),
        extraction_cache=MemoryCache(),
        references=store,
        clock=lambda: current_time[0],
    )

    first = await service.run(
        job=build_strategy_jobs(registry=registry, scheduled_at=NOW)[0],
    )
    current_time[0] = NOW + timedelta(days=1)
    gateway.youtube_views = 777
    second = await service.run(
        job=build_strategy_jobs(
            registry=registry,
            scheduled_at=NOW + timedelta(days=1),
        )[0],
    )

    assert first.content.extraction_cache.miss_count == 2
    assert second.content.extraction_cache.hit_count == 2
    assert sorted(gateway.materialization_calls) == [
        "blog:article-1",
        "youtube:video-1",
    ]
    assert len(analyzer.extraction_requests) == 1
    assert len(analyzer.synthesis_requests) == 2
    youtube_evidence = next(
        item
        for item in second.content.stories[0].evidence
        if item.document_id == "youtube:video-1"
    )
    assert isinstance(youtube_evidence.engagement, YouTubeEngagement)
    assert youtube_evidence.engagement.views == 777

    current_time[0] = NOW + timedelta(days=2)
    gateway.revisions["blog:article-1"] = "c" * 64
    third = await service.run(
        job=build_strategy_jobs(
            registry=registry,
            scheduled_at=NOW + timedelta(days=2),
        )[0],
    )
    assert third.content.extraction_cache.hit_count == 1
    assert third.content.extraction_cache.miss_count == 1
    assert gateway.materialization_calls[-1] == "blog:article-1"
    assert {
        topic.document_ids[0]
        for topic in analyzer.synthesis_requests[-1].topics
    } == {"youtube:video-1", "blog:article-1"}

    current_time[0] = NOW + timedelta(days=9)
    fourth = await service.run(
        job=build_strategy_jobs(
            registry=registry,
            scheduled_at=NOW + timedelta(days=3),
        )[0],
    )
    assert fourth.content.extraction_cache.expired_entry_count == 3
    assert fourth.content.extraction_cache.miss_count == 2
    assert len(analyzer.extraction_requests) == 3


@pytest.mark.asyncio
async def test_service_caches_empty_document_extractions() -> None:
    store = InMemoryStore()
    await IngestionService(
        client=FakeClient(),
        repository=store,
    ).ingest_reference_once()
    registry = CommunityStrategyRegistry(strategies=[strategy(sources=[source()])])
    gateway = FakeGateway(
        documents=[document(), document(document_id="x:2")],
    )
    analyzer = FakeAnalyzer(result=synthesis(story=candidate()))
    analyzer.empty_document_ids.add("x:2")
    cache = MemoryCache()
    current_time = [NOW]
    service = CommunityService(
        registry=registry,
        source_gateway=gateway,
        analyzer=analyzer,
        ranking_policy=CommunityMomentumRankingPolicy(),
        reports=MemoryReports(),
        extraction_cache=cache,
        references=store,
        clock=lambda: current_time[0],
    )

    await service.run(
        job=build_strategy_jobs(registry=registry, scheduled_at=NOW)[0],
    )
    current_time[0] = NOW + timedelta(days=1)
    second = await service.run(
        job=build_strategy_jobs(
            registry=registry,
            scheduled_at=NOW + timedelta(days=1),
        )[0],
    )

    empty_entry = next(item for item in cache.entries if item.document_id == "x:2")
    assert empty_entry.topics.topics == []
    assert second.content.extraction_cache.hit_count == 2
    assert len(analyzer.extraction_requests) == 1


def test_extraction_contract_hash_invalidates_every_model_input() -> None:
    original = _extraction_contract_hash(
        extraction_instructions="prompt v1",
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        chunk_characters=24_000,
    )
    changed = [
        _extraction_contract_hash(
            extraction_instructions="prompt v2",
            model="gpt-5.6-sol",
            reasoning_effort="medium",
            chunk_characters=24_000,
        ),
        _extraction_contract_hash(
            extraction_instructions="prompt v1",
            model="different-model",
            reasoning_effort="medium",
            chunk_characters=24_000,
        ),
        _extraction_contract_hash(
            extraction_instructions="prompt v1",
            model="gpt-5.6-sol",
            reasoning_effort="different-effort",
            chunk_characters=24_000,
        ),
        _extraction_contract_hash(
            extraction_instructions="prompt v1",
            model="gpt-5.6-sol",
            reasoning_effort="medium",
            chunk_characters=12_000,
        ),
    ]
    assert len(original) == 64
    assert all(item != original for item in changed)


def test_cache_and_extraction_integrity_checks_fail_fast() -> None:
    source_document = document()
    discovered = XDiscoveredDocument(
        **source_document.model_dump(exclude={"text"}),
        content_revision="a" * 64,
        transient_text=source_document.text,
    )
    topics = TopicMentionBatch(
        topics=[
            TopicMention(
                claim="Player is discussed.",
                category=TopicCategory.TRANSFER_IN,
                actionability=Actionability.HIGH,
                mentioned_entities=["Player"],
                document_ids=[source_document.document_id],
            ),
        ],
    )
    entry = ExtractionCacheEntry(
        id=1,
        strategy_key="weekly-community-momentum-v1",
        strategy_version=1,
        source_key=source_document.source_key,
        source_type=source_document.source_type,
        document_id=source_document.document_id,
        external_id=source_document.external_id,
        content_revision=discovered.content_revision,
        extraction_contract_hash="b" * 64,
        document=SourceDocumentMetadata.model_validate(
            source_document.model_dump(exclude={"text"}),
        ),
        topics=topics,
        published_at=source_document.published_at,
        expires_at=source_document.published_at + timedelta(days=8),
        created_at=NOW,
    )
    with pytest.raises(CommunityModelError, match="unknown document"):
        _validated_cache_hits(
            entries=[entry.model_copy(update={"document_id": "x:missing"})],
            discovered={discovered.document_id: discovered},
            strategy_key=entry.strategy_key,
            strategy_version=entry.strategy_version,
            extraction_contract_hash=entry.extraction_contract_hash,
            as_of=NOW,
        )
    with pytest.raises(CommunityModelError, match="inconsistent document"):
        _validated_cache_hits(
            entries=[entry.model_copy(update={"strategy_key": "wrong"})],
            discovered={discovered.document_id: discovered},
            strategy_key=entry.strategy_key,
            strategy_version=entry.strategy_version,
            extraction_contract_hash=entry.extraction_contract_hash,
            as_of=NOW,
        )
    with pytest.raises(CommunityModelError, match="duplicate document"):
        _validated_cache_hits(
            entries=[entry, entry],
            discovered={discovered.document_id: discovered},
            strategy_key=entry.strategy_key,
            strategy_version=entry.strategy_version,
            extraction_contract_hash=entry.extraction_contract_hash,
            as_of=NOW,
        )
    with pytest.raises(CommunityModelError, match="did not match"):
        _validated_extractions(
            extracted=[],
            documents={source_document.document_id: source_document},
        )
    with pytest.raises(CommunityModelError, match="usage models differ"):
        _combine_usage(
            extraction=ModelUsage(
                provider="openai",
                model="first",
                reasoning_effort="medium",
                response_ids=[],
                input_tokens=0,
                output_tokens=0,
            ),
            synthesis=ModelUsage(
                provider="openai",
                model="second",
                reasoning_effort="medium",
                response_ids=[],
                input_tokens=0,
                output_tokens=0,
            ),
        )


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
        analyzer=FakeAnalyzer(result=synthesis(story=candidate())),
        ranking_policy=CommunityMomentumRankingPolicy(),
        reports=reports,
        extraction_cache=MemoryCache(),
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
        analyzer=FakeAnalyzer(result=synthesis(story=candidate(entity_id=999))),
        ranking_policy=CommunityMomentumRankingPolicy(),
        reports=MemoryReports(),
        extraction_cache=MemoryCache(),
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
        analyzer=FakeAnalyzer(result=synthesis(story=candidate())),
        ranking_policy=CommunityMomentumRankingPolicy(),
        reports=MemoryReports(),
        extraction_cache=MemoryCache(),
        references=store,
        clock=lambda: NOW,
    )
    with pytest.raises(CommunitySourceError, match="fatal"):
        await fatal_service.run(
            job=build_strategy_jobs(registry=registry, scheduled_at=NOW)[0],
        )


@pytest.mark.asyncio
async def test_service_rejects_zero_documents_and_low_confidence_stories() -> None:
    missing_registry = CommunityStrategyRegistry(
        strategies=[strategy(sources=[source()])],
    )
    missing_references = CommunityService(
        registry=missing_registry,
        source_gateway=FakeGateway(documents=[document()]),
        analyzer=FakeAnalyzer(result=synthesis()),
        ranking_policy=CommunityMomentumRankingPolicy(),
        reports=MemoryReports(),
        extraction_cache=MemoryCache(),
        references=InMemoryStore(),
        clock=lambda: NOW,
    )
    with pytest.raises(CommunityPublicationError, match="No current FPL season"):
        await missing_references.run(
            job=build_strategy_jobs(
                registry=missing_registry,
                scheduled_at=NOW,
            )[0],
        )

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
        analyzer=FakeAnalyzer(result=synthesis()),
        ranking_policy=CommunityMomentumRankingPolicy(),
        reports=MemoryReports(),
        extraction_cache=MemoryCache(),
        references=store,
        clock=lambda: NOW,
    )
    with pytest.raises(CommunityPublicationError, match="No source documents"):
        await empty.run(job=job)
    empty_analyzer = FakeAnalyzer(result=synthesis())
    empty_analyzer.empty_document_ids.add("x:1")
    empty_topics = CommunityService(
        registry=registry,
        source_gateway=FakeGateway(documents=[document()]),
        analyzer=empty_analyzer,
        ranking_policy=CommunityMomentumRankingPolicy(),
        reports=MemoryReports(),
        extraction_cache=MemoryCache(),
        references=store,
        clock=lambda: NOW,
    )
    with pytest.raises(CommunityPublicationError, match="no topics"):
        await empty_topics.run(job=job)
    low = CommunityService(
        registry=registry,
        source_gateway=FakeGateway(documents=[document()]),
        analyzer=FakeAnalyzer(
            result=synthesis(
                story=candidate(confidence=EntityConfidence.LOW),
            ),
        ),
        ranking_policy=CommunityMomentumRankingPolicy(),
        reports=MemoryReports(),
        extraction_cache=MemoryCache(),
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
