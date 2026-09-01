from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from fpl_data_relay.application.administration import (
    AdministrationService,
    restored_schedule,
)
from fpl_data_relay.application.jobs import LiveJob
from fpl_data_relay.application.ports.administration import (
    AdministrationProgress,
    AdministrationWorkflow,
    AdministrationWorkflowProgress,
    AdministrationWorkflowStep,
    AdministrationWorkflowStepState,
    AwsAdministration,
    AwsIdentity,
    AwsResources,
    ChangeFeedRebaselineResult,
    MaintenancePhase,
    MaintenanceWindow,
    NasAdministration,
    NasCollectorStatus,
    ProductionAdministrationDatabase,
    QueueDepth,
    QueueDrainProgress,
    QueueDrainStage,
    ScheduleSnapshot,
    ScheduleState,
    ScheduleTargetSnapshot,
    SchemaStatus,
)
from fpl_data_relay.domain.fixtures import Fixture
from fpl_data_relay.domain.reference import Event, Season

NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


def queue_depth(*, name: str, total: int) -> QueueDepth:
    return QueueDepth(
        name=name,
        url=f"https://sqs/{name}",
        visible=total,
        in_flight=0,
        delayed=0,
    )


def target(*, live: bool) -> ScheduleTargetSnapshot:
    input_value = (
        LiveJob(
            version=1,
            kind="live",
            season_id="2026-27",
            event_id=1,
            window_start=NOW - timedelta(hours=1),
            window_end=NOW + timedelta(hours=2),
        ).model_dump_json()
        if live
        else '{"version":1,"kind":"reference"}'
    )
    return ScheduleTargetSnapshot(
        arn="arn:queue",
        role_arn="arn:role",
        input=input_value,
        dead_letter_arn="arn:dlq",
        maximum_event_age_seconds=900,
        maximum_retry_attempts=3,
    )


def schedule(
    *,
    name: str,
    state: ScheduleState = ScheduleState.ENABLED,
    expression: str = "cron(0 * * * ? *)",
) -> ScheduleSnapshot:
    live = name.startswith("fpl-live-")
    return ScheduleSnapshot(
        name=name,
        group_name="live" if live else "reference",
        state=state,
        schedule_expression=expression,
        schedule_expression_timezone="UTC",
        flexible_window_mode="OFF",
        action_after_completion="NONE" if live else None,
        description="test schedule",
        target=target(live=live),
    )


class FakeAws:
    def __init__(self) -> None:
        self.depth_sequences: list[list[QueueDepth]] = []
        self.default_depths = [
            queue_depth(name="fetch", total=0),
            queue_depth(name="result", total=0),
            queue_depth(name="community", total=0),
        ]
        self.schedules = [
            schedule(name="fpl-relay-app-reference-hourly"),
            schedule(
                name="fpl-relay-app-community-daily",
                state=ScheduleState.DISABLED,
            ),
            schedule(
                name="fpl-live-202627-1-202608241100",
                expression="at(2026-08-24T11:00:00)",
            ),
        ]
        self.state_changes: list[tuple[str, ScheduleState, str]] = []
        self.fetch_messages: list[str] = []
        self.community_messages: list[str] = []
        self.doctor_calls = 0

    def identity(self) -> AwsIdentity:
        self.doctor_calls += 1
        return AwsIdentity(account_id="123456789012", arn="arn:operator")

    def app_deployed_revision(self) -> str:
        return "a" * 40

    def resources(self) -> AwsResources:
        return AwsResources(
            database_resource_arn="arn:db",
            database_secret_arn="arn:secret",
            database_name="relay",
            fetch_queue_url="https://sqs/fetch",
            fetch_dead_letter_queue_url="https://sqs/fetch-dlq",
            result_queue_url="https://sqs/result",
            result_dead_letter_queue_url="https://sqs/result-dlq",
            schedule_dead_letter_queue_url="https://sqs/schedule-dlq",
            community_queue_url="https://sqs/community",
            community_dead_letter_queue_url="https://sqs/community-dlq",
            reference_schedule_group_name="reference",
            reference_schedule_name="reference-job",
            live_schedule_group_name="live",
            community_schedule_group_name="community",
            community_schedule_name="community-job",
        )

    def queue_depths(self, *, include_dead_letters: bool) -> list[QueueDepth]:
        if self.depth_sequences:
            return self.depth_sequences.pop(0)
        depths = list(self.default_depths)
        if include_dead_letters:
            depths.extend(
                [
                    queue_depth(name="fetch-dead-letter", total=0),
                    queue_depth(name="result-dead-letter", total=0),
                    queue_depth(name="schedule-dead-letter", total=0),
                    queue_depth(name="community-dead-letter", total=0),
                ],
            )
        return depths

    def schedule_snapshots(self) -> list[ScheduleSnapshot]:
        return list(self.schedules)

    def set_schedule_state(
        self,
        *,
        schedule: ScheduleSnapshot,
        state: ScheduleState,
        schedule_expression: str,
    ) -> None:
        self.state_changes.append((schedule.name, state, schedule_expression))

    def send_fetch_job(self, *, message_body: str) -> str:
        self.fetch_messages.append(message_body)
        return f"fetch-{len(self.fetch_messages)}"

    def send_community_job(self, *, message_body: str) -> str:
        self.community_messages.append(message_body)
        return "community-1"

    def peek_dead_letters(
        self,
        *,
        queue_name: str,
        max_messages: int,
    ) -> list[str]:
        del queue_name, max_messages
        return []


class FakeNas:
    def __init__(self, *, running: bool) -> None:
        self.running = running
        self.doctor_calls = 0
        self.actions: list[str] = []

    def doctor(self) -> None:
        self.doctor_calls += 1

    def status(self) -> NasCollectorStatus:
        return self._status()

    def start(self) -> NasCollectorStatus:
        self.actions.append("start")
        self.running = True
        return self._status()

    def stop(self) -> NasCollectorStatus:
        self.actions.append("stop")
        self.running = False
        return self._status()

    def logs(self, *, tail_lines: int) -> str:
        return f"logs={tail_lines}"

    def update(self, *, image_tag: str) -> NasCollectorStatus:
        self.actions.append(image_tag)
        return self._status()

    def _status(self) -> NasCollectorStatus:
        return NasCollectorStatus(
            running=self.running,
            health="healthy" if self.running else "stopped",
            image="ghcr.io/collector:sha-" + "a" * 40,
        )


class FakeDatabase:
    def __init__(self) -> None:
        self.window: MaintenanceWindow | None = None
        self.latest: MaintenanceWindow | None = None
        self.schema = SchemaStatus(applied_versions=[5], pending_versions=[])
        self.schema_applied = False
        self.rebaseline_reasons: list[str] = []
        self.season: Season | None = Season(
            id="2026-27",
            start_year=2026,
            end_year=2027,
            first_deadline_time=NOW,
            last_deadline_time=NOW + timedelta(days=200),
            is_current=True,
        )
        self.event: Event | None = Event(id=1, name="Gameweek 1", is_current=True)
        self.fixtures = [
            Fixture(
                id=1,
                event=1,
                finished=False,
                kickoff_time=NOW,
                started=True,
                team_a=1,
                team_h=2,
            ),
        ]

    async def apply_schema(self) -> None:
        self.schema_applied = True

    async def schema_status(self) -> SchemaStatus:
        return self.schema

    async def check_schema_version(self, *, expected_version: int) -> None:
        assert expected_version == 5

    async def maintenance_active(self) -> bool:
        return self.window is not None

    async def get_open_maintenance(self) -> MaintenanceWindow | None:
        return self.window

    async def get_latest_maintenance(self) -> MaintenanceWindow | None:
        return self.latest

    async def open_maintenance(
        self,
        *,
        reason: str,
        operator_arn: str,
        schedules: list[ScheduleSnapshot],
        queues_before: list[QueueDepth],
        collector_was_running: bool | None,
    ) -> MaintenanceWindow:
        self.window = MaintenanceWindow(
            id=1,
            reason=reason,
            operator_arn=operator_arn,
            phase=MaintenancePhase.ENTERING,
            schedules=schedules,
            collector_was_running=collector_was_running,
            queues_before=queues_before,
            queues_after=[],
            started_at=NOW,
            activated_at=None,
            closed_at=None,
            closed_by=None,
        )
        return self.window

    async def activate_maintenance(
        self,
        *,
        maintenance_id: int,
        collector_was_running: bool,
        queues_after: list[QueueDepth],
    ) -> MaintenanceWindow:
        assert self.window is not None and maintenance_id == self.window.id
        self.window = self.window.model_copy(
            update={
                "phase": MaintenancePhase.ACTIVE,
                "collector_was_running": collector_was_running,
                "queues_after": queues_after,
                "activated_at": NOW,
            },
        )
        return self.window

    async def begin_maintenance_exit(
        self,
        *,
        maintenance_id: int,
    ) -> MaintenanceWindow:
        assert self.window is not None and maintenance_id == self.window.id
        self.window = self.window.model_copy(
            update={"phase": MaintenancePhase.EXITING},
        )
        return self.window

    async def close_maintenance(
        self,
        *,
        maintenance_id: int,
        operator_arn: str,
    ) -> MaintenanceWindow:
        assert self.window is not None and maintenance_id == self.window.id
        closed = self.window.model_copy(
            update={
                "phase": MaintenancePhase.CLOSED,
                "closed_at": NOW,
                "closed_by": operator_arn,
            },
        )
        self.latest = closed
        self.window = None
        return closed

    async def rebaseline_current_change_feed(
        self,
        *,
        reason: str,
    ) -> ChangeFeedRebaselineResult:
        self.rebaseline_reasons.append(reason)
        return ChangeFeedRebaselineResult(
            id=1,
            season_id="2026-27",
            reason=reason,
            change_events_deleted=3,
            entity_changes_deleted=4,
            snapshots_rebuilt=5,
            created_at=NOW,
        )

    async def get_current_season(self) -> Season | None:
        return self.season

    async def get_current_event(self, *, season_id: str) -> Event | None:
        assert season_id == "2026-27"
        return self.event

    async def list_fixtures(
        self,
        *,
        season_id: str,
        event_id: int | None,
        after_id: int,
        limit: int,
    ) -> list[Fixture]:
        assert season_id == "2026-27"
        assert event_id == 1
        assert after_id == 0
        assert limit == 200
        return self.fixtures


class FakeTime:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def service(
    *,
    aws: FakeAws,
    nas: FakeNas,
    database: FakeDatabase,
    fake_time: FakeTime,
) -> AdministrationService:
    return AdministrationService(
        aws=cast("AwsAdministration", aws),
        nas=cast("NasAdministration", nas),
        database=cast("ProductionAdministrationDatabase", database),
        drain_timeout_seconds=20,
        drain_poll_seconds=2,
        drain_stable_seconds=4,
        monotonic_clock=fake_time.monotonic,
        sleeper=fake_time.sleep,
        clock=lambda: NOW,
    )


def test_queue_drain_requires_stable_zero_and_times_out() -> None:
    aws = FakeAws()
    aws.depth_sequences = [
        [queue_depth(name="fetch", total=1)],
        [queue_depth(name="fetch", total=0)],
        [queue_depth(name="fetch", total=0)],
    ]
    fake_time = FakeTime()
    admin = service(
        aws=aws,
        nas=FakeNas(running=True),
        database=FakeDatabase(),
        fake_time=fake_time,
    )
    events: list[AdministrationProgress] = []
    assert admin.drain_queues_with_progress(
        stage=QueueDrainStage.STANDALONE,
        progress=events.append,
    )[0].total == 0
    assert fake_time.value == 6
    samples = [event for event in events if isinstance(event, QueueDrainProgress)]
    assert [sample.stable_for_seconds for sample in samples] == [0, 0, 2, 4]
    assert all(sample.stage is QueueDrainStage.STANDALONE for sample in samples)

    busy_aws = FakeAws()
    busy_aws.default_depths = [queue_depth(name="fetch", total=1)]
    with pytest.raises(TimeoutError, match="fetch=1"):
        service(
            aws=busy_aws,
            nas=FakeNas(running=True),
            database=FakeDatabase(),
            fake_time=FakeTime(),
        ).drain_queues()


@pytest.mark.asyncio
async def test_status_doctors_schema_and_strict_job_sends() -> None:
    aws = FakeAws()
    nas = FakeNas(running=True)
    database = FakeDatabase()
    admin = service(
        aws=aws,
        nas=nas,
        database=database,
        fake_time=FakeTime(),
    )
    assert admin.aws_doctor().arn == "arn:operator"
    admin.nas_doctor()
    status = await admin.production_status()
    assert status.collector.running is True
    assert status.maintenance is None
    assert await admin.latest_maintenance() is None
    assert (await admin.apply_schema()).pending_versions == []
    assert database.schema_applied is True
    assert await admin.send_reference(allow_maintenance=False) == "fetch-1"
    assert '"kind":"reference"' in aws.fetch_messages[-1]
    assert await admin.send_current_live(allow_maintenance=False) == "fetch-2"
    assert LiveJob.model_validate_json(aws.fetch_messages[-1]).event_id == 1
    assert await admin.send_community(allow_maintenance=False) == "community-1"
    assert '"kind":"community_dispatch"' in aws.community_messages[-1]

    await admin.pause_schedules(reason="work", collector_was_running=True)
    with pytest.raises(RuntimeError, match="blocked"):
        await admin.send_reference(allow_maintenance=False)


@pytest.mark.asyncio
async def test_maintenance_begin_and_end_restores_prior_state() -> None:
    aws = FakeAws()
    nas = FakeNas(running=True)
    database = FakeDatabase()
    admin = service(
        aws=aws,
        nas=nas,
        database=database,
        fake_time=FakeTime(),
    )
    events: list[AdministrationProgress] = []
    active = await admin.begin_production_maintenance_with_progress(
        reason="schema work",
        progress=events.append,
    )
    assert active.phase is MaintenancePhase.ACTIVE
    assert nas.actions == ["stop"]
    assert all(change[1] is ScheduleState.DISABLED for change in aws.state_changes)

    closed = await admin.end_production_maintenance_with_progress(
        progress=events.append,
    )
    assert closed.phase is MaintenancePhase.CLOSED
    assert nas.actions == ["stop", "start"]
    assert aws.fetch_messages
    live_restore = next(
        change for change in aws.state_changes if change[0].startswith("fpl-live-")
    )
    assert live_restore[1] is ScheduleState.DISABLED
    steps = [
        event for event in events if isinstance(event, AdministrationWorkflowProgress)
    ]
    assert any(
        event.workflow is AdministrationWorkflow.BEGIN_MAINTENANCE
        and event.step is AdministrationWorkflowStep.STOP_COLLECTOR
        and event.state is AdministrationWorkflowStepState.COMPLETED
        for event in steps
    )
    assert any(
        event.workflow is AdministrationWorkflow.END_MAINTENANCE
        and event.step is AdministrationWorkflowStep.RESTORE_SCHEDULES
        and event.state is AdministrationWorkflowStepState.COMPLETED
        for event in steps
    )


@pytest.mark.asyncio
async def test_maintenance_progress_reports_failed_step() -> None:
    aws = FakeAws()
    aws.default_depths = [queue_depth(name="fetch-dead-letter", total=1)]
    admin = service(
        aws=aws,
        nas=FakeNas(running=True),
        database=FakeDatabase(),
        fake_time=FakeTime(),
    )
    events: list[AdministrationProgress] = []
    with pytest.raises(RuntimeError, match="require review"):
        await admin.begin_production_maintenance_with_progress(
            reason="schema work",
            progress=events.append,
        )
    failure = next(
        event
        for event in events
        if isinstance(event, AdministrationWorkflowProgress)
        and event.state is AdministrationWorkflowStepState.FAILED
    )
    assert failure.step is AdministrationWorkflowStep.CHECK_DEAD_LETTERS
    assert failure.detail is not None


@pytest.mark.asyncio
async def test_pause_retry_validation_activation_and_schedule_only_restore() -> None:
    aws = FakeAws()
    database = FakeDatabase()
    admin = service(
        aws=aws,
        nas=FakeNas(running=False),
        database=database,
        fake_time=FakeTime(),
    )
    first = await admin.pause_schedules(reason="repair", collector_was_running=None)
    assert await admin.pause_schedules(
        reason="repair",
        collector_was_running=None,
    ) == first
    with pytest.raises(RuntimeError, match="reason"):
        await admin.pause_schedules(reason="other", collector_was_running=None)
    active = await admin.activate_maintenance(collector_was_running=False)
    assert active.phase is MaintenancePhase.ACTIVE
    with pytest.raises(RuntimeError, match="active"):
        await admin.pause_schedules(reason="repair", collector_was_running=False)
    closed = await admin.restore_schedules()
    assert closed.phase is MaintenancePhase.CLOSED
    assert await admin.latest_maintenance() == closed

    with pytest.raises(RuntimeError, match="No maintenance"):
        await admin.restore_schedules()
    with pytest.raises(ValueError, match="blank"):
        await admin.pause_schedules(reason=" ", collector_was_running=None)


@pytest.mark.asyncio
async def test_rebaseline_refreshes_reference_and_live_then_stops_collector() -> None:
    aws = FakeAws()
    nas = FakeNas(running=False)
    database = FakeDatabase()
    admin = service(
        aws=aws,
        nas=nas,
        database=database,
        fake_time=FakeTime(),
    )
    await admin.pause_schedules(reason="baseline", collector_was_running=False)
    await admin.activate_maintenance(collector_was_running=False)
    result = await admin.rebaseline_current(
        reason="season repair",
        refresh_normalized_data=True,
    )
    assert result.snapshots_rebuilt == 5
    assert nas.actions == ["start", "stop"]
    assert len(aws.fetch_messages) == 2
    repeated = await admin.rebaseline_current(
        reason="  repeat  ",
        refresh_normalized_data=False,
    )
    assert repeated.reason == "repeat"
    with pytest.raises(ValueError, match="blank"):
        await admin.rebaseline_current(
            reason=" ",
            refresh_normalized_data=False,
        )


@pytest.mark.asyncio
async def test_rebaseline_and_live_job_fail_fast_on_invalid_state() -> None:
    aws = FakeAws()
    database = FakeDatabase()
    admin = service(
        aws=aws,
        nas=FakeNas(running=False),
        database=database,
        fake_time=FakeTime(),
    )
    with pytest.raises(RuntimeError, match="No maintenance"):
        await admin.rebaseline_current(reason="x", refresh_normalized_data=False)
    database.season = None
    with pytest.raises(RuntimeError, match="No current normalized season"):
        await admin.send_current_live(allow_maintenance=True)
    database.season = FakeDatabase().season
    database.event = None
    with pytest.raises(RuntimeError, match="No current normalized event"):
        await admin.send_current_live(allow_maintenance=True)
    database.event = FakeDatabase().event
    database.fixtures = []
    with pytest.raises(RuntimeError, match="kickoff"):
        await admin.send_current_live(allow_maintenance=True)


def test_restored_schedule_handles_recurring_future_active_and_expired() -> None:
    recurring = schedule(name="reference")
    assert restored_schedule(schedule=recurring, now=NOW) == (
        ScheduleState.ENABLED,
        recurring.schedule_expression,
    )
    future = schedule(
        name="fpl-live-future",
        expression="at(2026-08-24T13:00:00)",
    )
    assert restored_schedule(schedule=future, now=NOW) == (
        ScheduleState.ENABLED,
        future.schedule_expression,
    )
    active = schedule(
        name="fpl-live-active",
        expression="at(2026-08-24T11:00:00)",
    )
    assert restored_schedule(schedule=active, now=NOW) == (
        ScheduleState.ENABLED,
        "at(2026-08-24T12:01:00)",
    )
    disabled = active.model_copy(update={"state": ScheduleState.DISABLED})
    assert restored_schedule(schedule=disabled, now=NOW)[0] is ScheduleState.DISABLED
    expired_target = active.target.model_copy(
        update={
            "input": LiveJob.model_validate_json(active.target.input)
            .model_copy(update={"window_end": NOW})
            .model_dump_json(),
        },
    )
    expired = active.model_copy(update={"target": expired_target})
    assert restored_schedule(schedule=expired, now=NOW)[0] is ScheduleState.DISABLED
    malformed = active.model_copy(update={"schedule_expression": "rate(1 minute)"})
    with pytest.raises(ValueError, match="at expression"):
        restored_schedule(schedule=malformed, now=NOW)
