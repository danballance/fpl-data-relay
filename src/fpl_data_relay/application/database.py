"""Database administration use cases."""

from fpl_data_relay.application.ports.administration import (
    DatabaseRecreator,
    SchemaManager,
    SchemaStatus,
)

SCHEMA_VERSION = 5


class DatabaseService:
    """Coordinate schema and destructive database operations."""

    def __init__(
        self,
        *,
        schema_manager: SchemaManager,
        recreator: DatabaseRecreator,
    ) -> None:
        self._schema_manager = schema_manager
        self._recreator = recreator

    async def apply_schema(self, *, expected_version: int) -> None:
        await self._schema_manager.apply_schema()
        await self._schema_manager.check_schema_version(
            expected_version=expected_version,
        )

    async def check_schema(self, *, expected_version: int) -> None:
        await self._schema_manager.check_schema_version(
            expected_version=expected_version,
        )

    async def schema_status(self) -> SchemaStatus:
        return await self._schema_manager.schema_status()

    async def drop_and_create(
        self,
        *,
        database_url: str,
        maintenance_database_url: str,
    ) -> None:
        await self._recreator.drop_and_create(
            database_url=database_url,
            maintenance_database_url=maintenance_database_url,
        )
