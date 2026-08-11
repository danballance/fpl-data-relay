from typing import cast

import pytest

from fpl_data_relay.adapters.outbound.postgres.changes import (
    PostgresChangeEventRepository,
)
from fpl_data_relay.adapters.outbound.postgres.database import PostgresDatabase
from fpl_data_relay.adapters.outbound.postgres.ingestion import (
    PostgresIngestionRepository,
)
from fpl_data_relay.adapters.outbound.postgres.live import PostgresLiveRepository
from fpl_data_relay.adapters.outbound.postgres.reference import (
    PostgresReferenceRepository,
)
from fpl_data_relay.adapters.outbound.postgres.schema_manager import (
    PostgresSchemaManager,
)
from fpl_data_relay.application.change_feed import ChangeFeed
from fpl_data_relay.application.database import SCHEMA_VERSION, DatabaseService
from fpl_data_relay.application.ingestion.service import IngestionService
from fpl_data_relay.application.live_queries import LiveQueries
from fpl_data_relay.application.reference_queries import ReferenceQueries
from tests.conftest import FakeClient, InMemoryStore


@pytest.mark.asyncio
async def test_narrow_postgres_adapters_expose_complete_use_cases() -> None:
    store = InMemoryStore()
    database = cast("PostgresDatabase", store)
    ingestion = IngestionService(
        client=FakeClient(),
        repository=PostgresIngestionRepository(database=database),
    )
    await ingestion.ingest_all_once()

    reference = ReferenceQueries(
        repository=PostgresReferenceRepository(database=database),
    )
    assert len(await reference.list_seasons()) == 1
    assert await reference.get_current_season() is not None
    assert await reference.get_season(season_id="2025-26") is not None
    assert await reference.get_current_event(season_id="2025-26") is not None
    assert len(await reference.list_events(season_id="2025-26")) == 2
    assert await reference.get_event(season_id="2025-26", event_id=1) is not None
    assert await reference.list_phases(season_id="2025-26") == []
    assert len(await reference.list_teams(season_id="2025-26")) == 2
    assert await reference.get_team(season_id="2025-26", team_id=1) is not None
    assert len(await reference.list_element_types(season_id="2025-26")) == 1
    assert len(
        await reference.list_elements(
            season_id="2025-26",
            after_id=0,
            limit=100,
        ),
    ) == 1
    assert (
        await reference.get_element(season_id="2025-26", element_id=1)
        is not None
    )
    assert len(
        await reference.list_fixtures(
            season_id="2025-26",
            event_id=None,
            after_id=0,
            limit=100,
        ),
    ) == 1
    assert (
        await reference.get_fixture(season_id="2025-26", fixture_id=1)
        is not None
    )

    live = LiveQueries(repository=PostgresLiveRepository(database=database))
    assert await live.get_event_status(season_id="2025-26") is not None
    assert len(
        await live.list_live_elements(
            season_id="2025-26",
            event_id=1,
            after_id=0,
            limit=100,
        ),
    ) == 1
    assert (
        await live.get_live_element(
            season_id="2025-26",
            event_id=1,
            element_id=1,
        )
        is not None
    )

    changes = ChangeFeed(
        repository=PostgresChangeEventRepository(database=database),
    )
    events = await changes.list_events(after_id=0, limit=100)
    assert len(events) == 1
    assert events[0].entity_family.value == "fixtures"


@pytest.mark.asyncio
async def test_schema_and_database_services_delegate_to_narrow_adapters() -> None:
    class FakeRecreator:
        def __init__(self) -> None:
            self.urls: tuple[str, str] | None = None

        async def drop_and_create(
            self,
            *,
            database_url: str,
            maintenance_database_url: str,
        ) -> None:
            self.urls = (database_url, maintenance_database_url)

    store = InMemoryStore()
    schema = PostgresSchemaManager(database=cast("PostgresDatabase", store))
    recreator = FakeRecreator()
    service = DatabaseService(schema_manager=schema, recreator=recreator)
    await service.apply_schema(expected_version=SCHEMA_VERSION)
    await service.check_schema(expected_version=SCHEMA_VERSION)
    await service.drop_and_create(
        database_url="postgresql://localhost/relay",
        maintenance_database_url="postgresql://localhost/postgres",
    )
    assert store.schema_applied is True
    assert recreator.urls == (
        "postgresql://localhost/relay",
        "postgresql://localhost/postgres",
    )
