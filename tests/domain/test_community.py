from datetime import datetime, timedelta

import pytest
from pydantic import HttpUrl, ValidationError

from fpl_data_relay.domain.community import (
    Actionability,
    AgentExtractionResult,
    BlogSource,
    CollectionCoverage,
    CommunityReport,
    CommunityReportContent,
    CommunityReportDraft,
    CommunityStory,
    DocumentExtraction,
    EvidenceReference,
    ExtractionCacheEntry,
    ExtractionCacheEntryDraft,
    ExtractionCacheUsage,
    ModelUsage,
    SourceDiscoveryResult,
    SourceDocumentMetadata,
    SourceMaterializationResult,
    SourceType,
    TopicCategory,
    TopicMention,
    TopicMentionBatch,
    XEngagement,
    YouTubeEngagement,
)
from tests.adapters.outbound.test_community_postgres import content, draft


def test_engagement_formulas_match_documented_provider_weights() -> None:
    assert XEngagement(
        type=SourceType.X,
        likes=2,
        replies=3,
        reposts=4,
        quotes=5,
    ).weighted_score == 23
    assert YouTubeEngagement(
        type=SourceType.YOUTUBE,
        views=100,
        likes=2,
        comments=3,
    ).weighted_score == 180


def test_blog_hosts_are_normalized_unique_and_required() -> None:
    blog = BlogSource(
        type=SourceType.BLOG,
        key="blog",
        label="Blog",
        feed_url=HttpUrl("https://feed.example/rss"),
        allowed_article_hosts=["BLOG.EXAMPLE"],
        max_articles=10,
        timeout_seconds=5,
        max_response_bytes=1000,
    )
    assert blog.allowed_article_hosts == ["blog.example"]
    with pytest.raises(ValidationError, match="unique"):
        blog.model_copy(
            update={"allowed_article_hosts": ["blog.example", "blog.example"]},
        ).model_validate(
            {
                **blog.model_dump(),
                "allowed_article_hosts": ["blog.example", "blog.example"],
            },
        )
    with pytest.raises(ValidationError, match="hostnames"):
        BlogSource.model_validate(
            {**blog.model_dump(), "allowed_article_hosts": ["blog.example/path"]},
        )


def test_coverage_and_story_rank_invariants_fail_fast() -> None:
    with pytest.raises(ValidationError, match="inconsistent"):
        CollectionCoverage(
            configured_source_count=2,
            successful_source_count=1,
            failed_sources=[],
            excluded_document_count=0,
            exclusions=[],
            x_document_count=0,
            youtube_document_count=0,
            blog_document_count=0,
        )
    original = content()
    duplicate_rank = original.stories[0].model_copy(update={"rank": 2})
    with pytest.raises(ValidationError, match="contiguous"):
        CommunityReportContent.model_validate(
            {
                **original.model_dump(),
                "stories": [duplicate_rank],
            },
        )
    with pytest.raises(ValidationError, match="component sum"):
        CommunityStory.model_validate(
            {
                **original.stories[0].model_dump(),
                "momentum_score": original.stories[0].momentum_score + 1,
            },
        )
    with pytest.raises(ValidationError, match="exclusion counts"):
        SourceMaterializationResult(
            source_key="blog",
            documents=[],
            excluded_document_count=1,
            exclusions=[],
        )
    with pytest.raises(ValidationError, match="discovery exclusion"):
        SourceDiscoveryResult(
            source_key="blog",
            documents=[],
            excluded_document_count=1,
            exclusions=[],
        )
    with pytest.raises(ValidationError, match="Coverage exclusion"):
        CollectionCoverage(
            configured_source_count=1,
            successful_source_count=1,
            failed_sources=[],
            excluded_document_count=1,
            exclusions=[],
            x_document_count=0,
            youtube_document_count=0,
            blog_document_count=0,
        )
    with pytest.raises(ValidationError, match="published_at"):
        EvidenceReference.model_validate(
            {
                **original.stories[0].evidence[0].model_dump(),
                "published_at": datetime(2026, 8, 1),
            },
        )


def test_report_content_rejects_duplicate_references_and_excess_stories() -> None:
    original = content()
    story = original.stories[0]
    with pytest.raises(ValidationError, match="evidence document IDs"):
        CommunityStory.model_validate(
            {**story.model_dump(), "evidence": [story.evidence[0]] * 2},
        )
    with pytest.raises(ValidationError, match="entity references"):
        CommunityStory.model_validate(
            {**story.model_dump(), "entities": [story.entities[0]] * 2},
        )
    second = story.model_copy(update={"rank": 2, "headline": "Second story"})
    with pytest.raises(ValidationError, match="cannot exceed"):
        CommunityReportContent.model_validate(
            {
                **original.model_dump(),
                "target_story_count": 1,
                "stories": [story, second],
            },
        )


def test_report_and_draft_reject_naive_or_reversed_windows() -> None:
    original = draft()
    with pytest.raises(ValidationError, match="timezone-aware"):
        CommunityReportDraft.model_validate(
            {
                **original.model_dump(),
                "window_start": datetime(2026, 8, 1),
            },
        )
    with pytest.raises(ValidationError, match="after"):
        CommunityReport.model_validate(
            {
                "id": 1,
                **original.model_dump(),
                "window_start": original.window_end,
            },
        )


def test_extraction_cache_contracts_retain_only_structured_derivatives() -> None:
    evidence = content().stories[0].evidence[0]
    document = SourceDocumentMetadata(
        document_id=evidence.document_id,
        source_key=evidence.source_key,
        source_type=evidence.source_type,
        external_id="article-1",
        publisher=evidence.publisher,
        title=evidence.title,
        url=evidence.url,
        published_at=evidence.published_at,
        engagement=evidence.engagement,
    )
    entry = ExtractionCacheEntryDraft(
        strategy_key="weekly-community-momentum-v1",
        strategy_version=1,
        source_key=document.source_key,
        source_type=document.source_type,
        document_id=document.document_id,
        external_id=document.external_id,
        content_revision="a" * 64,
        extraction_contract_hash="b" * 64,
        document=document,
        topics=TopicMentionBatch(topics=[]),
        published_at=document.published_at,
        expires_at=document.published_at + timedelta(days=8),
    )
    assert "text" not in entry.model_dump(mode="json")["document"]
    assert ExtractionCacheUsage(
        eligible_document_count=2,
        hit_count=1,
        miss_count=1,
        write_count=1,
        expired_entry_count=0,
    ).hit_count == 1
    with pytest.raises(ValidationError, match="follow"):
        ExtractionCacheEntryDraft.model_validate(
            {**entry.model_dump(), "expires_at": document.published_at},
        )
    with pytest.raises(ValidationError, match="hits and misses"):
        ExtractionCacheUsage(
            eligible_document_count=2,
            hit_count=2,
            miss_count=1,
            write_count=1,
            expired_entry_count=0,
        )
    for changed in (
        {"published_at": datetime(2026, 8, 1)},
        {"expires_at": datetime(2026, 8, 10)},
        {"external_id": "wrong"},
    ):
        with pytest.raises(ValidationError):
            ExtractionCacheEntryDraft.model_validate(
                {**entry.model_dump(), **changed},
            )
    wrong_topic = TopicMention(
        claim="Claim",
        category=TopicCategory.OTHER,
        actionability=Actionability.LOW,
        mentioned_entities=[],
        document_ids=["other"],
    )
    with pytest.raises(ValidationError, match="own document"):
        ExtractionCacheEntryDraft.model_validate(
            {
                **entry.model_dump(),
                "topics": TopicMentionBatch(topics=[wrong_topic]),
            },
        )
    with pytest.raises(ValidationError, match="created_at"):
        ExtractionCacheEntry(
            id=1,
            created_at=datetime(2026, 8, 1),
            **entry.model_dump(),
        )
    with pytest.raises(ValidationError, match="own document"):
        DocumentExtraction(
            document_id=document.document_id,
            topics=TopicMentionBatch(topics=[wrong_topic]),
        )
    valid_extraction = DocumentExtraction(
        document_id=document.document_id,
        topics=TopicMentionBatch(topics=[]),
    )
    with pytest.raises(ValidationError, match="unique"):
        AgentExtractionResult(
            documents=[valid_extraction, valid_extraction],
            usage=ModelUsage(
                provider="openai",
                model="gpt-5.6-sol",
                reasoning_effort="medium",
                response_ids=[],
                input_tokens=0,
                output_tokens=0,
            ),
        )
