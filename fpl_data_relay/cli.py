import asyncio

import typer
import uvicorn

from fpl_data_relay.config import load_settings_from_environment
from fpl_data_relay.factory import build_ingestion_service, build_postgres_store
from fpl_data_relay.schemas import SCHEMA_VERSION

app = typer.Typer(no_args_is_help=True)


@app.command("config-check")
def config_check() -> None:
    load_settings_from_environment()
    typer.echo("configuration ok")


@app.command("db-apply")
def db_apply() -> None:
    asyncio.run(_db_apply())


@app.command("ingest-once")
def ingest_once() -> None:
    asyncio.run(_ingest_once())


@app.command("serve")
def serve() -> None:
    uvicorn.run(
        "fpl_data_relay.factory:create_production_app",
        factory=True,
        host="0.0.0.0",
        port=8000,
        workers=1,
    )


async def _db_apply() -> None:
    settings = load_settings_from_environment()
    store = await build_postgres_store(settings=settings)
    try:
        await store.apply_schema()
        await store.check_schema_version(expected_version=SCHEMA_VERSION)
        typer.echo(f"schema version {SCHEMA_VERSION} applied")
    finally:
        await store.close()


async def _ingest_once() -> None:
    settings = load_settings_from_environment()
    store = await build_postgres_store(settings=settings)
    service = build_ingestion_service(settings=settings, store=store)
    try:
        await store.check_schema_version(expected_version=SCHEMA_VERSION)
        result = await service.ingest_all_once()
        typer.echo(
            "ingested "
            f"changed={result.changed_count} "
            f"unchanged={result.unchanged_count} "
            f"current_event_id={result.current_event_id}"
        )
    finally:
        await service.close()
        await store.close()
