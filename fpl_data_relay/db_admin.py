"""Administrative database helpers for destructive CLI operations."""

from typing import Protocol, cast
from urllib.parse import unquote, urlsplit

import asyncpg


class MaintenanceConnection(Protocol):
    """Subset of asyncpg connection behaviour used by admin helpers."""

    async def execute(self, query: str, *arguments: object) -> str:
        """Execute a SQL statement."""
        ...

    async def fetchval(self, query: str, *arguments: object) -> object:
        """Fetch one scalar value from a SQL query."""
        ...

    async def close(self) -> None:
        """Close the connection."""
        ...


async def drop_database(
    *,
    database_url: str,
    maintenance_database_url: str,
) -> None:
    """Drop the application database using a maintenance database connection."""
    target_database = parse_database_name(database_url=database_url)
    connection = cast(
        "MaintenanceConnection",
        await asyncpg.connect(dsn=maintenance_database_url),
    )
    try:
        await connection.execute(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = $1 AND pid <> pg_backend_pid()
            """,
            target_database,
        )
        quoted_database = await connection.fetchval(
            "SELECT quote_ident($1)",
            target_database,
        )
        if not isinstance(quoted_database, str):
            raise RuntimeError("Failed to quote target database name.")
        await connection.execute(f"DROP DATABASE {quoted_database}")
    finally:
        await connection.close()


def parse_database_name(*, database_url: str) -> str:
    """Extract and validate the database name from a PostgreSQL URL."""
    parsed_url = urlsplit(database_url)
    database_name = unquote(parsed_url.path.removeprefix("/"))
    if database_name == "":
        raise ValueError("DATABASE_URL must include a database name.")
    return database_name
