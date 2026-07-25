"""Event-status and live-gameweek entities."""

from datetime import date

from pydantic import Field

from fpl_data_relay.domain.types import FplModel, ScalarValue


class EventStatusDay(FplModel):
    """Per-event/day status row from event-status."""

    event: int
    bonus_added: bool
    date: date
    leagues_updated: bool | None = None


class EventStatusResponse(FplModel):
    """Aggregate response from event-status."""

    status: list[EventStatusDay] = Field(default_factory=list)
    leagues: str | None = None


class LiveElementStats(FplModel):
    """Live aggregate stats for one FPL element in a gameweek."""

    minutes: int | None = None
    goals_scored: int | None = None
    assists: int | None = None
    clean_sheets: int | None = None
    goals_conceded: int | None = None
    own_goals: int | None = None
    penalties_saved: int | None = None
    penalties_missed: int | None = None
    yellow_cards: int | None = None
    red_cards: int | None = None
    saves: int | None = None
    bonus: int | None = None
    bps: int | None = None
    influence: str | None = None
    creativity: str | None = None
    threat: str | None = None
    ict_index: str | None = None
    starts: int | None = None
    expected_goals: str | None = None
    expected_assists: str | None = None
    expected_goal_involvements: str | None = None
    expected_goals_conceded: str | None = None
    defensive_contribution: int | None = None
    total_points: int | None = None
    in_dreamteam: bool | None = None


class LiveElementExplainStat(FplModel):
    """One live points explanation stat row."""

    identifier: str
    points: int
    value: ScalarValue = None


class LiveElementExplain(FplModel):
    """Fixture-level live points explanation for one element."""

    fixture: int
    stats: list[LiveElementExplainStat] = Field(default_factory=list)


class LiveElement(FplModel):
    """Live gameweek state for one FPL player/element."""

    id: int
    stats: LiveElementStats
    explain: list[LiveElementExplain] = Field(default_factory=list)


class EventLiveResponse(FplModel):
    """Aggregate event-live response composed from live element rows."""

    elements: list[LiveElement]
