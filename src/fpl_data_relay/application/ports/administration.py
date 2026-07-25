"""Database administration ports."""

from typing import Protocol

from pydantic import BaseModel, ConfigDict


class SchemaStatus(BaseModel):
    """Validated migration state exposed to administration clients."""

    model_config = ConfigDict(frozen=True)

    applied_versions: list[int]
    pending_versions: list[int]


class SchemaManager(Protocol):
    """Apply and validate the application schema."""

    async def apply_schema(self) -> None: ...

    async def check_schema_version(self, *, expected_version: int) -> None: ...

    async def schema_status(self) -> SchemaStatus: ...


class DatabaseRecreator(Protocol):
    """Destructively recreate an application database."""

    async def drop_and_create(
        self,
        *,
        database_url: str,
        maintenance_database_url: str,
    ) -> None: ...
