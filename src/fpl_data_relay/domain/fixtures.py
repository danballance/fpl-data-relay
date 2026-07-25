"""Fixture entities and fixture-stat values."""

from datetime import datetime

from pydantic import Field

from fpl_data_relay.domain.types import FplModel, ScalarValue


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
