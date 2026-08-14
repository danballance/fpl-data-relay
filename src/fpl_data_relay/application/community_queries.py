"""Read-only community report API use cases."""

from fpl_data_relay.application.community_strategies import (
    CommunityStrategyRegistry,
)
from fpl_data_relay.application.ports.persistence import CommunityReportRepository
from fpl_data_relay.domain.community import (
    CommunityReport,
    CommunityReportSummary,
    CommunityStrategySummary,
)


class CommunityQueries:
    """Validate strategy keys and expose immutable report history."""

    def __init__(
        self,
        *,
        repository: CommunityReportRepository,
        registry: CommunityStrategyRegistry,
    ) -> None:
        self._repository = repository
        self._registry = registry

    def list_strategies(self) -> list[CommunityStrategySummary]:
        return [
            CommunityStrategySummary(
                key=item.key,
                name=item.name,
                description=item.description,
                cadence=item.schedule_expression,
                timezone=item.schedule_timezone,
                lookback_days=item.lookback_days,
                target_story_count=item.target_story_count,
            )
            for item in self._registry.list_definitions()
        ]

    def has_strategy(self, *, strategy_key: str) -> bool:
        return self._registry.get(strategy_key=strategy_key) is not None

    async def latest(self, *, strategy_key: str) -> CommunityReport | None:
        return await self._repository.get_latest_report(strategy_key=strategy_key)

    async def get(self, *, report_id: int) -> CommunityReport | None:
        return await self._repository.get_report(report_id=report_id)

    async def recent(
        self,
        *,
        strategy_key: str,
        limit: int,
    ) -> list[CommunityReportSummary]:
        return await self._repository.list_recent_reports(
            strategy_key=strategy_key,
            limit=limit,
        )

    async def history(
        self,
        *,
        strategy_key: str,
        before_id: int,
        limit: int,
    ) -> list[CommunityReportSummary]:
        return await self._repository.list_reports_before(
            strategy_key=strategy_key,
            before_id=before_id,
            limit=limit,
        )
