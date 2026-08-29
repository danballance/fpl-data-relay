"""File-backed schedule control for the pre-migration maintenance bootstrap."""

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from fpl_data_relay.application.administration import (
    LIVE_SCHEDULE_PREFIX,
    restored_schedule,
)
from fpl_data_relay.application.ports.administration import (
    AwsAdministration,
    ScheduleBootstrapSnapshot,
    ScheduleSnapshot,
    ScheduleState,
)


def pause_schedules_to_snapshot(
    *,
    aws: AwsAdministration,
    snapshot_path: Path,
    aws_region: str,
    app_stack_name: str,
    captured_at: datetime,
) -> ScheduleBootstrapSnapshot:
    """Capture schedules once, then disable the captured relay schedule set."""
    capture_time = aware_utc(value=captured_at, label="capture")
    identity = aws.identity()
    if snapshot_path.exists():
        snapshot = load_schedule_snapshot(snapshot_path=snapshot_path)
    else:
        snapshot = ScheduleBootstrapSnapshot(
            version=1,
            account_id=identity.account_id,
            aws_region=aws_region,
            app_stack_name=app_stack_name,
            captured_at=capture_time,
            schedules=aws.schedule_snapshots(),
        )
        write_schedule_snapshot(
            snapshot_path=snapshot_path,
            snapshot=snapshot,
        )
    validate_snapshot_context(
        snapshot=snapshot,
        account_id=identity.account_id,
        aws_region=aws_region,
        app_stack_name=app_stack_name,
    )
    current = aws.schedule_snapshots()
    validate_schedule_set(
        stored=snapshot.schedules,
        current=current,
        allow_live_expression_change=False,
    )
    for schedule in current:
        aws.set_schedule_state(
            schedule=schedule,
            state=ScheduleState.DISABLED,
            schedule_expression=schedule.schedule_expression,
        )
    disabled = aws.schedule_snapshots()
    validate_schedule_set(
        stored=snapshot.schedules,
        current=disabled,
        allow_live_expression_change=False,
    )
    enabled = [
        schedule
        for schedule in disabled
        if schedule.state is not ScheduleState.DISABLED
    ]
    if enabled:
        names = ", ".join(schedule.name for schedule in enabled)
        raise RuntimeError(f"Schedules remained enabled after pause: {names}")
    return snapshot


def restore_schedules_from_snapshot(
    *,
    aws: AwsAdministration,
    snapshot_path: Path,
    aws_region: str,
    app_stack_name: str,
    restored_at: datetime,
) -> ScheduleBootstrapSnapshot:
    """Restore the immutable snapshot with safe live-schedule reconciliation."""
    restoration_time = aware_utc(value=restored_at, label="restoration")
    identity = aws.identity()
    snapshot = load_schedule_snapshot(snapshot_path=snapshot_path)
    validate_snapshot_context(
        snapshot=snapshot,
        account_id=identity.account_id,
        aws_region=aws_region,
        app_stack_name=app_stack_name,
    )
    current = aws.schedule_snapshots()
    validate_schedule_set(
        stored=snapshot.schedules,
        current=current,
        allow_live_expression_change=True,
    )
    current_by_identity = schedule_map(schedules=current)
    expected: dict[tuple[str, str], tuple[ScheduleState, str]] = {}
    for stored in snapshot.schedules:
        schedule_identity = (stored.group_name, stored.name)
        state, expression = restored_schedule(
            schedule=stored,
            now=restoration_time,
        )
        expected[schedule_identity] = (state, expression)
        aws.set_schedule_state(
            schedule=current_by_identity[schedule_identity],
            state=state,
            schedule_expression=expression,
        )
    restored = aws.schedule_snapshots()
    validate_schedule_set(
        stored=snapshot.schedules,
        current=restored,
        allow_live_expression_change=True,
    )
    mismatches = [
        schedule.name
        for schedule in restored
        if (schedule.state, schedule.schedule_expression)
        != expected[(schedule.group_name, schedule.name)]
    ]
    if mismatches:
        raise RuntimeError(
            "Schedules did not match the restored snapshot: "
            + ", ".join(mismatches),
        )
    return snapshot


def write_schedule_snapshot(
    *,
    snapshot_path: Path,
    snapshot: ScheduleBootstrapSnapshot,
) -> None:
    """Publish one complete snapshot atomically without overwriting a prior one."""
    snapshot_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=snapshot_path.parent,
        prefix=f".{snapshot_path.name}.",
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(snapshot.model_dump_json(indent=2))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_path, snapshot_path)
        except FileExistsError as error:
            raise RuntimeError(
                f"Schedule snapshot already exists: {snapshot_path}",
            ) from error
    finally:
        temporary_path.unlink(missing_ok=True)


def load_schedule_snapshot(*, snapshot_path: Path) -> ScheduleBootstrapSnapshot:
    """Read and strictly validate one schedule snapshot file."""
    if not snapshot_path.is_file():
        raise RuntimeError(f"Schedule snapshot does not exist: {snapshot_path}")
    try:
        return ScheduleBootstrapSnapshot.model_validate_json(
            snapshot_path.read_text(encoding="utf-8"),
        )
    except (OSError, ValidationError) as error:
        raise RuntimeError(
            f"Schedule snapshot is invalid: {snapshot_path}: {error}",
        ) from error


def validate_snapshot_context(
    *,
    snapshot: ScheduleBootstrapSnapshot,
    account_id: str,
    aws_region: str,
    app_stack_name: str,
) -> None:
    """Reject a snapshot captured for a different production boundary."""
    expected = (account_id, aws_region, app_stack_name)
    actual = (
        snapshot.account_id,
        snapshot.aws_region,
        snapshot.app_stack_name,
    )
    if actual != expected:
        raise RuntimeError(
            "Schedule snapshot context mismatch: "
            f"expected {expected}, found {actual}.",
        )


def validate_schedule_set(
    *,
    stored: list[ScheduleSnapshot],
    current: list[ScheduleSnapshot],
    allow_live_expression_change: bool,
) -> None:
    """Require the same relay schedules and reject unowned definition drift."""
    stored_by_identity = schedule_map(schedules=stored)
    current_by_identity = schedule_map(schedules=current)
    if current_by_identity.keys() != stored_by_identity.keys():
        missing = sorted(stored_by_identity.keys() - current_by_identity.keys())
        added = sorted(current_by_identity.keys() - stored_by_identity.keys())
        raise RuntimeError(
            f"Schedule set changed; missing={missing}, added={added}.",
        )
    drifted: list[str] = []
    for schedule_identity, stored_schedule in stored_by_identity.items():
        current_schedule = current_by_identity[schedule_identity]
        excluded = {"state"}
        if (
            allow_live_expression_change
            and stored_schedule.name.startswith(LIVE_SCHEDULE_PREFIX)
        ):
            excluded.add("schedule_expression")
        if stored_schedule.model_dump(exclude=excluded) != current_schedule.model_dump(
            exclude=excluded,
        ):
            drifted.append(stored_schedule.name)
    if drifted:
        raise RuntimeError(
            "Schedule definitions changed after capture: " + ", ".join(drifted),
        )


def schedule_map(
    *,
    schedules: list[ScheduleSnapshot],
) -> dict[tuple[str, str], ScheduleSnapshot]:
    """Index schedules by their full Scheduler identity and reject duplicates."""
    indexed = {
        (schedule.group_name, schedule.name): schedule for schedule in schedules
    }
    if len(indexed) != len(schedules):
        raise RuntimeError("Schedule set contains duplicate identities.")
    return indexed


def aware_utc(*, value: datetime, label: str) -> datetime:
    """Require one timezone-aware operational timestamp in UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"Schedule {label} time must be timezone-aware.")
    return value.astimezone(UTC)
