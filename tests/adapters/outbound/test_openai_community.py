import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest
from openai import AsyncOpenAI, OpenAIError
from pydantic import HttpUrl

from fpl_data_relay.adapters.outbound.openai_community import (
    OpenAICommunityAnalyzer,
    _extraction_packets,
    _split_complete_text,
)
from fpl_data_relay.application.errors import CommunityModelError
from fpl_data_relay.domain.community import (
    Actionability,
    AgentAnalysisRequest,
    BlogEngagement,
    CandidateStory,
    CandidateStoryBatch,
    EntityCatalogItem,
    EntityConfidence,
    EntityLinkCandidate,
    EntityType,
    SourceDocument,
    SourceType,
    TopicCategory,
    TopicMention,
    TopicMentionBatch,
    XEngagement,
)

NOW = datetime(2026, 8, 13, 6, tzinfo=UTC)


def document(
    *,
    document_id: str,
    source_type: SourceType,
    text: str,
) -> SourceDocument:
    engagement = (
        XEngagement(
            type=SourceType.X,
            likes=1,
            replies=0,
            reposts=0,
            quotes=0,
        )
        if source_type is SourceType.X
        else BlogEngagement(type=SourceType.BLOG)
    )
    return SourceDocument(
        document_id=document_id,
        source_key="x" if source_type is SourceType.X else "blog",
        source_type=source_type,
        external_id=document_id,
        publisher="Publisher",
        title="Title",
        url=HttpUrl("https://example.com/item"),
        published_at=NOW,
        text=text,
        engagement=engagement,
    )


def request(*, maximum: int = 10) -> AgentAnalysisRequest:
    return AgentAnalysisRequest(
        documents=[
            document(document_id="x:1", source_type=SourceType.X, text="Player " * 20),
            document(document_id="blog:1", source_type=SourceType.BLOG, text="Player"),
        ],
        entity_catalog=[
            EntityCatalogItem(
                entity_type=EntityType.PLAYER,
                entity_id=1,
                display_name="Player",
                aliases=["Player"],
                context="Team; Goalkeeper",
            ),
        ],
        extraction_instructions="Extract untrusted topics.",
        synthesis_instructions="Synthesize grounded stories.",
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        extraction_concurrency=2,
        chunk_characters=180,
        maximum_candidate_stories=maximum,
    )


def topic(*, document_id: str) -> TopicMentionBatch:
    return TopicMentionBatch(
        topics=[
            TopicMention(
                claim="Player is being discussed",
                category=TopicCategory.TRANSFER_IN,
                actionability=Actionability.HIGH,
                mentioned_entities=["Player"],
                document_ids=[document_id],
            ),
        ],
    )


def candidates(*, count: int = 1) -> CandidateStoryBatch:
    return CandidateStoryBatch(
        stories=[
            CandidateStory(
                headline=f"Player story {index}",
                summary="The community discussed Player.",
                category=TopicCategory.TRANSFER_IN,
                actionability=Actionability.HIGH,
                evidence_document_ids=["x:1"],
                entity_links=[
                    EntityLinkCandidate(
                        entity_type=EntityType.PLAYER,
                        entity_id=1,
                        confidence=EntityConfidence.HIGH,
                    ),
                ],
            )
            for index in range(count)
        ],
    )


class FakeResponses:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.status = "completed"
        self.parsed_none = False
        self.bad_citation = False
        self.empty_topics = False
        self.candidate_count = 1
        self.error: OpenAIError | None = None
        self.no_usage = False

    async def parse(self, **parameters: object) -> object:
        self.calls.append(parameters)
        if self.error is not None:
            raise self.error
        output_type = parameters["text_format"]
        if output_type is TopicMentionBatch:
            input_text = str(parameters["input"])
            if self.empty_topics:
                parsed = TopicMentionBatch(topics=[])
            else:
                parsed = topic(
                    document_id=(
                        "invented"
                        if self.bad_citation
                        else "blog:1"
                        if "blog:1" in input_text
                        else "x:1"
                    ),
                )
        else:
            parsed = candidates(count=self.candidate_count)
        return SimpleNamespace(
            status=self.status,
            output_parsed=None if self.parsed_none else parsed,
            id=f"resp_{len(self.calls)}",
            usage=(
                None
                if self.no_usage
                else SimpleNamespace(input_tokens=10, output_tokens=5)
            ),
        )


class FakeClient:
    def __init__(self) -> None:
        self.responses = FakeResponses()


@pytest.mark.asyncio
async def test_analyzer_chunks_without_truncation_and_uses_safe_settings() -> None:
    client = FakeClient()
    analyzer = OpenAICommunityAnalyzer(client=cast("AsyncOpenAI", client))
    result = await analyzer.analyze(request=request())
    assert len(result.candidates.stories) == 1
    assert result.usage.input_tokens == 10 * len(client.responses.calls)
    assert len(client.responses.calls) >= 3
    assert all(call["store"] is False for call in client.responses.calls)
    assert all(
        call["reasoning"] == {"effort": "medium"}
        for call in client.responses.calls
    )
    assert "https://" not in str(client.responses.calls[0]["input"])
    assert "untrusted" in str(client.responses.calls[0]["input"])


def test_complete_chunking_and_packet_grouping() -> None:
    text = "alpha beta\n\ngamma delta"
    parts = _split_complete_text(text=text, limit=8)
    assert "".join(parts) == text
    packets = _extraction_packets(
        documents=request().documents,
        maximum_characters=180,
    )
    assert all(
        len(json.dumps(packet.documents, ensure_ascii=False)) <= 180
        for packet in packets
    )
    assert {identifier for packet in packets for identifier in packet.document_ids} == {
        "x:1",
        "blog:1",
    }
    with pytest.raises(CommunityModelError, match="too small"):
        _extraction_packets(
            documents=request().documents,
            maximum_characters=1,
        )


@pytest.mark.asyncio
async def test_analyzer_returns_no_candidates_without_topics_or_entities() -> None:
    client = FakeClient()
    client.responses.empty_topics = True
    analyzer = OpenAICommunityAnalyzer(client=cast("AsyncOpenAI", client))
    assert (await analyzer.analyze(request=request())).candidates.stories == []

    client = FakeClient()
    no_match = request().model_copy(
        update={
            "entity_catalog": [
                request().entity_catalog[0].model_copy(update={"aliases": ["Nobody"]}),
            ],
        },
    )
    analyzer = OpenAICommunityAnalyzer(client=cast("AsyncOpenAI", client))
    assert (await analyzer.analyze(request=no_match)).candidates.stories == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["status", "parsed", "citation", "usage", "api"])
async def test_analyzer_fails_on_provider_and_integrity_errors(failure: str) -> None:
    client = FakeClient()
    if failure == "status":
        client.responses.status = "incomplete"
    elif failure == "parsed":
        client.responses.parsed_none = True
    elif failure == "citation":
        client.responses.bad_citation = True
    elif failure == "usage":
        client.responses.no_usage = True
    else:
        client.responses.error = OpenAIError("provider failed")
    analyzer = OpenAICommunityAnalyzer(client=cast("AsyncOpenAI", client))
    with pytest.raises(CommunityModelError):
        await analyzer.analyze(request=request())


@pytest.mark.asyncio
async def test_analyzer_rejects_excess_candidates() -> None:
    client = FakeClient()
    client.responses.candidate_count = 2
    analyzer = OpenAICommunityAnalyzer(client=cast("AsyncOpenAI", client))
    with pytest.raises(CommunityModelError, match="more candidate"):
        await analyzer.analyze(request=request(maximum=1))
