"""Application boundaries for community source and agent adapters."""

from datetime import datetime
from typing import Protocol

from fpl_data_relay.domain.community import (
    AgentExtractionRequest,
    AgentExtractionResult,
    AgentSynthesisRequest,
    AgentSynthesisResult,
    CommunitySource,
    DiscoveredDocument,
    SourceDiscoveryResult,
    SourceMaterializationResult,
)


class CommunitySourceGateway(Protocol):
    """Discover source metadata and materialize bodies only when required."""

    async def discover(
        self,
        *,
        source: CommunitySource,
        window_start: datetime,
        window_end: datetime,
    ) -> SourceDiscoveryResult: ...

    async def materialize(
        self,
        *,
        source: CommunitySource,
        documents: list[DiscoveredDocument],
    ) -> SourceMaterializationResult: ...

    async def close(self) -> None: ...


class AgentAnalyzer(Protocol):
    """Extract and synthesize community topics using structured model output."""

    async def extract(
        self,
        *,
        request: AgentExtractionRequest,
    ) -> AgentExtractionResult: ...

    async def synthesize(
        self,
        *,
        request: AgentSynthesisRequest,
    ) -> AgentSynthesisResult: ...


class CommunityJobQueue(Protocol):
    """Enqueue versioned strategy jobs after a scheduled dispatch."""

    async def send(self, *, message_body: str) -> None: ...
