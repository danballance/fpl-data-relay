from datetime import UTC, datetime, timedelta

import pytest

from fpl_data_relay.application.jobs import (
    INGESTION_JOB_ADAPTER,
    LiveJob,
    ReferenceJob,
    build_match_windows,
    next_live_delay,
)
from fpl_data_relay.domain.fixtures import Fixture


def fixture(
    *,
    fixture_id: int,
    event_id: int | None,
    kickoff: datetime | None,
) -> Fixture:
    return Fixture(
        id=fixture_id,
        event=event_id,
        kickoff_time=kickoff,
        started=False,
        finished=False,
        team_a=1,
        team_h=2,
    )


def test_ingestion_jobs_are_strict_and_discriminated() -> None:
    reference = INGESTION_JOB_ADAPTER.validate_json(
        '{"version":1,"kind":"reference"}',
    )
    assert isinstance(reference, ReferenceJob)
    with pytest.raises(ValueError):
        INGESTION_JOB_ADAPTER.validate_json(
            '{"version":2,"kind":"reference"}',
        )
    with pytest.raises(ValueError):
        INGESTION_JOB_ADAPTER.validate_json(
            '{"version":1,"kind":"unknown"}',
        )
    with pytest.raises(ValueError):
        INGESTION_JOB_ADAPTER.validate_json(
            '{"version":1,"kind":"reference","extra":true}',
        )


def test_match_windows_merge_overlaps_only_within_an_event() -> None:
    now = datetime(2026, 7, 25, 12, tzinfo=UTC)
    kickoff = now + timedelta(hours=1)
    windows, missing = build_match_windows(
        season_id="2026-27",
        fixtures=[
            fixture(fixture_id=1, event_id=1, kickoff=kickoff),
            fixture(
                fixture_id=2,
                event_id=1,
                kickoff=kickoff + timedelta(hours=2),
            ),
            fixture(fixture_id=3, event_id=2, kickoff=kickoff),
            fixture(fixture_id=4, event_id=None, kickoff=kickoff),
            fixture(fixture_id=5, event_id=3, kickoff=None),
        ],
        now=now,
    )
    assert len(windows) == 2
    assert windows[0].end == kickoff + timedelta(hours=6)
    assert windows[0].schedule_name.startswith("fpl-live-202627-1-")
    assert isinstance(windows[0].job(), LiveJob)
    assert missing == [4, 5]


def test_match_windows_skip_finished_windows_and_require_aware_now() -> None:
    now = datetime(2026, 7, 25, 12, tzinfo=UTC)
    windows, missing = build_match_windows(
        season_id="2026-27",
        fixtures=[
            fixture(
                fixture_id=1,
                event_id=1,
                kickoff=now - timedelta(hours=4),
            ),
        ],
        now=now,
    )
    assert windows == []
    assert missing == []
    with pytest.raises(ValueError, match="timezone-aware"):
        build_match_windows(
            season_id="2026-27",
            fixtures=[],
            now=now.replace(tzinfo=None),
        )
    with pytest.raises(
        ValueError,
        match="Fixture 2 kickoff_time must be timezone-aware",
    ):
        build_match_windows(
            season_id="2026-27",
            fixtures=[
                fixture(
                    fixture_id=2,
                    event_id=1,
                    kickoff=now.replace(tzinfo=None),
                ),
            ],
            now=now,
        )


def test_active_match_window_keeps_stable_identity_across_reconciliation() -> None:
    kickoff = datetime(2026, 8, 21, 19, tzinfo=UTC)
    first_now = kickoff + timedelta(minutes=5)
    second_now = first_now + timedelta(minutes=15)
    first, _ = build_match_windows(
        season_id="2026-27",
        fixtures=[fixture(fixture_id=1, event_id=1, kickoff=kickoff)],
        now=first_now,
    )
    second, _ = build_match_windows(
        season_id="2026-27",
        fixtures=[fixture(fixture_id=1, event_id=1, kickoff=kickoff)],
        now=second_now,
    )
    assert first[0].start == kickoff - timedelta(minutes=10)
    assert first[0].schedule_name == second[0].schedule_name
    assert first[0].schedule_at(now=first_now) == first_now + timedelta(minutes=1)


def test_live_requeue_delays_and_window_termination() -> None:
    now = datetime(2026, 7, 25, 12, tzinfo=UTC)
    job = LiveJob(
        version=1,
        kind="live",
        season_id="2026-27",
        event_id=1,
        window_start=now - timedelta(minutes=10),
        window_end=now + timedelta(hours=3),
    )
    assert next_live_delay(
        job=job,
        now=now,
        has_active_fixture=True,
    ) == 15
    assert next_live_delay(
        job=job,
        now=now,
        has_active_fixture=False,
    ) == 60
    assert next_live_delay(
        job=job,
        now=job.window_end,
        has_active_fixture=True,
    ) is None
    with pytest.raises(ValueError, match="timezone-aware"):
        next_live_delay(
            job=job,
            now=now.replace(tzinfo=None),
            has_active_fixture=False,
        )
