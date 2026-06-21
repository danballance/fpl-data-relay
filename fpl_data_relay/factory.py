"""Production object factory for the relay application."""

import asyncpg
from fastapi import FastAPI

from fpl_data_relay.api import create_app
from fpl_data_relay.config import Settings, load_settings_from_environment
from fpl_data_relay.fpl_client import FplClient
from fpl_data_relay.ingestion import IngestionService
from fpl_data_relay.store import PostgresStore, ResourceStore


async def build_postgres_store(*, settings: Settings) -> PostgresStore:
    """Create a Postgres-backed resource store from validated settings."""
    pool = await asyncpg.create_pool(dsn=settings.database_url)
    if pool is None:
        raise RuntimeError("asyncpg.create_pool returned None")
    return PostgresStore(pool=pool)


def build_ingestion_service(
    *,
    settings: Settings,
    store: ResourceStore,
) -> IngestionService:
    """Wire the FPL HTTP client to the ingestion service."""
    client = FplClient(
        base_url=str(settings.fpl_api_base_url),
        user_agent=settings.fpl_client_user_agent,
        timeout_seconds=settings.http_timeout_seconds,
    )
    return IngestionService(client=client, store=store)


async def create_production_app() -> FastAPI:
    """Create the scheduler-enabled FastAPI app used by Uvicorn."""
    settings = load_settings_from_environment()
    store = await build_postgres_store(settings=settings)
    ingestion_service = build_ingestion_service(settings=settings, store=store)
    return create_app(
        settings=settings,
        store=store,
        ingestion_service=ingestion_service,
        start_scheduler=True,
    )
