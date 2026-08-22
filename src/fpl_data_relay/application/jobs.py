"""Strict production ingestion jobs and fixture-window planning."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from fpl_data_relay.domain.fixtures import Fixture
from fpl_data_relay.domain.rules import LIVE_WINDOW_AFTER_KICKOFF

WINDOW_BEFORE_KICKOFF = timedelta(minutes=10)
WINDOW_CATCHUP_DELAY = timedelta(minutes=1)
LIVE_ACTIVE_DELAY_SECONDS = 15
LIVE_IDLE_DELAY_SECONDS = 60
DATABASE_WAKING_DELAY_SECONDS = 15


class Job(BaseModel):
    """Immutable base for SQS ingestion jobs."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    version: Literal[1]


class ReferenceJob(Job):
    """Refresh reference data and rebuild live schedules."""

    kind: Literal["reference"]


class LiveJob(Job):
    """Poll one event during a bounded fixture window."""

    kind: Literal["live"]
    season_id: str
    event_id: int = Field(ge=1)
    window_start: datetime
    window_end: datetime

    @field_validator("window_start", "window_end")
    @classmethod
    def window_timestamp_must_be_timezone_aware(
        cls,
        value: datetime,
    ) -> datetime:
        """Reject ambiguous live-window timestamps."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Live job window timestamps must be timezone-aware.")
        return value

    @model_validator(mode="after")
    def window_must_have_positive_duration(self) -> LiveJob:
        """Reject empty or reversed live collection windows."""
        if self.window_end <= self.window_start:
            raise ValueError("Live job window_end must be after window_start.")
        return self


IngestionJob = Annotated[ReferenceJob | LiveJob, Field(discriminator="kind")]
INGESTION_JOB_ADAPTER = TypeAdapter(IngestionJob)


class MatchWindow(BaseModel):
    """Merged polling window for one season and event."""

    model_config = ConfigDict(frozen=True)

    season_id: str
    event_id: int
    start: datetime
    end: datetime

    def schedule_at(self, *, now: datetime) -> datetime:
        """Return the future trigger time without changing window identity."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware.")
        return max(self.start, now + WINDOW_CATCHUP_DELAY)

    @property
    def schedule_name(self) -> str:
        """Return a stable EventBridge Scheduler resource name."""
        season = self.season_id.replace("-", "")
        stamp = self.start.astimezone(UTC).strftime("%Y%m%d%H%M")
        return f"fpl-live-{season}-{self.event_id}-{stamp}"

    def job(self) -> LiveJob:
        """Build the first queue message for this window."""
        return LiveJob(
            version=1,
            kind="live",
            season_id=self.season_id,
            event_id=self.event_id,
            window_start=self.start,
            window_end=self.end,
        )


def next_live_delay(
    *,
    job: LiveJob,
    now: datetime,
    has_active_fixture: bool | None,
    database_waking: bool,
) -> int | None:
    """Return the next SQS delay or stop after the final window poll."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware.")
    if now >= job.window_end:
        return None
    if database_waking:
        return DATABASE_WAKING_DELAY_SECONDS
    if has_active_fixture is None:
        raise ValueError(
            "has_active_fixture is required after a successful live poll.",
        )
    return (
        LIVE_ACTIVE_DELAY_SECONDS
        if has_active_fixture
        else LIVE_IDLE_DELAY_SECONDS
    )


def build_match_windows(
    *,
    season_id: str,
    fixtures: list[Fixture],
    now: datetime,
) -> tuple[list[MatchWindow], list[int]]:
    """Build merged future windows and return fixtures missing schedule data."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware.")
    missing_fixture_ids: list[int] = []
    candidates: list[MatchWindow] = []
    for fixture in fixtures:
        if fixture.event is None or fixture.kickoff_time is None:
            missing_fixture_ids.append(fixture.id)
            continue
        kickoff_time = fixture.kickoff_time
        if kickoff_time.tzinfo is None or kickoff_time.utcoffset() is None:
            raise ValueError(
                f"Fixture {fixture.id} kickoff_time must be timezone-aware.",
            )
        end = kickoff_time + LIVE_WINDOW_AFTER_KICKOFF
        if end <= now:
            continue
        candidates.append(
            MatchWindow(
                season_id=season_id,
                event_id=fixture.event,
                start=kickoff_time - WINDOW_BEFORE_KICKOFF,
                end=end,
            ),
        )
    candidates.sort(key=lambda window: (window.event_id, window.start, window.end))
    merged: list[MatchWindow] = []
    for window in candidates:
        if (
            merged
            and merged[-1].event_id == window.event_id
            and window.start <= merged[-1].end
        ):
            previous = merged[-1]
            merged[-1] = previous.model_copy(
                update={"end": max(previous.end, window.end)},
            )
        else:
            merged.append(window)
    return merged, missing_fixture_ids
