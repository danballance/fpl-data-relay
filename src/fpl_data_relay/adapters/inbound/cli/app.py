"""Typer command line adapter for relay operations."""

import asyncio
import time
from datetime import datetime
from typing import Annotated, Protocol

import typer

from fpl_data_relay.application.database import SCHEMA_VERSION
from fpl_data_relay.application.errors import DatabaseWakingError
from fpl_data_relay.application.ingestion.service import IngestionResult
from fpl_data_relay.application.ports.administration import SchemaStatus
from fpl_data_relay.domain.community import CommunityReport


class CliOperations(Protocol):
    """Application operations driven by CLI commands."""

    def validate_config(self) -> None: ...

    async def apply_schema(self) -> None: ...

    async def schema_status(self) -> SchemaStatus: ...

    async def drop_and_create_database(self) -> None: ...

    async def ingest_reference(self) -> IngestionResult: ...

    async def ingest_live(
        self,
        *,
        target_event_id: int | None,
        fixture_id: int | None,
    ) -> IngestionResult: ...

    def validate_community_config(self) -> int: ...

    async def run_community(
        self,
        *,
        strategy_key: str,
        scheduled_at: datetime,
    ) -> CommunityReport: ...

    async def serve(self) -> None: ...


def create_cli_app(*, operations: CliOperations) -> typer.Typer:
    """Create the CLI around injected production operations."""
    app = typer.Typer(no_args_is_help=True)
    db_app = typer.Typer(no_args_is_help=True)
    ingest_app = typer.Typer(no_args_is_help=True)
    community_app = typer.Typer(no_args_is_help=True)
    app.add_typer(db_app, name="db")
    app.add_typer(ingest_app, name="ingest")
    app.add_typer(community_app, name="community")

    @app.command("config-check")
    def config_check() -> None:
        """Validate required environment configuration."""
        operations.validate_config()
        typer.echo("configuration ok")

    @db_app.command("apply")
    def db_apply() -> None:
        """Apply the database schema and verify its version."""
        asyncio.run(operations.apply_schema())
        typer.echo(f"schema version {SCHEMA_VERSION} applied")

    @db_app.command("status")
    def db_status() -> None:
        """Validate and display applied and pending migrations."""
        status = asyncio.run(operations.schema_status())
        applied = ",".join(str(version) for version in status.applied_versions)
        pending = ",".join(str(version) for version in status.pending_versions)
        typer.echo(f"applied=[{applied}] pending=[{pending}]")

    @db_app.command("wait-ready")
    def db_wait_ready(
        *,
        attempts: Annotated[
            int,
            typer.Option(
                "--attempts",
                min=1,
                help="Maximum database readiness attempts.",
            ),
        ],
        interval_seconds: Annotated[
            int,
            typer.Option(
                "--interval-seconds",
                min=1,
                help="Seconds between Aurora resume attempts.",
            ),
        ],
    ) -> None:
        """Wait for an auto-paused Aurora database to resume."""
        for attempt in range(1, attempts + 1):
            try:
                asyncio.run(operations.schema_status())
            except DatabaseWakingError:
                typer.echo(f"database waking attempt={attempt}/{attempts}")
                if attempt == attempts:
                    raise
                time.sleep(interval_seconds)
            else:
                typer.echo(f"database ready attempt={attempt}/{attempts}")
                return
        raise AssertionError("Database readiness loop completed unexpectedly.")

    @db_app.command("drop-and-create")
    def db_drop_and_create(
        *,
        yes: Annotated[
            bool,
            typer.Option(
                "--yes",
                help="Confirm irreversible database drop and create.",
            ),
        ] = False,
    ) -> None:
        """Drop and recreate the configured application database."""
        if not yes:
            typer.echo(
                "Refusing to drop and create database without --yes.",
                err=True,
            )
            raise typer.Exit(code=1)
        asyncio.run(operations.drop_and_create_database())
        typer.echo("database dropped and created")

    @ingest_app.command("reference")
    def ingest_reference() -> None:
        """Run one reference ingestion cycle."""
        result = asyncio.run(operations.ingest_reference())
        echo_ingestion_result(label="reference ingested", result=result)

    @ingest_app.command("live")
    def ingest_live(
        *,
        target_id: Annotated[
            int | None,
            typer.Option(
                "--target-id",
                min=1,
                help="FPL event/gameweek id to ingest.",
            ),
        ] = None,
        fixture_id: Annotated[
            int | None,
            typer.Option(
                "--fixture-id",
                min=1,
                help="Fixture id whose event/gameweek should be ingested.",
            ),
        ] = None,
    ) -> None:
        """Run one live ingestion cycle."""
        if target_id is not None and fixture_id is not None:
            typer.echo(
                "Provide either --target-id or --fixture-id, not both.",
                err=True,
            )
            raise typer.Exit(code=1)
        result = asyncio.run(
            operations.ingest_live(
                target_event_id=target_id,
                fixture_id=fixture_id,
            ),
        )
        echo_ingestion_result(label="live ingested", result=result)

    @app.command("serve")
    def serve() -> None:
        """Start the production FastAPI server."""
        asyncio.run(operations.serve())

    @community_app.command("validate-config")
    def validate_community_config() -> None:
        """Validate the packaged strategy and source manifest without network calls."""
        count = operations.validate_community_config()
        typer.echo(f"community configuration ok strategies={count}")

    @community_app.command("run")
    def run_community(
        *,
        strategy_key: Annotated[
            str,
            typer.Option("--strategy-key", help="Packaged community strategy key."),
        ],
        scheduled_at: Annotated[
            str,
            typer.Option(
                "--scheduled-at",
                help="Timezone-aware ISO-8601 scheduled invocation timestamp.",
            ),
        ],
    ) -> None:
        """Run one idempotent manual community report."""
        try:
            parsed_scheduled_at = datetime.fromisoformat(scheduled_at)
        except ValueError as error:
            raise typer.BadParameter(
                "--scheduled-at must be an ISO-8601 timestamp.",
            ) from error
        if (
            parsed_scheduled_at.tzinfo is None
            or parsed_scheduled_at.utcoffset() is None
        ):
            raise typer.BadParameter("--scheduled-at must include a UTC offset.")
        report = asyncio.run(
            operations.run_community(
                strategy_key=strategy_key,
                scheduled_at=parsed_scheduled_at,
            ),
        )
        typer.echo(
            f"community report id={report.id} strategy={report.strategy_key} "
            f"date={report.report_date} stories={len(report.content.stories)}",
        )

    return app


def echo_ingestion_result(*, label: str, result: IngestionResult) -> None:
    """Print a concise ingestion result summary."""
    typer.echo(
        f"{label} "
        f"changed={result.changed_count} "
        f"unchanged={result.unchanged_count} "
        f"season_id={result.season_id} "
        f"current_event_id={result.current_event_id}",
    )
