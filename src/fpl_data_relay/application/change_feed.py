"""Change-event replay use cases."""

from fpl_data_relay.application.ports.persistence import ChangeEventRepository
from fpl_data_relay.domain.changes import (
    ChangeEvent,
    EntityChange,
    IngestionSourceStatus,
)


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

    async def list_recent_events(self, *, limit: int) -> list[ChangeEvent]:
        return await self._repository.list_recent_change_events(limit=limit)

    async def list_events_before(
        self,
        *,
        before_id: int,
        limit: int,
    ) -> list[ChangeEvent]:
        return await self._repository.list_change_events_before(
            before_id=before_id,
            limit=limit,
        )

    async def list_entity_changes(
        self,
        *,
        change_event_id: int,
        after_id: int,
        limit: int,
    ) -> list[EntityChange]:
        return await self._repository.list_entity_changes(
            change_event_id=change_event_id,
            after_id=after_id,
            limit=limit,
        )

    async def list_source_statuses(
        self,
        *,
        season_id: str,
    ) -> list[IngestionSourceStatus]:
        return await self._repository.list_ingestion_source_statuses(
            season_id=season_id,
        )
