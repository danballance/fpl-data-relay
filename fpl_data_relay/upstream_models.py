"""Compatibility exports for FPL Pydantic models.

The relay now models FPL component entities explicitly in ``fpl_models`` rather
than preserving endpoint blobs with permissive models.
"""

from fpl_data_relay.fpl_models import (
    BootstrapStatic,
    Element,
    ElementStatDefinition,
    ElementType,
    Event,
    EventLiveResponse,
    EventStatusDay,
    EventStatusResponse,
    Fixture,
    FixtureStat,
    FixtureStatEntry,
    LiveElement,
    LiveElementExplain,
    LiveElementExplainStat,
    LiveElementStats,
    Phase,
    Season,
    Team,
)

__all__ = [
    "BootstrapStatic",
    "Element",
    "ElementStatDefinition",
    "ElementType",
    "Event",
    "EventLiveResponse",
    "EventStatusDay",
    "EventStatusResponse",
    "Fixture",
    "FixtureStat",
    "FixtureStatEntry",
    "LiveElement",
    "LiveElementExplain",
    "LiveElementExplainStat",
    "LiveElementStats",
    "Phase",
    "Season",
    "Team",
]
