"""Deterministic ranking of model-synthesized community stories."""

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

from fpl_data_relay.domain.community import (
    Actionability,
    CandidateStory,
    MomentumComponents,
    ScoredCandidate,
    SourceDocument,
    SourceType,
)

ACTIONABILITY_FACTOR = {
    Actionability.LOW: 0.25,
    Actionability.MEDIUM: 0.60,
    Actionability.HIGH: 1.0,
}


class CommunityRankingPolicy(Protocol):
    """Rank validated candidate stories using only deterministic inputs."""

    def rank(
        self,
        *,
        candidates: list[CandidateStory],
        documents: Mapping[str, SourceDocument],
        window_start: datetime,
        window_end: datetime,
        limit: int,
    ) -> list[ScoredCandidate]: ...


class CommunityMomentumRankingPolicy:
    """Community breadth, volume, engagement, recency, and actionability."""

    def rank(
        self,
        *,
        candidates: list[CandidateStory],
        documents: Mapping[str, SourceDocument],
        window_start: datetime,
        window_end: datetime,
        limit: int,
    ) -> list[ScoredCandidate]:
        if window_end <= window_start:
            raise ValueError("Ranking window_end must be after window_start.")
        if limit < 1:
            raise ValueError("Ranking limit must be positive.")
        percentiles = engagement_percentiles(documents=documents)
        scored = [
            score_candidate(
                candidate=candidate,
                documents=documents,
                percentiles=percentiles,
                window_start=window_start,
                window_end=window_end,
            )
            for candidate in candidates
        ]
        scored.sort(
            key=lambda item: (
                -item.score,
                -len(
                    {
                        documents[document_id].source_key
                        for document_id in item.candidate.evidence_document_ids
                    },
                ),
                -len(set(item.candidate.evidence_document_ids)),
                -item.newest_evidence_at.timestamp(),
                item.candidate.headline.casefold().strip(),
            ),
        )
        return scored[:limit]


def engagement_percentiles(
    *,
    documents: Mapping[str, SourceDocument],
) -> dict[str, float]:
    """Normalize public engagement only against documents on the same platform."""
    platform_values: dict[SourceType, list[int]] = {
        SourceType.X: [],
        SourceType.YOUTUBE: [],
        SourceType.BLOG: [],
    }
    for document in documents.values():
        if document.engagement_score is not None:
            platform_values[document.source_type].append(document.engagement_score)
    percentiles: dict[str, float] = {}
    for document_id, document in documents.items():
        score = document.engagement_score
        if score is None:
            continue
        values = platform_values[document.source_type]
        percentiles[document_id] = sum(value <= score for value in values) / len(
            values,
        )
    return percentiles


def score_candidate(
    *,
    candidate: CandidateStory,
    documents: Mapping[str, SourceDocument],
    percentiles: Mapping[str, float],
    window_start: datetime,
    window_end: datetime,
) -> ScoredCandidate:
    """Calculate all documented momentum components for one candidate."""
    evidence_ids = list(dict.fromkeys(candidate.evidence_document_ids))
    try:
        evidence = [documents[document_id] for document_id in evidence_ids]
    except KeyError as exception:
        raise ValueError(
            f"Candidate cites unknown document {exception.args[0]!r}.",
        ) from exception
    if not evidence:
        raise ValueError("A candidate requires at least one evidence document.")
    source_breadth = 35 * min(len({item.source_key for item in evidence}), 5) / 5
    evidence_volume = 20 * min(len(evidence), 10) / 10
    measured = [
        percentiles[item.document_id]
        for item in evidence
        if item.document_id in percentiles
    ]
    engagement = 0.0 if not measured else 20 * sum(measured) / len(measured)
    window_seconds = (window_end - window_start).total_seconds()
    recencies = [
        max(
            0.0,
            min(
                1.0,
                (item.published_at - window_start).total_seconds() / window_seconds,
            ),
        )
        for item in evidence
    ]
    recency = 15 * sum(recencies) / len(recencies)
    actionability = 10 * ACTIONABILITY_FACTOR[candidate.actionability]
    components = MomentumComponents(
        source_breadth=round(source_breadth, 4),
        evidence_volume=round(evidence_volume, 4),
        engagement=round(engagement, 4),
        recency=round(recency, 4),
        actionability=round(actionability, 4),
    )
    total = sum(
        (
            components.source_breadth,
            components.evidence_volume,
            components.engagement,
            components.recency,
            components.actionability,
        ),
    )
    return ScoredCandidate(
        candidate=candidate,
        score=round(total, 4),
        components=components,
        newest_evidence_at=max(item.published_at for item in evidence),
    )
