from datetime import datetime

import pytest
from pydantic import HttpUrl, ValidationError

from fpl_data_relay.domain.community import (
    BlogSource,
    CollectionCoverage,
    CommunityReport,
    CommunityReportContent,
    CommunityReportDraft,
    CommunityStory,
    EvidenceReference,
    SourceCollectionResult,
    SourceType,
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
        SourceCollectionResult(
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
