"""Strict contracts for scheduled community intelligence reports."""

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    TypeAdapter,
    field_validator,
    model_validator,
)


class CommunityModel(BaseModel):
    """Immutable strict base for community-owned values."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class SourceType(StrEnum):
    """Supported public community media sources."""

    X = "x"
    YOUTUBE = "youtube"
    BLOG = "blog"


class TopicCategory(StrEnum):
    """FPL decision categories used by analysis and presentation."""

    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"
    HOLD = "hold"
    SELL = "sell"
    CAPTAINCY = "captaincy"
    INJURY = "injury"
    FIXTURE = "fixture"
    CHIP = "chip"
    TEAM = "team"
    OTHER = "other"


class Actionability(StrEnum):
    """How directly a topic informs an FPL decision."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EntityType(StrEnum):
    """Canonical FPL entities that a story may reference."""

    PLAYER = "player"
    TEAM = "team"
    EVENT = "event"
    FIXTURE = "fixture"


class EntityConfidence(StrEnum):
    """Model confidence used before canonical links are accepted."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class XSource(CommunityModel):
    """One allow-listed X account timeline."""

    type: Literal[SourceType.X]
    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    user_id: str = Field(pattern=r"^[0-9]{1,19}$")
    username: str = Field(min_length=1)
    include_replies: bool
    include_reposts: bool
    max_documents: int = Field(ge=1, le=800)
    timeout_seconds: float = Field(gt=0)


class YouTubeSource(CommunityModel):
    """One allow-listed YouTube channel and transcript policy."""

    type: Literal[SourceType.YOUTUBE]
    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    channel_id: str = Field(min_length=1)
    max_videos: int = Field(ge=1, le=50)
    timeout_seconds: float = Field(gt=0)
    transcript_language: str = Field(min_length=2)
    transcript_mode: Literal["native"]
    transcript_poll_seconds: float = Field(gt=0)
    transcript_timeout_seconds: float = Field(gt=0)


class BlogSource(CommunityModel):
    """One allow-listed RSS or Atom blog feed."""

    type: Literal[SourceType.BLOG]
    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    feed_url: HttpUrl
    allowed_article_hosts: list[str] = Field(min_length=1)
    max_articles: int = Field(ge=1, le=100)
    timeout_seconds: float = Field(gt=0)
    max_response_bytes: int = Field(ge=1)

    @field_validator("allowed_article_hosts")
    @classmethod
    def hosts_are_unique_and_normalized(cls, value: list[str]) -> list[str]:
        """Reject ambiguous host allow-lists."""
        normalized = [host.strip().lower() for host in value]
        if any(not host or "/" in host for host in normalized):
            raise ValueError("Article hosts must be non-empty hostnames.")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Article hosts must be unique.")
        return normalized


CommunitySource = Annotated[
    XSource | YouTubeSource | BlogSource,
    Field(discriminator="type"),
]
COMMUNITY_SOURCE_ADAPTER = TypeAdapter(CommunitySource)


class CommunityStrategyDefinition(CommunityModel):
    """Complete versioned configuration for one agentic strategy."""

    key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    version: int = Field(ge=1)
    active: bool
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    schedule_expression: str = Field(min_length=1)
    schedule_timezone: str = Field(min_length=1)
    lookback_days: int = Field(ge=1)
    target_story_count: int = Field(ge=1, le=10)
    minimum_story_count: int = Field(ge=1, le=10)
    maximum_candidate_stories: int = Field(ge=1)
    ranking_policy: Literal["community_momentum_v1"]
    model: Literal["gpt-5.6-sol"]
    reasoning_effort: Literal["medium"]
    extraction_prompt_version: Literal[1]
    synthesis_prompt_version: Literal[1]
    extraction_concurrency: int = Field(ge=1)
    chunk_characters: int = Field(ge=1)
    extraction_cache_retention_days: int = Field(ge=1)
    sources: list[CommunitySource]

    @field_validator("schedule_timezone")
    @classmethod
    def schedule_timezone_is_known(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exception:
            raise ValueError(
                "schedule_timezone must be an IANA timezone.",
            ) from exception
        return value

    @model_validator(mode="after")
    def validate_strategy(self) -> CommunityStrategyDefinition:
        """Enforce publication and source invariants."""
        if self.minimum_story_count > self.target_story_count:
            raise ValueError("minimum_story_count cannot exceed target_story_count.")
        if self.maximum_candidate_stories < self.target_story_count:
            raise ValueError(
                "maximum_candidate_stories cannot be below target_story_count.",
            )
        if self.extraction_cache_retention_days <= self.lookback_days:
            raise ValueError(
                "extraction_cache_retention_days must exceed lookback_days.",
            )
        keys = [source.key for source in self.sources]
        if len(keys) != len(set(keys)):
            raise ValueError("Community source keys must be unique.")
        if self.active and not self.sources:
            raise ValueError("An active strategy requires at least one source.")
        return self


class CommunityStrategySummary(CommunityModel):
    """Public strategy metadata without private source configuration."""

    key: str
    name: str
    description: str
    cadence: str
    timezone: str
    lookback_days: int
    target_story_count: int


class XEngagement(CommunityModel):
    """Public X engagement captured with one post."""

    type: Literal[SourceType.X]
    likes: int = Field(ge=0)
    replies: int = Field(ge=0)
    reposts: int = Field(ge=0)
    quotes: int = Field(ge=0)

    @property
    def weighted_score(self) -> int:
        return self.likes + self.replies + 2 * (self.reposts + self.quotes)


class YouTubeEngagement(CommunityModel):
    """Public YouTube engagement captured with one video."""

    type: Literal[SourceType.YOUTUBE]
    views: int = Field(ge=0)
    likes: int = Field(ge=0)
    comments: int = Field(ge=0)

    @property
    def weighted_score(self) -> int:
        return self.views + 10 * self.likes + 20 * self.comments


class BlogEngagement(CommunityModel):
    """Explicit marker for sources without comparable engagement metrics."""

    type: Literal[SourceType.BLOG]


Engagement = Annotated[
    XEngagement | YouTubeEngagement | BlogEngagement,
    Field(discriminator="type"),
]


class SourceExclusion(CommunityModel):
    """Stable reason and count for documents intentionally not analyzed."""

    source_key: str
    source_type: SourceType
    code: str = Field(min_length=1)
    count: int = Field(ge=1)


class SourceDocumentMetadata(CommunityModel):
    """Normalized source metadata retained without the source body."""

    document_id: str = Field(min_length=1)
    source_key: str = Field(min_length=1)
    source_type: SourceType
    external_id: str = Field(min_length=1)
    publisher: str = Field(min_length=1)
    title: str = Field(min_length=1)
    url: HttpUrl
    published_at: datetime
    engagement: Engagement

    @field_validator("published_at")
    @classmethod
    def published_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("published_at must be timezone-aware.")
        return value

    @property
    def engagement_score(self) -> int | None:
        if isinstance(self.engagement, BlogEngagement):
            return None
        return self.engagement.weighted_score


class XDiscoveredDocument(SourceDocumentMetadata):
    """X discovery result whose body already arrived with timeline metadata."""

    source_type: Literal[SourceType.X]
    engagement: XEngagement
    content_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    transient_text: str = Field(min_length=1)


class YouTubeDiscoveredDocument(SourceDocumentMetadata):
    """YouTube metadata discovered before native transcript retrieval."""

    source_type: Literal[SourceType.YOUTUBE]
    engagement: YouTubeEngagement
    content_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class BlogDiscoveredDocument(SourceDocumentMetadata):
    """Feed metadata discovered before allow-listed article retrieval."""

    source_type: Literal[SourceType.BLOG]
    engagement: BlogEngagement
    content_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


DiscoveredDocument = Annotated[
    XDiscoveredDocument | YouTubeDiscoveredDocument | BlogDiscoveredDocument,
    Field(discriminator="source_type"),
]


class SourceDocument(SourceDocumentMetadata):
    """Normalized untrusted content passed transiently to extraction."""

    text: str = Field(min_length=1)


class SourceDiscoveryResult(CommunityModel):
    """Lightweight document discovery and intentional exclusions."""

    source_key: str
    documents: list[DiscoveredDocument]
    excluded_document_count: int = Field(ge=0)
    exclusions: list[SourceExclusion]

    @model_validator(mode="after")
    def exclusion_counts_are_consistent(self) -> SourceDiscoveryResult:
        if sum(item.count for item in self.exclusions) != (
            self.excluded_document_count
        ):
            raise ValueError("Source discovery exclusion counts are inconsistent.")
        return self


class SourceMaterializationResult(CommunityModel):
    """Transient bodies and intentional exclusions for cache misses."""

    source_key: str
    documents: list[SourceDocument]
    excluded_document_count: int = Field(ge=0)
    exclusions: list[SourceExclusion]

    @model_validator(mode="after")
    def exclusion_counts_are_consistent(self) -> SourceMaterializationResult:
        if sum(item.count for item in self.exclusions) != (
            self.excluded_document_count
        ):
            raise ValueError(
                "Source materialization exclusion counts are inconsistent.",
            )
        return self


class SourceFailure(CommunityModel):
    """Stable, safe record of a source-level collection failure."""

    source_key: str
    source_type: SourceType
    code: str = Field(min_length=1)


class CollectionCoverage(CommunityModel):
    """Collection completeness recorded with a report."""

    configured_source_count: int = Field(ge=1)
    successful_source_count: int = Field(ge=1)
    failed_sources: list[SourceFailure]
    excluded_document_count: int = Field(ge=0)
    exclusions: list[SourceExclusion]
    x_document_count: int = Field(ge=0)
    youtube_document_count: int = Field(ge=0)
    blog_document_count: int = Field(ge=0)

    @model_validator(mode="after")
    def counts_are_consistent(self) -> CollectionCoverage:
        if self.successful_source_count + len(self.failed_sources) != (
            self.configured_source_count
        ):
            raise ValueError("Source coverage counts are inconsistent.")
        if sum(item.count for item in self.exclusions) != (
            self.excluded_document_count
        ):
            raise ValueError("Coverage exclusion counts are inconsistent.")
        return self


class TopicMention(CommunityModel):
    """One structured topic extracted from a bounded content packet."""

    claim: str = Field(min_length=1)
    category: TopicCategory
    actionability: Actionability
    mentioned_entities: list[str]
    document_ids: list[str] = Field(min_length=1)


class TopicMentionBatch(CommunityModel):
    """Structured output from one extraction request."""

    topics: list[TopicMention]


class ExtractedTopic(CommunityModel):
    """One document-local topic emitted by Structured Outputs."""

    claim: str = Field(min_length=1)
    category: TopicCategory
    actionability: Actionability
    mentioned_entities: list[str]


class DocumentTopicExtraction(CommunityModel):
    """Structured topics belonging to exactly one supplied document."""

    document_id: str = Field(min_length=1)
    topics: list[ExtractedTopic]


class DocumentTopicExtractionBatch(CommunityModel):
    """Per-document extraction output for one bounded request packet."""

    documents: list[DocumentTopicExtraction]


class EntityLinkCandidate(CommunityModel):
    """Model-selected canonical entity before confidence filtering."""

    entity_type: EntityType
    entity_id: int = Field(ge=1)
    confidence: EntityConfidence


class CandidateStory(CommunityModel):
    """Semantically clustered story before deterministic ranking."""

    headline: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    category: TopicCategory
    actionability: Actionability
    evidence_document_ids: list[str] = Field(min_length=1)
    entity_links: list[EntityLinkCandidate]


class CandidateStoryBatch(CommunityModel):
    """Structured synthesis output."""

    stories: list[CandidateStory]


class EntityCatalogItem(CommunityModel):
    """Compact canonical candidate passed to model synthesis."""

    entity_type: EntityType
    entity_id: int = Field(ge=1)
    display_name: str = Field(min_length=1)
    aliases: list[str] = Field(min_length=1)
    context: str = Field(min_length=1)


class AgentExtractionRequest(CommunityModel):
    """Explicit extraction input with no writable tools or trusted URLs."""

    documents: list[SourceDocument] = Field(min_length=1)
    extraction_instructions: str = Field(min_length=1)
    model: str = Field(min_length=1)
    reasoning_effort: Literal["medium"]
    extraction_concurrency: int = Field(ge=1)
    chunk_characters: int = Field(ge=1)


class ModelUsage(CommunityModel):
    """Aggregate OpenAI usage retained for operations and cost review."""

    provider: Literal["openai"]
    model: str
    reasoning_effort: str
    response_ids: list[str]
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class DocumentExtraction(CommunityModel):
    """Cacheable topic output for one complete source document."""

    document_id: str = Field(min_length=1)
    topics: TopicMentionBatch

    @model_validator(mode="after")
    def topics_only_cite_this_document(self) -> DocumentExtraction:
        if any(
            topic.document_ids != [self.document_id]
            for topic in self.topics.topics
        ):
            raise ValueError("Cached topics may cite only their own document.")
        return self


class AgentExtractionResult(CommunityModel):
    """Per-document extraction results and current-run model usage."""

    documents: list[DocumentExtraction]
    usage: ModelUsage

    @model_validator(mode="after")
    def document_ids_are_unique(self) -> AgentExtractionResult:
        ids = [item.document_id for item in self.documents]
        if len(ids) != len(set(ids)):
            raise ValueError("Extraction document IDs must be unique.")
        return self


class AgentSynthesisRequest(CommunityModel):
    """Daily synthesis input over cached and newly extracted topics."""

    topics: list[TopicMention] = Field(min_length=1)
    entity_catalog: list[EntityCatalogItem] = Field(min_length=1)
    synthesis_instructions: str = Field(min_length=1)
    model: str = Field(min_length=1)
    reasoning_effort: Literal["medium"]
    maximum_candidate_stories: int = Field(ge=1)


class AgentSynthesisResult(CommunityModel):
    """Strict synthesized candidates and current-run model usage."""

    candidates: CandidateStoryBatch
    usage: ModelUsage


class ExtractionCacheLookup(CommunityModel):
    """One exact document revision requested from the extraction cache."""

    source_key: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    content_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class ExtractionCacheEntryDraft(CommunityModel):
    """Insert-only structured extraction without source body text."""

    strategy_key: str = Field(min_length=1)
    strategy_version: int = Field(ge=1)
    source_key: str = Field(min_length=1)
    source_type: SourceType
    document_id: str = Field(min_length=1)
    external_id: str = Field(min_length=1)
    content_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    extraction_contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    document: SourceDocumentMetadata
    topics: TopicMentionBatch
    published_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def values_are_consistent(self) -> ExtractionCacheEntryDraft:
        if self.published_at.tzinfo is None or self.published_at.utcoffset() is None:
            raise ValueError("Cache published_at must be timezone-aware.")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("Cache expires_at must be timezone-aware.")
        if self.expires_at <= self.published_at:
            raise ValueError("Cache expires_at must follow published_at.")
        if (
            self.document.document_id != self.document_id
            or self.document.external_id != self.external_id
            or self.document.source_key != self.source_key
            or self.document.source_type is not self.source_type
            or self.document.published_at != self.published_at
        ):
            raise ValueError("Cache entry metadata does not match its key columns.")
        if any(
            topic.document_ids != [self.document_id]
            for topic in self.topics.topics
        ):
            raise ValueError("Cache topics may cite only their own document.")
        return self


class ExtractionCacheEntry(ExtractionCacheEntryDraft):
    """Stored extraction cache entry with database identity."""

    id: int = Field(ge=1)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def created_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Cache created_at must be timezone-aware.")
        return value


class ExtractionCacheUsage(CommunityModel):
    """Per-report cache behavior retained for cost review."""

    eligible_document_count: int = Field(ge=0)
    hit_count: int = Field(ge=0)
    miss_count: int = Field(ge=0)
    write_count: int = Field(ge=0)
    expired_entry_count: int = Field(ge=0)

    @model_validator(mode="after")
    def counts_are_consistent(self) -> ExtractionCacheUsage:
        if self.hit_count + self.miss_count != self.eligible_document_count:
            raise ValueError("Cache hits and misses must equal eligible documents.")
        if self.write_count > self.miss_count:
            raise ValueError("Cache writes cannot exceed cache misses.")
        return self


class EvidenceReference(CommunityModel):
    """Auditable source metadata retained without source body text."""

    document_id: str = Field(min_length=1)
    source_key: str = Field(min_length=1)
    source_type: SourceType
    publisher: str = Field(min_length=1)
    title: str = Field(min_length=1)
    url: HttpUrl
    published_at: datetime
    engagement: Engagement

    @field_validator("published_at")
    @classmethod
    def published_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Evidence published_at must be timezone-aware.")
        return value


class PlayerSnapshot(CommunityModel):
    """Player values as they existed when a report was generated."""

    web_name: str
    first_name: str
    second_name: str
    team_id: int
    team_name: str
    element_type_id: int
    element_type_name: str
    now_cost: int | None
    selected_by_percent: str | None
    total_points: int | None
    form: str | None
    minutes: int | None
    goals_scored: int | None
    assists: int | None
    clean_sheets: int | None
    status: str | None
    news: str | None
    chance_of_playing_next_round: int | None
    chance_of_playing_this_round: int | None


class TeamSnapshot(CommunityModel):
    """Team values as they existed when a report was generated."""

    name: str
    short_name: str
    strength: int | None
    strength_overall_home: int | None
    strength_overall_away: int | None
    strength_attack_home: int | None
    strength_attack_away: int | None
    strength_defence_home: int | None
    strength_defence_away: int | None


class EventSnapshot(CommunityModel):
    """Gameweek values as they existed when a report was generated."""

    name: str
    deadline_time: datetime | None
    average_entry_score: int | None
    highest_score: int | None
    highest_scoring_entry: int | None
    finished: bool | None
    data_checked: bool | None
    is_previous: bool
    is_current: bool
    is_next: bool

    @field_validator("deadline_time")
    @classmethod
    def deadline_is_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("Event deadline_time must be timezone-aware.")
        return value


class FixtureSnapshot(CommunityModel):
    """Fixture values as they existed when a report was generated."""

    event_id: int | None
    kickoff_time: datetime | None
    home_team_id: int
    home_team_name: str
    away_team_id: int
    away_team_name: str
    home_score: int | None
    away_score: int | None
    started: bool
    finished: bool

    @field_validator("kickoff_time")
    @classmethod
    def kickoff_is_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("Fixture kickoff_time must be timezone-aware.")
        return value


class PlayerReference(CommunityModel):
    entity_type: Literal[EntityType.PLAYER]
    season_id: str
    entity_id: int
    display_name: str
    snapshot: PlayerSnapshot


class TeamReference(CommunityModel):
    entity_type: Literal[EntityType.TEAM]
    season_id: str
    entity_id: int
    display_name: str
    snapshot: TeamSnapshot


class EventReference(CommunityModel):
    entity_type: Literal[EntityType.EVENT]
    season_id: str
    entity_id: int
    display_name: str
    snapshot: EventSnapshot


class FixtureReference(CommunityModel):
    entity_type: Literal[EntityType.FIXTURE]
    season_id: str
    entity_id: int
    display_name: str
    snapshot: FixtureSnapshot


EntityReference = Annotated[
    PlayerReference | TeamReference | EventReference | FixtureReference,
    Field(discriminator="entity_type"),
]


class MomentumComponents(CommunityModel):
    """Explainable component scores that add to the story score."""

    source_breadth: float = Field(ge=0, le=35)
    evidence_volume: float = Field(ge=0, le=20)
    engagement: float = Field(ge=0, le=20)
    recency: float = Field(ge=0, le=15)
    actionability: float = Field(ge=0, le=10)


class CommunityStory(CommunityModel):
    """One ranked, validated community story."""

    rank: int = Field(ge=1, le=10)
    headline: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    category: TopicCategory
    momentum_score: float = Field(ge=0, le=100)
    momentum_components: MomentumComponents
    evidence: list[EvidenceReference] = Field(min_length=1)
    entities: list[EntityReference] = Field(min_length=1)

    @model_validator(mode="after")
    def momentum_and_references_are_consistent(self) -> CommunityStory:
        component_total = sum(
            (
                self.momentum_components.source_breadth,
                self.momentum_components.evidence_volume,
                self.momentum_components.engagement,
                self.momentum_components.recency,
                self.momentum_components.actionability,
            ),
        )
        if abs(component_total - self.momentum_score) > 0.0001:
            raise ValueError("Momentum score must equal its component sum.")
        evidence_ids = [item.document_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("Story evidence document IDs must be unique.")
        entity_ids = [
            (item.entity_type, item.entity_id) for item in self.entities
        ]
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError("Story entity references must be unique.")
        return self


class CommunityReportContent(CommunityModel):
    """Single JSON document persisted for one report resource."""

    strategy_name: str = Field(min_length=1)
    strategy_description: str = Field(min_length=1)
    ranking_policy: str = Field(min_length=1)
    extraction_prompt_version: int = Field(ge=1)
    synthesis_prompt_version: int = Field(ge=1)
    target_story_count: int = Field(ge=1, le=10)
    coverage: CollectionCoverage
    extraction_cache: ExtractionCacheUsage
    model_usage: ModelUsage
    stories: list[CommunityStory] = Field(min_length=1, max_length=10)

    @field_validator("stories")
    @classmethod
    def story_ranks_are_contiguous(
        cls,
        value: list[CommunityStory],
    ) -> list[CommunityStory]:
        if [story.rank for story in value] != list(range(1, len(value) + 1)):
            raise ValueError("Community story ranks must be contiguous.")
        return value

    @model_validator(mode="after")
    def story_count_does_not_exceed_target(self) -> CommunityReportContent:
        if len(self.stories) > self.target_story_count:
            raise ValueError("Story count cannot exceed target_story_count.")
        return self


class CommunityReport(CommunityModel):
    """One immutable stored community report."""

    id: int = Field(ge=1)
    strategy_key: str = Field(min_length=1)
    strategy_version: int = Field(ge=1)
    report_date: date
    season_id: str = Field(min_length=1)
    as_of_event_id: int | None
    window_start: datetime
    window_end: datetime
    generated_at: datetime
    content: CommunityReportContent

    @model_validator(mode="after")
    def timestamps_and_date_are_consistent(self) -> CommunityReport:
        for name, value in (
            ("window_start", self.window_start),
            ("window_end", self.window_end),
            ("generated_at", self.generated_at),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware.")
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start.")
        if self.generated_at < self.window_end:
            raise ValueError("generated_at cannot precede window_end.")
        return self


class CommunityReportDraft(CommunityModel):
    """Insert-only report before PostgreSQL assigns its identity."""

    strategy_key: str = Field(min_length=1)
    strategy_version: int = Field(ge=1)
    report_date: date
    season_id: str = Field(min_length=1)
    as_of_event_id: int | None
    window_start: datetime
    window_end: datetime
    generated_at: datetime
    content: CommunityReportContent

    @model_validator(mode="after")
    def timestamps_are_consistent(self) -> CommunityReportDraft:
        for name, value in (
            ("window_start", self.window_start),
            ("window_end", self.window_end),
            ("generated_at", self.generated_at),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware.")
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start.")
        if self.generated_at < self.window_end:
            raise ValueError("generated_at cannot precede window_end.")
        return self


class CommunityReportSummary(CommunityModel):
    """Bounded history item without the complete report JSON."""

    id: int = Field(ge=1)
    strategy_key: str = Field(min_length=1)
    strategy_version: int = Field(ge=1)
    report_date: date
    season_id: str = Field(min_length=1)
    as_of_event_id: int | None
    window_start: datetime
    window_end: datetime
    generated_at: datetime
    story_count: int = Field(ge=1, le=10)
    successful_source_count: int = Field(ge=0)
    failed_source_count: int = Field(ge=0)

    @model_validator(mode="after")
    def timestamps_are_aware(self) -> CommunityReportSummary:
        for name, value in (
            ("window_start", self.window_start),
            ("window_end", self.window_end),
            ("generated_at", self.generated_at),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware.")
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start.")
        if self.generated_at < self.window_end:
            raise ValueError("generated_at cannot precede window_end.")
        return self


class ScoredCandidate(CommunityModel):
    """Candidate plus deterministic score before report enrichment."""

    candidate: CandidateStory
    score: float
    components: MomentumComponents
    newest_evidence_at: datetime
