import pytest

from fpl_data_relay.ingestion import (
    IngestionResult,
    IngestionService,
    RelayScheduler,
    has_active_fixture,
    select_current_event_id,
)
from fpl_data_relay.resources import ResourceKey
from fpl_data_relay.upstream_models import BootstrapStatic, Fixture
from tests.conftest import FakeClient, InMemoryStore, bootstrap_payload


def test_current_event_selection_succeeds_with_one_current_event() -> None:
    bootstrap = BootstrapStatic.model_validate(bootstrap_payload(current_ids=[2]))
    assert select_current_event_id(bootstrap=bootstrap) == 2


@pytest.mark.parametrize("current_ids", [[], [1, 2]])
def test_current_event_selection_fails_without_exactly_one_current_event(
    current_ids: list[int],
) -> None:
    bootstrap = BootstrapStatic.model_validate(
        bootstrap_payload(current_ids=current_ids),
    )
    with pytest.raises(ValueError, match="Expected exactly one current FPL event"):
        select_current_event_id(bootstrap=bootstrap)


@pytest.mark.asyncio
async def test_ingest_all_once_writes_five_resources() -> None:
    store = InMemoryStore()
    service = IngestionService(client=FakeClient(), store=store)
    result = await service.ingest_all_once()
    assert result.changed_count == 5
    assert result.unchanged_count == 0
    assert result.current_event_id == 1
    assert result.has_active_fixture is True
    assert set(store.resources) == set(ResourceKey)


@pytest.mark.asyncio
async def test_reference_and_live_ingestion_can_run_separately() -> None:
    store = InMemoryStore()
    service = IngestionService(client=FakeClient(), store=store)
    reference = await service.ingest_reference_once()
    live = await service.ingest_live_once()
    assert reference.changed_count == 2
    assert live.changed_count == 3
    assert ResourceKey.EVENT_LIVE in store.resources


@pytest.mark.asyncio
async def test_live_ingestion_fails_before_bootstrap_exists() -> None:
    service = IngestionService(client=FakeClient(), store=InMemoryStore())
    with pytest.raises(RuntimeError, match="before bootstrap exists"):
        await service.ingest_live_once()


@pytest.mark.asyncio
async def test_same_payload_hash_does_not_duplicate_change_events() -> None:
    store = InMemoryStore()
    service = IngestionService(client=FakeClient(), store=store)
    first = await service.ingest_all_once()
    second = await service.ingest_all_once()
    assert first.changed_count == 5
    assert second.changed_count == 0
    assert second.unchanged_count == 5
    assert len(store.events) == 5


@pytest.mark.asyncio
async def test_changed_payload_creates_new_change_event() -> None:
    store = InMemoryStore()
    client = FakeClient()
    service = IngestionService(client=client, store=store)
    await service.ingest_all_once()
    client.bootstrap_current_id = 2
    result = await service.ingest_all_once()
    assert result.changed_count >= 2
    assert len(store.events) > 5


def test_has_active_fixture_detects_started_unfinished_fixture() -> None:
    fixtures = [
        Fixture.model_validate(
            {
                "id": 1,
                "team_h": 1,
                "team_a": 2,
                "started": True,
                "finished": False,
            },
        ),
    ]
    assert has_active_fixture(fixtures=fixtures) is True


@pytest.mark.asyncio
async def test_scheduler_start_and_stop() -> None:
    service = IngestionService(client=FakeClient(), store=InMemoryStore())
    scheduler = RelayScheduler(
        ingestion_service=service,
        reference_poll_seconds=100,
        live_poll_seconds=100,
        idle_poll_seconds=100,
    )
    task = scheduler.start()
    await scheduler.stop(task=task)


@pytest.mark.asyncio
async def test_scheduler_cycles_return_idle_result_on_live_failure() -> None:
    class RaisingService:
        async def ingest_reference_once(self) -> IngestionResult:
            raise RuntimeError("reference failed")

        async def ingest_live_once(self) -> IngestionResult:
            raise RuntimeError("live failed")

    scheduler = RelayScheduler(
        ingestion_service=RaisingService(),  # type: ignore[arg-type]
        reference_poll_seconds=100,
        live_poll_seconds=100,
        idle_poll_seconds=100,
    )
    await scheduler._run_reference_cycle()
    result = await scheduler._run_live_cycle()
    assert result.has_active_fixture is False
    assert result.changed_count == 0
