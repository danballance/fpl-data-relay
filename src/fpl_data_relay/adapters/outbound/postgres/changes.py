"""PostgreSQL change-event repository adapter."""

from fpl_data_relay.adapters.outbound.postgres.database import PostgresDatabase
from fpl_data_relay.domain.changes import (
    ChangeEvent,
    EntityChange,
    IngestionSourceStatus,
)


class PostgresChangeEventRepository:
    """Expose only change-event replay."""

    def __init__(self, *, database: PostgresDatabase) -> None:
        self._database = database

    async def list_change_events(
        self,
        *,
        after_id: int,
        limit: int,
    ) -> list[ChangeEvent]:
        return await self._database.list_change_events(
            after_id=after_id,
            limit=limit,
        )

    async def list_recent_change_events(self, *, limit: int) -> list[ChangeEvent]:
        return await self._database.list_recent_change_events(limit=limit)

    async def list_change_events_before(
        self,
        *,
        before_id: int,
        limit: int,
    ) -> list[ChangeEvent]:
        return await self._database.list_change_events_before(
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
        return await self._database.list_entity_changes(
            change_event_id=change_event_id,
            after_id=after_id,
            limit=limit,
        )

    async def list_ingestion_source_statuses(
        self,
        *,
        season_id: str,
    ) -> list[IngestionSourceStatus]:
        return await self._database.list_ingestion_source_statuses(
            season_id=season_id,
        )
