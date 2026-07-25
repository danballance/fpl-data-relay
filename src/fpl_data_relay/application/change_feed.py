"""Change-event replay and stream use cases."""

from collections.abc import AsyncIterator

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

    def watch_events(
        self,
        *,
        after_id: int,
        heartbeat_seconds: int,
    ) -> AsyncIterator[ChangeEvent | None]:
        return self._repository.watch_change_events(
            after_id=after_id,
            heartbeat_seconds=heartbeat_seconds,
        )
