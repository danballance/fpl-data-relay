"""Typer command line interface for relay operations."""

import asyncio
from typing import Annotated

import typer
import uvicorn

from fpl_data_relay.config import (
    load_postgres_maintenance_database_url_from_environment,
    load_settings_from_environment,
)
from fpl_data_relay.db_admin import drop_and_create_database
from fpl_data_relay.factory import build_ingestion_service, build_postgres_store
from fpl_data_relay.ingestion import IngestionResult
from fpl_data_relay.schemas import SCHEMA_VERSION

app = typer.Typer(no_args_is_help=True)
db_app = typer.Typer(no_args_is_help=True)
ingest_app = typer.Typer(no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(ingest_app, name="ingest")


@app.command("config-check")
def config_check() -> None:
    """Validate required environment configuration."""
    load_settings_from_environment()
    typer.echo("configuration ok")


@db_app.command("apply")
def db_apply() -> None:
    """Apply the database schema and verify its version."""
    asyncio.run(_db_apply())


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
        typer.echo("Refusing to drop and create database without --yes.", err=True)
        raise typer.Exit(code=1)
    asyncio.run(_db_drop_and_create())


@ingest_app.command("reference")
def ingest_reference() -> None:
    """Run one reference ingestion cycle."""
    asyncio.run(_ingest_reference())


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
        typer.echo("Provide either --target-id or --fixture-id, not both.", err=True)
        raise typer.Exit(code=1)
    asyncio.run(_ingest_live(target_id=target_id, fixture_id=fixture_id))


@app.command("serve")
def serve() -> None:
    """Start the production FastAPI server."""
    uvicorn.run(
        "fpl_data_relay.factory:create_production_app",
        factory=True,
        host="0.0.0.0",
        port=8000,
        workers=1,
    )


async def _db_apply() -> None:
    """Async implementation for applying schema migrations."""
    settings = load_settings_from_environment()
    store = await build_postgres_store(settings=settings)
    try:
        await store.apply_schema()
        await store.check_schema_version(expected_version=SCHEMA_VERSION)
        typer.echo(f"schema version {SCHEMA_VERSION} applied")
    finally:
        await store.close()


async def _db_drop_and_create() -> None:
    """Async implementation for recreating the configured app database."""
    settings = load_settings_from_environment()
    maintenance_database_url = load_postgres_maintenance_database_url_from_environment()
    await drop_and_create_database(
        database_url=settings.database_url,
        maintenance_database_url=maintenance_database_url,
    )
    typer.echo("database dropped and created")


async def _ingest_reference() -> None:
    """Async implementation for one reference ingestion run."""
    settings = load_settings_from_environment()
    store = await build_postgres_store(settings=settings)
    service = build_ingestion_service(settings=settings, store=store)
    try:
        await store.check_schema_version(expected_version=SCHEMA_VERSION)
        result = await service.ingest_reference_once()
        echo_ingestion_result(label="reference ingested", result=result)
    finally:
        await service.close()
        await store.close()


async def _ingest_live(*, target_id: int | None, fixture_id: int | None) -> None:
    """Async implementation for one live ingestion run."""
    settings = load_settings_from_environment()
    store = await build_postgres_store(settings=settings)
    service = build_ingestion_service(settings=settings, store=store)
    try:
        await store.check_schema_version(expected_version=SCHEMA_VERSION)
        result = await service.ingest_live_once(
            target_event_id=target_id,
            fixture_id=fixture_id,
        )
        echo_ingestion_result(label="live ingested", result=result)
    finally:
        await service.close()
        await store.close()


def echo_ingestion_result(*, label: str, result: IngestionResult) -> None:
    """Print a concise ingestion result summary."""
    typer.echo(
        f"{label} "
        f"changed={result.changed_count} "
        f"unchanged={result.unchanged_count} "
        f"current_event_id={result.current_event_id}",
    )
