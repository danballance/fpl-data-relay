"""PostgreSQL reference-data repository adapter."""

from fpl_data_relay.adapters.outbound.postgres.database import PostgresDatabase
from fpl_data_relay.domain.fixtures import Fixture
from fpl_data_relay.domain.reference import (
    Element,
    ElementType,
    Event,
    Phase,
    Season,
    Team,
)


class PostgresReferenceRepository:
    """Expose only reference-data reads."""

    def __init__(self, *, database: PostgresDatabase) -> None:
        self._database = database

    async def list_seasons(self) -> list[Season]:
        return await self._database.list_seasons()

    async def get_current_season(self) -> Season | None:
        return await self._database.get_current_season()

    async def get_season(self, *, season_id: str) -> Season | None:
        return await self._database.get_season(season_id=season_id)

    async def get_current_event(self, *, season_id: str) -> Event | None:
        return await self._database.get_current_event(season_id=season_id)

    async def list_events(self, *, season_id: str) -> list[Event]:
        return await self._database.list_events(season_id=season_id)

    async def get_event(self, *, season_id: str, event_id: int) -> Event | None:
        return await self._database.get_event(
            season_id=season_id,
            event_id=event_id,
        )

    async def list_phases(self, *, season_id: str) -> list[Phase]:
        return await self._database.list_phases(season_id=season_id)

    async def list_teams(self, *, season_id: str) -> list[Team]:
        return await self._database.list_teams(season_id=season_id)

    async def get_team(self, *, season_id: str, team_id: int) -> Team | None:
        return await self._database.get_team(
            season_id=season_id,
            team_id=team_id,
        )

    async def list_element_types(self, *, season_id: str) -> list[ElementType]:
        return await self._database.list_element_types(season_id=season_id)

    async def list_elements(self, *, season_id: str) -> list[Element]:
        return await self._database.list_elements(season_id=season_id)

    async def get_element(
        self,
        *,
        season_id: str,
        element_id: int,
    ) -> Element | None:
        return await self._database.get_element(
            season_id=season_id,
            element_id=element_id,
        )

    async def list_fixtures(
        self,
        *,
        season_id: str,
        event_id: int | None,
    ) -> list[Fixture]:
        return await self._database.list_fixtures(
            season_id=season_id,
            event_id=event_id,
        )

    async def get_fixture(
        self,
        *,
        season_id: str,
        fixture_id: int,
    ) -> Fixture | None:
        return await self._database.get_fixture(
            season_id=season_id,
            fixture_id=fixture_id,
        )
