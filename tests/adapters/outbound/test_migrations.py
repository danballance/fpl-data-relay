from hashlib import sha256

import pytest

from fpl_data_relay.adapters.outbound.postgres.migrations import (
    MIGRATIONS,
    AppliedMigration,
    MigrationError,
    apply_migrations,
    migration_row_values,
    migration_status,
    split_sql_statements,
    validate_migration_history,
)
from tests.conftest import FakePostgresPool


class ItemsRow:
    def __init__(self, *, values: dict[str, object]) -> None:
        self._values = values

    def items(self) -> list[tuple[str, object]]:
        return list(self._values.items())


def applied_initial() -> AppliedMigration:
    migration = MIGRATIONS[0]
    return AppliedMigration(
        version=migration.version,
        name=migration.name,
        checksum=migration.checksum,
    )


def test_migration_checksum_is_sha256() -> None:
    migration = MIGRATIONS[0]
    assert migration.checksum == sha256(migration.sql.encode()).hexdigest()


def test_community_migration_enforces_aggregate_and_immutability() -> None:
    sql = MIGRATIONS[2].sql
    assert "relay_community_reports" in sql
    assert "UNIQUE (strategy_key, report_date)" in sql
    assert "jsonb_array_length(content -> 'stories') BETWEEN 1 AND 10" in sql
    assert "relay_community_reports_immutable" in sql
    assert "BEFORE UPDATE OR DELETE" in sql

    cache_sql = MIGRATIONS[3].sql
    assert "relay_community_extraction_cache" in cache_sql
    assert "NOT (document ? 'text')" in cache_sql
    assert "relay_community_extraction_cache_insert_only" in cache_sql
    assert "BEFORE UPDATE" in cache_sql


def test_migration_row_values_accepts_asyncpg_mapping_shape() -> None:
    values: dict[str, object] = {
        "version": 1,
        "name": "initial_schema",
        "checksum": "a" * 64,
    }
    assert migration_row_values(row=ItemsRow(values=values)) == values


def test_split_sql_statements_handles_quotes_comments_and_dollar_blocks() -> None:
    statements = split_sql_statements(
        sql="""
        -- a ; comment
        SELECT ';';
        /* block ; comment */ SELECT "semi;colon";
        DO $$ BEGIN RAISE NOTICE ';'; END $$;
        """,
    )
    assert len(statements) == 3
    assert statements[0].endswith("SELECT ';'")
    assert "semi;colon" in statements[1]
    assert statements[2].startswith("DO $$")


@pytest.mark.parametrize(
    ("stored", "message"),
    [
        (
            AppliedMigration(version=999, name="future", checksum="a" * 64),
            "unknown migration",
        ),
        (
            AppliedMigration(version=1, name="renamed", checksum="a" * 64),
            "name mismatch",
        ),
        (
            AppliedMigration(
                version=1,
                name=MIGRATIONS[0].name,
                checksum="a" * 64,
            ),
            "checksum mismatch",
        ),
    ],
)
def test_validate_migration_history_rejects_inconsistent_rows(
    stored: AppliedMigration,
    message: str,
) -> None:
    with pytest.raises(MigrationError, match=message):
        validate_migration_history(applied=[stored])


def test_validate_migration_history_accepts_known_prefix() -> None:
    validate_migration_history(applied=[])
    validate_migration_history(applied=[applied_initial()])


@pytest.mark.asyncio
async def test_apply_migrations_is_ordered_and_idempotent() -> None:
    pool = FakePostgresPool()
    assert (await migration_status(pool=pool)).model_dump() == {
        "applied_versions": [],
        "pending_versions": [1, 2, 3, 4],
    }
    await apply_migrations(pool=pool)
    assert pool.schema_version == 4
    assert len(pool.applied_migrations) == 4
    await apply_migrations(pool=pool)
    assert len(pool.applied_migrations) == 4
    assert (await migration_status(pool=pool)).model_dump() == {
        "applied_versions": [1, 2, 3, 4],
        "pending_versions": [],
    }
