"""Live-data query use cases."""

from fpl_data_relay.application.ports.persistence import LiveRepository
from fpl_data_relay.domain.live import EventStatusResponse, LiveElement


class LiveQueries:
    """Expose live reads through a narrow repository."""

    def __init__(self, *, repository: LiveRepository) -> None:
        self._repository = repository

    async def get_event_status(
        self,
        *,
        season_id: str,
    ) -> EventStatusResponse | None:
        return await self._repository.get_event_status(season_id=season_id)

    async def list_live_elements(
        self,
        *,
        season_id: str,
        event_id: int,
    ) -> list[LiveElement]:
        return await self._repository.list_live_elements(
            season_id=season_id,
            event_id=event_id,
        )

    async def get_live_element(
        self,
        *,
        season_id: str,
        event_id: int,
        element_id: int,
    ) -> LiveElement | None:
        return await self._repository.get_live_element(
            season_id=season_id,
            event_id=event_id,
            element_id=element_id,
        )
