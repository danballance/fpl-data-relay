"""Scheduled community collection, analysis, validation, and publication."""

import asyncio
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from fpl_data_relay.application.community_jobs import CommunityStrategyJob
from fpl_data_relay.application.community_ranking import CommunityRankingPolicy
from fpl_data_relay.application.community_strategies import (
    CommunityStrategyRegistry,
)
from fpl_data_relay.application.errors import (
    CommunityModelError,
    CommunityPublicationError,
    CommunitySourceError,
)
from fpl_data_relay.application.ports.community import (
    AgentAnalyzer,
    CommunitySourceGateway,
)
from fpl_data_relay.application.ports.persistence import (
    CommunityExtractionCacheRepository,
    CommunityReportRepository,
    ReferenceRepository,
)
from fpl_data_relay.domain.community import (
    AgentExtractionRequest,
    AgentSynthesisRequest,
    CandidateStory,
    CollectionCoverage,
    CommunityReport,
    CommunityReportContent,
    CommunityReportDraft,
    CommunitySource,
    CommunityStory,
    DiscoveredDocument,
    DocumentExtraction,
    EntityCatalogItem,
    EntityConfidence,
    EntityReference,
    EntityType,
    EventReference,
    EventSnapshot,
    EvidenceReference,
    ExtractionCacheEntry,
    ExtractionCacheEntryDraft,
    ExtractionCacheLookup,
    ExtractionCacheUsage,
    FixtureReference,
    FixtureSnapshot,
    ModelUsage,
    PlayerReference,
    PlayerSnapshot,
    SourceDiscoveryResult,
    SourceDocument,
    SourceDocumentMetadata,
    SourceFailure,
    SourceMaterializationResult,
    SourceType,
    TeamReference,
    TeamSnapshot,
    TopicMention,
)
from fpl_data_relay.domain.fixtures import Fixture
from fpl_data_relay.domain.reference import Element, ElementType, Event, Season, Team

PAGE_SIZE = 200


class ReferenceBundle(BaseModel):
    """In-memory canonical state used for model validation and snapshots."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    season: Season
    current_event: Event | None
    events: dict[int, Event]
    teams: dict[int, Team]
    element_types: dict[int, ElementType]
    elements: dict[int, Element]
    fixtures: dict[int, Fixture]


class CommunityService:
    """Run one versioned strategy job and insert at most one report."""

    def __init__(
        self,
        *,
        registry: CommunityStrategyRegistry,
        source_gateway: CommunitySourceGateway,
        analyzer: AgentAnalyzer,
        ranking_policy: CommunityRankingPolicy,
        reports: CommunityReportRepository,
        extraction_cache: CommunityExtractionCacheRepository,
        references: ReferenceRepository,
        clock: Callable[[], datetime],
    ) -> None:
        self._registry = registry
        self._source_gateway = source_gateway
        self._analyzer = analyzer
        self._ranking_policy = ranking_policy
        self._reports = reports
        self._extraction_cache = extraction_cache
        self._references = references
        self._clock = clock

    async def run(self, *, job: CommunityStrategyJob) -> CommunityReport:
        """Run an idempotent complete report workflow."""
        strategy = self._registry.require(strategy_key=job.strategy_key)
        definition = strategy.definition
        if definition.version != job.strategy_version:
            raise ValueError(
                f"Strategy version mismatch: job={job.strategy_version}, "
                f"configured={definition.version}.",
            )
        expected_report_date = job.window_end.astimezone(
            ZoneInfo(definition.schedule_timezone),
        ).date()
        if job.report_date != expected_report_date:
            raise ValueError(
                "Strategy job report_date does not match its London window end.",
            )
        if job.window_start != (
            job.window_end - timedelta(days=definition.lookback_days)
        ):
            raise ValueError(
                "Strategy job window does not match its configured lookback.",
            )
        existing = await self._reports.get_report_for_date(
            strategy_key=job.strategy_key,
            report_date=job.report_date,
        )
        if existing is not None:
            return existing
        cache_as_of = self._clock().astimezone(UTC)
        expired_entry_count = await self._extraction_cache.prune_expired(
            as_of=cache_as_of,
        )
        bundle = await self._load_references()
        discovery_results, failures = await self._discover_sources(
            sources=definition.sources,
            window_start=job.window_start,
            window_end=job.window_end,
        )
        discovered = _deduplicate_discovered(results=discovery_results)
        if not discovered:
            raise CommunityPublicationError(
                "No source documents were available for community analysis.",
            )
        extraction_instructions = strategy.extraction_instructions()
        extraction_contract_hash = _extraction_contract_hash(
            extraction_instructions=extraction_instructions,
            model=definition.model,
            reasoning_effort=definition.reasoning_effort,
            chunk_characters=definition.chunk_characters,
        )
        cached_entries = await self._extraction_cache.get_entries(
            strategy_key=definition.key,
            strategy_version=definition.version,
            extraction_contract_hash=extraction_contract_hash,
            lookups=[
                ExtractionCacheLookup(
                    source_key=document.source_key,
                    document_id=document.document_id,
                    content_revision=document.content_revision,
                )
                for document in discovered.values()
            ],
            as_of=cache_as_of,
        )
        cache_hits = _validated_cache_hits(
            entries=cached_entries,
            discovered=discovered,
            strategy_key=definition.key,
            strategy_version=definition.version,
            extraction_contract_hash=extraction_contract_hash,
            as_of=cache_as_of,
        )
        misses = [
            document
            for document in discovered.values()
            if document.document_id not in cache_hits
        ]
        materialization_results, materialization_failures = (
            await self._materialize_sources(
                sources=definition.sources,
                documents=misses,
            )
        )
        failures.extend(materialization_failures)
        failed_source_keys = {failure.source_key for failure in failures}
        successful_discovery_results = [
            result
            for result in discovery_results
            if result.source_key not in failed_source_keys
        ]
        successful_discovered = {
            document_id: document
            for document_id, document in discovered.items()
            if document.source_key not in failed_source_keys
        }
        cache_hits = {
            document_id: entry
            for document_id, entry in cache_hits.items()
            if entry.source_key not in failed_source_keys
        }
        materialized = _validated_materialized_documents(
            results=materialization_results,
            discovered=successful_discovered,
        )
        documents = {
            document_id: _cache_hit_metadata(
                discovered=successful_discovered[document_id],
                cached=entry.document,
            )
            for document_id, entry in cache_hits.items()
        }
        documents.update(
            {
                document_id: _metadata(document=document)
                for document_id, document in materialized.items()
            },
        )
        if not documents:
            raise CommunityPublicationError(
                "No source documents were available for community analysis.",
            )
        extraction_usage = _empty_usage(
            model=definition.model,
            reasoning_effort=definition.reasoning_effort,
        )
        extracted_topics: dict[str, list[TopicMention]] = {}
        write_count = 0
        if materialized:
            extraction = await self._analyzer.extract(
                request=AgentExtractionRequest(
                    documents=list(materialized.values()),
                    extraction_instructions=extraction_instructions,
                    model=definition.model,
                    reasoning_effort=definition.reasoning_effort,
                    extraction_concurrency=definition.extraction_concurrency,
                    chunk_characters=definition.chunk_characters,
                ),
            )
            extracted_topics = _validated_extractions(
                extracted=extraction.documents,
                documents=materialized,
            )
            extraction_usage = extraction.usage
            cache_drafts = [
                ExtractionCacheEntryDraft(
                    strategy_key=definition.key,
                    strategy_version=definition.version,
                    source_key=document.source_key,
                    source_type=document.source_type,
                    document_id=document.document_id,
                    external_id=document.external_id,
                    content_revision=(
                        successful_discovered[document.document_id].content_revision
                    ),
                    extraction_contract_hash=extraction_contract_hash,
                    document=_metadata(document=document),
                    topics=next(
                        item.topics
                        for item in extraction.documents
                        if item.document_id == document.document_id
                    ),
                    published_at=document.published_at,
                    expires_at=document.published_at
                    + timedelta(days=definition.extraction_cache_retention_days),
                )
                for document in materialized.values()
            ]
            write_count = await self._extraction_cache.insert_entries(
                entries=cache_drafts,
            )
        topics = [
            topic
            for document_id in documents
            for topic in (
                cache_hits[document_id].topics.topics
                if document_id in cache_hits
                else extracted_topics[document_id]
            )
        ]
        if not topics:
            raise CommunityPublicationError(
                "Community extraction produced no topics for synthesis.",
            )
        synthesis = await self._analyzer.synthesize(
            request=AgentSynthesisRequest(
                topics=topics,
                entity_catalog=_catalog(bundle=bundle),
                synthesis_instructions=strategy.synthesis_instructions(),
                model=definition.model,
                reasoning_effort=definition.reasoning_effort,
                maximum_candidate_stories=definition.maximum_candidate_stories,
            ),
        )
        candidates = _validated_candidates(
            candidates=synthesis.candidates.stories,
            documents=documents,
            bundle=bundle,
        )
        ranked = self._ranking_policy.rank(
            candidates=candidates,
            documents=documents,
            window_start=job.window_start,
            window_end=job.window_end,
            limit=definition.target_story_count,
        )
        if len(ranked) < definition.minimum_story_count:
            raise CommunityPublicationError(
                "Community analysis produced no publishable canonical stories.",
            )
        stories = [
            CommunityStory(
                rank=index,
                headline=scored.candidate.headline,
                summary=scored.candidate.summary,
                category=scored.candidate.category,
                momentum_score=scored.score,
                momentum_components=scored.components,
                evidence=[
                    _evidence(document=documents[document_id])
                    for document_id in dict.fromkeys(
                        scored.candidate.evidence_document_ids,
                    )
                ],
                entities=[
                    _entity_reference(
                        entity_type=link.entity_type,
                        entity_id=link.entity_id,
                        bundle=bundle,
                    )
                    for link in scored.candidate.entity_links
                    if link.confidence is EntityConfidence.HIGH
                ],
            )
            for index, scored in enumerate(ranked, start=1)
        ]
        coverage = _coverage(
            sources=definition.sources,
            discovery_results=successful_discovery_results,
            materialization_results=materialization_results,
            failures=failures,
            documents=documents,
        )
        extraction_cache_usage = ExtractionCacheUsage(
            eligible_document_count=len(successful_discovered),
            hit_count=len(cache_hits),
            miss_count=len(successful_discovered) - len(cache_hits),
            write_count=write_count,
            expired_entry_count=expired_entry_count,
        )
        generated_at = self._clock().astimezone(UTC)
        return await self._reports.insert_report(
            report=CommunityReportDraft(
                strategy_key=definition.key,
                strategy_version=definition.version,
                report_date=job.report_date,
                season_id=bundle.season.id,
                as_of_event_id=(
                    None if bundle.current_event is None else bundle.current_event.id
                ),
                window_start=job.window_start.astimezone(UTC),
                window_end=job.window_end.astimezone(UTC),
                generated_at=generated_at,
                content=CommunityReportContent(
                    strategy_name=definition.name,
                    strategy_description=definition.description,
                    ranking_policy=definition.ranking_policy,
                    extraction_prompt_version=(
                        definition.extraction_prompt_version
                    ),
                    synthesis_prompt_version=definition.synthesis_prompt_version,
                    target_story_count=definition.target_story_count,
                    coverage=coverage,
                    extraction_cache=extraction_cache_usage,
                    model_usage=_combine_usage(
                        extraction=extraction_usage,
                        synthesis=synthesis.usage,
                    ),
                    stories=stories,
                ),
            ),
        )

    async def _load_references(self) -> ReferenceBundle:
        season = await self._references.get_current_season()
        if season is None:
            raise CommunityPublicationError("No current FPL season is stored.")
        current_event, events, teams, element_types, elements, fixtures = (
            await asyncio.gather(
                self._references.get_current_event(season_id=season.id),
                self._references.list_events(season_id=season.id),
                self._references.list_teams(season_id=season.id),
                self._references.list_element_types(season_id=season.id),
                _all_elements(repository=self._references, season_id=season.id),
                _all_fixtures(repository=self._references, season_id=season.id),
            )
        )
        return ReferenceBundle(
            season=season,
            current_event=current_event,
            events={item.id: item for item in events},
            teams={item.id: item for item in teams},
            element_types={item.id: item for item in element_types},
            elements={item.id: item for item in elements},
            fixtures={item.id: item for item in fixtures},
        )

    async def _discover_sources(
        self,
        *,
        sources: list[CommunitySource],
        window_start: datetime,
        window_end: datetime,
    ) -> tuple[list[SourceDiscoveryResult], list[SourceFailure]]:
        tasks = [
            asyncio.create_task(
                self._discover_source(
                    source=source,
                    window_start=window_start,
                    window_end=window_end,
                ),
            )
            for source in sources
        ]
        try:
            collected = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        results: list[SourceDiscoveryResult] = []
        failures: list[SourceFailure] = []
        for result, failure in collected:
            if result is not None:
                results.append(result)
            if failure is not None:
                failures.append(failure)
        return results, failures

    async def _discover_source(
        self,
        *,
        source: CommunitySource,
        window_start: datetime,
        window_end: datetime,
    ) -> tuple[SourceDiscoveryResult | None, SourceFailure | None]:
        try:
            result = await self._source_gateway.discover(
                source=source,
                window_start=window_start,
                window_end=window_end,
            )
        except CommunitySourceError as exception:
            if exception.fatal:
                raise
            return None, SourceFailure(
                source_key=source.key,
                source_type=source.type,
                code=exception.code,
            )
        if result.source_key != source.key:
            raise CommunityModelError("Collector returned the wrong source key.")
        for document in result.documents:
            if (
                document.source_key != source.key
                or document.source_type is not source.type
            ):
                raise CommunityModelError(
                    "Collector returned a document for the wrong source.",
                )
            if not window_start <= document.published_at < window_end:
                raise CommunityModelError(
                    "Collector returned a document outside the requested window.",
                )
        return result, None

    async def _materialize_sources(
        self,
        *,
        sources: list[CommunitySource],
        documents: list[DiscoveredDocument],
    ) -> tuple[list[SourceMaterializationResult], list[SourceFailure]]:
        by_source: dict[str, list[DiscoveredDocument]] = {}
        for document in documents:
            by_source.setdefault(document.source_key, []).append(document)
        tasks = [
            asyncio.create_task(
                self._materialize_source(
                    source=source,
                    documents=by_source[source.key],
                ),
            )
            for source in sources
            if source.key in by_source
        ]
        try:
            collected = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        results: list[SourceMaterializationResult] = []
        failures: list[SourceFailure] = []
        for result, failure in collected:
            if result is not None:
                results.append(result)
            if failure is not None:
                failures.append(failure)
        return results, failures

    async def _materialize_source(
        self,
        *,
        source: CommunitySource,
        documents: list[DiscoveredDocument],
    ) -> tuple[SourceMaterializationResult | None, SourceFailure | None]:
        try:
            result = await self._source_gateway.materialize(
                source=source,
                documents=documents,
            )
        except CommunitySourceError as exception:
            if exception.fatal:
                raise
            return None, SourceFailure(
                source_key=source.key,
                source_type=source.type,
                code=exception.code,
            )
        if result.source_key != source.key:
            raise CommunityModelError("Materializer returned the wrong source key.")
        requested_ids = {document.document_id for document in documents}
        for document in result.documents:
            if (
                document.source_key != source.key
                or document.source_type is not source.type
                or document.document_id not in requested_ids
            ):
                raise CommunityModelError(
                    "Materializer returned an unexpected source document.",
                )
        return result, None


async def _all_elements(
    *,
    repository: ReferenceRepository,
    season_id: str,
) -> list[Element]:
    results: list[Element] = []
    after_id = 0
    while True:
        page = await repository.list_elements(
            season_id=season_id,
            after_id=after_id,
            limit=PAGE_SIZE,
        )
        results.extend(page)
        if len(page) < PAGE_SIZE:
            return results
        after_id = page[-1].id


async def _all_fixtures(
    *,
    repository: ReferenceRepository,
    season_id: str,
) -> list[Fixture]:
    results: list[Fixture] = []
    after_id = 0
    while True:
        page = await repository.list_fixtures(
            season_id=season_id,
            event_id=None,
            after_id=after_id,
            limit=PAGE_SIZE,
        )
        results.extend(page)
        if len(page) < PAGE_SIZE:
            return results
        after_id = page[-1].id


def _deduplicate_discovered(
    *,
    results: list[SourceDiscoveryResult],
) -> dict[str, DiscoveredDocument]:
    documents: dict[str, DiscoveredDocument] = {}
    native_ids: set[tuple[SourceType, str]] = set()
    urls: set[str] = set()
    for result in results:
        for document in result.documents:
            native_key = (document.source_type, document.external_id)
            canonical_url = str(document.url)
            if native_key in native_ids or canonical_url in urls:
                continue
            if document.document_id in documents:
                raise CommunityModelError(
                    f"Duplicate document ID {document.document_id!r}.",
                )
            native_ids.add(native_key)
            urls.add(canonical_url)
            documents[document.document_id] = document
    return dict(
        sorted(
            documents.items(),
            key=lambda item: (item[1].published_at, item[0]),
        ),
    )


def _validated_cache_hits(
    *,
    entries: list[ExtractionCacheEntry],
    discovered: dict[str, DiscoveredDocument],
    strategy_key: str,
    strategy_version: int,
    extraction_contract_hash: str,
    as_of: datetime,
) -> dict[str, ExtractionCacheEntry]:
    hits: dict[str, ExtractionCacheEntry] = {}
    for entry in entries:
        document = discovered.get(entry.document_id)
        if document is None:
            raise CommunityModelError(
                f"Cache returned unknown document ID {entry.document_id!r}.",
            )
        if (
            entry.strategy_key != strategy_key
            or entry.strategy_version != strategy_version
            or entry.source_key != document.source_key
            or entry.source_type is not document.source_type
            or entry.external_id != document.external_id
            or entry.content_revision != document.content_revision
            or entry.extraction_contract_hash != extraction_contract_hash
            or entry.published_at != document.published_at
            or entry.expires_at <= as_of
        ):
            raise CommunityModelError(
                f"Cache returned inconsistent document {entry.document_id!r}.",
            )
        if entry.document_id in hits:
            raise CommunityModelError(
                f"Cache returned duplicate document {entry.document_id!r}.",
            )
        hits[entry.document_id] = entry
    return hits


def _validated_materialized_documents(
    *,
    results: list[SourceMaterializationResult],
    discovered: dict[str, DiscoveredDocument],
) -> dict[str, SourceDocument]:
    documents: dict[str, SourceDocument] = {}
    for result in results:
        for document in result.documents:
            expected = discovered.get(document.document_id)
            if expected is None:
                raise CommunityModelError(
                    f"Materializer returned unknown document {document.document_id!r}.",
                )
            if (
                document.source_key != expected.source_key
                or document.source_type is not expected.source_type
                or document.external_id != expected.external_id
                or document.published_at != expected.published_at
            ):
                raise CommunityModelError(
                    f"Materialized document {document.document_id!r} changed identity.",
                )
            if document.document_id in documents:
                raise CommunityModelError(
                    f"Materializer duplicated document {document.document_id!r}.",
                )
            documents[document.document_id] = document
    return documents


def _validated_extractions(
    *,
    extracted: list[DocumentExtraction],
    documents: dict[str, SourceDocument],
) -> dict[str, list[TopicMention]]:
    returned = {item.document_id: item for item in extracted}
    if len(returned) != len(extracted) or set(returned) != set(documents):
        raise CommunityModelError(
            "Extraction results did not match materialized documents.",
        )
    return {
        document_id: item.topics.topics for document_id, item in returned.items()
    }


def _metadata(*, document: SourceDocument) -> SourceDocumentMetadata:
    return SourceDocumentMetadata.model_validate(
        document.model_dump(exclude={"text"}),
    )


def _cache_hit_metadata(
    *,
    discovered: DiscoveredDocument,
    cached: SourceDocumentMetadata,
) -> SourceDocumentMetadata:
    values = discovered.model_dump(
        exclude={"content_revision", "transient_text"},
    )
    if discovered.source_type is SourceType.BLOG:
        values["url"] = cached.url
    return SourceDocumentMetadata.model_validate(values)


def _extraction_contract_hash(
    *,
    extraction_instructions: str,
    model: str,
    reasoning_effort: str,
    chunk_characters: int,
) -> str:
    payload = json.dumps(
        {
            "extraction_instructions": extraction_instructions,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "chunk_characters": chunk_characters,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _empty_usage(*, model: str, reasoning_effort: str) -> ModelUsage:
    return ModelUsage(
        provider="openai",
        model=model,
        reasoning_effort=reasoning_effort,
        response_ids=[],
        input_tokens=0,
        output_tokens=0,
    )


def _combine_usage(
    *,
    extraction: ModelUsage,
    synthesis: ModelUsage,
) -> ModelUsage:
    if (
        extraction.model != synthesis.model
        or extraction.reasoning_effort != synthesis.reasoning_effort
    ):
        raise CommunityModelError("Extraction and synthesis usage models differ.")
    return ModelUsage(
        provider="openai",
        model=extraction.model,
        reasoning_effort=extraction.reasoning_effort,
        response_ids=[*extraction.response_ids, *synthesis.response_ids],
        input_tokens=extraction.input_tokens + synthesis.input_tokens,
        output_tokens=extraction.output_tokens + synthesis.output_tokens,
    )


def _catalog(*, bundle: ReferenceBundle) -> list[EntityCatalogItem]:
    catalog: list[EntityCatalogItem] = []
    for player in bundle.elements.values():
        team = bundle.teams[player.team]
        position = bundle.element_types[player.element_type]
        catalog.append(
            EntityCatalogItem(
                entity_type=EntityType.PLAYER,
                entity_id=player.id,
                display_name=player.web_name,
                aliases=list(
                    dict.fromkeys(
                        (
                            player.web_name,
                            player.first_name,
                            player.second_name,
                            f"{player.first_name} {player.second_name}",
                        ),
                    ),
                ),
                context=(
                    f"{team.name}; {position.singular_name}; "
                    f"price={player.now_cost}; status={player.status}"
                ),
            ),
        )
    for team in bundle.teams.values():
        catalog.append(
            EntityCatalogItem(
                entity_type=EntityType.TEAM,
                entity_id=team.id,
                display_name=team.name,
                aliases=list(dict.fromkeys((team.name, team.short_name))),
                context="Premier League team",
            ),
        )
    for event in bundle.events.values():
        catalog.append(
            EntityCatalogItem(
                entity_type=EntityType.EVENT,
                entity_id=event.id,
                display_name=event.name,
                aliases=[event.name, f"GW{event.id}", f"Gameweek {event.id}"],
                context=(
                    f"deadline={event.deadline_time}; current={event.is_current}; "
                    f"next={event.is_next}"
                ),
            ),
        )
    for fixture in bundle.fixtures.values():
        home = bundle.teams[fixture.team_h]
        away = bundle.teams[fixture.team_a]
        display_name = f"{home.short_name} v {away.short_name}"
        catalog.append(
            EntityCatalogItem(
                entity_type=EntityType.FIXTURE,
                entity_id=fixture.id,
                display_name=display_name,
                aliases=[display_name, f"{home.name} v {away.name}"],
                context=f"gameweek={fixture.event}; kickoff={fixture.kickoff_time}",
            ),
        )
    return catalog


def _validated_candidates(
    *,
    candidates: list[CandidateStory],
    documents: dict[str, SourceDocumentMetadata],
    bundle: ReferenceBundle,
) -> list[CandidateStory]:
    valid_entity_ids = {
        EntityType.PLAYER: set(bundle.elements),
        EntityType.TEAM: set(bundle.teams),
        EntityType.EVENT: set(bundle.events),
        EntityType.FIXTURE: set(bundle.fixtures),
    }
    accepted: list[CandidateStory] = []
    for candidate in candidates:
        for document_id in candidate.evidence_document_ids:
            if document_id not in documents:
                raise CommunityModelError(
                    f"Model cited unknown document ID {document_id!r}.",
                )
        for link in candidate.entity_links:
            if link.entity_id not in valid_entity_ids[link.entity_type]:
                raise CommunityModelError(
                    f"Model selected invalid {link.entity_type} ID {link.entity_id}.",
                )
        high_links = [
            link
            for link in candidate.entity_links
            if link.confidence is EntityConfidence.HIGH
        ]
        if high_links:
            accepted.append(candidate.model_copy(update={"entity_links": high_links}))
    return accepted


def _evidence(*, document: SourceDocumentMetadata) -> EvidenceReference:
    return EvidenceReference(
        document_id=document.document_id,
        source_key=document.source_key,
        source_type=document.source_type,
        publisher=document.publisher,
        title=document.title,
        url=document.url,
        published_at=document.published_at,
        engagement=document.engagement,
    )


def _entity_reference(
    *,
    entity_type: EntityType,
    entity_id: int,
    bundle: ReferenceBundle,
) -> EntityReference:
    season_id = bundle.season.id
    if entity_type is EntityType.PLAYER:
        player = bundle.elements[entity_id]
        team = bundle.teams[player.team]
        position = bundle.element_types[player.element_type]
        return PlayerReference(
            entity_type=EntityType.PLAYER,
            season_id=season_id,
            entity_id=entity_id,
            display_name=player.web_name,
            snapshot=PlayerSnapshot(
                web_name=player.web_name,
                first_name=player.first_name,
                second_name=player.second_name,
                team_id=team.id,
                team_name=team.name,
                element_type_id=position.id,
                element_type_name=position.singular_name,
                now_cost=player.now_cost,
                selected_by_percent=player.selected_by_percent,
                total_points=player.total_points,
                form=player.form,
                minutes=player.minutes,
                goals_scored=player.goals_scored,
                assists=player.assists,
                clean_sheets=player.clean_sheets,
                status=player.status,
                news=player.news,
                chance_of_playing_next_round=player.chance_of_playing_next_round,
                chance_of_playing_this_round=player.chance_of_playing_this_round,
            ),
        )
    if entity_type is EntityType.TEAM:
        team = bundle.teams[entity_id]
        return TeamReference(
            entity_type=EntityType.TEAM,
            season_id=season_id,
            entity_id=entity_id,
            display_name=team.name,
            snapshot=TeamSnapshot(
                name=team.name,
                short_name=team.short_name,
                strength=team.strength,
                strength_overall_home=team.strength_overall_home,
                strength_overall_away=team.strength_overall_away,
                strength_attack_home=team.strength_attack_home,
                strength_attack_away=team.strength_attack_away,
                strength_defence_home=team.strength_defence_home,
                strength_defence_away=team.strength_defence_away,
            ),
        )
    if entity_type is EntityType.EVENT:
        event = bundle.events[entity_id]
        return EventReference(
            entity_type=EntityType.EVENT,
            season_id=season_id,
            entity_id=entity_id,
            display_name=event.name,
            snapshot=EventSnapshot(
                name=event.name,
                deadline_time=event.deadline_time,
                average_entry_score=event.average_entry_score,
                highest_score=event.highest_score,
                highest_scoring_entry=event.highest_scoring_entry,
                finished=event.finished,
                data_checked=event.data_checked,
                is_previous=event.is_previous,
                is_current=event.is_current,
                is_next=event.is_next,
            ),
        )
    fixture = bundle.fixtures[entity_id]
    home = bundle.teams[fixture.team_h]
    away = bundle.teams[fixture.team_a]
    return FixtureReference(
        entity_type=EntityType.FIXTURE,
        season_id=season_id,
        entity_id=entity_id,
        display_name=f"{home.short_name} v {away.short_name}",
        snapshot=FixtureSnapshot(
            event_id=fixture.event,
            kickoff_time=fixture.kickoff_time,
            home_team_id=home.id,
            home_team_name=home.name,
            away_team_id=away.id,
            away_team_name=away.name,
            home_score=fixture.team_h_score,
            away_score=fixture.team_a_score,
            started=fixture.started,
            finished=fixture.finished,
        ),
    )


def _coverage(
    *,
    sources: list[CommunitySource],
    discovery_results: list[SourceDiscoveryResult],
    materialization_results: list[SourceMaterializationResult],
    failures: list[SourceFailure],
    documents: dict[str, SourceDocumentMetadata],
) -> CollectionCoverage:
    counts = {source_type: 0 for source_type in SourceType}
    for document in documents.values():
        counts[document.source_type] += 1
    return CollectionCoverage(
        configured_source_count=len(sources),
        successful_source_count=len(discovery_results),
        failed_sources=failures,
        excluded_document_count=sum(
            result.excluded_document_count
            for result in [*discovery_results, *materialization_results]
        ),
        exclusions=[
            exclusion
            for result in [*discovery_results, *materialization_results]
            for exclusion in result.exclusions
        ],
        x_document_count=counts[SourceType.X],
        youtube_document_count=counts[SourceType.YOUTUBE],
        blog_document_count=counts[SourceType.BLOG],
    )
