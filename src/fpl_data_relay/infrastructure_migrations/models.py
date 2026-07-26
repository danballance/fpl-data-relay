"""Strict configuration and state contracts for infrastructure deployments."""

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

AccountId = Annotated[str, StringConstraints(pattern=r"^[0-9]{12}$")]
CommitSha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ProductionConfig(BaseModel):
    """All non-secret values required for one production deployment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: AccountId
    region: str = Field(min_length=1)
    data_stack_name: str = Field(min_length=1)
    application_stack_name: str = Field(min_length=1)
    alert_email: str = Field(min_length=3)
    collector_source_user_name: str = Field(
        pattern=r"^[A-Za-z0-9+=,.@_-]{1,64}$",
    )
    payload_prefix: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9/_-]*$")
    migration_parameter_prefix: str = Field(
        pattern=r"^/[A-Za-z0-9_.\-/]+$",
    )
    collector_image: str = Field(min_length=1)

    @classmethod
    def from_toml(cls, *, path: Path) -> Self:
        """Load strict production configuration from TOML."""
        import tomllib

        with path.open("rb") as config_file:
            return cls.model_validate(tomllib.load(config_file))

    @property
    def collector_source_user_arn(self) -> str:
        """Return the exact principal trusted by the collector role."""
        return (
            f"arn:aws:iam::{self.account_id}:"
            f"user/{self.collector_source_user_name}"
        )


class MigrationBoundary(StrEnum):
    """Core deployment operation wrapped by a migration."""

    DATA_STACK = "data-stack"
    APPLICATION_STACK = "application-stack"
    POST_DEPLOYMENT = "post-deployment"


class MigrationDefinition(BaseModel):
    """Immutable identity and placement of one repository migration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(gt=0)
    name: str = Field(pattern=r"^[a-z][a-z0-9_]+$")
    boundary: MigrationBoundary
    checksum: Sha256

    @property
    def identifier(self) -> str:
        """Return the stable path-safe migration identifier."""
        return f"{self.version:04d}_{self.name}"


class AppliedMigrationRecord(BaseModel):
    """Migration metadata persisted in SSM Parameter Store."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(gt=0)
    name: str = Field(pattern=r"^[a-z][a-z0-9_]+$")
    checksum: Sha256
    applied_at: datetime
    commit_sha: CommitSha
    account_id: AccountId
    region: str = Field(min_length=1)
    stack_name: str = Field(min_length=1)

    @field_validator("applied_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        """Reject timestamps without an explicit UTC offset."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("applied_at must be timezone-aware")
        return value


class MigrationRunStatus(StrEnum):
    """Progress of one pending migration in the current workflow."""

    PENDING = "pending"
    PREPARED = "prepared"
    FINALIZED = "finalized"


class PendingMigrationState(BaseModel):
    """Ephemeral state for one pending migration."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(gt=0)
    name: str = Field(pattern=r"^[a-z][a-z0-9_]+$")
    boundary: MigrationBoundary
    checksum: Sha256
    status: MigrationRunStatus
    context_json: str | None

    @property
    def identifier(self) -> str:
        """Return the stable migration identifier."""
        return f"{self.version:04d}_{self.name}"


class DeploymentMigrationState(BaseModel):
    """Ephemeral state shared by migration workflow phases."""

    model_config = ConfigDict(extra="forbid")

    account_id: AccountId
    region: str
    commit_sha: CommitSha
    migrations: list[PendingMigrationState]


class ChangeSetPolicy(StrEnum):
    """Safety policy used to inspect a CloudFormation change set."""

    DATA = "data"
    APPLICATION = "application"
