"""PostgreSQL schema-management adapter."""

from fpl_data_relay.adapters.outbound.postgres.database import PostgresDatabase
from fpl_data_relay.application.ports.administration import SchemaStatus


class PostgresSchemaManager:
    """Expose schema operations without the rest of the database engine."""

    def __init__(self, *, database: PostgresDatabase) -> None:
        self._database = database

    async def apply_schema(self) -> None:
        await self._database.apply_schema()

    async def check_schema_version(self, *, expected_version: int) -> None:
        await self._database.check_schema_version(expected_version=expected_version)

    async def schema_status(self) -> SchemaStatus:
        return await self._database.schema_status()
