"""Phase-aware runner for ordered infrastructure migrations."""

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from fpl_data_relay.infrastructure_migrations.interfaces import (
    InfrastructureAws,
    InfrastructureMigration,
)
from fpl_data_relay.infrastructure_migrations.models import (
    AppliedMigrationRecord,
    DeploymentMigrationState,
    MigrationBoundary,
    MigrationRunStatus,
    PendingMigrationState,
    ProductionConfig,
)

from .versions.v0001_split_collector_ingestion import (
    MIGRATION as SPLIT_COLLECTOR_INGESTION,
)

MIGRATIONS: tuple[InfrastructureMigration, ...] = (
    SPLIT_COLLECTOR_INGESTION,
)


class InfrastructureMigrationError(RuntimeError):
    """Raised when repository or SSM migration history is inconsistent."""


def begin_migration_run(
    *,
    aws: InfrastructureAws,
    config: ProductionConfig,
    commit_sha: str,
    state_path: Path,
) -> None:
    """Validate durable history and create ephemeral state for pending work."""
    validate_registry()
    aws.verify_identity(expected_account_id=config.account_id)
    applied = aws.read_migration_records(
        parameter_prefix=config.migration_parameter_prefix,
    )
    validate_migration_history(applied=applied, config=config)
    applied_versions = {record.version for record in applied}
    state = DeploymentMigrationState(
        account_id=config.account_id,
        region=config.region,
        commit_sha=commit_sha,
        migrations=[
            PendingMigrationState(
                version=migration.definition.version,
                name=migration.definition.name,
                boundary=migration.definition.boundary,
                checksum=migration.definition.checksum,
                status=MigrationRunStatus.PENDING,
                context_json=None,
            )
            for migration in MIGRATIONS
            if migration.definition.version not in applied_versions
        ],
    )
    _write_state(state_path=state_path, state=state)


def prepare_boundary(
    *,
    aws: InfrastructureAws,
    config: ProductionConfig,
    boundary: MigrationBoundary,
    state_path: Path,
) -> None:
    """Prepare pending migrations that wrap one deployment boundary."""
    state = _read_and_validate_state(config=config, state_path=state_path)
    for pending in state.migrations:
        if pending.boundary is not boundary:
            continue
        if pending.status is not MigrationRunStatus.PENDING:
            continue
        migration = _migration_for(version=pending.version)
        pending.context_json = migration.prepare(aws=aws, config=config)
        pending.status = MigrationRunStatus.PREPARED
    _write_state(state_path=state_path, state=state)


def finalize_boundary(
    *,
    aws: InfrastructureAws,
    config: ProductionConfig,
    boundary: MigrationBoundary,
    state_path: Path,
) -> None:
    """Finalize prepared migrations after the wrapped deployment succeeds."""
    state = _read_and_validate_state(config=config, state_path=state_path)
    for pending in state.migrations:
        if pending.boundary is not boundary:
            continue
        if pending.status is MigrationRunStatus.FINALIZED:
            continue
        if (
            pending.status is not MigrationRunStatus.PREPARED
            or pending.context_json is None
        ):
            raise InfrastructureMigrationError(
                f"Migration {pending.identifier} was not prepared.",
            )
        migration = _migration_for(version=pending.version)
        migration.finalize(
            aws=aws,
            config=config,
            context_json=pending.context_json,
        )
        pending.status = MigrationRunStatus.FINALIZED
    _write_state(state_path=state_path, state=state)


def commit_migration_run(
    *,
    aws: InfrastructureAws,
    config: ProductionConfig,
    state_path: Path,
    applied_at: datetime,
) -> None:
    """Verify all migrations and write new SSM records in version order."""
    state = _read_and_validate_state(config=config, state_path=state_path)
    if applied_at.tzinfo is None or applied_at.utcoffset() is None:
        raise InfrastructureMigrationError("applied_at must be timezone-aware.")
    applied = aws.read_migration_records(
        parameter_prefix=config.migration_parameter_prefix,
    )
    validate_migration_history(applied=applied, config=config)
    applied_versions = {record.version for record in applied}
    pending_state = {migration.version: migration for migration in state.migrations}
    for migration in MIGRATIONS:
        definition = migration.definition
        if definition.version in applied_versions:
            migration.verify(aws=aws, config=config)
            continue
        pending = pending_state.get(definition.version)
        if pending is None or pending.status is not MigrationRunStatus.FINALIZED:
            raise InfrastructureMigrationError(
                f"Migration {definition.identifier} has not finalized.",
            )
        migration.verify(aws=aws, config=config)
        aws.write_migration_record(
            parameter_prefix=config.migration_parameter_prefix,
            record=AppliedMigrationRecord(
                version=definition.version,
                name=definition.name,
                checksum=definition.checksum,
                applied_at=applied_at.astimezone(UTC),
                commit_sha=state.commit_sha,
                account_id=config.account_id,
                region=config.region,
                stack_name=_stack_name(
                    boundary=definition.boundary,
                    config=config,
                ),
            ),
        )
        applied_versions.add(definition.version)
    final_records = aws.read_migration_records(
        parameter_prefix=config.migration_parameter_prefix,
    )
    validate_migration_history(applied=final_records, config=config)


def verify_all_migrations(
    *,
    aws: InfrastructureAws,
    config: ProductionConfig,
) -> None:
    """Verify durable history and every applied migration postcondition."""
    validate_registry()
    aws.verify_identity(expected_account_id=config.account_id)
    applied = aws.read_migration_records(
        parameter_prefix=config.migration_parameter_prefix,
    )
    validate_migration_history(applied=applied, config=config)
    for record in applied:
        _migration_for(version=record.version).verify(aws=aws, config=config)


def secure_failed_deployment(
    *,
    aws: InfrastructureAws,
    config: ProductionConfig,
) -> None:
    """Run every migration's idempotent safety guard."""
    aws.verify_identity(expected_account_id=config.account_id)
    for migration in MIGRATIONS:
        migration.secure_failure(aws=aws, config=config)


def reconcile_collector_source_user(
    *,
    aws: InfrastructureAws,
    config: ProductionConfig,
) -> None:
    """Converge the NAS source identity without touching its access keys."""
    aws.verify_identity(expected_account_id=config.account_id)
    outputs = aws.stack_outputs(stack_name=config.application_stack_name)
    collector_role_arn = outputs.get("CollectorRoleArn")
    if collector_role_arn is None:
        raise RuntimeError("Application stack lacks CollectorRoleArn output.")
    aws.reconcile_collector_source_policy(
        source_user_name=config.collector_source_user_name,
        source_user_arn=config.collector_source_user_arn,
        collector_role_arn=collector_role_arn,
    )


def validate_registry() -> None:
    """Reject reordered, renamed, or unexpectedly modified source files."""
    versions = [migration.definition.version for migration in MIGRATIONS]
    if versions != list(range(1, len(MIGRATIONS) + 1)):
        raise InfrastructureMigrationError(
            "Repository migrations are not a contiguous sequence from version 1.",
        )
    identifiers: set[str] = set()
    for migration in MIGRATIONS:
        definition = migration.definition
        if definition.identifier in identifiers:
            raise InfrastructureMigrationError(
                f"Duplicate migration {definition.identifier}.",
            )
        identifiers.add(definition.identifier)
        expected_prefix = f"v{definition.version:04d}_"
        if not migration.source_path.name.startswith(expected_prefix):
            raise InfrastructureMigrationError(
                f"Migration {definition.identifier} has an unexpected filename.",
            )
        actual_checksum = sha256(migration.source_path.read_bytes()).hexdigest()
        if definition.checksum != actual_checksum:
            raise InfrastructureMigrationError(
                f"Migration {definition.identifier} source checksum "
                "changed at runtime.",
            )


def validate_migration_history(
    *,
    applied: list[AppliedMigrationRecord],
    config: ProductionConfig,
) -> None:
    """Reject unknown, gapped, renamed, modified, or cross-account history."""
    known = {
        migration.definition.version: migration.definition
        for migration in MIGRATIONS
    }
    for record in applied:
        definition = known.get(record.version)
        if definition is None:
            raise InfrastructureMigrationError(
                f"SSM contains unknown migration version {record.version}.",
            )
        if record.name != definition.name:
            raise InfrastructureMigrationError(
                f"Migration {record.version} name mismatch.",
            )
        if record.checksum != definition.checksum:
            raise InfrastructureMigrationError(
                f"Migration {record.version} checksum mismatch.",
            )
        if record.account_id != config.account_id or record.region != config.region:
            raise InfrastructureMigrationError(
                f"Migration {record.version} belongs to another AWS environment.",
            )
        expected_stack = _stack_name(
            boundary=definition.boundary,
            config=config,
        )
        if record.stack_name != expected_stack:
            raise InfrastructureMigrationError(
                f"Migration {record.version} stack-name mismatch.",
            )
    stored_versions = [record.version for record in applied]
    expected_versions = [
        migration.definition.version for migration in MIGRATIONS[: len(applied)]
    ]
    if stored_versions != expected_versions:
        raise InfrastructureMigrationError(
            "Applied infrastructure migrations are not a contiguous prefix.",
        )


def _read_and_validate_state(
    *,
    config: ProductionConfig,
    state_path: Path,
) -> DeploymentMigrationState:
    state = DeploymentMigrationState.model_validate_json(state_path.read_text())
    if state.account_id != config.account_id or state.region != config.region:
        raise InfrastructureMigrationError(
            "Ephemeral migration state belongs to another AWS environment.",
        )
    known = {
        migration.definition.version: migration.definition
        for migration in MIGRATIONS
    }
    for pending in state.migrations:
        definition = known.get(pending.version)
        if (
            definition is None
            or pending.name != definition.name
            or pending.boundary is not definition.boundary
            or pending.checksum != definition.checksum
        ):
            raise InfrastructureMigrationError(
                f"Ephemeral state for migration {pending.version} is inconsistent.",
            )
    return state


def _write_state(
    *,
    state_path: Path,
    state: DeploymentMigrationState,
) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = state_path.with_suffix(f"{state_path.suffix}.tmp")
    temporary_path.write_text(state.model_dump_json(indent=2) + "\n")
    temporary_path.replace(state_path)


def _migration_for(*, version: int) -> InfrastructureMigration:
    for migration in MIGRATIONS:
        if migration.definition.version == version:
            return migration
    raise InfrastructureMigrationError(f"Unknown migration version {version}.")


def _stack_name(
    *,
    boundary: MigrationBoundary,
    config: ProductionConfig,
) -> str:
    if boundary is MigrationBoundary.DATA_STACK:
        return config.data_stack_name
    return config.application_stack_name
