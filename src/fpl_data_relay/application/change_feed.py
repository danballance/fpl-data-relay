"""Change-event replay use cases."""

from fpl_data_relay.application.ports.persistence import ChangeEventRepository
from fpl_data_relay.domain.changes import ChangeEvent


class ChangeFeed:
    """Expose change-event reads through a narrow repository."""

    def __init__(self, *, repository: ChangeEventRepository) -> None:
        self._repository = repository

    async def list_events(
        self,
        *,
        after_id: int,
        limit: int,
    ) -> list[ChangeEvent]:
        return await self._repository.list_change_events(
            after_id=after_id,
            limit=limit,
        )
