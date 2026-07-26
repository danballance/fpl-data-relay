"""Typer entrypoint for production infrastructure migration operations."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from fpl_data_relay.infrastructure_migrations.aws import BotoInfrastructureAws
from fpl_data_relay.infrastructure_migrations.models import (
    ChangeSetPolicy,
    MigrationBoundary,
    ProductionConfig,
)
from fpl_data_relay.infrastructure_migrations.runner import (
    begin_migration_run,
    commit_migration_run,
    finalize_boundary,
    prepare_boundary,
    reconcile_collector_source_user,
    secure_failed_deployment,
    verify_all_migrations,
)

app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    help="Apply ordered, retry-safe production infrastructure migrations.",
)

ConfigOption = Annotated[
    Path,
    typer.Option(
        "--config",
        exists=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
    ),
]
StateOption = Annotated[
    Path,
    typer.Option(
        "--state",
        dir_okay=False,
        resolve_path=True,
    ),
]


def _load(*, config_path: Path) -> tuple[ProductionConfig, BotoInfrastructureAws]:
    config = ProductionConfig.from_toml(path=config_path)
    return config, BotoInfrastructureAws(region=config.region)


@app.command()
def begin(
    *,
    config_path: ConfigOption,
    commit_sha: Annotated[str, typer.Option("--commit-sha")],
    state_path: StateOption,
) -> None:
    """Validate SSM history and initialize this workflow's pending state."""
    config, aws = _load(config_path=config_path)
    begin_migration_run(
        aws=aws,
        config=config,
        commit_sha=commit_sha,
        state_path=state_path,
    )


@app.command()
def prepare(
    *,
    config_path: ConfigOption,
    boundary: Annotated[MigrationBoundary, typer.Option("--boundary")],
    state_path: StateOption,
) -> None:
    """Prepare pending migrations before a core deployment boundary."""
    config, aws = _load(config_path=config_path)
    prepare_boundary(
        aws=aws,
        config=config,
        boundary=boundary,
        state_path=state_path,
    )


@app.command()
def finalize(
    *,
    config_path: ConfigOption,
    boundary: Annotated[MigrationBoundary, typer.Option("--boundary")],
    state_path: StateOption,
) -> None:
    """Finalize pending migrations after a core deployment boundary."""
    config, aws = _load(config_path=config_path)
    finalize_boundary(
        aws=aws,
        config=config,
        boundary=boundary,
        state_path=state_path,
    )


@app.command()
def commit(
    *,
    config_path: ConfigOption,
    state_path: StateOption,
) -> None:
    """Verify and atomically record all finalized migrations in SSM."""
    config, aws = _load(config_path=config_path)
    commit_migration_run(
        aws=aws,
        config=config,
        state_path=state_path,
        applied_at=datetime.now(tz=UTC),
    )


@app.command("secure-failure")
def secure_failure(
    *,
    config_path: ConfigOption,
) -> None:
    """Leave protected resources safe after an interrupted deployment."""
    config, aws = _load(config_path=config_path)
    secure_failed_deployment(aws=aws, config=config)


@app.command("reconcile-collector-user")
def reconcile_collector_user(
    *,
    config_path: ConfigOption,
) -> None:
    """Converge the NAS source user's assume-role-only policy."""
    config, aws = _load(config_path=config_path)
    reconcile_collector_source_user(aws=aws, config=config)


@app.command("verify")
def verify(
    *,
    config_path: ConfigOption,
) -> None:
    """Verify SSM history and all applied migration postconditions."""
    config, aws = _load(config_path=config_path)
    verify_all_migrations(aws=aws, config=config)


@app.command("check-change-set")
def check_change_set(
    *,
    config_path: ConfigOption,
    change_set_arn: Annotated[str, typer.Option("--change-set-arn")],
    policy: Annotated[ChangeSetPolicy, typer.Option("--policy")],
) -> None:
    """Reject protected destructive changes before stack execution."""
    config, aws = _load(config_path=config_path)
    aws.verify_identity(expected_account_id=config.account_id)
    aws.validate_change_set(change_set_arn=change_set_arn, policy=policy)
