"""PostgreSQL change-event repository adapter."""

from fpl_data_relay.adapters.outbound.postgres.database import PostgresDatabase
from fpl_data_relay.domain.changes import ChangeEvent


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
