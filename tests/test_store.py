import pytest

from fpl_data_relay.resources import ResourceKey
from fpl_data_relay.store import IngestionLockError
from tests.conftest import InMemoryStore, resource_write


@pytest.mark.asyncio
async def test_store_updates_checked_at_when_hash_is_unchanged() -> None:
    store = InMemoryStore()
    resource = resource_write(resource_key=ResourceKey.BOOTSTRAP, payload_hash="a" * 64)
    changed = await store.upsert_resource(resource=resource)
    second_resource = resource_write(
        resource_key=ResourceKey.BOOTSTRAP,
        payload_hash="a" * 64,
    )
    unchanged = await store.upsert_resource(resource=second_resource)
    assert changed.changed is True
    assert unchanged.changed is False
    assert len(store.events) == 1


@pytest.mark.asyncio
async def test_store_creates_event_when_hash_changes() -> None:
    store = InMemoryStore()
    first = resource_write(resource_key=ResourceKey.BOOTSTRAP, payload_hash="a" * 64)
    second = resource_write(resource_key=ResourceKey.BOOTSTRAP, payload_hash="b" * 64)
    await store.upsert_resource(resource=first)
    changed = await store.upsert_resource(resource=second)
    assert changed.changed is True
    assert len(store.events) == 2


@pytest.mark.asyncio
async def test_ingestion_lock_prevents_overlap() -> None:
    store = InMemoryStore()
    async with store.ingestion_lock():
        with pytest.raises(IngestionLockError):
            async with store.ingestion_lock():
                pass
    assert store.locked is False


@pytest.mark.asyncio
async def test_watch_change_events_replays_and_heartbeats() -> None:
    store = InMemoryStore()
    resource = resource_write(
        resource_key=ResourceKey.EVENT_STATUS,
        payload_hash="a" * 64,
    )
    await store.upsert_resource(resource=resource)
    events = []
    async for event in store.watch_change_events(after_id=0, heartbeat_seconds=1):
        events.append(event)
        if len(events) == 2:
            break
    assert events[0] is not None
    assert events[1] is None
