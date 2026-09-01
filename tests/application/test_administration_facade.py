from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from fpl_data_relay.application.administration import (
    AdministrationService,
    ProductionStatus,
)
from fpl_data_relay.application.administration_facade import AdministrationFacade
from fpl_data_relay.application.ports.administration import (
    AdministrationDoctorScope,
    AdministrationJobKind,
    AdministrationProgress,
    AdministrationProgressReporter,
    AdministrationReason,
    AdministrationSnapshot,
    AdministrationWorkflow,
    AdministrationWorkflowProgress,
    AdministrationWorkflowStep,
    AdministrationWorkflowStepState,
    AwsAdministration,
    AwsAdministrationSnapshot,
    AwsIdentity,
    AwsResources,
    ChangeFeedRebaselineResult,
    DeadLetterPeekRequest,
    DeadLetterQueueName,
    GitShaRequest,
    MaintenancePhase,
    MaintenanceWindow,
    NasAdministration,
    NasCollectorStatus,
    NasLogsRequest,
    ProductionAdministrationDatabase,
    QueueDepth,
    QueueDrainProgress,
    QueueDrainStage,
    ScheduleBootstrapSnapshot,
    ScheduleSnapshot,
    ScheduleState,
    ScheduleStateFileRequest,
    ScheduleTargetSnapshot,
    SchemaStatus,
)

NOW = datetime(2026, 8, 30, 12, tzinfo=UTC)


def queue(*, name: str) -> QueueDepth:
    return QueueDepth(
        name=name,
        url=f"https://sqs/{name}",
        visible=0,
        in_flight=0,
        delayed=0,
    )


def schedule() -> ScheduleSnapshot:
    return ScheduleSnapshot(
        name="reference",
        group_name="reference",
        state=ScheduleState.ENABLED,
        schedule_expression="cron(0 * * * ? *)",
        schedule_expression_timezone="UTC",
        flexible_window_mode="OFF",
        action_after_completion=None,
        description="reference schedule",
        target=ScheduleTargetSnapshot(
            arn="arn:queue",
            role_arn="arn:role",
            input='{"version":1,"kind":"reference"}',
            dead_letter_arn="arn:dlq",
            maximum_event_age_seconds=900,
            maximum_retry_attempts=3,
        ),
    )


def maintenance() -> MaintenanceWindow:
    return MaintenanceWindow(
        id=1,
        reason="work",
        operator_arn="arn:operator",
        phase=MaintenancePhase.ACTIVE,
        schedules=[schedule()],
        collector_was_running=True,
        queues_before=[queue(name="fetch")],
        queues_after=[queue(name="fetch")],
        started_at=NOW,
        activated_at=NOW,
        closed_at=None,
        closed_by=None,
    )


def resources() -> AwsResources:
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
        reference_schedule_name="reference",
        live_schedule_group_name="live",
        community_schedule_group_name="community",
        community_schedule_name="community",
    )


class FakeAws:
    def __init__(self) -> None:
        self.identity_calls = 0

    def identity(self) -> AwsIdentity:
        self.identity_calls += 1
        return AwsIdentity(account_id="123456789012", arn="arn:operator")

    def resources(self) -> AwsResources:
        return resources()

    def app_deployed_revision(self) -> str:
        return "a" * 40

    def queue_depths(self, *, include_dead_letters: bool) -> list[QueueDepth]:
        depths = [queue(name="fetch")]
        if include_dead_letters:
            depths.append(queue(name="fetch-dead-letter"))
        return depths

    def schedule_snapshots(self) -> list[ScheduleSnapshot]:
        return [schedule()]

    def set_schedule_state(
        self,
        *,
        schedule: ScheduleSnapshot,
        state: ScheduleState,
        schedule_expression: str,
    ) -> None:
        del schedule, state, schedule_expression

    def send_fetch_job(self, *, message_body: str) -> str:
        del message_body
        return "fetch-id"

    def send_community_job(self, *, message_body: str) -> str:
        del message_body
        return "community-id"

    def peek_dead_letters(
        self,
        *,
        queue_name: str,
        max_messages: int,
    ) -> list[str]:
        assert queue_name == "fetch"
        assert max_messages == 2
        return ["first", "second"]


class FakeNas:
    def __init__(self) -> None:
        self.image_tag: str | None = None

    def doctor(self) -> None:
        return None

    def status(self) -> NasCollectorStatus:
        return NasCollectorStatus(running=True, health="healthy", image="image")

    def start(self) -> NasCollectorStatus:
        return self.status()

    def stop(self) -> NasCollectorStatus:
        return NasCollectorStatus(running=False, health="stopped", image="image")

    def logs(self, *, tail_lines: int) -> str:
        return f"logs={tail_lines}"

    def update(self, *, image_tag: str) -> NasCollectorStatus:
        self.image_tag = image_tag
        return self.status()


class FakeDatabase:
    async def schema_status(self) -> SchemaStatus:
        return SchemaStatus(applied_versions=[5], pending_versions=[])


class FakeService:
    def __init__(self) -> None:
        self.aws_doctor_calls = 0
        self.nas_doctor_calls = 0
        self.progress_calls = 0

    def aws_doctor(self) -> AwsIdentity:
        self.aws_doctor_calls += 1
        return AwsIdentity(account_id="123456789012", arn="arn:operator")

    def nas_doctor(self) -> None:
        self.nas_doctor_calls += 1

    async def production_status(self) -> ProductionStatus:
        return ProductionStatus(
            schema_status=SchemaStatus(applied_versions=[5], pending_versions=[]),
            maintenance=maintenance(),
            queues=[queue(name="fetch")],
            schedules=[schedule()],
            collector=FakeNas().status(),
        )

    async def latest_maintenance(self) -> MaintenanceWindow:
        return maintenance()

    async def apply_schema(self) -> SchemaStatus:
        return SchemaStatus(applied_versions=[5], pending_versions=[])

    async def send_reference(self, *, allow_maintenance: bool) -> str:
        assert allow_maintenance is False
        return "reference-id"

    async def send_current_live(self, *, allow_maintenance: bool) -> str:
        assert allow_maintenance is False
        return "live-id"

    async def send_community(self, *, allow_maintenance: bool) -> str:
        assert allow_maintenance is False
        return "community-id"

    def drain_queues_with_progress(
        self,
        *,
        stage: QueueDrainStage,
        progress: AdministrationProgressReporter,
    ) -> list[QueueDepth]:
        assert stage is QueueDrainStage.STANDALONE
        progress(
            QueueDrainProgress(
                stage=stage,
                queues=[queue(name="fetch")],
                elapsed_seconds=0,
                stable_for_seconds=0,
                required_stable_seconds=4,
                sampled_at=NOW,
            ),
        )
        return [queue(name="fetch")]

    async def pause_schedules(
        self,
        *,
        reason: str,
        collector_was_running: bool | None,
    ) -> MaintenanceWindow:
        assert reason == "work"
        assert collector_was_running is None
        return maintenance()

    async def restore_schedules(self) -> MaintenanceWindow:
        return maintenance().model_copy(update={"phase": MaintenancePhase.CLOSED})

    async def begin_production_maintenance_with_progress(
        self,
        *,
        reason: str,
        progress: AdministrationProgressReporter,
    ) -> MaintenanceWindow:
        assert reason == "work"
        self._report(
            progress=progress,
            workflow=AdministrationWorkflow.BEGIN_MAINTENANCE,
        )
        return maintenance()

    async def end_production_maintenance_with_progress(
        self,
        *,
        progress: AdministrationProgressReporter,
    ) -> MaintenanceWindow:
        self._report(progress=progress, workflow=AdministrationWorkflow.END_MAINTENANCE)
        return maintenance().model_copy(update={"phase": MaintenancePhase.CLOSED})

    async def rebaseline_current_with_progress(
        self,
        *,
        reason: str,
        refresh_normalized_data: bool,
        progress: AdministrationProgressReporter,
    ) -> ChangeFeedRebaselineResult:
        assert reason == "work"
        assert refresh_normalized_data is False
        self._report(progress=progress, workflow=AdministrationWorkflow.REBASELINE)
        return ChangeFeedRebaselineResult(
            id=1,
            season_id="2026-27",
            reason=reason,
            change_events_deleted=1,
            entity_changes_deleted=2,
            snapshots_rebuilt=3,
            created_at=NOW,
        )

    def _report(
        self,
        *,
        progress: AdministrationProgressReporter,
        workflow: AdministrationWorkflow,
    ) -> None:
        progress(
            AdministrationWorkflowProgress(
                workflow=workflow,
                step=AdministrationWorkflowStep.CHECK_MAINTENANCE,
                state=AdministrationWorkflowStepState.COMPLETED,
                occurred_at=NOW,
                detail=None,
            ),
        )
        self.progress_calls += 1


def facade() -> tuple[
    AdministrationFacade,
    FakeAws,
    FakeNas,
    FakeService,
]:
    aws = FakeAws()
    nas = FakeNas()
    database = FakeDatabase()
    service = FakeService()
    return (
        AdministrationFacade(
            service_factory=lambda: cast("AdministrationService", service),
            aws_factory=lambda: cast("AwsAdministration", aws),
            nas_factory=lambda: cast("NasAdministration", nas),
            database_factory=lambda: cast(
                "ProductionAdministrationDatabase",
                database,
            ),
            aws_profile="admin",
            aws_region="eu-west-2",
            app_stack_name="app",
            clock=lambda: NOW,
        ),
        aws,
        nas,
        service,
    )


def test_facade_validates_inputs_centrally(tmp_path: Path) -> None:
    assert AdministrationReason(reason="  work  ").reason == "work"
    assert GitShaRequest(sha="a" * 40).image_tag == "sha-" + "a" * 40
    assert DeadLetterPeekRequest(
        queue=DeadLetterQueueName.FETCH,
        max_messages=10,
    ).queue is DeadLetterQueueName.FETCH
    assert ScheduleStateFileRequest(path=tmp_path / "state.json").path.name == (
        "state.json"
    )
    with pytest.raises(ValidationError, match="must not be blank"):
        AdministrationReason(reason=" ")
    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        GitShaRequest(sha="A" * 40)
    with pytest.raises(ValidationError, match="less_than_equal"):
        DeadLetterPeekRequest(queue=DeadLetterQueueName.FETCH, max_messages=11)
    with pytest.raises(ValidationError, match="must not be blank"):
        ScheduleStateFileRequest.model_validate({"path": ""})
    with pytest.raises(ValidationError, match="must not be a directory"):
        ScheduleStateFileRequest(path=tmp_path)


@pytest.mark.asyncio
async def test_facade_resolves_production_dependencies_only_when_needed() -> None:
    aws = FakeAws()
    nas = FakeNas()
    database = FakeDatabase()
    service = FakeService()
    resolutions: list[str] = []

    def service_factory() -> AdministrationService:
        resolutions.append("service")
        return cast("AdministrationService", service)

    def aws_factory() -> AwsAdministration:
        resolutions.append("aws")
        return cast("AwsAdministration", aws)

    def nas_factory() -> NasAdministration:
        resolutions.append("nas")
        return cast("NasAdministration", nas)

    def database_factory() -> ProductionAdministrationDatabase:
        resolutions.append("database")
        return cast("ProductionAdministrationDatabase", database)

    admin = AdministrationFacade(
        service_factory=service_factory,
        aws_factory=aws_factory,
        nas_factory=nas_factory,
        database_factory=database_factory,
        aws_profile="admin",
        aws_region="eu-west-2",
        app_stack_name="app",
        clock=lambda: NOW,
    )
    assert resolutions == []
    assert admin.connection_status().profile_name == "admin"
    assert resolutions == ["aws"]
    admin.collector_status()
    assert resolutions == ["aws", "nas"]
    await admin.schema_status()
    assert resolutions == ["aws", "nas", "aws", "database"]
    await admin.production_status()
    assert resolutions[-1] == "service"


@pytest.mark.asyncio
async def test_aws_snapshot_never_resolves_nas_factory() -> None:
    aws = FakeAws()
    database = FakeDatabase()
    service = FakeService()

    def reject_nas_resolution() -> NasAdministration:
        raise AssertionError("AWS snapshot must not resolve the NAS adapter")

    admin = AdministrationFacade(
        service_factory=lambda: cast("AdministrationService", service),
        aws_factory=lambda: cast("AwsAdministration", aws),
        nas_factory=reject_nas_resolution,
        database_factory=lambda: cast(
            "ProductionAdministrationDatabase",
            database,
        ),
        aws_profile="admin",
        aws_region="eu-west-2",
        app_stack_name="app",
        clock=lambda: NOW,
    )

    snapshot = await admin.aws_snapshot()

    assert isinstance(snapshot, AwsAdministrationSnapshot)
    assert snapshot.connection.profile_name == "admin"
    assert snapshot.connection.account_id == "123456789012"
    assert snapshot.deployed_revision == "a" * 40
    assert snapshot.schema_status.applied_versions == [5]
    assert snapshot.maintenance is not None
    assert snapshot.queues[-1].name == "fetch-dead-letter"
    assert snapshot.schedules[0].name == "reference"


@pytest.mark.asyncio
async def test_facade_returns_typed_read_results_and_snapshot() -> None:
    admin, aws, _, service = facade()
    doctor = admin.aws_doctor()
    assert doctor.connection.profile_name == "admin"
    assert doctor.connection.account_id == "123456789012"
    assert admin.nas_doctor().scope is AdministrationDoctorScope.NAS
    assert (await admin.production_doctor()).scope is (
        AdministrationDoctorScope.PRODUCTION
    )
    assert service.aws_doctor_calls == 2
    assert service.nas_doctor_calls == 2
    assert admin.identity().arn == "arn:operator"
    assert admin.resources().database_name == "relay"
    assert admin.deployed_revision().revision == "a" * 40

    snapshot = await admin.snapshot()
    assert isinstance(snapshot, AdministrationSnapshot)
    assert snapshot.connection.profile_name == "admin"
    assert snapshot.schema_status.pending_versions == []
    assert snapshot.collector.running is True
    assert (await admin.production_status()).collector.running is True
    assert (await admin.schema_status()).applied_versions == [5]
    latest = await admin.latest_maintenance()
    assert latest is not None
    assert latest.phase is MaintenancePhase.ACTIVE
    assert len(admin.queue_depths(include_dead_letters=False)) == 1
    assert admin.dead_letter_depths()[0].name == "fetch-dead-letter"
    assert admin.schedule_snapshots()[0].name == "reference"
    peek = admin.peek_dead_letters(
        request=DeadLetterPeekRequest(
            queue=DeadLetterQueueName.FETCH,
            max_messages=2,
        ),
    )
    assert [message.body for message in peek.messages] == ["first", "second"]
    assert aws.identity_calls > 0


@pytest.mark.asyncio
async def test_facade_returns_typed_mutation_results_and_progress() -> None:
    admin, _, nas, service = facade()
    events: list[AdministrationProgress] = []
    reason = AdministrationReason(reason="work")
    assert (await admin.apply_schema()).pending_versions == []
    assert (await admin.send_reference()).kind is AdministrationJobKind.REFERENCE
    assert (await admin.send_current_live()).kind is AdministrationJobKind.LIVE
    assert (await admin.send_community()).kind is AdministrationJobKind.COMMUNITY
    assert admin.drain_queues(progress=events.append)[0].total == 0
    assert (await admin.pause_schedules(reason=reason)).reason == "work"
    assert (await admin.restore_schedules()).phase is MaintenancePhase.CLOSED
    assert admin.collector_status().running is True
    assert admin.collector_start().running is True
    assert admin.collector_stop().running is False
    assert admin.collector_update(request=GitShaRequest(sha="b" * 40)).running
    assert nas.image_tag == "sha-" + "b" * 40
    logs = admin.nas_logs(request=NasLogsRequest(tail_lines=25))
    assert logs.output == "logs=25"
    assert (await admin.begin_production_maintenance(
        reason=reason,
        progress=events.append,
    )).phase is MaintenancePhase.ACTIVE
    assert (await admin.end_production_maintenance(
        progress=events.append,
    )).phase is MaintenancePhase.CLOSED
    rebaseline = await admin.rebaseline_current(
        reason=reason,
        refresh_normalized_data=False,
        progress=events.append,
    )
    assert rebaseline.snapshots_rebuilt == 3
    assert service.progress_calls == 3
    assert any(isinstance(event, QueueDrainProgress) for event in events)


def test_facade_routes_schedule_state_file_workflows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin, _, _, _ = facade()
    state_file = ScheduleStateFileRequest(path=tmp_path / "schedules.json")
    snapshot = ScheduleBootstrapSnapshot(
        version=1,
        account_id="123456789012",
        aws_region="eu-west-2",
        app_stack_name="app",
        captured_at=NOW,
        schedules=[schedule()],
    )
    calls: list[str] = []

    def pause(
        *,
        aws: AwsAdministration,
        snapshot_path: Path,
        aws_region: str,
        app_stack_name: str,
        captured_at: datetime,
    ) -> ScheduleBootstrapSnapshot:
        del aws, captured_at
        assert snapshot_path == state_file.path
        assert (aws_region, app_stack_name) == ("eu-west-2", "app")
        calls.append("pause")
        return snapshot

    def restore(
        *,
        aws: AwsAdministration,
        snapshot_path: Path,
        aws_region: str,
        app_stack_name: str,
        restored_at: datetime,
    ) -> ScheduleBootstrapSnapshot:
        del aws, restored_at
        assert snapshot_path == state_file.path
        assert (aws_region, app_stack_name) == ("eu-west-2", "app")
        calls.append("restore")
        return snapshot

    monkeypatch.setattr(
        "fpl_data_relay.application.administration_facade.pause_schedules_to_snapshot",
        pause,
    )
    monkeypatch.setattr(
        "fpl_data_relay.application.administration_facade.restore_schedules_from_snapshot",
        restore,
    )
    assert admin.pause_schedules_to_state_file(request=state_file) == snapshot
    assert admin.restore_schedules_from_state_file(request=state_file) == snapshot
    assert calls == ["pause", "restore"]
