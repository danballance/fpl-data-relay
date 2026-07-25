"""Outbound port for the public FPL API."""

from typing import Protocol

from fpl_data_relay.domain.fixtures import Fixture
from fpl_data_relay.domain.live import EventLiveResponse, EventStatusResponse
from fpl_data_relay.domain.reference import BootstrapStatic


class FplGateway(Protocol):
    """Upstream operations required by ingestion."""

    async def fetch_bootstrap_static(self) -> BootstrapStatic:
        """Fetch bootstrap reference data."""
        ...

    async def fetch_fixtures(self) -> list[Fixture]:
        """Fetch all fixtures."""
        ...

    async def fetch_current_fixtures(self, *, event_id: int) -> list[Fixture]:
        """Fetch fixtures assigned to one event."""
        ...

    async def fetch_event_status(self) -> EventStatusResponse:
        """Fetch event-status data."""
        ...

    async def fetch_event_live(self, *, event_id: int) -> EventLiveResponse:
        """Fetch live data for one event."""
        ...
