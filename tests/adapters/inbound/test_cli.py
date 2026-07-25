from typing import cast

import pytest
from typer.testing import CliRunner

from fpl_data_relay.adapters.inbound.cli.app import create_cli_app
from fpl_data_relay.adapters.outbound.fpl.client import FplClient
from fpl_data_relay.adapters.outbound.postgres import administration
from fpl_data_relay.adapters.outbound.postgres.database import PostgresDatabase
from fpl_data_relay.adapters.outbound.postgres.schema_manager import (
    PostgresSchemaManager,
)
from fpl_data_relay.application.change_feed import ChangeFeed
from fpl_data_relay.application.ingestion.service import (
    IngestionResult,
    IngestionService,
)
from fpl_data_relay.application.live_queries import LiveQueries
from fpl_data_relay.application.reference_queries import ReferenceQueries
from fpl_data_relay.bootstrap import RelayRuntime, build_postgres_database
from fpl_data_relay.config import Settings
from tests.conftest import FakeClient, FakePostgresPool, InMemoryStore


class FakeCliOperations:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None, int | None]] = []

    def validate_config(self) -> None:
        self.calls.append(("config", None, None))

    async def apply_schema(self) -> None:
        self.calls.append(("apply", None, None))

    async def drop_and_create_database(self) -> None:
        self.calls.append(("drop", None, None))

    async def ingest_reference(self) -> IngestionResult:
        self.calls.append(("reference", None, None))
        return ingestion_result()

    async def ingest_live(
        self,
        *,
        target_event_id: int | None,
        fixture_id: int | None,
    ) -> IngestionResult:
        self.calls.append(("live", target_event_id, fixture_id))
        return ingestion_result()

    async def serve(self) -> None:
        self.calls.append(("serve", None, None))


def ingestion_result() -> IngestionResult:
    return IngestionResult(
        changed_count=2,
        unchanged_count=1,
        season_id="2025-26",
        current_event_id=3,
        has_active_fixture=True,
    )


def settings() -> Settings:
    return Settings.model_validate(
        {
            "DATABASE_URL": "postgresql://relay:relay@localhost:5432/relay",
            "FPL_API_BASE_URL": "https://fantasy.premierleague.com/api",
            "FPL_CLIENT_USER_AGENT": "tests",
            "HTTP_TIMEOUT_SECONDS": 10,
            "REFERENCE_POLL_SECONDS": 300,
            "LIVE_POLL_SECONDS": 15,
            "IDLE_POLL_SECONDS": 120,
            "SSE_HEARTBEAT_SECONDS": 5,
        },
    )


def test_cli_preserves_config_schema_and_serve_commands() -> None:
    operations = FakeCliOperations()
    runner = CliRunner()
    app = create_cli_app(operations=operations)
    assert runner.invoke(app, ["config-check"]).output == "configuration ok\n"
    assert "schema version 3 applied" in runner.invoke(app, ["db", "apply"]).output
    assert runner.invoke(app, ["serve"]).exit_code == 0
    assert operations.calls == [
        ("config", None, None),
        ("apply", None, None),
        ("serve", None, None),
    ]


def test_cli_drop_and_create_requires_confirmation() -> None:
    operations = FakeCliOperations()
    runner = CliRunner()
    app = create_cli_app(operations=operations)
    refused = runner.invoke(app, ["db", "drop-and-create"])
    accepted = runner.invoke(app, ["db", "drop-and-create", "--yes"])
    assert refused.exit_code == 1
    assert "without --yes" in refused.output
    assert accepted.exit_code == 0
    assert operations.calls == [("drop", None, None)]


def test_cli_ingestion_commands_preserve_options_and_output() -> None:
    operations = FakeCliOperations()
    runner = CliRunner()
    app = create_cli_app(operations=operations)
    reference = runner.invoke(app, ["ingest", "reference"])
    live = runner.invoke(app, ["ingest", "live", "--target-id", "3"])
    fixture = runner.invoke(app, ["ingest", "live", "--fixture-id", "12"])
    conflict = runner.invoke(
        app,
        ["ingest", "live", "--target-id", "3", "--fixture-id", "12"],
    )
    assert "reference ingested changed=2 unchanged=1" in reference.output
    assert "live ingested changed=2 unchanged=1" in live.output
    assert fixture.exit_code == 0
    assert conflict.exit_code == 1
    assert operations.calls == [
        ("reference", None, None),
        ("live", 3, None),
        ("live", None, 12),
    ]


@pytest.mark.asyncio
async def test_database_recreator_uses_maintenance_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeMaintenanceConnection:
        def __init__(self) -> None:
            self.executed: list[tuple[str, tuple[object, ...]]] = []
            self.closed = False

        async def execute(self, query: str, *arguments: object) -> str:
            self.executed.append((query, arguments))
            return "OK"

        async def fetchval(self, query: str, *arguments: object) -> object:
            self.executed.append((query, arguments))
            return '"relay-db"'

        async def close(self) -> None:
            self.closed = True

    connection = FakeMaintenanceConnection()

    async def fake_connect(*, dsn: str) -> FakeMaintenanceConnection:
        assert dsn == "postgresql://relay:relay@localhost:5432/postgres"
        return connection

    monkeypatch.setattr(administration.asyncpg, "connect", fake_connect)
    await administration.PostgresDatabaseRecreator().drop_and_create(
        database_url="postgresql://relay:relay@localhost:5432/relay-db",
        maintenance_database_url="postgresql://relay:relay@localhost:5432/postgres",
    )
    assert connection.closed is True
    assert connection.executed[-2][0] == 'DROP DATABASE IF EXISTS "relay-db"'
    assert connection.executed[-1][0] == 'CREATE DATABASE "relay-db"'


@pytest.mark.asyncio
async def test_build_postgres_database_uses_asyncpg_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = FakePostgresPool()

    async def fake_create_pool(*, dsn: str) -> FakePostgresPool:
        assert dsn == settings().database_url
        return pool

    monkeypatch.setattr(
        "fpl_data_relay.bootstrap.asyncpg.create_pool",
        fake_create_pool,
    )
    database = await build_postgres_database(settings=settings())
    assert database._pool is pool


@pytest.mark.asyncio
async def test_relay_runtime_closes_shared_resources_exactly_once() -> None:
    store = InMemoryStore()
    client = FakeClient()
    service = IngestionService(client=client, repository=store)
    runtime = RelayRuntime(
        database=cast("PostgresDatabase", store),
        client=cast("FplClient", client),
        ingestion_service=service,
        reference_queries=ReferenceQueries(repository=store),
        live_queries=LiveQueries(repository=store),
        change_feed=ChangeFeed(repository=store),
        schema_manager=cast("PostgresSchemaManager", store),
    )
    await runtime.close()
    await runtime.close()
    assert client.closed is True
    assert store.closed is True
