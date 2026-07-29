"""Ordered, checksum-validated PostgreSQL migrations."""

from collections.abc import Iterable, Mapping
from hashlib import sha256
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict

from fpl_data_relay.adapters.outbound.postgres.connection import PoolProtocol
from fpl_data_relay.adapters.outbound.postgres.schema import SCHEMA_SQL
from fpl_data_relay.application.ports.administration import SchemaStatus

MIGRATION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS relay_schema_migrations (
    version integer PRIMARY KEY,
    name text NOT NULL UNIQUE,
    checksum text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT relay_schema_migrations_version_positive CHECK (version > 0),
    CONSTRAINT relay_schema_migrations_checksum_sha256 CHECK (
        length(checksum) = 64
    )
)
"""
MIGRATION_TABLE_LOOKUP_SQL = (
    "SELECT to_regclass('relay_schema_migrations')::text AS migration_table"
)


class Migration(BaseModel):
    """One immutable database migration."""

    model_config = ConfigDict(frozen=True)

    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        """Return the stable SHA-256 checksum of the SQL body."""
        return sha256(self.sql.encode("utf-8")).hexdigest()


class AppliedMigration(BaseModel):
    """Migration metadata stored in PostgreSQL."""

    model_config = ConfigDict(frozen=True)

    version: int
    name: str
    checksum: str


class RowProtocol(Protocol):
    """Mapping-like row shape returned by asyncpg."""

    def items(self) -> Iterable[tuple[str, object]]: ...


MIGRATIONS = (
    Migration(version=1, name="initial_schema", sql=SCHEMA_SQL.strip()),
)


class MigrationError(RuntimeError):
    """Raised when migration history is inconsistent."""


async def apply_migrations(*, pool: PoolProtocol) -> None:
    """Validate migration history and apply every pending migration in order."""
    await ensure_migration_table(pool=pool)
    applied = await read_applied_migrations(pool=pool)
    validate_migration_history(applied=applied)
    applied_versions = {migration.version for migration in applied}
    for migration in MIGRATIONS:
        if migration.version in applied_versions:
            continue
        async with pool.acquire() as connection, connection.transaction():
            for statement in split_sql_statements(sql=migration.sql):
                await connection.execute(statement)
            await connection.execute(
                """
                INSERT INTO relay_schema_migrations (version, name, checksum)
                VALUES ($1, $2, $3)
                """,
                migration.version,
                migration.name,
                migration.checksum,
            )


async def migration_status(*, pool: PoolProtocol) -> SchemaStatus:
    """Return validated applied and pending migration versions without mutation."""
    async with pool.acquire() as connection:
        migration_table = await connection.fetchval(MIGRATION_TABLE_LOOKUP_SQL)
    if migration_table is None:
        applied: list[AppliedMigration] = []
    else:
        applied = await read_applied_migrations(pool=pool)
    validate_migration_history(applied=applied)
    applied_versions = [migration.version for migration in applied]
    return SchemaStatus(
        applied_versions=applied_versions,
        pending_versions=[
            migration.version
            for migration in MIGRATIONS
            if migration.version not in applied_versions
        ],
    )


async def ensure_migration_table(*, pool: PoolProtocol) -> None:
    """Create the migration history table before reading it."""
    async with pool.acquire() as connection:
        await connection.execute(MIGRATION_TABLE_SQL)


async def read_applied_migrations(*, pool: PoolProtocol) -> list[AppliedMigration]:
    """Return stored migration history ordered by version."""
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT version, name, checksum
            FROM relay_schema_migrations
            ORDER BY version
            """,
        )
    migrations: list[AppliedMigration] = []
    for row in rows:
        migrations.append(
            AppliedMigration.model_validate(
                migration_row_values(row=row),
            ),
        )
    return migrations


def migration_row_values(*, row: object) -> dict[str, object]:
    """Normalize a mapping-like migration row into a plain dictionary."""
    if isinstance(row, Mapping):
        mapping = cast("Mapping[str, object]", row)
        return {key: mapping[key] for key in mapping}
    if hasattr(row, "items"):
        mapping_like = cast("RowProtocol", row)
        return {key: value for key, value in mapping_like.items()}
    raise TypeError(f"Unsupported migration row type: {type(row).__name__}.")


def validate_migration_history(*, applied: list[AppliedMigration]) -> None:
    """Reject unknown, renamed, reordered, or modified migrations."""
    known = {migration.version: migration for migration in MIGRATIONS}
    for stored in applied:
        expected = known.get(stored.version)
        if expected is None:
            raise MigrationError(
                f"Database contains unknown migration version {stored.version}.",
            )
        if stored.name != expected.name:
            raise MigrationError(
                f"Migration {stored.version} name mismatch: "
                f"expected {expected.name!r}, found {stored.name!r}.",
            )
        if stored.checksum != expected.checksum:
            raise MigrationError(
                f"Migration {stored.version} checksum mismatch.",
            )
    expected_prefix = [migration.version for migration in MIGRATIONS[: len(applied)]]
    stored_versions = [migration.version for migration in applied]
    if stored_versions != expected_prefix:
        raise MigrationError(
            "Applied migrations are not a contiguous prefix of known migrations.",
        )


def split_sql_statements(*, sql: str) -> list[str]:
    """Split migration SQL without breaking quoted strings or comments."""
    statements: list[str] = []
    current: list[str] = []
    index = 0
    quote: str | None = None
    dollar_tag: str | None = None
    line_comment = False
    block_comment = False
    while index < len(sql):
        character = sql[index]
        following = sql[index + 1] if index + 1 < len(sql) else ""
        if line_comment:
            current.append(character)
            if character == "\n":
                line_comment = False
            index += 1
            continue
        if block_comment:
            current.append(character)
            if character == "*" and following == "/":
                current.append(following)
                block_comment = False
                index += 2
            else:
                index += 1
            continue
        if quote is not None:
            current.append(character)
            if character == quote:
                if following == quote:
                    current.append(following)
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if dollar_tag is not None:
            if sql.startswith(dollar_tag, index):
                current.append(dollar_tag)
                index += len(dollar_tag)
                dollar_tag = None
            else:
                current.append(character)
                index += 1
            continue
        if character == "-" and following == "-":
            current.extend([character, following])
            line_comment = True
            index += 2
            continue
        if character == "/" and following == "*":
            current.extend([character, following])
            block_comment = True
            index += 2
            continue
        if character in {"'", '"'}:
            quote = character
            current.append(character)
            index += 1
            continue
        if character == "$":
            tag_end = sql.find("$", index + 1)
            if tag_end != -1:
                candidate = sql[index : tag_end + 1]
                if candidate == "$$" or candidate[1:-1].replace("_", "").isalnum():
                    dollar_tag = candidate
                    current.append(candidate)
                    index = tag_end + 1
                    continue
        if character == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
            index += 1
            continue
        current.append(character)
        index += 1
    trailing = "".join(current).strip()
    if trailing:
        statements.append(trailing)
    return statements
