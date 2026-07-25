"""Composition root for production relay runtimes."""

from typing import cast

import asyncpg
import uvicorn
from fastapi import FastAPI

from fpl_data_relay.adapters.inbound.cli.app import create_cli_app
from fpl_data_relay.adapters.inbound.http.app import create_app
from fpl_data_relay.adapters.outbound.fpl.client import FplClient
from fpl_data_relay.adapters.outbound.postgres.administration import (
    PostgresDatabaseRecreator,
)
from fpl_data_relay.adapters.outbound.postgres.changes import (
    PostgresChangeEventRepository,
)
from fpl_data_relay.adapters.outbound.postgres.connection import PoolProtocol
from fpl_data_relay.adapters.outbound.postgres.database import PostgresDatabase
from fpl_data_relay.adapters.outbound.postgres.ingestion import (
    PostgresIngestionRepository,
)
from fpl_data_relay.adapters.outbound.postgres.live import PostgresLiveRepository
from fpl_data_relay.adapters.outbound.postgres.reference import (
    PostgresReferenceRepository,
)
from fpl_data_relay.adapters.outbound.postgres.schema_manager import (
    PostgresSchemaManager,
)
from fpl_data_relay.application.change_feed import ChangeFeed
from fpl_data_relay.application.database import SCHEMA_VERSION, DatabaseService
from fpl_data_relay.application.ingestion.service import (
    IngestionResult,
    IngestionService,
)
from fpl_data_relay.application.live_queries import LiveQueries
from fpl_data_relay.application.reference_queries import ReferenceQueries
from fpl_data_relay.config import (
    Settings,
    load_postgres_maintenance_database_url_from_environment,
    load_settings_from_environment,
)


class RelayRuntime:
    """Owned production resources and wired application use cases."""

    def __init__(
        self,
        *,
        database: PostgresDatabase,
        client: FplClient,
        ingestion_service: IngestionService,
        reference_queries: ReferenceQueries,
        live_queries: LiveQueries,
        change_feed: ChangeFeed,
        schema_manager: PostgresSchemaManager,
    ) -> None:
        self.database = database
        self.client = client
        self.ingestion_service = ingestion_service
        self.reference_queries = reference_queries
        self.live_queries = live_queries
        self.change_feed = change_feed
        self.schema_manager = schema_manager
        self._closed = False

    async def close(self) -> None:
        """Close each shared external resource once."""
        if self._closed:
            return
        self._closed = True
        await self.client.close()
        await self.database.close()


class SchemaRuntime:
    """Owned database resource and administration use cases."""

    def __init__(
        self,
        *,
        database: PostgresDatabase,
        database_service: DatabaseService,
    ) -> None:
        self.database_service = database_service
        self._database = database
        self._closed = False

    async def close(self) -> None:
        """Close the database resource once."""
        if self._closed:
            return
        self._closed = True
        await self._database.close()


async def build_postgres_database(*, settings: Settings) -> PostgresDatabase:
    """Create the shared PostgreSQL persistence engine."""
    pool = await asyncpg.create_pool(dsn=settings.database_url)
    if pool is None:
        raise RuntimeError("asyncpg.create_pool returned None")
    return PostgresDatabase(pool=cast("PoolProtocol", pool))


async def build_relay_runtime(*, settings: Settings) -> RelayRuntime:
    """Wire production adapters to application services."""
    database = await build_postgres_database(settings=settings)
    client = FplClient(
        base_url=str(settings.fpl_api_base_url),
        user_agent=settings.fpl_client_user_agent,
        timeout_seconds=settings.http_timeout_seconds,
    )
    ingestion_repository = PostgresIngestionRepository(database=database)
    reference_repository = PostgresReferenceRepository(database=database)
    live_repository = PostgresLiveRepository(database=database)
    change_repository = PostgresChangeEventRepository(database=database)
    return RelayRuntime(
        database=database,
        client=client,
        ingestion_service=IngestionService(
            client=client,
            repository=ingestion_repository,
        ),
        reference_queries=ReferenceQueries(repository=reference_repository),
        live_queries=LiveQueries(repository=live_repository),
        change_feed=ChangeFeed(repository=change_repository),
        schema_manager=PostgresSchemaManager(database=database),
    )


async def build_schema_runtime(*, settings: Settings) -> SchemaRuntime:
    """Wire database administration use cases with owned resources."""
    database = await build_postgres_database(settings=settings)
    return SchemaRuntime(
        database=database,
        database_service=DatabaseService(
            schema_manager=PostgresSchemaManager(database=database),
            recreator=PostgresDatabaseRecreator(),
        ),
    )


async def recreate_database(
    *,
    database_url: str,
    maintenance_database_url: str,
) -> None:
    """Run destructive recreation through the configured outbound adapter."""
    await PostgresDatabaseRecreator().drop_and_create(
        database_url=database_url,
        maintenance_database_url=maintenance_database_url,
    )


async def create_production_app() -> FastAPI:
    """Create the scheduler-enabled FastAPI app used by Uvicorn."""
    settings = load_settings_from_environment()
    runtime = await build_relay_runtime(settings=settings)
    return create_app(
        reference_queries=runtime.reference_queries,
        live_queries=runtime.live_queries,
        change_feed=runtime.change_feed,
        schema_manager=runtime.schema_manager,
        ingestion_service=runtime.ingestion_service,
        reference_poll_seconds=settings.reference_poll_seconds,
        live_poll_seconds=settings.live_poll_seconds,
        idle_poll_seconds=settings.idle_poll_seconds,
        sse_heartbeat_seconds=settings.sse_heartbeat_seconds,
        start_scheduler=True,
        shutdown=runtime.close,
    )


class ProductionCliOperations:
    """Production implementations of commands exposed by the CLI adapter."""

    def validate_config(self) -> None:
        load_settings_from_environment()

    async def apply_schema(self) -> None:
        settings = load_settings_from_environment()
        runtime = await build_schema_runtime(settings=settings)
        try:
            await runtime.database_service.apply_schema(
                expected_version=SCHEMA_VERSION,
            )
        finally:
            await runtime.close()

    async def drop_and_create_database(self) -> None:
        settings = load_settings_from_environment()
        maintenance_url = (
            load_postgres_maintenance_database_url_from_environment()
        )
        await recreate_database(
            database_url=settings.database_url,
            maintenance_database_url=maintenance_url,
        )

    async def ingest_reference(self) -> IngestionResult:
        settings = load_settings_from_environment()
        runtime = await build_relay_runtime(settings=settings)
        try:
            await runtime.schema_manager.check_schema_version(
                expected_version=SCHEMA_VERSION,
            )
            return await runtime.ingestion_service.ingest_reference_once()
        finally:
            await runtime.close()

    async def ingest_live(
        self,
        *,
        target_event_id: int | None,
        fixture_id: int | None,
    ) -> IngestionResult:
        settings = load_settings_from_environment()
        runtime = await build_relay_runtime(settings=settings)
        try:
            await runtime.schema_manager.check_schema_version(
                expected_version=SCHEMA_VERSION,
            )
            return await runtime.ingestion_service.ingest_live_once(
                target_event_id=target_event_id,
                fixture_id=fixture_id,
            )
        finally:
            await runtime.close()

    async def serve(self) -> None:
        api_app = await create_production_app()
        config = uvicorn.Config(
            app=api_app,
            host="0.0.0.0",
            port=8000,
            workers=1,
        )
        await uvicorn.Server(config=config).serve()


app = create_cli_app(operations=ProductionCliOperations())
