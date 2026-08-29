"""Administration models and ports."""

from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fpl_data_relay.domain.fixtures import Fixture
from fpl_data_relay.domain.reference import Event, Season


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


class AwsProfileStatus(BaseModel):
    """Verified local AWS console-login profile state."""

    model_config = ConfigDict(frozen=True)

    profile_name: str = Field(min_length=1)
    region: str = Field(min_length=1)
    authentication: Literal["console-login"]
    authenticated: bool
    account_id: str = Field(pattern=r"^\d{12}$")
    arn: str = Field(min_length=1)


class AwsIamPrincipalType(StrEnum):
    """IAM principal kinds supported by local profile bootstrapping."""

    USER = "user"
    GROUP = "group"
    ROLE = "role"


class AwsManagedPolicyState(StrEnum):
    """Change made to the generated relay administration policy."""

    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


class AwsProfileBootstrapStatus(BaseModel):
    """Auditable result of granting one console identity relay access."""

    model_config = ConfigDict(frozen=True)

    bootstrap_profile: str = Field(min_length=1)
    bootstrap_arn: str = Field(min_length=1)
    principal_type: AwsIamPrincipalType
    principal_name: str = Field(min_length=1)
    sign_in_policy_arn: str = Field(min_length=1)
    relay_policy_arn: str = Field(min_length=1)
    relay_policy_state: AwsManagedPolicyState


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


class AwsProfileAdministration(Protocol):
    """Local AWS CLI console-login profile operations."""

    def setup(self) -> AwsProfileStatus: ...

    def login(self) -> AwsProfileStatus: ...

    def status(self) -> AwsProfileStatus: ...

    def logout(self) -> None: ...


class AwsProfileBootstrapAdministration(Protocol):
    """One-time IAM preparation for a dedicated console-login profile."""

    def instructions(self) -> str: ...

    def bootstrap(
        self,
        *,
        bootstrap_profile: str,
        principal_type: AwsIamPrincipalType,
        principal_name: str,
    ) -> AwsProfileBootstrapStatus: ...


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
