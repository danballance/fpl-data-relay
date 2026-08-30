import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest
from textual.widgets import Button, ContentSwitcher, DataTable, Input, RichLog, Static

import fpl_data_relay.adapters.inbound.tui.app as app_module
from fpl_data_relay.adapters.inbound.tui.app import (
    AdministrationFacadeFactory,
    FplDataRelayTui,
    TargetCommandProvider,
    build_tui,
)
from fpl_data_relay.adapters.inbound.tui.logging import (
    OperationProgressEvent,
)
from fpl_data_relay.adapters.inbound.tui.process_runner import (
    MakeProcessResult,
    MakeProcessRunner,
    ManagedMakeProcess,
)
from fpl_data_relay.adapters.inbound.tui.screens import (
    ArgumentsScreen,
    ConfirmationScreen,
    FormSubmission,
    FormValue,
    InformationScreen,
)
from fpl_data_relay.adapters.inbound.tui.settings import TuiSettings
from fpl_data_relay.application.administration_facade import AdministrationFacade
from fpl_data_relay.application.ports.administration import (
    AdministrationSnapshot,
    AdministrationWorkflow,
    AdministrationWorkflowProgress,
    AdministrationWorkflowStep,
    AdministrationWorkflowStepState,
    AwsConnectionStatus,
    AwsResources,
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
from fpl_data_relay.application.tui_catalogue import (
    COMMAND_CATALOGUE,
    CommandParameters,
    DeadLetterQueue,
    DlqSelectionParameters,
    ExecutionKind,
    NoParameters,
    ParameterKind,
    ReasonParameters,
    ShaParameters,
    StateFileParameters,
    TuiCommand,
    command_for_target,
)


def interaction_settings(*, tmp_path: Path) -> TuiSettings:
    root = tmp_path / "project"
    root.mkdir()
    (root / "Makefile").write_text("help:\n")
    (root / "pyproject.toml").write_text("[project]\nname = 'test'\n")
    (root / ".admin.env").write_text("invalid=[value\n")
    return TuiSettings(
        project_root=root,
        admin_config=root / ".admin.env",
        log_path=root / ".admin-state" / "tui" / "fpl-tui.jsonl",
        log_max_bytes=10_485_760,
        log_file_count=5,
    )


def administration_snapshot() -> AdministrationSnapshot:
    captured_at = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    connection = AwsConnectionStatus(
        profile_name="relay-admin",
        region="eu-west-2",
        account_id="123456789012",
        arn="arn:aws:iam::123456789012:user/developer",
    )
    return AdministrationSnapshot(
        connection=connection,
        resources=AwsResources(
            database_resource_arn="database-resource",
            database_secret_arn="database-secret",
            database_name="relay",
            fetch_queue_url="https://sqs.example/fetch",
            fetch_dead_letter_queue_url="https://sqs.example/fetch-dlq",
            result_queue_url="https://sqs.example/result",
            result_dead_letter_queue_url="https://sqs.example/result-dlq",
            schedule_dead_letter_queue_url="https://sqs.example/schedule-dlq",
            community_queue_url="https://sqs.example/community",
            community_dead_letter_queue_url="https://sqs.example/community-dlq",
            reference_schedule_group_name="reference-group",
            reference_schedule_name="reference",
            live_schedule_group_name="live-group",
            community_schedule_group_name="community-group",
            community_schedule_name="community",
        ),
        deployed_revision="a" * 40,
        schema_status=SchemaStatus(applied_versions=[1, 2], pending_versions=[]),
        maintenance=None,
        queues=[
            QueueDepth(
                name="fetch",
                url="https://sqs.example/fetch",
                visible=2,
                in_flight=1,
                delayed=0,
            ),
        ],
        schedules=[
            ScheduleSnapshot(
                name="reference",
                group_name="reference-group",
                state=ScheduleState.ENABLED,
                schedule_expression="rate(15 minutes)",
                schedule_expression_timezone="UTC",
                flexible_window_mode="OFF",
                action_after_completion=None,
                description="reference schedule",
                target=ScheduleTargetSnapshot(
                    arn="arn:aws:lambda:eu-west-2:123456789012:function:relay",
                    role_arn="arn:aws:iam::123456789012:role/scheduler",
                    input='{"job":"reference"}',
                    dead_letter_arn="arn:aws:sqs:eu-west-2:123456789012:dlq",
                    maximum_event_age_seconds=60,
                    maximum_retry_attempts=1,
                ),
            ),
        ],
        collector=NasCollectorStatus(
            running=True,
            health="healthy",
            image="collector:sha-aaaaaaaa",
        ),
        captured_at=captured_at,
    )


class SnapshotFacade:
    def __init__(self, *, results: list[AdministrationSnapshot | Exception]) -> None:
        self.results = results
        self.collector_start_requests = 0

    async def snapshot(self) -> AdministrationSnapshot:
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def collector_start(self) -> NasCollectorStatus:
        self.collector_start_requests += 1
        return administration_snapshot().collector

def app_with_facade(
    *,
    tmp_path: Path,
    facade: SnapshotFacade,
):
    settings = interaction_settings(tmp_path=tmp_path)
    app = build_tui(
        settings=settings,
        facade_factory=cast(
            "AdministrationFacadeFactory",
            lambda _path: facade,
        ),
    )
    return app, settings


async def test_manual_refresh_renders_snapshot_and_retains_stale_state(
    tmp_path: Path,
) -> None:
    snapshot = administration_snapshot()
    facade = SnapshotFacade(
        results=[snapshot, RuntimeError("refresh [credentials] expired")],
    )
    app, _settings = app_with_facade(tmp_path=tmp_path, facade=facade)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.press("r")
        await pilot.pause(0.2)

        assert app.query_one("#queue-table", DataTable).row_count == 1
        assert "relay-admin" in str(app.query_one("#profile-card", Static).content)

        await pilot.press("r")
        await pilot.pause(0.2)

        message = str(app.query_one("#overview-message", Static).content)
        assert "STALE" in message
        assert "refresh [credentials] expired" in message
        assert app.query_one("#queue-table", DataTable).row_count == 1
        await app.workers.wait_for_complete()


async def test_raw_information_and_keyed_table_rows_render_without_markup(
    tmp_path: Path,
) -> None:
    facade = SnapshotFacade(results=[])
    app, _settings = app_with_facade(tmp_path=tmp_path, facade=facade)

    async with app.run_test(size=(110, 30)) as pilot:
        app._render_snapshot(snapshot=administration_snapshot())
        queue_table = app.query_one("#queue-table", DataTable)
        queue_table.focus()
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, InformationScreen)
        await pilot.click("#information-close")
        await pilot.pause()

        app.run_worker(
            app.push_screen_wait(
                InformationScreen(
                    title="Raw output",
                    content="[type=missing, input_value={}, input_type=dict]",
                ),
            ),
            exit_on_error=False,
        )
        await pilot.pause()

        assert isinstance(app.screen, InformationScreen)
        content = app.screen.query_one("#information-content", Static)
        assert "input_value" in str(content.content)
        await pilot.click("#information-close")
        await app.workers.wait_for_complete()


async def test_mutation_form_reserves_global_operation_slot(tmp_path: Path) -> None:
    facade = SnapshotFacade(results=[])
    app, _settings = app_with_facade(tmp_path=tmp_path, facade=facade)

    async with app.run_test(size=(110, 30)) as pilot:
        app.request_target("nas-update")
        await pilot.pause()
        assert isinstance(app.screen, ArgumentsScreen)
        assert app._exclusive_reservation == "nas-update"

        app.request_target("doctor")
        await pilot.pause()
        assert isinstance(app.screen, ArgumentsScreen)
        assert app._exclusive_reservation == "nas-update"

        await pilot.click("#arguments-cancel")
        await app.workers.wait_for_complete()
        assert app._exclusive_reservation is None


def test_typed_command_forms_build_only_explicit_parameter_models() -> None:
    reason = FormSubmission(
        target="prod-maintenance-begin",
        values=(FormValue(name="reason", value="planned work"),),
    )
    sha = FormSubmission(
        target="nas-update",
        values=(FormValue(name="sha", value="a" * 40),),
    )
    queue = FormSubmission(
        target="aws-dlq-peek",
        values=(FormValue(name="queue", value="fetch"),),
    )
    state = FormSubmission(
        target="aws-schedules-bootstrap-pause",
        values=(FormValue(name="state_file", value="/tmp/schedules.json"),),
    )

    assert app_parameter(command="prod-maintenance-begin", form=reason) == (
        ReasonParameters(reason="planned work")
    )
    assert app_parameter(command="nas-update", form=sha) == ShaParameters(
        sha="a" * 40,
    )
    assert app_parameter(command="aws-dlq-peek", form=queue) == (
        DlqSelectionParameters(queue=DeadLetterQueue.FETCH)
    )
    assert app_parameter(
        command="aws-schedules-bootstrap-pause",
        form=state,
    ) == StateFileParameters(
        state_file=Path("/tmp/schedules.json"),
    )


def app_parameter(*, command: str, form: FormSubmission) -> CommandParameters:
    return FplDataRelayTui._build_parameters(
        command=command_for_target(target=command),
        submission=form,
    )


def rich_log_text(log: RichLog) -> str:
    return "\n".join(line.text for line in log.lines)


async def test_typed_workflow_and_queue_progress_use_live_table(
    tmp_path: Path,
) -> None:
    facade = SnapshotFacade(results=[])
    app, _settings = app_with_facade(tmp_path=tmp_path, facade=facade)
    now = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)

    async with app.run_test(size=(110, 30)) as pilot:
        app._render_progress(
            AdministrationWorkflowProgress(
                workflow=AdministrationWorkflow.BEGIN_MAINTENANCE,
                step=AdministrationWorkflowStep.PAUSE_SCHEDULES,
                state=AdministrationWorkflowStepState.STARTED,
                occurred_at=now,
                detail="pausing configured schedules",
            ),
        )
        progress_table = app.query_one("#progress-table", DataTable)
        assert progress_table.display is True
        assert progress_table.row_count == 1
        await pilot.pause()

        task_drawer = app.query_one("#task-drawer")
        task_log = app.query_one("#task-log", RichLog)
        assert progress_table.region.height == 7
        assert task_log.region.height >= 10
        assert progress_table.region.bottom <= task_log.region.y
        assert task_log.region.bottom <= task_drawer.region.bottom

        app._render_progress(
            QueueDrainProgress(
                stage=QueueDrainStage.AFTER_COLLECTOR_STOP,
                queues=administration_snapshot().queues,
                elapsed_seconds=4.0,
                stable_for_seconds=2.0,
                required_stable_seconds=5,
                sampled_at=now,
            ),
        )
        assert progress_table.row_count == 1


@pytest.mark.parametrize(
    ("target", "field_names"),
    [
        ("doctor", ()),
        ("aws-db-migrate", ()),
        ("prod-maintenance-begin", ("reason",)),
        ("nas-update", ("sha",)),
        ("aws-dlq-peek", ("queue",)),
        ("aws-schedules-bootstrap-pause", ("state_file",)),
    ],
)
def test_every_parameter_kind_has_an_explicit_native_form(
    target: str,
    field_names: tuple[str, ...],
) -> None:
    fields = FplDataRelayTui._fields_for(
        command=command_for_target(target=target),
    )

    assert tuple(field.name for field in fields) == field_names
    assert all(field.password is False for field in fields)


def parameters_for_command(*, command: TuiCommand) -> CommandParameters:
    if command.parameter_kind is ParameterKind.NONE:
        return NoParameters()
    if command.parameter_kind is ParameterKind.REASON:
        return ReasonParameters(reason="planned work")
    if command.parameter_kind is ParameterKind.SHA:
        return ShaParameters(sha="a" * 40)
    if command.parameter_kind is ParameterKind.DLQ_SELECTION:
        return DlqSelectionParameters(queue=DeadLetterQueue.FETCH)
    if command.parameter_kind is ParameterKind.STATE_FILE:
        return StateFileParameters(
            state_file=Path("/tmp/schedules.json"),
        )
    raise AssertionError(f"Unhandled test parameter kind: {command.parameter_kind}")


EXPECTED_FACADE_METHOD = {
    "aws-doctor": "aws_doctor",
    "aws-status": "aws_snapshot",
    "prod-status": "snapshot",
    "aws-app-revision": "deployed_revision",
    "aws-db-status": "schema_status",
    "aws-db-migrate": "apply_schema",
    "aws-queues-status": "queue_depths",
    "aws-queues-drain": "drain_queues",
    "aws-dlqs-status": "dead_letter_depths",
    "aws-dlq-peek": "peek_dead_letters",
    "aws-send-reference": "send_reference",
    "aws-send-live": "send_current_live",
    "aws-send-community": "send_community",
    "aws-schedules-status": "schedule_snapshots",
    "aws-schedules-bootstrap-pause": "pause_schedules_to_state_file",
    "aws-schedules-bootstrap-restore": "restore_schedules_from_state_file",
    "aws-maintenance-status": "latest_maintenance",
    "aws-schedules-pause": "pause_schedules",
    "aws-schedules-restore": "restore_schedules",
    "aws-rebaseline-current": "rebaseline_current",
    "nas-doctor": "nas_doctor",
    "nas-status": "collector_status",
    "nas-start": "collector_start",
    "nas-stop": "collector_stop",
    "nas-logs": "nas_logs",
    "nas-update": "collector_update",
    "nas-rollback": "collector_update",
    "prod-doctor": "production_doctor",
    "prod-maintenance-begin": "begin_production_maintenance",
    "prod-maintenance-end": "end_production_maintenance",
    "prod-rebaseline-current": "rebaseline_current",
}


class DisplayAdminSettings:
    aws_profile = "relay-admin"
    aws_region = "eu-west-2"
    nas_log_tail_lines = 25


def test_every_administration_target_routes_to_the_shared_typed_facade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade = Mock()
    app, _settings = app_with_facade(
        tmp_path=tmp_path,
        facade=cast("SnapshotFacade", facade),
    )
    monkeypatch.setattr(
        app_module,
        "load_admin_settings",
        lambda *, path: DisplayAdminSettings(),
    )
    progress = Mock()

    try:
        for command in COMMAND_CATALOGUE:
            if command.execution_kind is ExecutionKind.MAKE_PROCESS:
                continue
            facade.reset_mock()
            app._invoke_admin(
                facade=cast("AdministrationFacade", facade),
                command=command,
                parameters=parameters_for_command(command=command),
                progress=cast("Callable[[object], None]", progress),
            )

            assert facade.mock_calls
            assert facade.mock_calls[0][0] == EXPECTED_FACADE_METHOD[command.target]

        unknown = command_for_target(target="aws-status").model_copy(
            update={"target": "unsupported-admin-target"},
        )
        with pytest.raises(AssertionError, match="No administration mapping"):
            app._invoke_admin(
                facade=cast("AdministrationFacade", facade),
                command=unknown,
                parameters=NoParameters(),
                progress=cast("Callable[[object], None]", progress),
            )
    finally:
        app.on_unmount()


class FakeManagedMakeProcess:
    def __init__(self, *, target: str, exit_code: int, remain_running: bool) -> None:
        self.target = target
        self.exit_code = exit_code
        self.running = remain_running
        self.interrupt_requests = 0
        self.termination_requests = 0

    @property
    def is_running(self) -> bool:
        return self.running

    def wait(self, *, timeout_seconds: float | None) -> MakeProcessResult:
        assert timeout_seconds is None
        self.running = False
        now = datetime.now(tz=UTC)
        return MakeProcessResult(
            target=self.target,
            argv=("uv", "run", "make", "--no-print-directory", self.target),
            exit_code=self.exit_code,
            started_at=now,
            finished_at=now,
            signals=(),
        )

    def request_interrupt(self) -> None:
        self.interrupt_requests += 1

    def request_termination(self) -> None:
        self.termination_requests += 1
        self.running = False


class FakeMakeProcessRunner:
    def __init__(self, *, processes: list[FakeManagedMakeProcess]) -> None:
        self.processes = processes
        self.started_targets: list[str] = []

    def start(
        self,
        *,
        target: str,
        task_id: str,
        on_output: Callable[[str], None],
    ) -> ManagedMakeProcess:
        assert task_id
        self.started_targets.append(target)
        on_output(f"output from {target}\n")
        return cast("ManagedMakeProcess", self.processes.pop(0))


async def test_make_task_lifecycle_and_two_stage_stop_are_visible(
    tmp_path: Path,
) -> None:
    facade = SnapshotFacade(results=[])
    app, _settings = app_with_facade(tmp_path=tmp_path, facade=facade)
    completed = FakeManagedMakeProcess(
        target="lint",
        exit_code=0,
        remain_running=True,
    )
    failed = FakeManagedMakeProcess(
        target="test",
        exit_code=2,
        remain_running=True,
    )
    runner = FakeMakeProcessRunner(processes=[completed, failed])
    app._process_runner = cast("MakeProcessRunner", runner)

    async with app.run_test(size=(110, 30)) as pilot:
        await app._run_make_command(command=command_for_target(target="lint"))
        await app._run_make_command(command=command_for_target(target="test"))
        await pilot.pause()

        assert runner.started_targets == ["lint", "test"]
        assert app._active_process is None
        assert "idle" in str(app.query_one("#task-status", Static).content)
        task_log = app.query_one("#task-log", RichLog)
        assert "output from lint" in rich_log_text(task_log)
        assert "output from test" in rich_log_text(task_log)

        line_count = len(task_log.lines)
        app._switch_page(page_id="workspace")
        await pilot.pause()
        assert app.query_one("#content-switcher", ContentSwitcher).current == (
            "workspace"
        )
        assert app.query_one("#task-log", RichLog) is task_log
        assert len(task_log.lines) == line_count
        assert "output from lint" in rich_log_text(task_log)
        assert "output from test" in rich_log_text(task_log)

        await app._stop_active_process()

        long_running = FakeManagedMakeProcess(
            target="local-dev",
            exit_code=0,
            remain_running=True,
        )
        app._active_process = cast("ManagedMakeProcess", long_running)
        app._active_process_started_at = app_module.time.monotonic() - 3
        app._refresh_process_elapsed()
        assert "Running make local-dev" in str(
            app.query_one("#task-status", Static).content,
        )

        await app._stop_active_process()
        assert long_running.interrupt_requests == 1
        app._refresh_process_elapsed()
        assert "Waiting after SIGINT" in str(
            app.query_one("#task-status", Static).content,
        )

        app._offer_force_termination()
        stop = app.query_one("#stop-task", Button)
        assert str(stop.label) == "Force terminate"

        app.run_worker(app._stop_active_process(), exit_on_error=False)
        await pilot.pause()
        assert isinstance(app.screen, ConfirmationScreen)
        await pilot.click("#confirm-submit")
        await app.workers.wait_for_complete()

        assert long_running.termination_requests == 1
        app._offer_force_termination()
        app._refresh_process_elapsed()


class RefreshFacade:
    def __init__(
        self,
        *,
        snapshot_result: object = None,
        maintenance_result: object = None,
    ) -> None:
        self.snapshot_result = snapshot_result
        self.maintenance_result = maintenance_result

    def snapshot(self) -> object:
        if isinstance(self.snapshot_result, Exception):
            raise self.snapshot_result
        return self.snapshot_result

    def latest_maintenance(self) -> object:
        if isinstance(self.maintenance_result, Exception):
            raise self.maintenance_result
        return self.maintenance_result


async def test_post_action_refresh_and_recovery_preserve_last_good_state(
    tmp_path: Path,
) -> None:
    app, _settings = app_with_facade(
        tmp_path=tmp_path,
        facade=SnapshotFacade(results=[]),
    )
    facades = iter(
        [
            RefreshFacade(snapshot_result=administration_snapshot()),
            RefreshFacade(snapshot_result="not a snapshot"),
            RefreshFacade(snapshot_result=RuntimeError("refresh unavailable")),
            RefreshFacade(maintenance_result=None),
            RefreshFacade(
                maintenance_result=RuntimeError("maintenance unavailable"),
            ),
        ],
    )
    app._facade_factory = cast(
        "AdministrationFacadeFactory",
        lambda _path: next(facades),
    )
    mutation = command_for_target(target="nas-start")
    recovery = command_for_target(target="prod-maintenance-begin")

    async with app.run_test(size=(110, 30)):
        await app._refresh_snapshot_after_action(command=mutation)
        assert app._last_snapshot == administration_snapshot()
        assert app._snapshot_error is None

        await app._refresh_snapshot_after_action(command=mutation)
        assert "invalid result" in cast("str", app._snapshot_error)
        assert app._last_snapshot == administration_snapshot()

        await app._refresh_snapshot_after_action(command=mutation)
        assert app._snapshot_error == "refresh unavailable"
        assert app._last_snapshot == administration_snapshot()

        await app._show_recovery_state(failed_command=recovery, task_id="task-1")
        await app._show_recovery_state(failed_command=recovery, task_id="task-2")

        app._last_snapshot = None
        app._mark_snapshot_stale(error=RuntimeError("first refresh failed"))
        assert "first refresh failed" in str(
            app.query_one("#overview-message", Static).content,
        )


async def test_history_navigation_actions_and_raw_progress_logging(
    tmp_path: Path,
) -> None:
    app, _settings = app_with_facade(
        tmp_path=tmp_path,
        facade=SnapshotFacade(results=[]),
    )
    progress = AdministrationWorkflowProgress(
        workflow=AdministrationWorkflow.BEGIN_MAINTENANCE,
        step=AdministrationWorkflowStep.PAUSE_SCHEDULES,
        state=AdministrationWorkflowStepState.COMPLETED,
        occurred_at=datetime.now(tz=UTC),
        detail="needle progress",
    )

    async with app.run_test(size=(110, 30)) as pilot:
        app.query_one("#overview-details").scroll_visible(
            animate=False,
            immediate=True,
        )
        await pilot.pause()
        await pilot.click("#overview-details")
        await pilot.click("#stop-task")
        app._log_output_event(
            target="nas-logs",
            task_id="1",
            output="needle raw output\n",
        )
        await app._run_blocking(
            lambda: app._admin_progress(
                progress,
                task_id="2",
                target="prod-maintenance-begin",
            ),
        )
        assert any(
            isinstance(event, OperationProgressEvent)
            for event in app._event_logger.read_events()
        )

        app._switch_page(page_id="history")
        await app.workers.wait_for_complete()
        await app._render_history()
        await pilot.pause()
        history = app.query_one("#history-log", RichLog)
        assert len(history.lines) >= 2

        app.query_one("#history-filter", Input).value = "no-match"
        await app._render_history()
        await pilot.pause()
        assert len(history.lines) == 0

        app.action_show_help()
        app.action_refresh()
        assert app.query_one("#content-switcher", ContentSwitcher).current == (
            "help-page"
        )

        app._exclusive_reservation = "busy"
        app.action_request_quit()
        app._exclusive_reservation = None

        with pytest.raises(ValueError, match="Unknown TUI page"):
            app._switch_page(page_id="unknown")
        await pilot.pause()

    app._process_output("ignored after unmount")
    app.on_unmount()
    with pytest.raises(RuntimeError, match="worker pool is shut down"):
        await app._run_blocking(lambda: None)


async def test_palette_provider_and_dispatch_cancellation_paths(
    tmp_path: Path,
) -> None:
    app, _settings = app_with_facade(
        tmp_path=tmp_path,
        facade=SnapshotFacade(results=[]),
    )

    async with app.run_test(size=(110, 30)) as pilot:
        provider = TargetCommandProvider(app.screen)
        matches = [hit async for hit in provider.search("nas-logs")]
        discoveries = [hit async for hit in provider.discover()]

        assert [hit.text for hit in matches] == ["nas-logs"]
        assert len(discoveries) == len(COMMAND_CATALOGUE)

        guarded_matches = [
            hit async for hit in provider.search("aws-dlq-peek")
        ]
        assert [hit.text for hit in guarded_matches] == ["aws-dlq-peek"]
        guarded_matches[0].command()
        await pilot.pause()
        assert isinstance(app.screen, ArgumentsScreen)
        await pilot.click("#arguments-cancel")
        await app.workers.wait_for_complete()

        app._switch_page(page_id="workspace")
        await pilot.pause()
        app.query_one("#command--help").scroll_visible()
        await pilot.pause()
        await pilot.click("#command--help")
        await app.workers.wait_for_complete()
        assert app.query_one("#content-switcher", ContentSwitcher).current == (
            "help-page"
        )

        app._last_snapshot = administration_snapshot()
        app._switch_page(page_id="overview")
        await pilot.pause()
        app.query_one("#overview-details").scroll_visible(
            animate=False,
            immediate=True,
        )
        await pilot.pause()
        await pilot.click("#overview-details")
        await pilot.pause()
        assert isinstance(app.screen, InformationScreen)
        await pilot.click("#information-close")

        app._switch_page(page_id="history")
        await pilot.pause()
        await pilot.click("#history-reload")
        await app.workers.wait_for_complete()


async def test_raw_admin_results_use_information_screen_and_validate_snapshot(
    tmp_path: Path,
) -> None:
    app, _settings = app_with_facade(
        tmp_path=tmp_path,
        facade=SnapshotFacade(results=[]),
    )

    async with app.run_test(size=(110, 30)) as pilot:
        snapshot = administration_snapshot()
        assert "MISSING  Local environment" in str(
            app.query_one("#workspace-state", Static).content,
        )

        with pytest.raises(TypeError, match="not AdministrationSnapshot"):
            await app._present_admin_result(
                command=command_for_target(target="prod-status"),
                result="wrong result",
                rendered="wrong result",
            )

        with pytest.raises(TypeError, match="not AwsAdministrationSnapshot"):
            await app._present_admin_result(
                command=command_for_target(target="aws-status"),
                result="wrong result",
                rendered="wrong result",
            )
        with pytest.raises(TypeError, match="not NasCollectorStatus"):
            await app._present_admin_result(
                command=command_for_target(target="nas-status"),
                result="wrong result",
                rendered="wrong result",
            )

        await app._present_admin_result(
            command=command_for_target(target="prod-status"),
            result=snapshot,
            rendered=snapshot.model_dump_json(),
        )
        assert app.query_one("#aws-resource-table", DataTable).row_count == 15
        assert app.query_one("#aws-queue-table", DataTable).row_count == 1
        assert app.query_one("#aws-schedule-table", DataTable).row_count == 1
        assert "RUNNING" in str(app.query_one("#nas-state", Static).content)
        assert "NONE" in str(app.query_one("#production-state", Static).content)

        await app._present_admin_result(
            command=command_for_target(target="aws-status"),
            result=snapshot,
            rendered=snapshot.model_dump_json(),
        )
        await app._present_admin_result(
            command=command_for_target(target="nas-status"),
            result=snapshot.collector,
            rendered=snapshot.collector.model_dump_json(),
        )
        assert app._last_aws_snapshot == snapshot
        assert app._last_nas_status == snapshot.collector

        maintenance = MaintenanceWindow(
            id=1,
            reason="schema maintenance",
            operator_arn=snapshot.connection.arn,
            phase=MaintenancePhase.ACTIVE,
            schedules=snapshot.schedules,
            collector_was_running=True,
            queues_before=snapshot.queues,
            queues_after=[],
            started_at=snapshot.captured_at,
            activated_at=snapshot.captured_at,
            closed_at=None,
            closed_by=None,
        )
        dead_letter = QueueDepth(
            name="fetch-dead-letter",
            url="https://sqs.example/fetch-dlq",
            visible=1,
            in_flight=0,
            delayed=0,
        )
        active_snapshot = snapshot.model_copy(
            update={
                "maintenance": maintenance,
                "queues": [*snapshot.queues, dead_letter],
                "collector": snapshot.collector.model_copy(
                    update={"running": False},
                ),
            },
        )
        app._render_snapshot(snapshot=active_snapshot)
        assert app.query_one("#maintenance-table", DataTable).row_count == 1
        assert app.query_one("#aws-queue-table", DataTable).row_count == 2
        assert "ACTIVE" in str(
            app.query_one("#production-state", Static).content,
        )
        assert "STOPPED" in str(app.query_one("#nas-state", Static).content)

        app._render_nas_status(
            status=active_snapshot.collector,
            stale_error=RuntimeError("collector refresh failed"),
        )
        assert "STALE" in str(app.query_one("#nas-state", Static).content)
        app._mark_remote_stale(
            widget_id="aws-state",
            captured_at=None,
            error=RuntimeError("AWS refresh failed"),
        )
        assert "no successful refresh" in str(
            app.query_one("#aws-state", Static).content,
        )

        app.run_worker(
            app._present_admin_result(
                command=command_for_target(target="nas-logs"),
                result="raw logs",
                rendered="raw logs",
            ),
            exit_on_error=False,
        )
        await pilot.pause()
        assert isinstance(app.screen, InformationScreen)
        await pilot.click("#information-close")
        await app.workers.wait_for_complete()


def test_result_rendering_and_coroutine_resolution_are_presentation_neutral() -> None:
    async def result() -> str:
        await asyncio.sleep(0)
        return "resolved"

    assert FplDataRelayTui._resolve_admin_result(result=result()) == "resolved"
    assert FplDataRelayTui._render_result(result=None) == "completed"
    assert FplDataRelayTui._render_result(result="raw") == "raw"
    assert FplDataRelayTui._render_result(result=(SchemaStatus(
        applied_versions=[1],
        pending_versions=[2],
    ), "plain")) == (
        '[\n  {\n    "applied_versions": [\n      1\n    ],\n'
        '    "pending_versions": [\n      2\n    ]\n  },\n  "plain"\n]'
    )
    assert FplDataRelayTui._render_result(result=42) == "42"
