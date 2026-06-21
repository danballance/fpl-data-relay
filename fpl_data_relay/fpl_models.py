"""Pydantic models for normalised public FPL API entities."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

ScalarValue = int | float | str | bool | None


class FplModel(BaseModel):
    """Base model for known FPL fields; unknown fields are ignored after logging."""

    model_config = ConfigDict(extra="ignore", frozen=True)


class Event(FplModel):
    """FPL gameweek/event metadata from bootstrap-static."""

    id: int
    name: str
    deadline_time: datetime | None = None
    average_entry_score: int | None = None
    finished: bool | None = None
    data_checked: bool | None = None
    highest_scoring_entry: int | None = None
    deadline_time_epoch: int | None = None
    deadline_time_game_offset: int | None = None
    highest_score: int | None = None
    is_previous: bool = False
    is_current: bool = False
    is_next: bool = False


class Phase(FplModel):
    """FPL phase metadata from bootstrap-static."""

    id: int
    name: str
    start_event: int
    stop_event: int


class Team(FplModel):
    """Premier League team metadata from bootstrap-static."""

    id: int
    name: str
    short_name: str
    code: int | None = None
    strength: int | None = None
    strength_overall_home: int | None = None
    strength_overall_away: int | None = None
    strength_attack_home: int | None = None
    strength_attack_away: int | None = None
    strength_defence_home: int | None = None
    strength_defence_away: int | None = None
    pulse_id: int | None = None


class ElementType(FplModel):
    """FPL player position/element type metadata."""

    id: int
    singular_name: str
    singular_name_short: str | None = None
    plural_name: str | None = None
    plural_name_short: str | None = None
    squad_select: int | None = None
    squad_min_play: int | None = None
    squad_max_play: int | None = None
    ui_shirt_specific: bool | None = None
    sub_positions_locked: list[int] = Field(default_factory=list)
    element_count: int | None = None


class ElementStatDefinition(FplModel):
    """Known element stat definition from bootstrap-static."""

    label: str
    name: str


class Element(FplModel):
    """FPL player/element metadata from bootstrap-static."""

    id: int
    code: int | None = None
    first_name: str
    second_name: str
    web_name: str
    team: int
    team_code: int | None = None
    element_type: int
    status: str | None = None
    news: str | None = None
    news_added: datetime | None = None
    now_cost: int | None = None
    selected_by_percent: str | None = None
    total_points: int | None = None
    chance_of_playing_next_round: int | None = None
    chance_of_playing_this_round: int | None = None
    form: str | None = None
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
    expected_goals: str | None = None
    expected_assists: str | None = None
    expected_goal_involvements: str | None = None
    expected_goals_conceded: str | None = None


class FixtureStatEntry(FplModel):
    """One player/value entry inside a fixture statistic side."""

    value: ScalarValue = None
    element: int | None = None


class FixtureStat(FplModel):
    """Fixture statistic split by home and away sides."""

    identifier: str
    a: list[FixtureStatEntry] = Field(default_factory=list)
    h: list[FixtureStatEntry] = Field(default_factory=list)


class Fixture(FplModel):
    """FPL fixture metadata from fixtures endpoints."""

    id: int
    code: int | None = None
    event: int | None = None
    finished: bool
    finished_provisional: bool | None = None
    kickoff_time: datetime | None = None
    minutes: int | None = None
    provisional_start_time: bool | None = None
    started: bool
    team_a: int
    team_a_score: int | None = None
    team_h: int
    team_h_score: int | None = None
    stats: list[FixtureStat] = Field(default_factory=list)
    pulse_id: int | None = None


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


class BootstrapStatic(FplModel):
    """Aggregate bootstrap-static response composed from entity models."""

    events: list[Event]
    phases: list[Phase] = Field(default_factory=list)
    teams: list[Team]
    elements: list[Element]
    element_stats: list[ElementStatDefinition] = Field(default_factory=list)
    element_types: list[ElementType]


class EventLiveResponse(FplModel):
    """Aggregate event-live response composed from live element rows."""

    elements: list[LiveElement]
