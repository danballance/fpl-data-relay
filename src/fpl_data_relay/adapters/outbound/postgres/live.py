"""PostgreSQL live-data repository adapter."""

from fpl_data_relay.adapters.outbound.postgres.database import PostgresDatabase
from fpl_data_relay.domain.live import EventStatusResponse, LiveElement


class PostgresLiveRepository:
    """Expose only live-data reads."""

    def __init__(self, *, database: PostgresDatabase) -> None:
        self._database = database

    async def get_event_status(
        self,
        *,
        season_id: str,
    ) -> EventStatusResponse | None:
        return await self._database.get_event_status(season_id=season_id)

    async def list_live_elements(
        self,
        *,
        season_id: str,
        event_id: int,
        after_id: int,
        limit: int,
    ) -> list[LiveElement]:
        return await self._database.list_live_elements(
            season_id=season_id,
            event_id=event_id,
            after_id=after_id,
            limit=limit,
        )

    async def get_live_element(
        self,
        *,
        season_id: str,
        event_id: int,
        element_id: int,
    ) -> LiveElement | None:
        return await self._database.get_live_element(
            season_id=season_id,
            event_id=event_id,
            element_id=element_id,
        )
