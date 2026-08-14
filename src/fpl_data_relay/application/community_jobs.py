"""Strict community jobs and timezone-aware dispatch planning."""

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from fpl_data_relay.application.community_strategies import (
    CommunityStrategyRegistry,
)


class CommunityJob(BaseModel):
    """Immutable strict base for community queue messages."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    version: Literal[1]


class CommunityDispatchJob(CommunityJob):
    """One Scheduler delivery using the scheduled timestamp, not arrival time."""

    kind: Literal["community_dispatch"]
    scheduled_at: datetime

    @field_validator("scheduled_at")
    @classmethod
    def scheduled_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scheduled_at must be timezone-aware.")
        return value


class CommunityStrategyJob(CommunityJob):
    """One idempotent strategy/date analysis window."""

    kind: Literal["community_strategy"]
    strategy_key: str = Field(min_length=1)
    strategy_version: int = Field(ge=1)
    report_date: date
    window_start: datetime
    window_end: datetime

    @field_validator("window_start", "window_end")
    @classmethod
    def window_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Community job windows must be timezone-aware.")
        return value

    @model_validator(mode="after")
    def window_is_ordered(self) -> CommunityStrategyJob:
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start.")
        return self


CommunityQueueJob = Annotated[
    CommunityDispatchJob | CommunityStrategyJob,
    Field(discriminator="kind"),
]
COMMUNITY_JOB_ADAPTER = TypeAdapter(CommunityQueueJob)


def build_strategy_jobs(
    *,
    registry: CommunityStrategyRegistry,
    scheduled_at: datetime,
) -> list[CommunityStrategyJob]:
    """Expand one scheduled dispatch into every active versioned strategy."""
    if scheduled_at.tzinfo is None or scheduled_at.utcoffset() is None:
        raise ValueError("scheduled_at must be timezone-aware.")
    jobs: list[CommunityStrategyJob] = []
    for strategy in registry.list_active():
        definition = strategy.definition
        local_date = scheduled_at.astimezone(
            ZoneInfo(definition.schedule_timezone),
        ).date()
        window_end = scheduled_at.astimezone(UTC)
        jobs.append(
            CommunityStrategyJob(
                version=1,
                kind="community_strategy",
                strategy_key=definition.key,
                strategy_version=definition.version,
                report_date=local_date,
                window_start=window_end - timedelta(days=definition.lookback_days),
                window_end=window_end,
            ),
        )
    return jobs
