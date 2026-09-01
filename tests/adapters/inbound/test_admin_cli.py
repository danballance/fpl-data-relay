from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
import typer
from click import Command
from typer.testing import CliRunner

from fpl_data_relay.adapters.inbound.cli.admin import (
    AdminRuntime,
    admin_runtime,
    build_administration_facade,
    create_admin_app,
)
from fpl_data_relay.application.administration import ProductionStatus
from fpl_data_relay.application.ports.administration import (
    AdministrationProgressReporter,
    AdministrationWorkflow,
    AdministrationWorkflowProgress,
    AdministrationWorkflowStep,
    AdministrationWorkflowStepState,
    AwsIdentity,
    AwsResources,
    ChangeFeedRebaselineResult,
    MaintenancePhase,
    MaintenanceWindow,
    NasCollectorStatus,
    QueueDepth,
    QueueDrainProgress,
    QueueDrainStage,
    ScheduleSnapshot,
    ScheduleState,
    ScheduleTargetSnapshot,
    SchemaStatus,
)
from fpl_data_relay.config import AdminSettings

NOW = datetime(2026, 8, 24, tzinfo=UTC)


def admin_settings() -> AdminSettings:
    return AdminSettings.model_validate(
        {
            "aws_profile": "admin",
            "aws_region": "eu-west-2",
            "data_stack_name": "data",
            "app_stack_name": "app",
            "nas_ssh_target": "nas",
            "nas_stack_directory": "/stack",
            "nas_compose_executable": "/compose",
            "nas_docker_executable": "/docker",
            "nas_ssh_connect_timeout_seconds": 10,
            "drain_timeout_seconds": 20,
            "drain_poll_seconds": 2,
            "drain_stable_seconds": 4,
            "nas_health_attempts": 2,
            "nas_health_interval_seconds": 1,
            "nas_log_tail_lines": 10,
        },
    )


def depth(*, name: str) -> QueueDepth:
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
        description="reference",
        target=ScheduleTargetSnapshot(
            arn="arn:queue",
            role_arn="arn:role",
            input='{"version":1,"kind":"reference"}',
            dead_letter_arn="arn:dlq",
            maximum_event_age_seconds=900,
            maximum_retry_attempts=3,
        ),
    )


def window(*, phase: MaintenancePhase) -> MaintenanceWindow:
    return MaintenanceWindow(
        id=1,
        reason="work",
        operator_arn="arn:operator",
        phase=phase,
        schedules=[schedule()],
        collector_was_running=True,
        queues_before=[depth(name="fetch")],
        queues_after=[depth(name="fetch")],
        started_at=NOW,
        activated_at=NOW,
        closed_at=NOW if phase is MaintenancePhase.CLOSED else None,
        closed_by="arn:operator" if phase is MaintenancePhase.CLOSED else None,
    )


def result() -> ChangeFeedRebaselineResult:
    return ChangeFeedRebaselineResult(
        id=2,
        season_id="2026-27",
        reason="work",
        change_events_deleted=3,
        entity_changes_deleted=4,
        snapshots_rebuilt=5,
        created_at=NOW,
    )


class FakeAws:
    def __init__(self) -> None:
        self.schedules = [schedule()]

    def identity(self) -> AwsIdentity:
        return AwsIdentity(account_id="123456789012", arn="arn:operator")

    def app_deployed_revision(self) -> str:
        return "a" * 40

    def resources(self) -> AwsResources:
        return AwsResources(
            database_resource_arn="arn:db",
            database_secret_arn="arn:secret",
            database_name="relay",
            fetch_queue_url="fetch",
            fetch_dead_letter_queue_url="fetch-dlq",
            result_queue_url="result",
            result_dead_letter_queue_url="result-dlq",
            schedule_dead_letter_queue_url="schedule-dlq",
            community_queue_url="community",
            community_dead_letter_queue_url="community-dlq",
            reference_schedule_group_name="reference",
            reference_schedule_name="reference",
            live_schedule_group_name="live",
            community_schedule_group_name="community",
            community_schedule_name="community",
        )

    def queue_depths(self, *, include_dead_letters: bool) -> list[QueueDepth]:
        values = [depth(name="fetch")]
        if include_dead_letters:
            values.append(depth(name="fetch-dead-letter"))
        return values

    def schedule_snapshots(self) -> list[ScheduleSnapshot]:
        return list(self.schedules)

    def set_schedule_state(
        self,
        *,
        schedule: ScheduleSnapshot,
        state: ScheduleState,
        schedule_expression: str,
    ) -> None:
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

    def peek_dead_letters(
        self,
        *,
        queue_name: str,
        max_messages: int,
    ) -> list[str]:
        assert queue_name == "fetch"
        assert max_messages == 10
        return ["failure"]


class FakeNas:
    def status(self) -> NasCollectorStatus:
        return NasCollectorStatus(running=True, health="healthy", image="image")

    def start(self) -> NasCollectorStatus:
        return self.status()

    def stop(self) -> NasCollectorStatus:
        return NasCollectorStatus(running=False, health="stopped", image="image")

    def logs(self, *, tail_lines: int) -> str:
        return f"logs={tail_lines}"

    def update(self, *, image_tag: str) -> NasCollectorStatus:
        assert image_tag == "sha-" + "a" * 40
        return self.status()


class FakeDatabase:
    async def schema_status(self) -> SchemaStatus:
        return SchemaStatus(applied_versions=[5], pending_versions=[])


class FakeService:
    def aws_doctor(self) -> AwsIdentity:
        return AwsIdentity(account_id="123456789012", arn="arn:operator")

    def nas_doctor(self) -> None:
        return None

    async def latest_maintenance(self) -> MaintenanceWindow:
        return window(phase=MaintenancePhase.ACTIVE)

    async def apply_schema(self) -> SchemaStatus:
        return SchemaStatus(applied_versions=[5], pending_versions=[])

    def drain_queues(self) -> list[QueueDepth]:
        return [depth(name="fetch")]

    def drain_queues_with_progress(
        self,
        *,
        stage: QueueDrainStage,
        progress: AdministrationProgressReporter,
    ) -> list[QueueDepth]:
        depths = self.drain_queues()
        progress(
            QueueDrainProgress(
                stage=stage,
                queues=depths,
                elapsed_seconds=0,
                stable_for_seconds=4,
                required_stable_seconds=4,
                sampled_at=NOW,
            ),
        )
        return depths

    async def send_reference(self, *, allow_maintenance: bool) -> str:
        assert allow_maintenance is False
        return "reference-id"

    async def send_current_live(self, *, allow_maintenance: bool) -> str:
        assert allow_maintenance is False
        return "live-id"

    async def send_community(self, *, allow_maintenance: bool) -> str:
        assert allow_maintenance is False
        return "community-id"

    async def pause_schedules(
        self,
        *,
        reason: str,
        collector_was_running: bool | None,
    ) -> MaintenanceWindow:
        assert reason == "work"
        assert collector_was_running is None
        return window(phase=MaintenancePhase.ENTERING)

    async def restore_schedules(self) -> MaintenanceWindow:
        return window(phase=MaintenancePhase.CLOSED)

    async def rebaseline_current(
        self,
        *,
        reason: str,
        refresh_normalized_data: bool,
    ) -> ChangeFeedRebaselineResult:
        assert reason == "work"
        return result().model_copy(
            update={"reason": f"refresh={refresh_normalized_data}"},
        )

    async def rebaseline_current_with_progress(
        self,
        *,
        reason: str,
        refresh_normalized_data: bool,
        progress: AdministrationProgressReporter,
    ) -> ChangeFeedRebaselineResult:
        self._report_progress(
            workflow=AdministrationWorkflow.REBASELINE,
            progress=progress,
        )
        return await self.rebaseline_current(
            reason=reason,
            refresh_normalized_data=refresh_normalized_data,
        )

    async def production_status(self) -> ProductionStatus:
        return ProductionStatus(
            schema_status=SchemaStatus(applied_versions=[5], pending_versions=[]),
            maintenance=window(phase=MaintenancePhase.ACTIVE),
            queues=[depth(name="fetch")],
            schedules=[schedule()],
            collector=NasCollectorStatus(
                running=True,
                health="healthy",
                image="image",
            ),
        )

    async def begin_production_maintenance(self, *, reason: str) -> MaintenanceWindow:
        assert reason == "work"
        return window(phase=MaintenancePhase.ACTIVE)

    async def begin_production_maintenance_with_progress(
        self,
        *,
        reason: str,
        progress: AdministrationProgressReporter,
    ) -> MaintenanceWindow:
        self._report_progress(
            workflow=AdministrationWorkflow.BEGIN_MAINTENANCE,
            progress=progress,
        )
        return await self.begin_production_maintenance(reason=reason)

    async def end_production_maintenance(self) -> MaintenanceWindow:
        return window(phase=MaintenancePhase.CLOSED)

    async def end_production_maintenance_with_progress(
        self,
        *,
        progress: AdministrationProgressReporter,
    ) -> MaintenanceWindow:
        self._report_progress(
            workflow=AdministrationWorkflow.END_MAINTENANCE,
            progress=progress,
        )
        return await self.end_production_maintenance()

    def _report_progress(
        self,
        *,
        workflow: AdministrationWorkflow,
        progress: AdministrationProgressReporter,
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


class FakeRuntime:
    def __init__(self) -> None:
        self.settings = admin_settings()
        self.aws = FakeAws()
        self.nas = FakeNas()
        self.database = FakeDatabase()
        self.service = FakeService()


def test_admin_cli_facade_keeps_remote_runtime_properties_lazy() -> None:
    eager = FakeRuntime()

    class LazyRuntime:
        def __init__(self) -> None:
            self.settings = eager.settings
            self.resolved: list[str] = []

        @property
        def service(self) -> FakeService:
            self.resolved.append("service")
            return eager.service

        @property
        def aws(self) -> FakeAws:
            self.resolved.append("aws")
            return eager.aws

        @property
        def nas(self) -> FakeNas:
            self.resolved.append("nas")
            return eager.nas

        @property
        def database(self) -> FakeDatabase:
            self.resolved.append("database")
            return eager.database

    runtime = LazyRuntime()
    facade = build_administration_facade(runtime=cast("AdminRuntime", runtime))

    assert runtime.resolved == []
    assert facade.collector_status().running is True
    assert runtime.resolved == ["nas"]


def invoke(
    *,
    runner: CliRunner,
    app: typer.Typer,
    arguments: list[str],
) -> str:
    invocation = runner.invoke(app, ["--config", "/config", *arguments])
    assert invocation.exit_code == 0, invocation.output
    return invocation.output


def test_admin_cli_exposes_all_aws_commands(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    app = create_admin_app(
        runtime_factory=lambda path: cast("AdminRuntime", runtime),
    )
    runner = CliRunner()
    commands = [
        ["aws", "doctor"],
        ["aws", "app-revision"],
        ["aws", "status"],
        ["aws", "db-status"],
        ["aws", "db-migrate", "--confirm", "production"],
        ["aws", "queues-status"],
        ["aws", "queues-drain"],
        ["aws", "dlqs-status"],
        ["aws", "dlq-peek", "--queue", "fetch", "--max-messages", "10"],
        ["aws", "send-reference"],
        ["aws", "send-live"],
        ["aws", "send-community"],
        ["aws", "schedules-status"],
        [
            "aws",
            "schedules-bootstrap-pause",
            "--state-file",
            str(tmp_path / "schedules.json"),
            "--confirm",
            "production",
        ],
        [
            "aws",
            "schedules-bootstrap-restore",
            "--state-file",
            str(tmp_path / "schedules.json"),
            "--confirm",
            "production",
        ],
        ["aws", "maintenance-status"],
        [
            "aws",
            "schedules-pause",
            "--reason",
            "work",
            "--confirm",
            "production",
        ],
        ["aws", "schedules-restore", "--confirm", "production"],
        [
            "aws",
            "rebaseline-current",
            "--reason",
            "work",
            "--confirm",
            "production",
        ],
    ]
    outputs = [
        invoke(runner=runner, app=app, arguments=command) for command in commands
    ]
    assert "profile=admin region=eu-west-2" in outputs[0]
    assert "account=123456789012 operator=arn:operator" in outputs[0]
    assert "AWS administration checks passed" in outputs[0]
    assert "deployed_revision=" + "a" * 40 in outputs[1]
    assert "account=123456789012 operator=arn:operator" in outputs[2]
    assert "queue_drain_stage=standalone" in outputs[6]
    assert "message[1]=failure" in outputs[8]
    assert "reference sent" in outputs[9]
    assert "state=disabled" in outputs[13]
    assert "state=restored" in outputs[14]
    assert "workflow=rebaseline" in outputs[-1]
    assert "rebaseline_id=2" in outputs[-1]


def test_admin_cli_exposes_all_nas_and_prod_commands() -> None:
    runtime = FakeRuntime()
    app = create_admin_app(
        runtime_factory=lambda path: cast("AdminRuntime", runtime),
    )
    runner = CliRunner()
    sha = "a" * 40
    commands = [
        ["nas", "doctor"],
        ["nas", "status"],
        ["nas", "start", "--confirm", "production"],
        ["nas", "stop", "--confirm", "production"],
        ["nas", "logs"],
        ["nas", "update", "--sha", sha, "--confirm", "production"],
        ["nas", "rollback", "--sha", sha, "--confirm", "production"],
        ["prod", "doctor"],
        ["prod", "status"],
        [
            "prod",
            "maintenance-begin",
            "--reason",
            "work",
            "--confirm",
            "production",
        ],
        ["prod", "maintenance-end", "--confirm", "production"],
        [
            "prod",
            "rebaseline-current",
            "--reason",
            "work",
            "--confirm",
            "production",
        ],
    ]
    outputs = [
        invoke(runner=runner, app=app, arguments=command) for command in commands
    ]
    assert "NAS administration checks passed" in outputs[0]
    assert "collector_running=true" in outputs[1]
    assert "logs=10" in outputs[4]
    assert "production administration checks passed" in outputs[7]
    assert "workflow=begin_maintenance" in outputs[9]
    assert "workflow=end_maintenance" in outputs[10]
    assert "workflow=rebaseline" in outputs[11]
    assert "maintenance_id=1" in outputs[9]


def test_admin_cli_requires_exact_confirmation_and_runtime() -> None:
    runtime = FakeRuntime()
    app = create_admin_app(
        runtime_factory=lambda path: cast("AdminRuntime", runtime),
    )
    invocation = CliRunner().invoke(
        app,
        ["--config", "/config", "nas", "start", "--confirm", "yes"],
    )
    assert invocation.exit_code != 0
    assert "must be exactly" in invocation.output
    context = typer.Context(cast("Any", Command("test")))
    with pytest.raises(RuntimeError, match="not configured"):
        admin_runtime(context=context)


def test_admin_bootstrap_builds_explicit_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fpl_data_relay import admin_bootstrap

    runtime = FakeRuntime()
    construction: list[str] = []

    class BootstrapAws(FakeAws):
        def database(self) -> FakeDatabase:
            construction.append("database")
            return runtime.database

    aws = BootstrapAws()
    monkeypatch.setattr(
        admin_bootstrap,
        "load_admin_settings",
        lambda *, path: runtime.settings,
    )
    monkeypatch.setattr(
        admin_bootstrap,
        "AwsBotoAdministration",
        lambda *, settings: construction.append("aws") or aws,
    )
    monkeypatch.setattr(
        admin_bootstrap,
        "NasSshAdministration",
        lambda *, settings: construction.append("nas") or runtime.nas,
    )
    built = admin_bootstrap.build_admin_runtime(Path("/config"))
    assert built.settings.aws_profile == "admin"
    assert construction == []
    assert built.aws is aws
    assert construction == ["aws"]
    assert built.nas is runtime.nas
    assert construction == ["aws", "nas"]
    assert built.database is runtime.database
    assert construction == ["aws", "nas", "database"]
