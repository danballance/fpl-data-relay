"""Database administration ports."""

from typing import Protocol


class SchemaManager(Protocol):
    """Apply and validate the application schema."""

    async def apply_schema(self) -> None: ...

    async def check_schema_version(self, *, expected_version: int) -> None: ...


class DatabaseRecreator(Protocol):
    """Destructively recreate an application database."""

    async def drop_and_create(
        self,
        *,
        database_url: str,
        maintenance_database_url: str,
    ) -> None: ...
