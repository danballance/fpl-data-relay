"""Composition root for production relay runtimes."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import cast
from zoneinfo import ZoneInfo

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
from fpl_data_relay.adapters.outbound.postgres.community import (
    PostgresCommunityReportRepository,
)
from fpl_data_relay.adapters.outbound.postgres.community_cache import (
    PostgresCommunityExtractionCacheRepository,
)
from fpl_data_relay.adapters.outbound.postgres.connection import PoolProtocol
from fpl_data_relay.adapters.outbound.postgres.database import PostgresDatabase
from fpl_data_relay.adapters.outbound.postgres.ingestion import (
    PostgresIngestionRepository,
)
from fpl_data_relay.adapters.outbound.postgres.live import PostgresLiveRepository
from fpl_data_relay.adapters.outbound.postgres.rebaseline import (
    PostgresChangeFeedRebaseliner,
)
from fpl_data_relay.adapters.outbound.postgres.reference import (
    PostgresReferenceRepository,
)
from fpl_data_relay.adapters.outbound.postgres.schema_manager import (
    PostgresSchemaManager,
)
from fpl_data_relay.adapters.outbound.rds_data import create_rds_data_pool
from fpl_data_relay.application.change_feed import ChangeFeed
from fpl_data_relay.application.community_jobs import CommunityStrategyJob
from fpl_data_relay.application.community_queries import CommunityQueries
from fpl_data_relay.application.community_strategies import load_strategy_registry
from fpl_data_relay.application.database import SCHEMA_VERSION, DatabaseService
from fpl_data_relay.application.ingestion.service import (
    IngestionResult,
    IngestionService,
)
from fpl_data_relay.application.live_queries import LiveQueries
from fpl_data_relay.application.ports.administration import (
    ChangeFeedRebaselineResult,
    SchemaStatus,
)
from fpl_data_relay.application.rebaseline import ChangeFeedRebaselineService
from fpl_data_relay.application.reference_queries import ReferenceQueries
from fpl_data_relay.application.request_pacing import EvenlySpacedRequestPacer
from fpl_data_relay.config import (
    Settings,
    load_community_credentials_from_environment,
    load_postgres_maintenance_database_url_from_environment,
    load_rds_data_settings_from_environment,
    load_settings_from_environment,
)
from fpl_data_relay.domain.community import CommunityReport


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
        community_queries: CommunityQueries,
        schema_manager: PostgresSchemaManager,
    ) -> None:
        self.database = database
        self.client = client
        self.ingestion_service = ingestion_service
        self.reference_queries = reference_queries
        self.live_queries = live_queries
        self.change_feed = change_feed
        self.community_queries = community_queries
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


async def build_database_from_environment() -> PostgresDatabase:
    """Build the explicitly selected database executor."""
    executor = os.environ.get("DATABASE_EXECUTOR")
    if executor == "asyncpg":
        return await build_postgres_database(settings=load_settings_from_environment())
    if executor == "rds_data":
        settings = load_rds_data_settings_from_environment()
        pool = create_rds_data_pool(
            resource_arn=settings.resource_arn,
            secret_arn=settings.secret_arn,
            database_name=settings.database_name,
        )
        return PostgresDatabase(pool=cast("PoolProtocol", pool))
    raise RuntimeError(
        "DATABASE_EXECUTOR must be exactly 'asyncpg' or 'rds_data'.",
    )


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
        community_queries=CommunityQueries(
            repository=PostgresCommunityReportRepository(database=database),
            registry=load_strategy_registry(),
        ),
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


async def create_local_app() -> FastAPI:
    """Create the scheduler-enabled FastAPI app used by local Uvicorn."""
    settings = load_settings_from_environment()
    runtime = await build_relay_runtime(settings=settings)
    return create_app(
        reference_queries=runtime.reference_queries,
        live_queries=runtime.live_queries,
        change_feed=runtime.change_feed,
        community_queries=runtime.community_queries,
        schema_manager=runtime.schema_manager,
        ingestion_service=runtime.ingestion_service,
        reference_poll_seconds=settings.reference_poll_seconds,
        live_poll_seconds=settings.live_poll_seconds,
        idle_poll_seconds=settings.idle_poll_seconds,
        start_scheduler=True,
        check_schema_on_startup=True,
        shutdown=runtime.close,
    )


class ProductionCliOperations:
    """Production implementations of commands exposed by the CLI adapter."""

    def validate_config(self) -> None:
        executor = os.environ.get("DATABASE_EXECUTOR")
        if executor == "asyncpg":
            load_settings_from_environment()
        elif executor == "rds_data":
            load_rds_data_settings_from_environment()
        else:
            raise RuntimeError(
                "DATABASE_EXECUTOR must be exactly 'asyncpg' or 'rds_data'.",
            )

    async def apply_schema(self) -> None:
        database = await build_database_from_environment()
        runtime = SchemaRuntime(
            database=database,
            database_service=DatabaseService(
                schema_manager=PostgresSchemaManager(database=database),
                recreator=PostgresDatabaseRecreator(),
            ),
        )
        try:
            await runtime.database_service.apply_schema(
                expected_version=SCHEMA_VERSION,
            )
        finally:
            await runtime.close()

    async def schema_status(self) -> SchemaStatus:
        database = await build_database_from_environment()
        runtime = SchemaRuntime(
            database=database,
            database_service=DatabaseService(
                schema_manager=PostgresSchemaManager(database=database),
                recreator=PostgresDatabaseRecreator(),
            ),
        )
        try:
            return await runtime.database_service.schema_status()
        finally:
            await runtime.close()

    async def drop_and_create_database(self) -> None:
        if os.environ.get("DATABASE_EXECUTOR") != "asyncpg":
            raise RuntimeError(
                "db drop-and-create is available only for the asyncpg executor.",
            )
        settings = load_settings_from_environment()
        maintenance_url = (
            load_postgres_maintenance_database_url_from_environment()
        )
        await recreate_database(
            database_url=settings.database_url,
            maintenance_database_url=maintenance_url,
        )

    async def rebaseline_current_change_feed(
        self,
        *,
        reason: str,
    ) -> ChangeFeedRebaselineResult:
        database = await build_database_from_environment()
        try:
            await PostgresSchemaManager(database=database).check_schema_version(
                expected_version=SCHEMA_VERSION,
            )
            return await ChangeFeedRebaselineService(
                rebaseliner=PostgresChangeFeedRebaseliner(database=database),
            ).rebaseline_current(reason=reason)
        finally:
            await database.close()

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

    def validate_community_config(self) -> int:
        return len(load_strategy_registry().list_definitions())

    async def run_community(
        self,
        *,
        strategy_key: str,
        scheduled_at: datetime,
    ) -> CommunityReport:
        import httpx
        from openai import AsyncOpenAI

        from fpl_data_relay.adapters.outbound.community_sources import (
            CommunityHttpSourceGateway,
        )
        from fpl_data_relay.adapters.outbound.openai_community import (
            OpenAICommunityAnalyzer,
        )
        from fpl_data_relay.application.community_ranking import (
            CommunityMomentumRankingPolicy,
        )
        from fpl_data_relay.application.community_service import CommunityService

        registry = load_strategy_registry()
        definition = registry.require(strategy_key=strategy_key).definition
        credentials = load_community_credentials_from_environment()
        database = await build_database_from_environment()
        gateway = CommunityHttpSourceGateway(
            credentials=credentials,
            client=httpx.AsyncClient(),
            supadata_pacer=EvenlySpacedRequestPacer(
                requests_per_second=definition.supadata_requests_per_second,
                monotonic_clock=monotonic,
                sleeper=asyncio.sleep,
            ),
        )
        analyzer = OpenAICommunityAnalyzer(
            client=AsyncOpenAI(api_key=credentials.openai_api_key),
        )
        window_end = scheduled_at.astimezone(UTC)
        job = CommunityStrategyJob(
            version=1,
            kind="community_strategy",
            strategy_key=definition.key,
            strategy_version=definition.version,
            report_date=scheduled_at.astimezone(
                ZoneInfo(definition.schedule_timezone),
            ).date(),
            window_start=window_end - timedelta(days=definition.lookback_days),
            window_end=window_end,
        )
        try:
            await PostgresSchemaManager(database=database).check_schema_version(
                expected_version=SCHEMA_VERSION,
            )
            return await CommunityService(
                registry=registry,
                source_gateway=gateway,
                analyzer=analyzer,
                ranking_policy=CommunityMomentumRankingPolicy(),
                reports=PostgresCommunityReportRepository(database=database),
                extraction_cache=(
                    PostgresCommunityExtractionCacheRepository(database=database)
                ),
                references=PostgresReferenceRepository(database=database),
                clock=lambda: datetime.now(tz=UTC),
            ).run(job=job)
        finally:
            try:
                await analyzer.close()
            finally:
                try:
                    await gateway.close()
                finally:
                    await database.close()

    async def serve(self) -> None:
        api_app = await create_local_app()
        config = uvicorn.Config(
            app=api_app,
            host="0.0.0.0",
            port=8000,
            workers=1,
        )
        await uvicorn.Server(config=config).serve()


app = create_cli_app(operations=ProductionCliOperations())
