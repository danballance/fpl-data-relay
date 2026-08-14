"""Application boundaries for community source and agent adapters."""

from datetime import datetime
from typing import Protocol

from fpl_data_relay.domain.community import (
    AgentAnalysisRequest,
    AgentAnalysisResult,
    CommunitySource,
    SourceCollectionResult,
)


class CommunitySourceGateway(Protocol):
    """Collect one configured source into normalized documents."""

    async def collect(
        self,
        *,
        source: CommunitySource,
        window_start: datetime,
        window_end: datetime,
    ) -> SourceCollectionResult: ...

    async def close(self) -> None: ...


class AgentAnalyzer(Protocol):
    """Extract and synthesize community topics using structured model output."""

    async def analyze(
        self,
        *,
        request: AgentAnalysisRequest,
    ) -> AgentAnalysisResult: ...


class CommunityJobQueue(Protocol):
    """Enqueue versioned strategy jobs after a scheduled dispatch."""

    async def send(self, *, message_body: str) -> None: ...
