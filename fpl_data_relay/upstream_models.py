"""Permissive Pydantic models for the upstream FPL API payloads."""

from pydantic import BaseModel, ConfigDict


class UpstreamModel(BaseModel):
    """Base model that preserves upstream fields beyond those the relay uses."""

    model_config = ConfigDict(extra="allow")


class Event(UpstreamModel):
    """FPL gameweek event metadata needed to identify the current event."""

    id: int
    name: str
    is_current: bool


class Team(UpstreamModel):
    """Team metadata from the bootstrap document."""

    id: int
    name: str
    short_name: str


class Element(UpstreamModel):
    """Player metadata from the bootstrap document."""

    id: int
    first_name: str
    second_name: str
    web_name: str
    team: int
    element_type: int


class ElementType(UpstreamModel):
    """Player position metadata from the bootstrap document."""

    id: int
    singular_name: str
    plural_name: str


class BootstrapStatic(UpstreamModel):
    """Core bootstrap document containing events, teams, and players."""

    events: list[Event]
    teams: list[Team]
    elements: list[Element]
    element_types: list[ElementType]


class Fixture(UpstreamModel):
    """Fixture metadata used for full and current gameweek fixture resources."""

    id: int
    team_h: int
    team_a: int
    started: bool
    finished: bool


class EventStatusDay(UpstreamModel):
    """Per-event status row from the event-status endpoint."""

    event: int
    bonus_added: bool
    date: str
    leagues_updated: bool


class EventStatusResponse(UpstreamModel):
    """Event status endpoint response."""

    status: list[EventStatusDay]


class LiveElementStats(UpstreamModel):
    """Live aggregate player stats used by the relay."""

    total_points: int


class LiveElementExplainStat(UpstreamModel):
    """One scoring explanation item for a live player fixture."""

    identifier: str
    points: int


class LiveElementExplain(UpstreamModel):
    """Fixture-level scoring explanation for one live player."""

    fixture: int
    stats: list[LiveElementExplainStat]


class LiveElement(UpstreamModel):
    """Live player data for a current gameweek."""

    id: int
    stats: LiveElementStats
    explain: list[LiveElementExplain]


class EventLiveResponse(UpstreamModel):
    """Live endpoint response for all players in one event."""

    elements: list[LiveElement]
