"""Reference-data query use cases."""

from fpl_data_relay.application.ports.persistence import ReferenceRepository
from fpl_data_relay.domain.fixtures import Fixture
from fpl_data_relay.domain.reference import (
    Element,
    ElementType,
    Event,
    Phase,
    Season,
    Team,
)


class ReferenceQueries:
    """Expose reference reads through a narrow repository."""

    def __init__(self, *, repository: ReferenceRepository) -> None:
        self._repository = repository

    async def list_seasons(self) -> list[Season]:
        return await self._repository.list_seasons()

    async def get_current_season(self) -> Season | None:
        return await self._repository.get_current_season()

    async def get_season(self, *, season_id: str) -> Season | None:
        return await self._repository.get_season(season_id=season_id)

    async def get_current_event(self, *, season_id: str) -> Event | None:
        return await self._repository.get_current_event(season_id=season_id)

    async def list_events(self, *, season_id: str) -> list[Event]:
        return await self._repository.list_events(season_id=season_id)

    async def get_event(self, *, season_id: str, event_id: int) -> Event | None:
        return await self._repository.get_event(
            season_id=season_id,
            event_id=event_id,
        )

    async def list_phases(self, *, season_id: str) -> list[Phase]:
        return await self._repository.list_phases(season_id=season_id)

    async def list_teams(self, *, season_id: str) -> list[Team]:
        return await self._repository.list_teams(season_id=season_id)

    async def get_team(self, *, season_id: str, team_id: int) -> Team | None:
        return await self._repository.get_team(
            season_id=season_id,
            team_id=team_id,
        )

    async def list_element_types(self, *, season_id: str) -> list[ElementType]:
        return await self._repository.list_element_types(season_id=season_id)

    async def list_elements(
        self,
        *,
        season_id: str,
        after_id: int,
        limit: int,
    ) -> list[Element]:
        return await self._repository.list_elements(
            season_id=season_id,
            after_id=after_id,
            limit=limit,
        )

    async def get_element(
        self,
        *,
        season_id: str,
        element_id: int,
    ) -> Element | None:
        return await self._repository.get_element(
            season_id=season_id,
            element_id=element_id,
        )

    async def list_fixtures(
        self,
        *,
        season_id: str,
        event_id: int | None,
        after_id: int,
        limit: int,
    ) -> list[Fixture]:
        return await self._repository.list_fixtures(
            season_id=season_id,
            event_id=event_id,
            after_id=after_id,
            limit=limit,
        )

    async def get_fixture(
        self,
        *,
        season_id: str,
        fixture_id: int,
    ) -> Fixture | None:
        return await self._repository.get_fixture(
            season_id=season_id,
            fixture_id=fixture_id,
        )
