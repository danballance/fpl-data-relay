from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from fpl_data_relay.application.jobs import LiveJob
from fpl_data_relay.application.ports.administration import (
    AwsAdministration,
    AwsIdentity,
    ScheduleBootstrapSnapshot,
    ScheduleSnapshot,
    ScheduleState,
    ScheduleTargetSnapshot,
)
from fpl_data_relay.application.schedule_bootstrap import (
    load_schedule_snapshot,
    pause_schedules_to_snapshot,
    restore_schedules_from_snapshot,
    write_schedule_snapshot,
)

NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


def target(*, live: bool, expired: bool = False) -> ScheduleTargetSnapshot:
    body = (
        LiveJob(
            version=1,
            kind="live",
            season_id="2026-27",
            event_id=1,
            window_start=NOW - timedelta(hours=1),
            window_end=NOW if expired else NOW + timedelta(hours=2),
        ).model_dump_json()
        if live
        else '{"version":1,"kind":"reference"}'
    )
    return ScheduleTargetSnapshot(
        arn="arn:queue",
        role_arn="arn:role",
        input=body,
        dead_letter_arn="arn:dlq",
        maximum_event_age_seconds=900,
        maximum_retry_attempts=3,
    )


def schedule(
    *,
    name: str,
    state: ScheduleState,
    live: bool = False,
    expired: bool = False,
) -> ScheduleSnapshot:
    return ScheduleSnapshot(
        name=name,
        group_name="live" if live else "fixed",
        state=state,
        schedule_expression=(
            "at(2026-08-24T11:00:00)"
            if live
            else "cron(0 * * * ? *)"
        ),
        schedule_expression_timezone="UTC",
        flexible_window_mode="OFF",
        action_after_completion="DELETE" if live else None,
        description="test schedule",
        target=target(live=live, expired=expired),
    )


class MutableAws:
    def __init__(self) -> None:
        self.schedules = [
            schedule(name="reference", state=ScheduleState.ENABLED),
            schedule(name="community", state=ScheduleState.DISABLED),
            schedule(
                name="fpl-live-active",
                state=ScheduleState.ENABLED,
                live=True,
            ),
        ]
        self.fail_after_updates: int | None = None
        self.update_count = 0

    def identity(self) -> AwsIdentity:
        return AwsIdentity(account_id="123456789012", arn="arn:operator")

    def schedule_snapshots(self) -> list[ScheduleSnapshot]:
        return list(self.schedules)

    def set_schedule_state(
        self,
        *,
        schedule: ScheduleSnapshot,
        state: ScheduleState,
        schedule_expression: str,
    ) -> None:
        if (
            self.fail_after_updates is not None
            and self.update_count >= self.fail_after_updates
        ):
            raise RuntimeError("simulated schedule update failure")
        self.update_count += 1
        self.schedules = [
            current.model_copy(
                update={
                    "state": state,
                    "schedule_expression": schedule_expression,
                },
            )
            if (current.group_name, current.name)
            == (schedule.group_name, schedule.name)
            else current
            for current in self.schedules
        ]


def pause(*, aws: MutableAws, path: Path) -> ScheduleBootstrapSnapshot:
    return pause_schedules_to_snapshot(
        aws=cast("AwsAdministration", aws),
        snapshot_path=path,
        aws_region="eu-west-2",
        app_stack_name="app",
        captured_at=NOW,
    )


def restore(
    *,
    aws: MutableAws,
    path: Path,
    restored_at: datetime = NOW,
) -> ScheduleBootstrapSnapshot:
    return restore_schedules_from_snapshot(
        aws=cast("AwsAdministration", aws),
        snapshot_path=path,
        aws_region="eu-west-2",
        app_stack_name="app",
        restored_at=restored_at,
    )


def test_schedule_snapshot_pause_is_immutable_and_retryable(tmp_path: Path) -> None:
    aws = MutableAws()
    snapshot_path = tmp_path / "state" / "schedules.json"

    aws.fail_after_updates = 1
    with pytest.raises(RuntimeError, match="simulated"):
        pause(aws=aws, path=snapshot_path)

    stored = load_schedule_snapshot(snapshot_path=snapshot_path)
    assert [value.state for value in stored.schedules] == [
        ScheduleState.ENABLED,
        ScheduleState.DISABLED,
        ScheduleState.ENABLED,
    ]
    assert snapshot_path.stat().st_mode & 0o777 == 0o600

    aws.fail_after_updates = None
    pause(aws=aws, path=snapshot_path)
    assert all(value.state is ScheduleState.DISABLED for value in aws.schedules)
    assert load_schedule_snapshot(snapshot_path=snapshot_path) == stored


def test_schedule_snapshot_restore_handles_live_catchup_and_retry(
    tmp_path: Path,
) -> None:
    aws = MutableAws()
    snapshot_path = tmp_path / "schedules.json"
    pause(aws=aws, path=snapshot_path)

    restore(aws=aws, path=snapshot_path)
    restored = {value.name: value for value in aws.schedules}
    assert restored["reference"].state is ScheduleState.ENABLED
    assert restored["community"].state is ScheduleState.DISABLED
    assert restored["fpl-live-active"].state is ScheduleState.ENABLED
    assert (
        restored["fpl-live-active"].schedule_expression
        == "at(2026-08-24T12:01:00)"
    )

    restore(aws=aws, path=snapshot_path, restored_at=NOW + timedelta(minutes=1))
    restored = {value.name: value for value in aws.schedules}
    assert (
        restored["fpl-live-active"].schedule_expression
        == "at(2026-08-24T12:02:00)"
    )


def test_schedule_snapshot_restore_disables_expired_live_schedule(
    tmp_path: Path,
) -> None:
    aws = MutableAws()
    aws.schedules[-1] = schedule(
        name="fpl-live-active",
        state=ScheduleState.ENABLED,
        live=True,
        expired=True,
    )
    snapshot_path = tmp_path / "schedules.json"
    pause(aws=aws, path=snapshot_path)
    restore(aws=aws, path=snapshot_path)
    assert aws.schedules[-1].state is ScheduleState.DISABLED


def test_schedule_snapshot_rejects_context_corruption_and_drift(
    tmp_path: Path,
) -> None:
    aws = MutableAws()
    snapshot_path = tmp_path / "schedules.json"
    pause(aws=aws, path=snapshot_path)

    with pytest.raises(RuntimeError, match="context mismatch"):
        restore_schedules_from_snapshot(
            aws=cast("AwsAdministration", aws),
            snapshot_path=snapshot_path,
            aws_region="us-east-1",
            app_stack_name="app",
            restored_at=NOW,
        )

    aws.schedules.pop()
    with pytest.raises(RuntimeError, match="Schedule set changed"):
        restore(aws=aws, path=snapshot_path)

    snapshot_path.write_text("not-json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="snapshot is invalid"):
        load_schedule_snapshot(snapshot_path=snapshot_path)


def test_schedule_snapshot_rejects_overwrite_duplicates_and_naive_times(
    tmp_path: Path,
) -> None:
    aws = MutableAws()
    snapshot_path = tmp_path / "schedules.json"
    snapshot = pause(aws=aws, path=snapshot_path)
    with pytest.raises(RuntimeError, match="already exists"):
        write_schedule_snapshot(snapshot_path=snapshot_path, snapshot=snapshot)
    with pytest.raises(ValueError, match="capture time"):
        pause_schedules_to_snapshot(
            aws=cast("AwsAdministration", MutableAws()),
            snapshot_path=tmp_path / "naive.json",
            aws_region="eu-west-2",
            app_stack_name="app",
            captured_at=datetime(2026, 8, 24, 12),
        )
    with pytest.raises(ValueError, match="duplicate"):
        ScheduleBootstrapSnapshot(
            version=1,
            account_id="123456789012",
            aws_region="eu-west-2",
            app_stack_name="app",
            captured_at=NOW,
            schedules=[snapshot.schedules[0], snapshot.schedules[0]],
        )
