"""Administrative change-feed rebaseline use case."""

from fpl_data_relay.application.ports.administration import (
    ChangeFeedRebaseliner,
    ChangeFeedRebaselineResult,
)


class ChangeFeedRebaselineService:
    """Coordinate a deliberate current-season change-feed rebaseline."""

    def __init__(self, *, rebaseliner: ChangeFeedRebaseliner) -> None:
        self._rebaseliner = rebaseliner

    async def rebaseline_current(
        self,
        *,
        reason: str,
    ) -> ChangeFeedRebaselineResult:
        """Validate intent and replace the current season's baseline."""
        if reason.strip() == "":
            raise ValueError("Rebaseline reason must not be blank.")
        return await self._rebaseliner.rebaseline_current(reason=reason)
