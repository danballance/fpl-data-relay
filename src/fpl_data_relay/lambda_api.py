"""AWS Lambda composition for the public read API."""

from typing import cast

from mangum import Mangum

from fpl_data_relay.adapters.inbound.http.app import create_app
from fpl_data_relay.adapters.outbound.postgres.changes import (
    PostgresChangeEventRepository,
)
from fpl_data_relay.adapters.outbound.postgres.connection import PoolProtocol
from fpl_data_relay.adapters.outbound.postgres.database import PostgresDatabase
from fpl_data_relay.adapters.outbound.postgres.live import PostgresLiveRepository
from fpl_data_relay.adapters.outbound.postgres.reference import (
    PostgresReferenceRepository,
)
from fpl_data_relay.adapters.outbound.postgres.schema_manager import (
    PostgresSchemaManager,
)
from fpl_data_relay.adapters.outbound.rds_data import create_rds_data_pool
from fpl_data_relay.application.change_feed import ChangeFeed
from fpl_data_relay.application.live_queries import LiveQueries
from fpl_data_relay.application.reference_queries import ReferenceQueries
from fpl_data_relay.config import load_rds_data_settings_from_environment

SETTINGS = load_rds_data_settings_from_environment()
POOL = create_rds_data_pool(
    resource_arn=SETTINGS.resource_arn,
    secret_arn=SETTINGS.secret_arn,
    database_name=SETTINGS.database_name,
)
DATABASE = PostgresDatabase(pool=cast("PoolProtocol", POOL))


async def close_database() -> None:
    """Close runtime resources if the HTTP lifespan is used outside Lambda."""
    await DATABASE.close()


application = create_app(
    reference_queries=ReferenceQueries(
        repository=PostgresReferenceRepository(database=DATABASE),
    ),
    live_queries=LiveQueries(repository=PostgresLiveRepository(database=DATABASE)),
    change_feed=ChangeFeed(
        repository=PostgresChangeEventRepository(database=DATABASE),
    ),
    schema_manager=PostgresSchemaManager(database=DATABASE),
    ingestion_service=None,
    reference_poll_seconds=300,
    live_poll_seconds=15,
    idle_poll_seconds=60,
    start_scheduler=False,
    check_schema_on_startup=False,
    shutdown=close_database,
)

handler = Mangum(application, lifespan="off")
