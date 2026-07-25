"""Reference-data entities for seasons, events, teams, and elements."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from fpl_data_relay.domain.types import FplModel


class Season(BaseModel):
    """Derived relay season metadata for one active FPL season."""

    model_config = ConfigDict(frozen=True)

    id: str
    start_year: int
    end_year: int
    first_deadline_time: datetime
    last_deadline_time: datetime
    is_current: bool


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
    photo: str | None = None
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


class BootstrapStatic(FplModel):
    """Aggregate bootstrap-static response composed from entity models."""

    events: list[Event]
    phases: list[Phase] = Field(default_factory=list)
    teams: list[Team]
    elements: list[Element]
    element_stats: list[ElementStatDefinition] = Field(default_factory=list)
    element_types: list[ElementType]
