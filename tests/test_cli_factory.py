import pytest
from typer.testing import CliRunner

from fpl_data_relay import cli, db_admin, factory
from fpl_data_relay.config import Settings
from fpl_data_relay.factory import build_ingestion_service, build_postgres_store
from fpl_data_relay.ingestion import IngestionService
from tests.conftest import FakeClient, FakePostgresPool, InMemoryStore


def set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://relay:relay@localhost:5432/relay")
    monkeypatch.setenv(
        "FPL_API_BASE_URL",
        "https://fantasy.premierleague.com/api",
    )
    monkeypatch.setenv("FPL_CLIENT_USER_AGENT", "fpl-data-relay-tests")
    monkeypatch.setenv("HTTP_TIMEOUT_SECONDS", "10")
    monkeypatch.setenv("REFERENCE_POLL_SECONDS", "300")
    monkeypatch.setenv("LIVE_POLL_SECONDS", "15")
    monkeypatch.setenv("IDLE_POLL_SECONDS", "120")
    monkeypatch.setenv("SSE_HEARTBEAT_SECONDS", "5")


def test_cli_config_check(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required_env(monkeypatch=monkeypatch)
    result = CliRunner().invoke(cli.app, ["config-check"])
    assert result.exit_code == 0
    assert "configuration ok" in result.stdout


def test_cli_serve_invokes_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(
        app_path: str,
        *,
        factory: bool,
        host: str,
        port: int,
        workers: int,
    ) -> None:
        calls.append(
            {
                "app_path": app_path,
                "factory": factory,
                "host": host,
                "port": port,
                "workers": workers,
            },
        )

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)
    result = CliRunner().invoke(cli.app, ["serve"])
    assert result.exit_code == 0
    assert calls[0]["factory"] is True
    assert calls[0]["port"] == 8000


@pytest.mark.asyncio
async def test_cli_db_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required_env(monkeypatch=monkeypatch)
    store = InMemoryStore()

    async def fake_build_postgres_store(*, settings: Settings) -> InMemoryStore:
        assert settings.database_url.startswith("postgresql://")
        return store

    monkeypatch.setattr(cli, "build_postgres_store", fake_build_postgres_store)
    await cli._db_apply()
    assert store.schema_applied is True
    assert store.closed is True


def test_cli_db_drop_and_create_requires_yes() -> None:
    result = CliRunner().invoke(cli.app, ["db", "drop-and-create"])
    assert result.exit_code == 1
    assert "without --yes" in result.stderr


@pytest.mark.asyncio
async def test_cli_db_drop_and_create_invokes_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_required_env(monkeypatch=monkeypatch)
    monkeypatch.setenv(
        "POSTGRES_MAINTENANCE_DATABASE_URL",
        "postgresql://relay:relay@localhost:5432/postgres",
    )
    calls: list[dict[str, str]] = []

    async def fake_drop_and_create_database(
        *,
        database_url: str,
        maintenance_database_url: str,
    ) -> None:
        calls.append(
            {
                "database_url": database_url,
                "maintenance_database_url": maintenance_database_url,
            },
        )

    monkeypatch.setattr(
        cli,
        "drop_and_create_database",
        fake_drop_and_create_database,
    )
    await cli._db_drop_and_create()
    assert calls == [
        {
            "database_url": "postgresql://relay:relay@localhost:5432/relay",
            "maintenance_database_url": (
                "postgresql://relay:relay@localhost:5432/postgres"
            ),
        },
    ]


@pytest.mark.asyncio
async def test_drop_and_create_database_uses_maintenance_connection(
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

    monkeypatch.setattr(db_admin.asyncpg, "connect", fake_connect)
    await db_admin.drop_and_create_database(
        database_url="postgresql://relay:relay@localhost:5432/relay-db",
        maintenance_database_url="postgresql://relay:relay@localhost:5432/postgres",
    )
    assert connection.closed is True
    assert connection.executed[-2][0] == 'DROP DATABASE IF EXISTS "relay-db"'
    assert connection.executed[-1][0] == 'CREATE DATABASE "relay-db"'


@pytest.mark.asyncio
async def test_cli_ingest_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required_env(monkeypatch=monkeypatch)
    store = InMemoryStore()

    async def fake_build_postgres_store(*, settings: Settings) -> InMemoryStore:
        assert settings.live_poll_seconds == 15
        return store

    def fake_build_ingestion_service(
        *,
        settings: Settings,
        store: InMemoryStore,
    ) -> IngestionService:
        assert settings.idle_poll_seconds == 120
        return IngestionService(client=FakeClient(), store=store)

    monkeypatch.setattr(cli, "build_postgres_store", fake_build_postgres_store)
    monkeypatch.setattr(cli, "build_ingestion_service", fake_build_ingestion_service)
    await cli._ingest_reference()
    assert len(store.resources) == 2
    assert store.closed is True


@pytest.mark.asyncio
async def test_cli_ingest_live(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required_env(monkeypatch=monkeypatch)
    store = InMemoryStore()
    service = IngestionService(client=FakeClient(), store=store)
    await service.ingest_reference_once()

    async def fake_build_postgres_store(*, settings: Settings) -> InMemoryStore:
        assert settings.live_poll_seconds == 15
        return store

    def fake_build_ingestion_service(
        *,
        settings: Settings,
        store: InMemoryStore,
    ) -> IngestionService:
        assert settings.idle_poll_seconds == 120
        return IngestionService(client=FakeClient(), store=store)

    monkeypatch.setattr(cli, "build_postgres_store", fake_build_postgres_store)
    monkeypatch.setattr(cli, "build_ingestion_service", fake_build_ingestion_service)
    await cli._ingest_live(target_id=None, fixture_id=None)
    assert len(store.resources) == 5
    assert store.closed is True


@pytest.mark.asyncio
async def test_build_postgres_store_uses_asyncpg_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings.model_validate(
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
    pool = FakePostgresPool()

    async def fake_create_pool(*, dsn: str) -> FakePostgresPool:
        assert dsn == settings.database_url
        return pool

    monkeypatch.setattr(factory.asyncpg, "create_pool", fake_create_pool)
    store = await build_postgres_store(settings=settings)
    assert store._pool is pool


def test_build_ingestion_service_creates_service() -> None:
    settings = Settings.model_validate(
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
    service = build_ingestion_service(
        settings=settings,
        store=InMemoryStore(),
    )
    assert isinstance(service, IngestionService)


@pytest.mark.asyncio
async def test_create_production_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_required_env(monkeypatch=monkeypatch)
    store = InMemoryStore()

    async def fake_build_postgres_store(*, settings: Settings) -> InMemoryStore:
        assert settings.sse_heartbeat_seconds == 5
        return store

    def fake_build_ingestion_service(
        *,
        settings: Settings,
        store: InMemoryStore,
    ) -> IngestionService:
        assert settings.reference_poll_seconds == 300
        return IngestionService(client=FakeClient(), store=store)

    monkeypatch.setattr(factory, "build_postgres_store", fake_build_postgres_store)
    monkeypatch.setattr(
        factory,
        "build_ingestion_service",
        fake_build_ingestion_service,
    )
    app = await factory.create_production_app()
    assert app.title == "FPL Data Relay"
