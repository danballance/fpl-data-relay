"""Administration models and ports."""

from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Protocol

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from fpl_data_relay.domain.fixtures import Fixture
from fpl_data_relay.domain.reference import Event, Season


def require_aware_datetime(value: datetime) -> datetime:
    """Require an aware timestamp on administration events and results."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Administration timestamp must be timezone-aware.")
    return value


type AwareAdministrationDatetime = Annotated[
    datetime,
    AfterValidator(require_aware_datetime),
]


class SchemaStatus(BaseModel):
    """Validated migration state exposed to administration clients."""

    model_config = ConfigDict(frozen=True)

    applied_versions: list[int]
    pending_versions: list[int]


class ChangeFeedRebaselineResult(BaseModel):
    """Audited result of replacing one season's change-feed baseline."""

    model_config = ConfigDict(frozen=True)

    id: int
    season_id: str
    reason: str
    change_events_deleted: int
    entity_changes_deleted: int
    snapshots_rebuilt: int
    created_at: datetime


class ScheduleState(StrEnum):
    """State exposed by Amazon EventBridge Scheduler."""

    ENABLED = "ENABLED"
    DISABLED = "DISABLED"


class ScheduleTargetSnapshot(BaseModel):
    """Restorable subset of an EventBridge Scheduler target."""

    model_config = ConfigDict(frozen=True)

    arn: str = Field(min_length=1)
    role_arn: str = Field(min_length=1)
    input: str = Field(min_length=1)
    dead_letter_arn: str
    maximum_event_age_seconds: int = Field(ge=60, le=86_400)
    maximum_retry_attempts: int = Field(ge=0, le=185)


class ScheduleSnapshot(BaseModel):
    """Complete schedule definition needed for a state-only update."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    group_name: str = Field(min_length=1)
    state: ScheduleState
    schedule_expression: str = Field(min_length=1)
    schedule_expression_timezone: str = Field(min_length=1)
    flexible_window_mode: str = Field(pattern="^OFF$")
    action_after_completion: str | None
    description: str | None
    target: ScheduleTargetSnapshot


class ScheduleBootstrapSnapshot(BaseModel):
    """Immutable pre-migration schedule state stored outside the database."""

    model_config = ConfigDict(frozen=True)

    version: Literal[1]
    account_id: str = Field(pattern=r"^\d{12}$")
    aws_region: str = Field(min_length=1)
    app_stack_name: str = Field(min_length=1)
    captured_at: datetime
    schedules: list[ScheduleSnapshot] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_snapshot(self) -> ScheduleBootstrapSnapshot:
        """Require an aware timestamp and unique schedule identities."""
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise ValueError("Schedule snapshot timestamp must be timezone-aware.")
        identities = [
            (schedule.group_name, schedule.name) for schedule in self.schedules
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("Schedule snapshot contains duplicate identities.")
        return self


class QueueDepth(BaseModel):
    """One complete SQS queue depth sample."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    url: str = Field(min_length=1)
    visible: int = Field(ge=0)
    in_flight: int = Field(ge=0)
    delayed: int = Field(ge=0)

    @property
    def total(self) -> int:
        """Return all messages represented by the sample."""
        return self.visible + self.in_flight + self.delayed


class MaintenancePhase(StrEnum):
    """Recoverable cross-system maintenance workflow phase."""

    ENTERING = "entering"
    ACTIVE = "active"
    EXITING = "exiting"
    CLOSED = "closed"


class MaintenanceWindow(BaseModel):
    """Durable state for one production maintenance workflow."""

    model_config = ConfigDict(frozen=True)

    id: int = Field(ge=1)
    reason: str = Field(min_length=1)
    operator_arn: str = Field(min_length=1)
    phase: MaintenancePhase
    schedules: list[ScheduleSnapshot]
    collector_was_running: bool | None
    queues_before: list[QueueDepth]
    queues_after: list[QueueDepth]
    started_at: datetime
    activated_at: datetime | None
    closed_at: datetime | None
    closed_by: str | None


class AwsIdentity(BaseModel):
    """Validated local administrator AWS identity."""

    model_config = ConfigDict(frozen=True)

    account_id: str = Field(pattern=r"^\d{12}$")
    arn: str = Field(min_length=1)


class AwsConnectionStatus(BaseModel):
    """Verified identity reached through the configured AWS profile and region."""

    model_config = ConfigDict(frozen=True)

    profile_name: str = Field(min_length=1)
    region: str = Field(min_length=1)
    account_id: str = Field(pattern=r"^\d{12}$")
    arn: str = Field(min_length=1)


class AdministrationReason(BaseModel):
    """One normalized nonblank operational reason."""

    model_config = ConfigDict(frozen=True)

    reason: str = Field(min_length=1)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        """Strip surrounding whitespace and reject a blank reason."""
        normalized = value.strip()
        if normalized == "":
            raise ValueError("Administration reason must not be blank.")
        return normalized


class GitShaRequest(BaseModel):
    """One full lowercase Git revision selected for a NAS image action."""

    model_config = ConfigDict(frozen=True)

    sha: str = Field(pattern=r"^[0-9a-f]{40}$")

    @property
    def image_tag(self) -> str:
        """Return the immutable collector tag derived from this revision."""
        return f"sha-{self.sha}"


class DeadLetterQueueName(StrEnum):
    """Relay dead-letter queues available for a non-destructive peek."""

    FETCH = "fetch"
    RESULT = "result"
    SCHEDULE = "schedule"
    COMMUNITY = "community"


class DeadLetterPeekRequest(BaseModel):
    """Validated selection for one bounded dead-letter queue peek."""

    model_config = ConfigDict(frozen=True)

    queue: DeadLetterQueueName
    max_messages: int = Field(ge=1, le=10)


class ScheduleStateFileRequest(BaseModel):
    """Validated filesystem location for a schedule bootstrap snapshot."""

    model_config = ConfigDict(frozen=True)

    path: Path

    @field_validator("path", mode="before")
    @classmethod
    def validate_path_text(cls, value: object) -> object:
        """Reject an empty path before Pydantic converts it to ``Path('.')``."""
        if isinstance(value, str) and value.strip() == "":
            raise ValueError("Schedule state-file path must not be blank.")
        return value

    @field_validator("path")
    @classmethod
    def validate_file_path(cls, value: Path) -> Path:
        """Require a path which names a file rather than a directory marker."""
        if value.name in {"", ".", ".."}:
            raise ValueError("Schedule state-file path must identify a file.")
        if value.is_dir():
            raise ValueError("Schedule state-file path must not be a directory.")
        return value


class NasLogsRequest(BaseModel):
    """Validated bound for a NAS collector log tail."""

    model_config = ConfigDict(frozen=True)

    tail_lines: int = Field(ge=1)


class AwsResources(BaseModel):
    """Resolved AWS resources required by relay administration."""

    model_config = ConfigDict(frozen=True)

    database_resource_arn: str = Field(min_length=1)
    database_secret_arn: str = Field(min_length=1)
    database_name: str = Field(min_length=1)
    fetch_queue_url: str = Field(min_length=1)
    fetch_dead_letter_queue_url: str = Field(min_length=1)
    result_queue_url: str = Field(min_length=1)
    result_dead_letter_queue_url: str = Field(min_length=1)
    schedule_dead_letter_queue_url: str = Field(min_length=1)
    community_queue_url: str = Field(min_length=1)
    community_dead_letter_queue_url: str = Field(min_length=1)
    reference_schedule_group_name: str = Field(min_length=1)
    reference_schedule_name: str = Field(min_length=1)
    live_schedule_group_name: str = Field(min_length=1)
    community_schedule_group_name: str = Field(min_length=1)
    community_schedule_name: str = Field(min_length=1)


class NasCollectorStatus(BaseModel):
    """Observable state of the NAS collector container."""

    model_config = ConfigDict(frozen=True)

    running: bool
    health: str = Field(min_length=1)
    image: str = Field(min_length=1)


class AdministrationDoctorScope(StrEnum):
    """Control-plane boundary covered by one doctor result."""

    AWS = "aws"
    NAS = "nas"
    PRODUCTION = "production"


class AdministrationDoctorCheck(StrEnum):
    """Typed checks available through the administration facade."""

    AWS_IDENTITY = "aws_identity"
    AWS_RESOURCES = "aws_resources"
    AWS_QUEUES = "aws_queues"
    AWS_SCHEDULES = "aws_schedules"
    NAS_CONTROL_PLANE = "nas_control_plane"
    DATABASE_SCHEMA = "database_schema"


class AwsDoctorResult(BaseModel):
    """Successful verification of the configured AWS connection and resources."""

    model_config = ConfigDict(frozen=True)

    connection: AwsConnectionStatus
    checks: list[AdministrationDoctorCheck] = Field(min_length=1)
    checked_at: AwareAdministrationDatetime


class AdministrationDoctorResult(BaseModel):
    """Successful completion of a typed control-plane health check."""

    model_config = ConfigDict(frozen=True)

    scope: AdministrationDoctorScope
    checks: list[AdministrationDoctorCheck] = Field(min_length=1)
    checked_at: AwareAdministrationDatetime


class AdministrationJobKind(StrEnum):
    """Jobs which an administrator may dispatch explicitly."""

    REFERENCE = "reference"
    LIVE = "live"
    COMMUNITY = "community"


class AdministrationJobDispatchResult(BaseModel):
    """Auditable result of dispatching one administration job."""

    model_config = ConfigDict(frozen=True)

    kind: AdministrationJobKind
    message_id: str = Field(min_length=1)
    dispatched_at: AwareAdministrationDatetime


class DeployedRevisionResult(BaseModel):
    """Immutable deployed application revision observed in AWS."""

    model_config = ConfigDict(frozen=True)

    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    captured_at: AwareAdministrationDatetime


class NasLogsResult(BaseModel):
    """Bounded raw NAS collector log output."""

    model_config = ConfigDict(frozen=True)

    tail_lines: int = Field(ge=1)
    output: str
    captured_at: AwareAdministrationDatetime


class DeadLetterMessage(BaseModel):
    """One ordered raw message body returned by a DLQ peek."""

    model_config = ConfigDict(frozen=True)

    position: int = Field(ge=1)
    body: str


class DeadLetterPeekResult(BaseModel):
    """Bounded non-destructive view of one relay dead-letter queue."""

    model_config = ConfigDict(frozen=True)

    queue: DeadLetterQueueName
    requested_messages: int = Field(ge=1, le=10)
    messages: list[DeadLetterMessage]
    captured_at: AwareAdministrationDatetime


class AwsAdministrationSnapshot(BaseModel):
    """Coherent manually captured AWS administration overview."""

    model_config = ConfigDict(frozen=True)

    connection: AwsConnectionStatus
    resources: AwsResources
    deployed_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    schema_status: SchemaStatus
    maintenance: MaintenanceWindow | None
    queues: list[QueueDepth]
    schedules: list[ScheduleSnapshot]
    captured_at: AwareAdministrationDatetime


class AdministrationSnapshot(AwsAdministrationSnapshot):
    """Coherent manually captured cross-system administration overview."""

    collector: NasCollectorStatus


class AdministrationWorkflow(StrEnum):
    """Long-running recoverable administration workflows."""

    BEGIN_MAINTENANCE = "begin_maintenance"
    END_MAINTENANCE = "end_maintenance"
    REBASELINE = "rebaseline"


class AdministrationWorkflowStep(StrEnum):
    """Typed steps rendered by CLI and TUI workflow presenters."""

    CHECK_MAINTENANCE = "check_maintenance"
    CHECK_DEAD_LETTERS = "check_dead_letters"
    CHECK_QUEUES = "check_queues"
    READ_COLLECTOR = "read_collector"
    PAUSE_SCHEDULES = "pause_schedules"
    DRAIN_BEFORE_COLLECTOR_STOP = "drain_before_collector_stop"
    STOP_COLLECTOR = "stop_collector"
    DRAIN_AFTER_COLLECTOR_STOP = "drain_after_collector_stop"
    ACTIVATE_MAINTENANCE = "activate_maintenance"
    BEGIN_EXIT = "begin_exit"
    START_COLLECTOR = "start_collector"
    RESTORE_SCHEDULES = "restore_schedules"
    SEND_REFERENCE = "send_reference"
    SEND_LIVE = "send_live"
    DRAIN_AFTER_REFERENCE = "drain_after_reference"
    DRAIN_AFTER_LIVE = "drain_after_live"
    DRAIN_BEFORE_REBASELINE = "drain_before_rebaseline"
    REBUILD_BASELINE = "rebuild_baseline"


class AdministrationWorkflowStepState(StrEnum):
    """Lifecycle state for a workflow step progress event."""

    STARTED = "started"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


class AdministrationWorkflowProgress(BaseModel):
    """One state transition in a long administration workflow."""

    model_config = ConfigDict(frozen=True)

    workflow: AdministrationWorkflow
    step: AdministrationWorkflowStep
    state: AdministrationWorkflowStepState
    occurred_at: AwareAdministrationDatetime
    detail: str | None


class QueueDrainStage(StrEnum):
    """Context identifying one queue drain within a workflow."""

    STANDALONE = "standalone"
    BEFORE_COLLECTOR_STOP = "before_collector_stop"
    AFTER_COLLECTOR_STOP = "after_collector_stop"
    AFTER_REFERENCE = "after_reference"
    AFTER_LIVE = "after_live"
    BEFORE_REBASELINE = "before_rebaseline"


class QueueDrainProgress(BaseModel):
    """One complete queue-depth sample observed during a stable drain."""

    model_config = ConfigDict(frozen=True)

    stage: QueueDrainStage
    queues: list[QueueDepth]
    elapsed_seconds: float = Field(ge=0)
    stable_for_seconds: float = Field(ge=0)
    required_stable_seconds: int = Field(ge=0)
    sampled_at: AwareAdministrationDatetime


type AdministrationProgress = AdministrationWorkflowProgress | QueueDrainProgress
type AdministrationProgressReporter = Callable[[AdministrationProgress], None]


class SchemaManager(Protocol):
    """Apply and validate the application schema."""

    async def apply_schema(self) -> None: ...

    async def check_schema_version(self, *, expected_version: int) -> None: ...

    async def schema_status(self) -> SchemaStatus: ...


class DatabaseRecreator(Protocol):
    """Destructively recreate an application database."""

    async def drop_and_create(
        self,
        *,
        database_url: str,
        maintenance_database_url: str,
    ) -> None: ...


class ChangeFeedRebaseliner(Protocol):
    """Replace the current season's feed baseline from normalized state."""

    async def rebaseline_current(
        self,
        *,
        reason: str,
    ) -> ChangeFeedRebaselineResult: ...


class MaintenanceRepository(Protocol):
    """Persist and query recoverable production maintenance state."""

    async def maintenance_active(self) -> bool: ...

    async def get_open_maintenance(self) -> MaintenanceWindow | None: ...

    async def get_latest_maintenance(self) -> MaintenanceWindow | None: ...

    async def open_maintenance(
        self,
        *,
        reason: str,
        operator_arn: str,
        schedules: list[ScheduleSnapshot],
        queues_before: list[QueueDepth],
        collector_was_running: bool | None,
    ) -> MaintenanceWindow: ...

    async def activate_maintenance(
        self,
        *,
        maintenance_id: int,
        collector_was_running: bool,
        queues_after: list[QueueDepth],
    ) -> MaintenanceWindow: ...

    async def begin_maintenance_exit(
        self,
        *,
        maintenance_id: int,
    ) -> MaintenanceWindow: ...

    async def close_maintenance(
        self,
        *,
        maintenance_id: int,
        operator_arn: str,
    ) -> MaintenanceWindow: ...


class AwsAdministration(Protocol):
    """AWS control-plane operations used by administration workflows."""

    def identity(self) -> AwsIdentity: ...

    def resources(self) -> AwsResources: ...

    def app_deployed_revision(self) -> str: ...

    def queue_depths(self, *, include_dead_letters: bool) -> list[QueueDepth]: ...

    def schedule_snapshots(self) -> list[ScheduleSnapshot]: ...

    def set_schedule_state(
        self,
        *,
        schedule: ScheduleSnapshot,
        state: ScheduleState,
        schedule_expression: str,
    ) -> None: ...

    def send_fetch_job(self, *, message_body: str) -> str: ...

    def send_community_job(self, *, message_body: str) -> str: ...

    def peek_dead_letters(
        self,
        *,
        queue_name: str,
        max_messages: int,
    ) -> list[str]: ...


class NasAdministration(Protocol):
    """NAS collector operations used by administration workflows."""

    def doctor(self) -> None: ...

    def status(self) -> NasCollectorStatus: ...

    def start(self) -> NasCollectorStatus: ...

    def stop(self) -> NasCollectorStatus: ...

    def logs(self, *, tail_lines: int) -> str: ...

    def update(self, *, image_tag: str) -> NasCollectorStatus: ...


class ProductionAdministrationDatabase(MaintenanceRepository, Protocol):
    """Database operations required by composed production administration."""

    async def apply_schema(self) -> None: ...

    async def schema_status(self) -> SchemaStatus: ...

    async def check_schema_version(self, *, expected_version: int) -> None: ...

    async def rebaseline_current_change_feed(
        self,
        *,
        reason: str,
    ) -> ChangeFeedRebaselineResult: ...

    async def get_current_season(self) -> Season | None: ...

    async def get_current_event(self, *, season_id: str) -> Event | None: ...

    async def list_fixtures(
        self,
        *,
        season_id: str,
        event_id: int | None,
        after_id: int,
        limit: int,
    ) -> list[Fixture]: ...
