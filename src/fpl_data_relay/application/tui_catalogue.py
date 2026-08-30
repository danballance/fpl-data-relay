"""Typed catalogue of public Make commands exposed by the TUI."""

from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from fpl_data_relay.application.ports.administration import (
    AdministrationReason,
    DeadLetterQueueName,
    GitShaRequest,
    ScheduleStateFileRequest,
)


class CommandGroup(StrEnum):
    """Navigation area that owns a public command."""

    WORKSPACE = "workspace"
    LOCAL_SERVICES = "local-services"
    AWS = "aws"
    NAS_COLLECTOR = "nas-collector"
    PRODUCTION = "production"
    QUALITY = "quality"


class ExecutionKind(StrEnum):
    """Boundary used to execute a command."""

    MAKE_PROCESS = "make-process"
    ADMINISTRATION_OPERATION = "administration-operation"


class ParameterKind(StrEnum):
    """Form presented before command execution."""

    NONE = "none"
    REASON = "reason"
    SHA = "sha"
    DLQ_SELECTION = "dlq-selection"
    STATE_FILE = "state-file"


class CommandPrerequisite(StrEnum):
    """State that must exist before a command can run."""

    LOCAL_ENV = "local-env"
    CLIENT_ENV = "client-env"
    ADMIN_CONFIG = "admin-config"


class CommandLifetime(StrEnum):
    """Expected process lifetime."""

    FINITE = "finite"
    LONG_RUNNING = "long-running"


class CommandRisk(StrEnum):
    """Highest-impact state a command can change."""

    READ_ONLY = "read-only"
    LOCAL_CHANGE = "local-change"
    PRODUCTION_CHANGE = "production-change"


class CommandParameters(BaseModel):
    """Strict immutable base for all command input models."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class NoParameters(CommandParameters):
    """Marker model for a command with no form input."""


class ReasonParameters(CommandParameters):
    """Audited reason for an administration operation."""

    reason: str

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        """Use the presentation-neutral administration reason validator."""
        return AdministrationReason(reason=value).reason


class ShaParameters(CommandParameters):
    """Immutable image revision for a collector image operation."""

    sha: str

    @field_validator("sha")
    @classmethod
    def validate_sha(cls, value: str) -> str:
        """Use the presentation-neutral immutable revision validator."""
        return GitShaRequest(sha=value).sha


DeadLetterQueue = DeadLetterQueueName


class DlqSelectionParameters(CommandParameters):
    """Queue selected for a bounded, non-destructive message peek."""

    queue: DeadLetterQueueName


class StateFileParameters(CommandParameters):
    """Explicit bootstrap snapshot path."""

    state_file: Path

    @field_validator("state_file")
    @classmethod
    def require_file_path(cls, value: Path) -> Path:
        """Use the presentation-neutral schedule state-file validator."""
        return ScheduleStateFileRequest(path=value).path


type ParameterModel = type[CommandParameters]

PARAMETER_MODELS: Mapping[ParameterKind, ParameterModel] = MappingProxyType(
    {
        ParameterKind.NONE: NoParameters,
        ParameterKind.REASON: ReasonParameters,
        ParameterKind.SHA: ShaParameters,
        ParameterKind.DLQ_SELECTION: DlqSelectionParameters,
        ParameterKind.STATE_FILE: StateFileParameters,
    },
)


class TuiCommand(BaseModel):
    """One immutable command exposed through navigation and the palette."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        arbitrary_types_allowed=True,
    )

    target: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str = Field(min_length=1)
    group: CommandGroup
    execution_kind: ExecutionKind
    parameter_kind: ParameterKind
    parameter_model: ParameterModel
    prerequisites: tuple[CommandPrerequisite, ...]
    lifetime: CommandLifetime
    risk: CommandRisk

    @model_validator(mode="after")
    def validate_command(self) -> TuiCommand:
        """Keep form, safety, and prerequisite metadata internally consistent."""
        expected_model = PARAMETER_MODELS[self.parameter_kind]
        if self.parameter_model is not expected_model:
            raise ValueError(
                f"Parameter kind {self.parameter_kind.value} requires "
                f"{expected_model.__name__}.",
            )
        if len(self.prerequisites) != len(set(self.prerequisites)):
            raise ValueError("Command prerequisites must be unique.")
        return self


def _command(
    *,
    target: str,
    description: str,
    group: CommandGroup,
    execution_kind: ExecutionKind,
    parameter_kind: ParameterKind,
    prerequisites: tuple[CommandPrerequisite, ...],
    lifetime: CommandLifetime,
    risk: CommandRisk,
) -> TuiCommand:
    return TuiCommand(
        target=target,
        description=description,
        group=group,
        execution_kind=execution_kind,
        parameter_kind=parameter_kind,
        parameter_model=PARAMETER_MODELS[parameter_kind],
        prerequisites=prerequisites,
        lifetime=lifetime,
        risk=risk,
    )


_FINITE = CommandLifetime.FINITE
_MAKE = ExecutionKind.MAKE_PROCESS
_ADMIN = ExecutionKind.ADMINISTRATION_OPERATION
_NONE = ParameterKind.NONE
_READ = CommandRisk.READ_ONLY
_LOCAL = CommandRisk.LOCAL_CHANGE
_PRODUCTION = CommandRisk.PRODUCTION_CHANGE
_ADMIN_CONFIG = (CommandPrerequisite.ADMIN_CONFIG,)
_ADMIN_WRITE = _ADMIN_CONFIG


COMMAND_CATALOGUE: tuple[TuiCommand, ...] = (
    _command(
        target="help",
        description="Show this help and the required developer tools.",
        group=CommandGroup.WORKSPACE,
        execution_kind=_MAKE,
        parameter_kind=_NONE,
        prerequisites=(),
        lifetime=_FINITE,
        risk=_READ,
    ),
    _command(
        target="doctor",
        description="Verify the complete local development and CI toolchain.",
        group=CommandGroup.WORKSPACE,
        execution_kind=_MAKE,
        parameter_kind=_NONE,
        prerequisites=(),
        lifetime=_FINITE,
        risk=_READ,
    ),
    _command(
        target="install",
        description="Install the locked Python and client dependencies.",
        group=CommandGroup.WORKSPACE,
        execution_kind=_MAKE,
        parameter_kind=_NONE,
        prerequisites=(),
        lifetime=_FINITE,
        risk=_LOCAL,
    ),
    _command(
        target="setup",
        description="Install dependencies and create new local environment files.",
        group=CommandGroup.WORKSPACE,
        execution_kind=_MAKE,
        parameter_kind=_NONE,
        prerequisites=(),
        lifetime=_FINITE,
        risk=_LOCAL,
    ),
    _command(
        target="local-dev",
        description="Start the local backend and Vite client.",
        group=CommandGroup.LOCAL_SERVICES,
        execution_kind=_MAKE,
        parameter_kind=_NONE,
        prerequisites=(
            CommandPrerequisite.LOCAL_ENV,
            CommandPrerequisite.CLIENT_ENV,
        ),
        lifetime=CommandLifetime.LONG_RUNNING,
        risk=_LOCAL,
    ),
    _command(
        target="local-up",
        description="Build and start the local database and API.",
        group=CommandGroup.LOCAL_SERVICES,
        execution_kind=_MAKE,
        parameter_kind=_NONE,
        prerequisites=(CommandPrerequisite.LOCAL_ENV,),
        lifetime=_FINITE,
        risk=_LOCAL,
    ),
    _command(
        target="local-client",
        description="Run the local Vite development client.",
        group=CommandGroup.LOCAL_SERVICES,
        execution_kind=_MAKE,
        parameter_kind=_NONE,
        prerequisites=(CommandPrerequisite.CLIENT_ENV,),
        lifetime=CommandLifetime.LONG_RUNNING,
        risk=_LOCAL,
    ),
    _command(
        target="local-logs",
        description="Follow local API and PostgreSQL logs.",
        group=CommandGroup.LOCAL_SERVICES,
        execution_kind=_MAKE,
        parameter_kind=_NONE,
        prerequisites=(CommandPrerequisite.LOCAL_ENV,),
        lifetime=CommandLifetime.LONG_RUNNING,
        risk=_READ,
    ),
    _command(
        target="local-ps",
        description="Show local Compose service status.",
        group=CommandGroup.LOCAL_SERVICES,
        execution_kind=_MAKE,
        parameter_kind=_NONE,
        prerequisites=(CommandPrerequisite.LOCAL_ENV,),
        lifetime=_FINITE,
        risk=_READ,
    ),
    _command(
        target="local-down",
        description="Stop local services while preserving database data.",
        group=CommandGroup.LOCAL_SERVICES,
        execution_kind=_MAKE,
        parameter_kind=_NONE,
        prerequisites=(CommandPrerequisite.LOCAL_ENV,),
        lifetime=_FINITE,
        risk=_LOCAL,
    ),
    _command(
        target="local-db-status",
        description="Show applied and pending local migrations.",
        group=CommandGroup.LOCAL_SERVICES,
        execution_kind=_MAKE,
        parameter_kind=_NONE,
        prerequisites=(CommandPrerequisite.LOCAL_ENV,),
        lifetime=_FINITE,
        risk=_READ,
    ),
    _command(
        target="local-db-migrate",
        description="Apply all pending local migrations.",
        group=CommandGroup.LOCAL_SERVICES,
        execution_kind=_MAKE,
        parameter_kind=_NONE,
        prerequisites=(CommandPrerequisite.LOCAL_ENV,),
        lifetime=_FINITE,
        risk=_LOCAL,
    ),
    _command(
        target="aws-doctor",
        description="Validate the local AWS administration connection.",
        group=CommandGroup.AWS,
        execution_kind=_ADMIN,
        parameter_kind=_NONE,
        prerequisites=_ADMIN_CONFIG,
        lifetime=_FINITE,
        risk=_READ,
    ),
    _command(
        target="aws-status",
        description="Show production AWS, schema, queue, and schedule status.",
        group=CommandGroup.AWS,
        execution_kind=_ADMIN,
        parameter_kind=_NONE,
        prerequisites=_ADMIN_CONFIG,
        lifetime=_FINITE,
        risk=_READ,
    ),
    _command(
        target="aws-app-revision",
        description="Show the Git revision deployed in the application stack.",
        group=CommandGroup.AWS,
        execution_kind=_ADMIN,
        parameter_kind=_NONE,
        prerequisites=_ADMIN_CONFIG,
        lifetime=_FINITE,
        risk=_READ,
    ),
    _command(
        target="aws-db-status",
        description="Show production database migration status.",
        group=CommandGroup.AWS,
        execution_kind=_ADMIN,
        parameter_kind=_NONE,
        prerequisites=_ADMIN_CONFIG,
        lifetime=_FINITE,
        risk=_READ,
    ),
    _command(
        target="aws-db-migrate",
        description="Apply production database migrations.",
        group=CommandGroup.AWS,
        execution_kind=_ADMIN,
        parameter_kind=_NONE,
        prerequisites=_ADMIN_WRITE,
        lifetime=_FINITE,
        risk=_PRODUCTION,
    ),
    _command(
        target="aws-queues-status",
        description="Show all production working-queue depths.",
        group=CommandGroup.AWS,
        execution_kind=_ADMIN,
        parameter_kind=_NONE,
        prerequisites=_ADMIN_CONFIG,
        lifetime=_FINITE,
        risk=_READ,
    ),
    _command(
        target="aws-queues-drain",
        description="Wait for all three working queues to drain.",
        group=CommandGroup.AWS,
        execution_kind=_ADMIN,
        parameter_kind=_NONE,
        prerequisites=_ADMIN_CONFIG,
        lifetime=_FINITE,
        risk=_READ,
    ),
    _command(
        target="aws-dlqs-status",
        description="Show all production dead-letter queue depths.",
        group=CommandGroup.AWS,
        execution_kind=_ADMIN,
        parameter_kind=_NONE,
        prerequisites=_ADMIN_CONFIG,
        lifetime=_FINITE,
        risk=_READ,
    ),
    _command(
        target="aws-dlq-peek",
        description="Inspect up to ten DLQ messages without deleting them.",
        group=CommandGroup.AWS,
        execution_kind=_ADMIN,
        parameter_kind=ParameterKind.DLQ_SELECTION,
        prerequisites=_ADMIN_CONFIG,
        lifetime=_FINITE,
        risk=_READ,
    ),
    _command(
        target="aws-send-reference",
        description="Send one strict production reference job.",
        group=CommandGroup.AWS,
        execution_kind=_ADMIN,
        parameter_kind=_NONE,
        prerequisites=_ADMIN_WRITE,
        lifetime=_FINITE,
        risk=_PRODUCTION,
    ),
    _command(
        target="aws-send-live",
        description="Send one strict current-event production live job.",
        group=CommandGroup.AWS,
        execution_kind=_ADMIN,
        parameter_kind=_NONE,
        prerequisites=_ADMIN_WRITE,
        lifetime=_FINITE,
        risk=_PRODUCTION,
    ),
    _command(
        target="aws-send-community",
        description="Send one strict production community dispatch job.",
        group=CommandGroup.AWS,
        execution_kind=_ADMIN,
        parameter_kind=_NONE,
        prerequisites=_ADMIN_WRITE,
        lifetime=_FINITE,
        risk=_PRODUCTION,
    ),
    _command(
        target="aws-schedules-status",
        description="Show fixed and dynamic production schedules.",
        group=CommandGroup.AWS,
        execution_kind=_ADMIN,
        parameter_kind=_NONE,
        prerequisites=_ADMIN_CONFIG,
        lifetime=_FINITE,
        risk=_READ,
    ),
    _command(
        target="aws-schedules-bootstrap-pause",
        description="Snapshot and pause schedules for the migration 0005 bootstrap.",
        group=CommandGroup.AWS,
        execution_kind=_ADMIN,
        parameter_kind=ParameterKind.STATE_FILE,
        prerequisites=_ADMIN_WRITE,
        lifetime=_FINITE,
        risk=_PRODUCTION,
    ),
    _command(
        target="aws-schedules-bootstrap-restore",
        description="Restore the migration 0005 bootstrap schedule snapshot.",
        group=CommandGroup.AWS,
        execution_kind=_ADMIN,
        parameter_kind=ParameterKind.STATE_FILE,
        prerequisites=_ADMIN_WRITE,
        lifetime=_FINITE,
        risk=_PRODUCTION,
    ),
    _command(
        target="aws-maintenance-status",
        description="Show the open or latest maintenance audit.",
        group=CommandGroup.AWS,
        execution_kind=_ADMIN,
        parameter_kind=_NONE,
        prerequisites=_ADMIN_CONFIG,
        lifetime=_FINITE,
        risk=_READ,
    ),
    _command(
        target="aws-schedules-pause",
        description="Open maintenance and pause AWS schedules only.",
        group=CommandGroup.AWS,
        execution_kind=_ADMIN,
        parameter_kind=ParameterKind.REASON,
        prerequisites=_ADMIN_WRITE,
        lifetime=_FINITE,
        risk=_PRODUCTION,
    ),
    _command(
        target="aws-schedules-restore",
        description="Restore audited AWS schedule states only.",
        group=CommandGroup.AWS,
        execution_kind=_ADMIN,
        parameter_kind=_NONE,
        prerequisites=_ADMIN_WRITE,
        lifetime=_FINITE,
        risk=_PRODUCTION,
    ),
    _command(
        target="aws-rebaseline-current",
        description="Rebaseline the current season during active maintenance.",
        group=CommandGroup.AWS,
        execution_kind=_ADMIN,
        parameter_kind=ParameterKind.REASON,
        prerequisites=_ADMIN_WRITE,
        lifetime=_FINITE,
        risk=_PRODUCTION,
    ),
    _command(
        target="nas-doctor",
        description="Validate SSH, Compose, and Docker on the NAS.",
        group=CommandGroup.NAS_COLLECTOR,
        execution_kind=_ADMIN,
        parameter_kind=_NONE,
        prerequisites=_ADMIN_CONFIG,
        lifetime=_FINITE,
        risk=_READ,
    ),
    _command(
        target="nas-status",
        description="Show the NAS collector state, health, and image.",
        group=CommandGroup.NAS_COLLECTOR,
        execution_kind=_ADMIN,
        parameter_kind=_NONE,
        prerequisites=_ADMIN_CONFIG,
        lifetime=_FINITE,
        risk=_READ,
    ),
    _command(
        target="nas-start",
        description="Start the NAS collector and wait for health.",
        group=CommandGroup.NAS_COLLECTOR,
        execution_kind=_ADMIN,
        parameter_kind=_NONE,
        prerequisites=_ADMIN_WRITE,
        lifetime=_FINITE,
        risk=_PRODUCTION,
    ),
    _command(
        target="nas-stop",
        description="Stop the NAS collector without removing it.",
        group=CommandGroup.NAS_COLLECTOR,
        execution_kind=_ADMIN,
        parameter_kind=_NONE,
        prerequisites=_ADMIN_WRITE,
        lifetime=_FINITE,
        risk=_PRODUCTION,
    ),
    _command(
        target="nas-logs",
        description="Show the configured bounded NAS collector log tail.",
        group=CommandGroup.NAS_COLLECTOR,
        execution_kind=_ADMIN,
        parameter_kind=_NONE,
        prerequisites=_ADMIN_CONFIG,
        lifetime=_FINITE,
        risk=_READ,
    ),
    _command(
        target="nas-update",
        description="Activate an existing immutable collector image.",
        group=CommandGroup.NAS_COLLECTOR,
        execution_kind=_ADMIN,
        parameter_kind=ParameterKind.SHA,
        prerequisites=_ADMIN_WRITE,
        lifetime=_FINITE,
        risk=_PRODUCTION,
    ),
    _command(
        target="nas-rollback",
        description="Return the NAS collector to an explicit prior image.",
        group=CommandGroup.NAS_COLLECTOR,
        execution_kind=_ADMIN,
        parameter_kind=ParameterKind.SHA,
        prerequisites=_ADMIN_WRITE,
        lifetime=_FINITE,
        risk=_PRODUCTION,
    ),
    _command(
        target="prod-doctor",
        description="Validate both production AWS and NAS control planes.",
        group=CommandGroup.PRODUCTION,
        execution_kind=_ADMIN,
        parameter_kind=_NONE,
        prerequisites=_ADMIN_CONFIG,
        lifetime=_FINITE,
        risk=_READ,
    ),
    _command(
        target="prod-status",
        description="Show one combined AWS, database, and NAS status.",
        group=CommandGroup.PRODUCTION,
        execution_kind=_ADMIN,
        parameter_kind=_NONE,
        prerequisites=_ADMIN_CONFIG,
        lifetime=_FINITE,
        risk=_READ,
    ),
    _command(
        target="prod-maintenance-begin",
        description="Quiesce writers, drain queues, and stop the collector.",
        group=CommandGroup.PRODUCTION,
        execution_kind=_ADMIN,
        parameter_kind=ParameterKind.REASON,
        prerequisites=_ADMIN_WRITE,
        lifetime=_FINITE,
        risk=_PRODUCTION,
    ),
    _command(
        target="prod-maintenance-end",
        description="Restore the audited collector and schedule states.",
        group=CommandGroup.PRODUCTION,
        execution_kind=_ADMIN,
        parameter_kind=_NONE,
        prerequisites=_ADMIN_WRITE,
        lifetime=_FINITE,
        risk=_PRODUCTION,
    ),
    _command(
        target="prod-rebaseline-current",
        description="Refresh normalized data and rebaseline under maintenance.",
        group=CommandGroup.PRODUCTION,
        execution_kind=_ADMIN,
        parameter_kind=ParameterKind.REASON,
        prerequisites=_ADMIN_WRITE,
        lifetime=_FINITE,
        risk=_PRODUCTION,
    ),
    _command(
        target="lint",
        description="Run all backend and client static checks.",
        group=CommandGroup.QUALITY,
        execution_kind=_MAKE,
        parameter_kind=_NONE,
        prerequisites=(),
        lifetime=_FINITE,
        risk=_READ,
    ),
    _command(
        target="test",
        description="Run backend and client tests with coverage gates.",
        group=CommandGroup.QUALITY,
        execution_kind=_MAKE,
        parameter_kind=_NONE,
        prerequisites=(),
        lifetime=_FINITE,
        risk=_READ,
    ),
    _command(
        target="check",
        description="Run the normal backend and client quality gates.",
        group=CommandGroup.QUALITY,
        execution_kind=_MAKE,
        parameter_kind=_NONE,
        prerequisites=(),
        lifetime=_FINITE,
        risk=_READ,
    ),
    _command(
        target="infra",
        description="Validate SAM and Compose infrastructure definitions.",
        group=CommandGroup.QUALITY,
        execution_kind=_MAKE,
        parameter_kind=_NONE,
        prerequisites=(),
        lifetime=_FINITE,
        risk=_READ,
    ),
    _command(
        target="images",
        description=(
            "Build and verify the application and collector container images."
        ),
        group=CommandGroup.QUALITY,
        execution_kind=_MAKE,
        parameter_kind=_NONE,
        prerequisites=(),
        lifetime=_FINITE,
        risk=_LOCAL,
    ),
    _command(
        target="ci",
        description="Run the complete local equivalent of the CI quality job.",
        group=CommandGroup.QUALITY,
        execution_kind=_MAKE,
        parameter_kind=_NONE,
        prerequisites=(),
        lifetime=_FINITE,
        risk=_LOCAL,
    ),
)

COMMANDS_BY_TARGET: Mapping[str, TuiCommand] = MappingProxyType(
    {command.target: command for command in COMMAND_CATALOGUE},
)

if len(COMMANDS_BY_TARGET) != len(COMMAND_CATALOGUE):
    raise RuntimeError("The TUI command catalogue contains duplicate targets.")


def command_for_target(*, target: str) -> TuiCommand:
    """Return one public command, failing clearly for arbitrary target names."""
    try:
        return COMMANDS_BY_TARGET[target]
    except KeyError as error:
        raise ValueError(f"Unknown public Make target: {target}") from error
