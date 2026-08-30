"""Textual developer and production console for FPL Data Relay."""

import asyncio
import inspect
import json
import time
import traceback as traceback_module
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import ClassVar, cast
from uuid import uuid4

from pydantic import BaseModel
from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import (
    Button,
    ContentSwitcher,
    DataTable,
    Footer,
    Input,
    Label,
    RichLog,
    Select,
    Static,
)

from fpl_data_relay.adapters.inbound.tui.logging import (
    LogParameter,
    LogStream,
    OperationCompletedEvent,
    OperationFailedEvent,
    OperationOutputEvent,
    OperationProgressEvent,
    OperationStartedEvent,
    SecureJsonlLogger,
)
from fpl_data_relay.adapters.inbound.tui.process_runner import (
    MakeProcessRunner,
    ManagedMakeProcess,
)
from fpl_data_relay.adapters.inbound.tui.screens import (
    ArgumentsScreen,
    ConfirmationScreen,
    FormField,
    FormRequest,
    FormSubmission,
    InformationScreen,
)
from fpl_data_relay.adapters.inbound.tui.settings import TuiSettings
from fpl_data_relay.application.administration_facade import AdministrationFacade
from fpl_data_relay.application.ports.administration import (
    AdministrationProgress,
    AdministrationReason,
    AdministrationSnapshot,
    AdministrationWorkflowProgress,
    AwsAdministrationSnapshot,
    DeadLetterPeekRequest,
    DeadLetterQueueName,
    GitShaRequest,
    NasCollectorStatus,
    NasLogsRequest,
    QueueDrainProgress,
    ScheduleStateFileRequest,
)
from fpl_data_relay.application.tui_catalogue import (
    COMMAND_CATALOGUE,
    CommandGroup,
    CommandParameters,
    CommandPrerequisite,
    CommandRisk,
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
from fpl_data_relay.config import load_admin_settings

type AdminResult = object
type AdminCoroutine = Coroutine[object, object, AdminResult]

BLOCKING_WORKER_COUNT = 8


type AdministrationFacadeFactory = Callable[[Path], AdministrationFacade]


NAVIGATION: tuple[tuple[str, str], ...] = (
    ("overview", "Overview"),
    (CommandGroup.WORKSPACE.value, "Workspace"),
    (CommandGroup.LOCAL_SERVICES.value, "Local Services"),
    (CommandGroup.AWS.value, "AWS"),
    (CommandGroup.NAS_COLLECTOR.value, "NAS Collector"),
    (CommandGroup.PRODUCTION.value, "Production"),
    (CommandGroup.QUALITY.value, "Quality"),
    ("history", "History"),
    ("help-page", "Help"),
)


class TargetCommandProvider(Provider):
    """Expose exact public Make target names through the command palette."""

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for command in COMMAND_CATALOGUE:
            candidate = f"{command.target} {command.description}"
            score = matcher.match(candidate)
            if score > 0:
                app = cast("FplDataRelayTui", self.app)
                yield Hit(
                    score,
                    matcher.highlight(command.target),
                    partial(app.request_target, command.target),
                    text=command.target,
                    help=command.description,
                )

    async def discover(self) -> Hits:
        app = cast("FplDataRelayTui", self.app)
        for command in COMMAND_CATALOGUE:
            yield DiscoveryHit(
                command.target,
                partial(app.request_target, command.target),
                text=command.target,
                help=command.description,
            )


class CommandGroupPanel(VerticalScroll):
    """Card list for one public Make command group."""

    def __init__(self, *, group: CommandGroup, title: str) -> None:
        super().__init__(id=group.value, classes="page command-page")
        self._group = group
        self._title = title

    def compose(self) -> ComposeResult:
        yield Label(self._title, classes="page-title")
        if self._group is CommandGroup.WORKSPACE:
            yield Static(
                "Prerequisites have not been inspected.",
                id="workspace-state",
                classes="state-panel",
                markup=False,
            )
        elif self._group is CommandGroup.AWS:
            yield Static(
                "Press r to load AWS state.",
                id="aws-state",
                classes="state-panel muted",
                markup=False,
            )
            yield Label("Resources", classes="section-title")
            yield DataTable(
                id="aws-resource-table",
                classes="remote-table",
                zebra_stripes=True,
                cursor_type="row",
            )
            yield Label("Queues / DLQs", classes="section-title")
            yield DataTable(
                id="aws-queue-table",
                classes="remote-table",
                zebra_stripes=True,
                cursor_type="row",
            )
            yield Label("Schedules", classes="section-title")
            yield DataTable(
                id="aws-schedule-table",
                classes="remote-table",
                zebra_stripes=True,
                cursor_type="row",
            )
        elif self._group is CommandGroup.NAS_COLLECTOR:
            yield Static(
                "Press r to load collector state.",
                id="nas-state",
                classes="state-panel muted",
                markup=False,
            )
        elif self._group is CommandGroup.PRODUCTION:
            yield Static(
                "Press r to load combined production state.",
                id="production-state",
                classes="state-panel muted",
                markup=False,
            )
            yield Label("Maintenance timeline", classes="section-title")
            yield DataTable(
                id="maintenance-table",
                classes="remote-table",
                zebra_stripes=True,
                cursor_type="row",
            )
        for command in COMMAND_CATALOGUE:
            if command.group is not self._group:
                continue
            with Container(classes="command-card"):
                yield Button(
                    command.target,
                    id=f"command--{command.target}",
                    classes="command-button",
                    variant=(
                        "warning"
                        if command.risk is CommandRisk.PRODUCTION_CHANGE
                        else "default"
                    ),
                )
                yield Static(
                    command.description,
                    classes="command-description",
                    markup=False,
                )


class FplDataRelayTui(App[None]):
    """Dashboard and guarded command runner for the relay repository."""

    TITLE = "FPL Data Relay"
    SUB_TITLE = "Developer and production console"
    COMMANDS = App.COMMANDS | {TargetCommandProvider}
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+p", "command_palette", "Commands"),
        Binding("r", "refresh", "Refresh"),
        Binding("?", "show_help", "Help"),
        Binding("q", "request_quit", "Quit"),
        Binding("ctrl+q", "request_quit", "Quit", show=False, priority=True),
    ]

    CSS = """
    Screen { min-width: 80; min-height: 24; }
    #topbar {
        height: 3;
        padding: 1 2;
        background: $primary-darken-2;
        color: $text;
        text-style: bold;
    }
    #workspace-layout { width: 100%; height: 1fr; layout: horizontal; }
    #control-pane { width: 3fr; height: 1fr; }
    #narrow-nav { display: none; margin: 0 1; }
    #body { width: 100%; height: 1fr; }
    #sidebar { width: 22; padding: 1; border-right: solid $primary-darken-1; }
    #sidebar Button { width: 100%; margin-bottom: 1; }
    #content-switcher { width: 1fr; height: 1fr; }
    .page { width: 100%; height: 100%; padding: 1 2; }
    .page-title { height: 2; text-style: bold; color: $accent; }
    .command-card {
        height: auto;
        min-height: 4;
        margin-bottom: 1;
        padding: 0 1;
        border: round $primary-darken-1;
        layout: horizontal;
    }
    .command-button { width: 28; margin: 1 2 1 0; }
    .command-description { width: 1fr; height: auto; padding: 1 0; }
    .state-panel {
        height: auto;
        min-height: 4;
        padding: 1;
        margin-bottom: 1;
        border: round $primary-darken-1;
    }
    .remote-table { height: 9; margin-bottom: 1; }
    #overview-cards {
        height: auto;
        layout: grid;
        grid-size: 4;
        grid-gutter: 1;
        margin-bottom: 1;
    }
    .summary-card { height: 6; padding: 1; border: round $primary-darken-1; }
    #queue-table { height: 10; margin-bottom: 1; }
    #schedule-table { height: 1fr; min-height: 8; }
    #task-drawer {
        width: 2fr;
        height: 1fr;
        border-left: heavy $primary;
    }
    #task-header { height: 3; padding: 1; background: $surface-lighten-1; }
    #task-status { width: 1fr; }
    #stop-task { width: 18; display: none; }
    #task-log { height: 1fr; padding: 0 1; }
    #progress-table { display: none; height: 7; margin: 0 1; }
    #history-filter { margin-bottom: 1; }
    #history-log { height: 1fr; border: round $primary-darken-1; }
    #size-warning {
        display: none;
        layer: warning;
        dock: top;
        height: 100%;
        width: 100%;
        content-align: center middle;
        background: $error 90%;
        text-style: bold;
    }
    .error { color: $error; }
    .muted { color: $text-muted; }
    .narrow #sidebar { display: none; }
    .narrow #narrow-nav { display: block; }
    .narrow #overview-cards { grid-size: 1; }
    .stacked #workspace-layout { layout: vertical; }
    .stacked #control-pane { width: 100%; height: 1fr; }
    .stacked #task-drawer {
        width: 100%;
        height: 13;
        border: none;
        border-top: heavy $primary;
    }
    """

    def __init__(
        self,
        *,
        settings: TuiSettings,
        facade_factory: AdministrationFacadeFactory,
        event_logger: SecureJsonlLogger,
        process_runner: MakeProcessRunner,
        session_id: str,
    ) -> None:
        super().__init__()
        self._settings = settings
        self._facade_factory = facade_factory
        self._event_logger = event_logger
        self._process_runner = process_runner
        self._session_id = session_id
        self._blocking_executor = ThreadPoolExecutor(
            max_workers=BLOCKING_WORKER_COUNT,
            thread_name_prefix="fpl-tui-blocking",
        )
        self._blocking_executor_shutdown = False
        self._active_admin_target: str | None = None
        self._active_process: ManagedMakeProcess | None = None
        self._active_process_started_at: float | None = None
        self._process_interrupt_requested = False
        self._exclusive_reservation: str | None = None
        self._force_termination_available = False
        self._last_snapshot: AdministrationSnapshot | None = None
        self._last_aws_snapshot: AwsAdministrationSnapshot | None = None
        self._last_nas_status: NasCollectorStatus | None = None
        self._last_refresh: datetime | None = None
        self._snapshot_error: str | None = None
        self._row_details: dict[tuple[str, str], str] = {}
        self._workflow_progress: dict[str, AdministrationWorkflowProgress] = {}

    def compose(self) -> ComposeResult:
        yield Static(self._header_text(), id="topbar", markup=False)
        with Container(id="workspace-layout"):
            with Vertical(id="control-pane"):
                yield Select(
                    ((label, page_id) for page_id, label in NAVIGATION),
                    allow_blank=False,
                    value="overview",
                    id="narrow-nav",
                )
                with Horizontal(id="body"):
                    with VerticalScroll(id="sidebar"):
                        for page_id, label in NAVIGATION:
                            yield Button(label, id=f"nav--{page_id}")
                    with ContentSwitcher(
                        initial="overview",
                        id="content-switcher",
                    ):
                        yield self._overview_page()
                        yield CommandGroupPanel(
                            group=CommandGroup.WORKSPACE,
                            title="Workspace setup and tooling",
                        )
                        yield CommandGroupPanel(
                            group=CommandGroup.LOCAL_SERVICES,
                            title="Local services",
                        )
                        yield CommandGroupPanel(
                            group=CommandGroup.AWS,
                            title="AWS",
                        )
                        yield CommandGroupPanel(
                            group=CommandGroup.NAS_COLLECTOR,
                            title="NAS collector",
                        )
                        yield CommandGroupPanel(
                            group=CommandGroup.PRODUCTION,
                            title="Production workflows",
                        )
                        yield CommandGroupPanel(
                            group=CommandGroup.QUALITY,
                            title="Quality and build gates",
                        )
                        yield self._history_page()
                        yield self._help_page()
            with Vertical(id="task-drawer"):
                with Horizontal(id="task-header"):
                    yield Static(
                        "Active task: idle",
                        id="task-status",
                        markup=False,
                    )
                    yield Button("Stop", id="stop-task", variant="warning")
                yield DataTable(id="progress-table", zebra_stripes=True)
                yield RichLog(
                    id="task-log",
                    highlight=False,
                    markup=False,
                    wrap=True,
                )
        yield Static("", id="size-warning", markup=False)
        yield Footer()

    def _overview_page(self) -> VerticalScroll:
        page = VerticalScroll(id="overview", classes="page")
        page.compose_add_child(Label("Production overview", classes="page-title"))
        cards = Container(id="overview-cards")
        cards.compose_add_child(
            Static(
                "Profile\nNot loaded",
                id="profile-card",
                classes="summary-card",
                markup=False,
            ),
        )
        cards.compose_add_child(
            Static(
                "Schema\nNot loaded",
                id="schema-card",
                classes="summary-card",
                markup=False,
            ),
        )
        cards.compose_add_child(
            Static(
                "Maintenance\nNot loaded",
                id="maintenance-card",
                classes="summary-card",
                markup=False,
            ),
        )
        cards.compose_add_child(
            Static(
                "Collector\nNot loaded",
                id="collector-card",
                classes="summary-card",
                markup=False,
            ),
        )
        page.compose_add_child(cards)
        page.compose_add_child(Button("Open full snapshot", id="overview-details"))
        page.compose_add_child(Label("Queues", classes="section-title"))
        page.compose_add_child(
            DataTable(
                id="queue-table",
                zebra_stripes=True,
                cursor_type="row",
            ),
        )
        page.compose_add_child(Label("Schedules", classes="section-title"))
        page.compose_add_child(
            DataTable(
                id="schedule-table",
                zebra_stripes=True,
                cursor_type="row",
            ),
        )
        page.compose_add_child(
            Static(
                "Press r to load production state.",
                id="overview-message",
                classes="muted",
                markup=False,
            ),
        )
        return page

    def _history_page(self) -> VerticalScroll:
        page = VerticalScroll(id="history", classes="page")
        page.compose_add_child(
            Label("Persistent operation history", classes="page-title"),
        )
        page.compose_add_child(
            Input(
                placeholder="Filter target or output",
                id="history-filter",
            ),
        )
        page.compose_add_child(Button("Reload history", id="history-reload"))
        page.compose_add_child(RichLog(id="history-log", markup=False, wrap=True))
        return page

    def _help_page(self) -> VerticalScroll:
        page = VerticalScroll(id="help-page", classes="page")
        page.compose_add_child(
            Label("Help and execution boundaries", classes="page-title"),
        )
        page.compose_add_child(
            Static(
                "Ctrl+P  Search every operational Make target\n"
                "r       Refresh the active remote page\n"
                "q       Quit when no operation is active\n\n"
                "local-* runs local Docker and development services.\n"
                "aws-* controls production AWS from this checkout.\n"
                "nas-* controls the NAS collector over SSH.\n"
                "prod-* coordinates AWS and the NAS.\n\n"
                "Remote state is never polled automatically. Mutations run "
                "immediately after their required inputs are submitted. "
                "Deployment remains "
                "exclusive to the Deploy production GitHub Actions workflow. "
                "Raw history may contain sensitive NAS logs and DLQ bodies.",
            ),
        )
        return page

    async def on_mount(self) -> None:
        queue_table = self.query_one("#queue-table", DataTable)
        queue_table.add_columns("Queue", "Visible", "In flight", "Delayed", "Total")
        schedule_table = self.query_one("#schedule-table", DataTable)
        schedule_table.add_columns("Schedule", "State", "Expression", "Timezone")
        self.query_one("#progress-table", DataTable).add_columns(
            "Step / queue",
            "State",
            "Detail",
        )
        self.query_one("#aws-resource-table", DataTable).add_columns(
            "Resource",
            "Value",
        )
        self.query_one("#aws-queue-table", DataTable).add_columns(
            "Queue",
            "Kind",
            "Visible",
            "In flight",
            "Delayed",
            "Total",
        )
        self.query_one("#aws-schedule-table", DataTable).add_columns(
            "Schedule",
            "State",
            "Expression",
            "Timezone",
        )
        self.query_one("#maintenance-table", DataTable).add_columns(
            "Window",
            "Phase",
            "Started",
            "Activated",
            "Closed",
        )
        self._render_workspace_state()
        self._write_task_output(
            "Console ready. No remote calls have been made; press r to refresh.\n",
        )
        self.set_interval(1.0, self._refresh_process_elapsed)
        self._update_size_warning(width=self.size.width, height=self.size.height)

    def on_unmount(self) -> None:
        """Wait for owned blocking workers before the terminal is restored."""
        if self._blocking_executor_shutdown:
            return
        self._blocking_executor_shutdown = True
        self._blocking_executor.shutdown(wait=True, cancel_futures=False)

    @on(Button.Pressed)
    def handle_button(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id.startswith("nav--"):
            self._switch_page(page_id=button_id.removeprefix("nav--"))
        elif button_id.startswith("command--"):
            self.request_target(button_id.removeprefix("command--"))
        elif button_id == "history-reload":
            self._load_history()
        elif button_id == "overview-details":
            if self._last_snapshot is None:
                self.notify(
                    "Refresh the overview before opening its details.",
                    markup=False,
                )
            else:
                self.run_worker(
                    self.push_screen_wait(
                        InformationScreen(
                            title="Administration snapshot",
                            content=self._last_snapshot.model_dump_json(indent=2),
                        ),
                    ),
                    exit_on_error=False,
                )
        elif button_id == "stop-task":
            self.run_worker(self._stop_active_process(), exit_on_error=False)

    @on(Select.Changed, "#narrow-nav")
    def change_narrow_navigation(self, event: Select.Changed) -> None:
        if isinstance(event.value, str):
            try:
                self._switch_page(page_id=event.value)
            except NoMatches:
                return

    @on(Input.Changed, "#history-filter")
    def filter_history(self, _event: Input.Changed) -> None:
        self._load_history()

    @on(DataTable.RowSelected)
    def show_table_detail(self, event: DataTable.RowSelected) -> None:
        """Open the complete typed row behind an overview table entry."""
        key = str(event.row_key.value)
        table_id = event.data_table.id
        titles = {
            "queue-table": "Queue detail",
            "schedule-table": "Schedule detail",
            "aws-resource-table": "AWS resource detail",
            "aws-queue-table": "Queue / DLQ detail",
            "aws-schedule-table": "Schedule detail",
            "maintenance-table": "Maintenance detail",
        }
        if table_id not in titles:
            return
        content = self._row_details.get((table_id, key))
        if content is not None:
            self.run_worker(
                self.push_screen_wait(
                    InformationScreen(title=titles[table_id], content=content),
                ),
                exit_on_error=False,
            )

    def on_resize(self, event: events.Resize) -> None:
        self._update_size_warning(width=event.size.width, height=event.size.height)

    def request_target(self, target: str) -> None:
        """Schedule one catalogue command from navigation or the palette."""
        self.run_worker(
            self._dispatch_target(target=target),
            name=f"dispatch-{target}",
            exit_on_error=False,
        )

    def action_refresh(self) -> None:
        current = self.query_one("#content-switcher", ContentSwitcher).current
        target = {
            "overview": "prod-status",
            CommandGroup.AWS.value: "aws-status",
            CommandGroup.NAS_COLLECTOR.value: "nas-status",
            CommandGroup.PRODUCTION.value: "prod-status",
        }.get(current)
        if target is None:
            self.notify(
                "The active page has no remote state to refresh.",
                markup=False,
            )
            return
        self.request_target(target)

    def action_show_help(self) -> None:
        self._switch_page(page_id="help-page")

    def action_request_quit(self) -> None:
        if self._exclusive_operation_active():
            self.notify(
                "Finish or stop the active operation before quitting.",
                severity="warning",
                markup=False,
            )
            return
        self.exit()

    async def action_quit(self) -> None:
        """Route Textual's inherited quit action through the operation guard."""
        self.action_request_quit()

    async def _dispatch_target(self, *, target: str) -> None:
        reserved = False
        execution_started = False
        dispatch_task_id = str(uuid4())
        try:
            command = command_for_target(target=target)
            if target == "help":
                self._switch_page(page_id="help-page")
                return
            self._check_prerequisites(command=command)
            is_exclusive = (
                command.execution_kind is ExecutionKind.MAKE_PROCESS
                or command.risk is not CommandRisk.READ_ONLY
            )
            if is_exclusive and self._exclusive_operation_active():
                raise RuntimeError("Another task or mutation is already active.")
            if is_exclusive:
                self._exclusive_reservation = target
                reserved = True
            submission = await self._collect_arguments(command=command)
            if submission is None:
                return
            parameters = self._build_parameters(command=command, submission=submission)
            if command.execution_kind is ExecutionKind.MAKE_PROCESS:
                execution_started = True
                await self._run_make_command(command=command)
            else:
                execution_started = True
                await self._run_admin_command(
                    command=command,
                    parameters=parameters,
                    submission=submission,
                )
        except Exception as error:
            if not execution_started:
                self._log_failure_event(
                    target=target,
                    task_id=dispatch_task_id,
                    error=error,
                )
            self.notify(
                str(error),
                severity="error",
                timeout=8,
                markup=False,
            )
            self._write_task_output(f"ERROR {target}: {error}\n")
        finally:
            if reserved and self._exclusive_reservation == target:
                self._exclusive_reservation = None

    def _check_prerequisites(self, *, command: TuiCommand) -> None:
        paths = {
            CommandPrerequisite.LOCAL_ENV: self._settings.project_root / ".env",
            CommandPrerequisite.CLIENT_ENV: (
                self._settings.project_root / "client" / ".env.local"
            ),
            CommandPrerequisite.ADMIN_CONFIG: self._settings.admin_config,
        }
        for prerequisite, path in paths.items():
            if prerequisite in command.prerequisites and not path.is_file():
                raise RuntimeError(
                    f"{path} is required for {command.target}; create and "
                    "configure it before retrying.",
                )

    async def _collect_arguments(
        self,
        *,
        command: TuiCommand,
    ) -> FormSubmission | None:
        fields = self._fields_for(command=command)
        if not fields:
            return FormSubmission(target=command.target, values=())
        return await self.push_screen_wait(
            ArgumentsScreen(
                request=FormRequest(
                    target=command.target,
                    title=f"Parameters for {command.target}",
                    fields=fields,
                ),
            ),
        )

    @staticmethod
    def _fields_for(*, command: TuiCommand) -> tuple[FormField, ...]:
        kind = command.parameter_kind
        if kind is ParameterKind.NONE:
            return ()
        if kind is ParameterKind.REASON:
            return (
                FormField(
                    name="reason",
                    label="Operational reason",
                    placeholder="Required audit reason",
                    password=False,
                ),
            )
        if kind is ParameterKind.SHA:
            return (
                FormField(
                    name="sha",
                    label="Full Git revision",
                    placeholder="40 lowercase hexadecimal characters",
                    password=False,
                ),
            )
        if kind is ParameterKind.DLQ_SELECTION:
            return (
                FormField(
                    name="queue",
                    label="DLQ",
                    placeholder="fetch, result, schedule, or community",
                    password=False,
                ),
            )
        if kind is ParameterKind.STATE_FILE:
            return (
                FormField(
                    name="state_file",
                    label="Schedule snapshot path",
                    placeholder="Explicit path to the JSON snapshot",
                    password=False,
                ),
            )
        raise AssertionError(f"Unhandled parameter kind: {kind}")

    @staticmethod
    def _build_parameters(
        *,
        command: TuiCommand,
        submission: FormSubmission,
    ) -> CommandParameters:
        kind = command.parameter_kind
        if kind is ParameterKind.NONE:
            return NoParameters()
        if kind is ParameterKind.REASON:
            return ReasonParameters(
                reason=submission.require(name="reason"),
            )
        if kind is ParameterKind.SHA:
            return ShaParameters(
                sha=submission.require(name="sha"),
            )
        if kind is ParameterKind.DLQ_SELECTION:
            return DlqSelectionParameters(
                queue=DeadLetterQueue(submission.require(name="queue")),
            )
        if kind is ParameterKind.STATE_FILE:
            return StateFileParameters(
                state_file=Path(submission.require(name="state_file")),
            )
        raise AssertionError(f"Unhandled parameter kind: {kind}")

    async def _run_make_command(self, *, command: TuiCommand) -> None:
        task_id = str(uuid4())
        self._reset_progress()
        self._set_task_status(text=f"Starting make {command.target}…", stoppable=False)
        try:
            process = await self._run_blocking(
                partial(
                    self._process_runner.start,
                    target=command.target,
                    task_id=task_id,
                    on_output=self._process_output,
                ),
            )
            self._active_process = process
            self._active_process_started_at = time.monotonic()
            self._set_task_status(
                text=f"Running make {command.target}",
                stoppable=True,
            )
            result = await self._run_blocking(
                partial(process.wait, timeout_seconds=None),
            )
            if result.exit_code == 0:
                self.notify(f"{command.target} completed.", markup=False)
            else:
                self.notify(
                    f"{command.target} exited with {result.exit_code}.",
                    severity="error",
                    markup=False,
                )
        finally:
            self._active_process = None
            self._active_process_started_at = None
            self._force_termination_available = False
            self._process_interrupt_requested = False
            self._set_task_status(text="Active task: idle", stoppable=False)

    async def _run_admin_command(
        self,
        *,
        command: TuiCommand,
        parameters: CommandParameters,
        submission: FormSubmission,
    ) -> None:
        task_id = str(uuid4())
        self._reset_progress()
        exclusive = command.risk is not CommandRisk.READ_ONLY
        if exclusive:
            self._active_admin_target = command.target
        self._log_admin_started(
            command=command,
            submission=submission,
            task_id=task_id,
        )
        self._set_task_status(text=f"Running {command.target}…", stoppable=False)
        try:
            facade = self._new_facade()
            progress = partial(
                self._admin_progress,
                task_id=task_id,
                target=command.target,
            )
            result = await self._run_blocking(
                partial(
                    self._invoke_and_resolve_admin,
                    facade,
                    command,
                    parameters,
                    progress,
                ),
            )
            rendered = self._render_result(result=result)
            self._log_admin_success(
                command=command,
                task_id=task_id,
                rendered=rendered,
            )
            self._write_task_output(f"{command.target}\n{rendered}\n")
            await self._present_admin_result(
                command=command,
                result=result,
                rendered=rendered,
            )
            if command.risk is CommandRisk.PRODUCTION_CHANGE:
                await self._refresh_snapshot_after_action(command=command)
        except Exception as error:
            self._log_admin_failure(
                command=command,
                task_id=task_id,
                error=error,
            )
            if command.target in {
                "aws-rebaseline-current",
                "aws-schedules-pause",
                "aws-schedules-restore",
                "prod-maintenance-begin",
                "prod-maintenance-end",
                "prod-rebaseline-current",
            }:
                await self._show_recovery_state(
                    failed_command=command,
                    task_id=task_id,
                )
            if command.target == "prod-status":
                self._mark_snapshot_stale(error=error)
            elif command.target == "aws-status":
                self._mark_remote_stale(
                    widget_id="aws-state",
                    captured_at=(
                        None
                        if self._last_aws_snapshot is None
                        else self._last_aws_snapshot.captured_at
                    ),
                    error=error,
                )
            elif command.target == "nas-status":
                if self._last_nas_status is None:
                    self._mark_remote_stale(
                        widget_id="nas-state",
                        captured_at=None,
                        error=error,
                    )
                else:
                    self._render_nas_status(
                        status=self._last_nas_status,
                        stale_error=error,
                    )
            raise
        finally:
            if exclusive:
                self._active_admin_target = None
            self._set_task_status(text="Active task: idle", stoppable=False)

    def _invoke_admin(
        self,
        *,
        facade: AdministrationFacade,
        command: TuiCommand,
        parameters: CommandParameters,
        progress: Callable[[AdministrationProgress], None],
    ) -> AdminResult | AdminCoroutine:
        """Map every typed administration target to the shared façade."""
        target = command.target
        if target == "aws-doctor":
            return facade.aws_doctor()
        if target == "aws-status":
            return facade.aws_snapshot()
        if target == "prod-status":
            return facade.snapshot()
        if target == "aws-app-revision":
            return facade.deployed_revision()
        if target == "aws-db-status":
            return facade.schema_status()
        if target == "aws-db-migrate":
            return facade.apply_schema()
        if target == "aws-queues-status":
            return facade.queue_depths(include_dead_letters=False)
        if target == "aws-queues-drain":
            return facade.drain_queues(progress=progress)
        if target == "aws-dlqs-status":
            return facade.dead_letter_depths()
        if target == "aws-dlq-peek":
            selected = cast("DlqSelectionParameters", parameters).queue.value
            return facade.peek_dead_letters(
                request=DeadLetterPeekRequest(
                    queue=DeadLetterQueueName(selected),
                    max_messages=10,
                ),
            )
        if target == "aws-send-reference":
            return facade.send_reference()
        if target == "aws-send-live":
            return facade.send_current_live()
        if target == "aws-send-community":
            return facade.send_community()
        if target == "aws-schedules-status":
            return facade.schedule_snapshots()
        if target == "aws-schedules-bootstrap-pause":
            return facade.pause_schedules_to_state_file(
                request=self._state_file_request(parameters=parameters),
            )
        if target == "aws-schedules-bootstrap-restore":
            return facade.restore_schedules_from_state_file(
                request=self._state_file_request(parameters=parameters),
            )
        if target == "aws-maintenance-status":
            return facade.latest_maintenance()
        if target == "aws-schedules-pause":
            reason = self._administration_reason(parameters=parameters)
            return facade.pause_schedules(reason=reason)
        if target == "aws-schedules-restore":
            return facade.restore_schedules()
        if target == "aws-rebaseline-current":
            return facade.rebaseline_current(
                reason=self._administration_reason(parameters=parameters),
                refresh_normalized_data=False,
                progress=progress,
            )
        if target == "nas-doctor":
            return facade.nas_doctor()
        if target == "nas-status":
            return facade.collector_status()
        if target == "nas-start":
            return facade.collector_start()
        if target == "nas-stop":
            return facade.collector_stop()
        if target == "nas-logs":
            tail_lines = load_admin_settings(
                path=self._settings.admin_config,
            ).nas_log_tail_lines
            return facade.nas_logs(
                request=NasLogsRequest(tail_lines=tail_lines),
            )
        if target in {"nas-update", "nas-rollback"}:
            sha = cast("ShaParameters", parameters).sha
            return facade.collector_update(request=GitShaRequest(sha=sha))
        if target == "prod-doctor":
            return facade.production_doctor()
        if target == "prod-maintenance-begin":
            return facade.begin_production_maintenance(
                reason=self._administration_reason(parameters=parameters),
                progress=progress,
            )
        if target == "prod-maintenance-end":
            return facade.end_production_maintenance(progress=progress)
        if target == "prod-rebaseline-current":
            return facade.rebaseline_current(
                reason=self._administration_reason(parameters=parameters),
                refresh_normalized_data=True,
                progress=progress,
            )
        raise AssertionError(f"No administration mapping for {target}.")

    @staticmethod
    def _state_file_request(
        *,
        parameters: CommandParameters,
    ) -> ScheduleStateFileRequest:
        values = cast("StateFileParameters", parameters)
        return ScheduleStateFileRequest(path=values.state_file)

    @staticmethod
    def _administration_reason(
        *,
        parameters: CommandParameters,
    ) -> AdministrationReason:
        return AdministrationReason(reason=cast("ReasonParameters", parameters).reason)

    def _invoke_and_resolve_admin(
        self,
        facade: AdministrationFacade,
        command: TuiCommand,
        parameters: CommandParameters,
        progress: Callable[[AdministrationProgress], None],
    ) -> AdminResult:
        result = self._invoke_admin(
            facade=facade,
            command=command,
            parameters=parameters,
            progress=progress,
        )
        return self._resolve_admin_result(result=result)

    @staticmethod
    def _resolve_admin_result(
        *,
        result: AdminResult | AdminCoroutine,
    ) -> AdminResult:
        if inspect.isawaitable(result):
            return asyncio.run(cast("AdminCoroutine", result))
        return result

    async def _run_blocking[Result](
        self,
        operation: Callable[[], Result],
    ) -> Result:
        """Run one blocking boundary on the app-owned worker pool."""
        if self._blocking_executor_shutdown:
            raise RuntimeError("The TUI blocking worker pool is shut down.")
        return await asyncio.get_running_loop().run_in_executor(
            self._blocking_executor,
            operation,
        )

    def _new_facade(self) -> AdministrationFacade:
        """Create one operation-scoped administration runtime."""
        return self._facade_factory(self._settings.admin_config)

    def _admin_progress(
        self,
        progress: AdministrationProgress,
        *,
        task_id: str,
        target: str,
    ) -> None:
        rendered = self._render_result(result=progress)
        self.call_from_thread(self._write_task_output, rendered + "\n")
        self.call_from_thread(self._render_progress, progress)
        self._event_logger.write(
            event=OperationProgressEvent(
                event="operation_progress",
                timestamp=datetime.now(tz=UTC),
                session_id=self._session_id,
                task_id=task_id,
                target=target,
                step=type(progress).__name__,
                progress=rendered,
            ),
        )

    def _reset_progress(self) -> None:
        table = self.query_one("#progress-table", DataTable)
        table.clear()
        table.display = False
        self._workflow_progress.clear()

    def _render_progress(self, progress: AdministrationProgress) -> None:
        """Render workflow steps or the latest queue-drain sample as text rows."""
        table = self.query_one("#progress-table", DataTable)
        table.display = True
        if isinstance(progress, QueueDrainProgress):
            table.clear()
            for queue in progress.queues:
                table.add_row(
                    queue.name,
                    f"total={queue.total}",
                    (
                        f"visible={queue.visible} in-flight={queue.in_flight} "
                        f"delayed={queue.delayed}; "
                        f"stable={progress.stable_for_seconds:.1f}s"
                    ),
                )
            return
        if isinstance(progress, AdministrationWorkflowProgress):
            row_key = progress.step.value
            self._workflow_progress[row_key] = progress
            table.clear()
            for step in self._workflow_progress.values():
                table.add_row(
                    step.step.value,
                    step.state.value,
                    step.detail or "",
                    key=step.step.value,
                )

    async def _present_admin_result(
        self,
        *,
        command: TuiCommand,
        result: object,
        rendered: str,
    ) -> None:
        if command.target == "prod-status":
            if not isinstance(result, AdministrationSnapshot):
                raise TypeError(
                    f"{command.target} returned {type(result).__name__}, "
                    "not AdministrationSnapshot.",
                )
            self._last_snapshot = result
            self._last_refresh = datetime.now(tz=UTC)
            self._snapshot_error = None
            self._render_snapshot(snapshot=result)
        elif command.target == "aws-status":
            if not isinstance(result, AwsAdministrationSnapshot):
                raise TypeError(
                    f"{command.target} returned {type(result).__name__}, "
                    "not AwsAdministrationSnapshot.",
                )
            self._last_aws_snapshot = result
            self._last_refresh = datetime.now(tz=UTC)
            self._render_aws_snapshot(snapshot=result)
        elif command.target == "nas-status":
            if not isinstance(result, NasCollectorStatus):
                raise TypeError(
                    f"{command.target} returned {type(result).__name__}, "
                    "not NasCollectorStatus.",
                )
            self._last_nas_status = result
            self._last_refresh = datetime.now(tz=UTC)
            self._render_nas_status(status=result, stale_error=None)
        if command.target in {"aws-dlq-peek", "nas-logs"}:
            await self.push_screen_wait(
                InformationScreen(title=command.target, content=rendered),
            )
        self.notify(f"{command.target} completed.", markup=False)

    async def _refresh_snapshot_after_action(self, *, command: TuiCommand) -> None:
        """Refresh production state once after a successful direct mutation."""
        refresh_task_id = str(uuid4())
        facade = self._new_facade()
        try:
            snapshot = await self._run_blocking(
                partial(
                    self._resolve_admin_result,
                    result=facade.snapshot(),
                ),
            )
            if not isinstance(snapshot, AdministrationSnapshot):
                raise TypeError(
                    "Administration snapshot refresh returned an invalid result.",
                )
        except Exception as error:
            self._log_failure_event(
                target=f"{command.target}-refresh",
                task_id=refresh_task_id,
                error=error,
            )
            self._mark_snapshot_stale(error=error)
            if command.group is CommandGroup.AWS:
                self._mark_remote_stale(
                    widget_id="aws-state",
                    captured_at=(
                        None
                        if self._last_aws_snapshot is None
                        else self._last_aws_snapshot.captured_at
                    ),
                    error=error,
                )
            elif command.group is CommandGroup.NAS_COLLECTOR:
                if self._last_nas_status is None:
                    self._mark_remote_stale(
                        widget_id="nas-state",
                        captured_at=None,
                        error=error,
                    )
                else:
                    self._render_nas_status(
                        status=self._last_nas_status,
                        stale_error=error,
                    )
            self.notify(
                f"{command.target} succeeded, but refresh failed: {error}",
                severity="error",
                timeout=10,
                markup=False,
            )
            return
        self._last_snapshot = snapshot
        self._last_refresh = datetime.now(tz=UTC)
        self._snapshot_error = None
        self._render_snapshot(snapshot=snapshot)

    async def _show_recovery_state(
        self,
        *,
        failed_command: TuiCommand,
        task_id: str,
    ) -> None:
        """Expose the durable maintenance phase after a workflow failure."""
        facade = self._new_facade()
        try:
            maintenance = await self._run_blocking(
                partial(
                    self._resolve_admin_result,
                    result=facade.latest_maintenance(),
                ),
            )
        except Exception as recovery_error:
            self._log_failure_event(
                target=f"{failed_command.target}-recovery",
                task_id=task_id,
                error=recovery_error,
            )
            self._write_task_output(
                "Recovery-state read failed: " f"{recovery_error}\n",
            )
            return
        rendered = self._render_result(result=maintenance)
        recovery_output = (
            "Recoverable maintenance state after failure:\n" f"{rendered}\n"
        )
        self._write_task_output(recovery_output)
        self._log_output_event(
            target=f"{failed_command.target}-recovery",
            task_id=task_id,
            output=recovery_output,
        )

    def _mark_snapshot_stale(self, *, error: Exception) -> None:
        """Preserve the last successful snapshot while exposing refresh failure."""
        self._snapshot_error = str(error)
        message = self.query_one("#overview-message", Static)
        if self._last_snapshot is None:
            message.update(f"Refresh failed: {error}")
        else:
            message.update(
                "STALE - last successful refresh "
                f"{self._last_snapshot.captured_at.isoformat()}\n"
                f"Refresh failed: {error}",
            )
        message.add_class("error")
        self._mark_remote_stale(
            widget_id="production-state",
            captured_at=(
                None if self._last_snapshot is None else self._last_snapshot.captured_at
            ),
            error=error,
        )

    def _render_snapshot(self, *, snapshot: AdministrationSnapshot) -> None:
        self._last_aws_snapshot = snapshot
        self._last_nas_status = snapshot.collector
        connection = snapshot.connection
        schema = snapshot.schema_status
        maintenance = snapshot.maintenance
        collector = snapshot.collector
        revision = snapshot.deployed_revision
        self.query_one("#profile-card", Static).update(
            "Profile\n"
            f"{connection.profile_name}\n{connection.account_id}\n{revision[:12]}",
        )
        self.query_one("#schema-card", Static).update(
            "Schema\nApplied: "
            + ", ".join(str(value) for value in schema.applied_versions)
            + "\nPending: "
            + (", ".join(str(value) for value in schema.pending_versions) or "none"),
        )
        self.query_one("#maintenance-card", Static).update(
            "Maintenance\n"
            + (
                "none"
                if maintenance is None
                else f"{maintenance.phase.value}\n{maintenance.reason}"
            ),
        )
        self.query_one("#collector-card", Static).update(
            "Collector\n"
            f"{'running' if collector.running else 'stopped'}\n"
            f"{collector.health}\n{collector.image}",
        )
        queue_table = self.query_one("#queue-table", DataTable)
        queue_table.clear()
        self._clear_row_details(table_id="queue-table")
        for depth in snapshot.queues:
            key = f"queue-{depth.name}"
            self._row_details[("queue-table", key)] = depth.model_dump_json(indent=2)
            queue_table.add_row(
                depth.name,
                str(depth.visible),
                str(depth.in_flight),
                str(depth.delayed),
                str(depth.total),
                key=key,
            )
        schedule_table = self.query_one("#schedule-table", DataTable)
        schedule_table.clear()
        self._clear_row_details(table_id="schedule-table")
        for schedule in snapshot.schedules:
            key = f"schedule-{schedule.group_name}-{schedule.name}"
            self._row_details[("schedule-table", key)] = schedule.model_dump_json(
                indent=2,
            )
            schedule_table.add_row(
                f"{schedule.group_name}/{schedule.name}",
                self._state_label(value=schedule.state.value),
                schedule.schedule_expression,
                schedule.schedule_expression_timezone,
                key=key,
            )
        message = self.query_one("#overview-message", Static)
        message.remove_class("error")
        message.update(
            f"Last successful refresh: {snapshot.captured_at.isoformat()}",
        )
        self._render_aws_snapshot(snapshot=snapshot)
        self._render_nas_status(status=snapshot.collector, stale_error=None)
        self._render_production_snapshot(snapshot=snapshot)
        self._refresh_header()

    def _render_workspace_state(self) -> None:
        """Show local prerequisites without invoking any external dependency."""
        prerequisites = (
            ("Local environment", self._settings.project_root / ".env"),
            (
                "Client environment",
                self._settings.project_root / "client" / ".env.local",
            ),
            ("Administration config", self._settings.admin_config),
        )
        lines = [
            f"{'READY' if path.is_file() else 'MISSING'}  {label}: {path}"
            for label, path in prerequisites
        ]
        self.query_one("#workspace-state", Static).update("\n".join(lines))

    def _render_aws_snapshot(
        self,
        *,
        snapshot: AwsAdministrationSnapshot,
    ) -> None:
        """Render a manually captured AWS snapshot into structured tables."""
        state = self.query_one("#aws-state", Static)
        state.remove_class("error")
        maintenance = (
            "none"
            if snapshot.maintenance is None
            else f"{snapshot.maintenance.phase.value}: {snapshot.maintenance.reason}"
        )
        pending = ", ".join(
            str(version) for version in snapshot.schema_status.pending_versions
        ) or "none"
        state.update(
            f"VERIFIED  {snapshot.connection.profile_name} / "
            f"{snapshot.connection.account_id} / {snapshot.connection.region}\n"
            f"Revision {snapshot.deployed_revision[:12]}  |  "
            f"Schema pending: {pending}  |  Maintenance: {maintenance}\n"
            f"Captured {snapshot.captured_at.isoformat()}",
        )

        resource_table = self.query_one("#aws-resource-table", DataTable)
        resource_table.clear()
        self._clear_row_details(table_id="aws-resource-table")
        for resource_name in type(snapshot.resources).model_fields:
            resource_value = str(getattr(snapshot.resources, resource_name))
            key = f"resource-{resource_name}"
            self._row_details[("aws-resource-table", key)] = json.dumps(
                {"name": resource_name, "value": resource_value},
                indent=2,
            )
            resource_table.add_row(
                resource_name.replace("_", " "),
                resource_value,
                key=key,
            )

        queue_table = self.query_one("#aws-queue-table", DataTable)
        queue_table.clear()
        self._clear_row_details(table_id="aws-queue-table")
        for depth in snapshot.queues:
            key = f"aws-queue-{depth.name}"
            is_dead_letter = "dead-letter" in depth.name
            kind = "DLQ" if is_dead_letter else "WORKING"
            kind_style = "bold red" if is_dead_letter and depth.total else "green"
            self._row_details[("aws-queue-table", key)] = depth.model_dump_json(
                indent=2,
            )
            queue_table.add_row(
                depth.name,
                Text(kind, style=kind_style),
                str(depth.visible),
                str(depth.in_flight),
                str(depth.delayed),
                str(depth.total),
                key=key,
            )

        schedule_table = self.query_one("#aws-schedule-table", DataTable)
        schedule_table.clear()
        self._clear_row_details(table_id="aws-schedule-table")
        for schedule in snapshot.schedules:
            key = f"aws-schedule-{schedule.group_name}-{schedule.name}"
            self._row_details[("aws-schedule-table", key)] = (
                schedule.model_dump_json(indent=2)
            )
            schedule_table.add_row(
                f"{schedule.group_name}/{schedule.name}",
                self._state_label(value=schedule.state.value),
                schedule.schedule_expression,
                schedule.schedule_expression_timezone,
                key=key,
            )
        self._refresh_header()

    def _render_nas_status(
        self,
        *,
        status: NasCollectorStatus,
        stale_error: Exception | None,
    ) -> None:
        """Render the collector lifecycle state with text and colour labels."""
        state = self.query_one("#nas-state", Static)
        state.set_class(stale_error is not None, "error")
        label = "RUNNING" if status.running else "STOPPED"
        prefix = "" if stale_error is None else f"STALE - {stale_error}\n"
        content = Text(prefix)
        content.append(label, style="bold green" if status.running else "bold yellow")
        content.append(f"\nHealth: {status.health}\nImage: {status.image}")
        state.update(content)
        self._refresh_header()

    def _render_production_snapshot(self, *, snapshot: AdministrationSnapshot) -> None:
        """Render combined state and the durable maintenance timeline."""
        state = self.query_one("#production-state", Static)
        state.remove_class("error")
        maintenance = snapshot.maintenance
        phase = "NONE" if maintenance is None else maintenance.phase.value.upper()
        state.update(
            f"{phase}  |  collector "
            f"{'RUNNING' if snapshot.collector.running else 'STOPPED'}  |  "
            f"schema {snapshot.schema_status.applied_versions}\n"
            f"Revision {snapshot.deployed_revision[:12]}  |  "
            f"captured {snapshot.captured_at.isoformat()}",
        )
        table = self.query_one("#maintenance-table", DataTable)
        table.clear()
        self._clear_row_details(table_id="maintenance-table")
        if maintenance is None:
            return
        key = f"maintenance-{maintenance.id}"
        self._row_details[("maintenance-table", key)] = maintenance.model_dump_json(
            indent=2,
        )
        table.add_row(
            str(maintenance.id),
            self._state_label(value=maintenance.phase.value),
            maintenance.started_at.isoformat(),
            (
                "—"
                if maintenance.activated_at is None
                else maintenance.activated_at.isoformat()
            ),
            "—" if maintenance.closed_at is None else maintenance.closed_at.isoformat(),
            key=key,
        )

    def _mark_remote_stale(
        self,
        *,
        widget_id: str,
        captured_at: datetime | None,
        error: Exception,
    ) -> None:
        """Preserve structured rows while making a failed refresh explicit."""
        captured = (
            "no successful refresh"
            if captured_at is None
            else captured_at.isoformat()
        )
        state = self.query_one(f"#{widget_id}", Static)
        state.add_class("error")
        state.update(f"STALE - {captured}\nRefresh failed: {error}")

    def _clear_row_details(self, *, table_id: str) -> None:
        self._row_details = {
            key: value
            for key, value in self._row_details.items()
            if key[0] != table_id
        }

    @staticmethod
    def _state_label(*, value: str) -> Text:
        normalized = value.casefold()
        style = (
            "bold green"
            if normalized in {"enabled", "active", "closed", "healthy"}
            else "bold yellow"
        )
        return Text(value.upper(), style=style)

    async def _stop_active_process(self) -> None:
        process = self._active_process
        if process is None or not process.is_running:
            self.notify("No Make process is running.", markup=False)
            return
        if not self._force_termination_available:
            await self._run_blocking(process.request_interrupt)
            self._process_interrupt_requested = True
            self._set_task_status(
                text=f"Stopping {process.target} with SIGINT…",
                stoppable=True,
            )
            self.set_timer(5.0, self._offer_force_termination)
            return
        confirmed = await self.push_screen_wait(
            ConfirmationScreen(
                title=f"Terminate {process.target}?",
                impact=(
                    "SIGINT did not stop the process within five seconds. Send "
                    "SIGTERM to the complete managed process group? SIGKILL is "
                    "never sent."
                ),
            ),
        )
        if confirmed:
            await self._run_blocking(process.request_termination)

    def _offer_force_termination(self) -> None:
        process = self._active_process
        if process is None or not process.is_running:
            return
        self._force_termination_available = True
        stop = self.query_one("#stop-task", Button)
        stop.label = "Force terminate"
        stop.variant = "error"

    def _refresh_process_elapsed(self) -> None:
        process = self._active_process
        started_at = self._active_process_started_at
        if process is None or started_at is None or not process.is_running:
            return
        if self._process_interrupt_requested:
            phase = "Waiting after SIGINT"
        else:
            phase = "Running make"
        elapsed = int(time.monotonic() - started_at)
        self.query_one("#task-status", Static).update(
            f"{phase} {process.target} ({elapsed}s)",
        )

    def _load_history(self) -> None:
        self.run_worker(self._render_history(), name="history", exit_on_error=False)

    async def _render_history(self) -> None:
        events_found = await self._run_blocking(self._event_logger.read_events)
        query = self.query_one("#history-filter", Input).value.casefold().strip()
        history = self.query_one("#history-log", RichLog)
        history.clear()
        for event in events_found:
            line = event.model_dump_json()
            if query and query not in line.casefold():
                continue
            history.write(line)

    def _switch_page(self, *, page_id: str) -> None:
        valid = {value for value, _ in NAVIGATION}
        if page_id not in valid:
            raise ValueError(f"Unknown TUI page: {page_id}")
        self.query_one("#content-switcher", ContentSwitcher).current = page_id
        narrow = self.query_one("#narrow-nav", Select)
        narrow.value = page_id
        if page_id == "history":
            self._load_history()

    def _process_output(self, output: str) -> None:
        try:
            self.call_from_thread(self._write_task_output, output)
        except RuntimeError:
            return

    def _write_task_output(self, output: str) -> None:
        self.query_one("#task-log", RichLog).write(Text.from_ansi(output))

    def _exclusive_operation_active(self) -> bool:
        return (
            self._exclusive_reservation is not None
            or self._active_admin_target is not None
            or self._active_process is not None
        )

    def _set_task_status(self, *, text: str, stoppable: bool) -> None:
        self.query_one("#task-status", Static).update(text)
        stop = self.query_one("#stop-task", Button)
        stop.display = stoppable
        if not self._force_termination_available:
            stop.label = "Stop"
            stop.variant = "warning"

    def _refresh_header(self) -> None:
        self.query_one("#topbar", Static).update(self._header_text())

    def _header_text(self) -> str:
        target = self._configured_target()
        refresh = (
            "never"
            if self._last_refresh is None
            else self._last_refresh.astimezone(UTC).strftime("%H:%M:%S UTC")
        )
        return f"FPL DATA RELAY  |  {target}  |  refreshed {refresh}"

    def _configured_target(self) -> str:
        if not self._settings.admin_config.is_file():
            return "admin config not created"
        try:
            settings = load_admin_settings(path=self._settings.admin_config)
        except Exception as error:
            return f"admin config invalid: {error}"
        return f"{settings.aws_profile} / {settings.aws_region}"

    def _log_admin_started(
        self,
        *,
        command: TuiCommand,
        submission: FormSubmission,
        task_id: str,
    ) -> None:
        self._event_logger.write(
            event=OperationStartedEvent(
                event="operation_started",
                timestamp=datetime.now(tz=UTC),
                session_id=self._session_id,
                task_id=task_id,
                target=command.target,
                parameters=tuple(
                    LogParameter(name=value.name, value=value.value)
                    for value in submission.values
                ),
            ),
        )

    def _log_admin_success(
        self,
        *,
        command: TuiCommand,
        task_id: str,
        rendered: str,
    ) -> None:
        now = datetime.now(tz=UTC)
        self._event_logger.write(
            event=OperationOutputEvent(
                event="operation_output",
                timestamp=now,
                session_id=self._session_id,
                task_id=task_id,
                target=command.target,
                stream=LogStream.STDOUT,
                raw_output=rendered,
            ),
        )
        self._event_logger.write(
            event=OperationCompletedEvent(
                event="operation_completed",
                timestamp=now,
                session_id=self._session_id,
                task_id=task_id,
                target=command.target,
                result=rendered,
                exit_code=None,
            ),
        )

    def _log_admin_failure(
        self,
        *,
        command: TuiCommand,
        task_id: str,
        error: Exception,
    ) -> None:
        self._log_failure_event(
            target=command.target,
            task_id=task_id,
            error=error,
        )

    def _log_failure_event(
        self,
        *,
        target: str,
        task_id: str,
        error: Exception,
    ) -> None:
        self._event_logger.write(
            event=OperationFailedEvent(
                event="operation_failed",
                timestamp=datetime.now(tz=UTC),
                session_id=self._session_id,
                task_id=task_id,
                target=target,
                error=str(error),
                traceback="".join(
                    traceback_module.format_exception(
                        type(error),
                        error,
                        error.__traceback__,
                    ),
                ),
                exit_code=None,
            ),
        )

    def _log_output_event(self, *, target: str, task_id: str, output: str) -> None:
        self._event_logger.write(
            event=OperationOutputEvent(
                event="operation_output",
                timestamp=datetime.now(tz=UTC),
                session_id=self._session_id,
                task_id=task_id,
                target=target,
                stream=LogStream.STDOUT,
                raw_output=output,
            ),
        )

    @staticmethod
    def _render_result(*, result: object) -> str:
        if result is None:
            return "completed"
        if isinstance(result, BaseModel):
            return result.model_dump_json(indent=2)
        if isinstance(result, str):
            return result
        if isinstance(result, list | tuple):
            values = [
                value.model_dump(mode="json")
                if isinstance(value, BaseModel)
                else value
                for value in result
            ]
            return json.dumps(values, indent=2, ensure_ascii=False)
        return str(result)

    def _update_size_warning(self, *, width: int, height: int) -> None:
        warning = self.query_one("#size-warning", Static)
        self.screen.set_class(width < 110, "stacked")
        self.screen.set_class(width < 140, "narrow")
        too_small = width < 80 or height < 24
        warning.display = too_small
        if too_small:
            warning.update(
                f"Terminal is {width}x{height}. "
                "FPL Data Relay requires at least 80x24.",
            )


def build_tui(
    *,
    settings: TuiSettings,
    facade_factory: AdministrationFacadeFactory,
) -> FplDataRelayTui:
    """Build production TUI dependencies from explicit launch settings."""
    session_id = str(uuid4())
    logger = SecureJsonlLogger(
        path=settings.log_path,
        max_bytes=settings.log_max_bytes,
        file_count=settings.log_file_count,
    )
    runner = MakeProcessRunner(
        project_root=settings.project_root,
        allowed_targets=frozenset(
            command.target
            for command in COMMAND_CATALOGUE
            if command.execution_kind is ExecutionKind.MAKE_PROCESS
        ),
        event_sink=logger,
        session_id=session_id,
    )
    return FplDataRelayTui(
        settings=settings,
        facade_factory=facade_factory,
        event_logger=logger,
        process_runner=runner,
        session_id=session_id,
    )
