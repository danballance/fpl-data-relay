from datetime import UTC, datetime

import pytest

from fpl_data_relay.application.ports.administration import (
    ChangeFeedRebaselineResult,
)
from fpl_data_relay.application.rebaseline import ChangeFeedRebaselineService


class FakeRebaseliner:
    def __init__(self) -> None:
        self.reasons: list[str] = []

    async def rebaseline_current(
        self,
        *,
        reason: str,
    ) -> ChangeFeedRebaselineResult:
        self.reasons.append(reason)
        return ChangeFeedRebaselineResult(
            id=1,
            season_id="2026-27",
            reason=reason,
            change_events_deleted=2,
            entity_changes_deleted=3,
            snapshots_rebuilt=4,
            created_at=datetime(2026, 8, 22, tzinfo=UTC),
        )


@pytest.mark.asyncio
async def test_rebaseline_service_validates_reason_and_delegates() -> None:
    rebaseliner = FakeRebaseliner()
    service = ChangeFeedRebaselineService(rebaseliner=rebaseliner)
    result = await service.rebaseline_current(reason="season repair")
    assert result.season_id == "2026-27"
    assert rebaseliner.reasons == ["season repair"]
    with pytest.raises(ValueError, match="must not be blank"):
        await service.rebaseline_current(reason="  ")
