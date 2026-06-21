from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import cast

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from fpl_data_relay.api import (
    create_app,
    encode_sse_event,
    parse_last_event_id,
    sse_generator,
)
from fpl_data_relay.config import Settings
from fpl_data_relay.ingestion import IngestionService
from fpl_data_relay.resources import ResourceKey
from fpl_data_relay.store import ChangeEvent
from tests.conftest import FakeClient, InMemoryStore


def settings() -> Settings:
    return Settings.model_validate(
        {
            "DATABASE_URL": "postgresql://relay:relay@localhost:5432/relay",
            "FPL_API_BASE_URL": "https://fantasy.premierleague.com/api",
            "FPL_CLIENT_USER_AGENT": "fpl-data-relay-tests",
            "HTTP_TIMEOUT_SECONDS": 10,
            "REFERENCE_POLL_SECONDS": 300,
            "LIVE_POLL_SECONDS": 15,
            "IDLE_POLL_SECONDS": 120,
            "SSE_HEARTBEAT_SECONDS": 5,
        },
    )


def test_rest_returns_503_before_first_successful_fetch() -> None:
    store = InMemoryStore()
    service = IngestionService(client=FakeClient(), store=store)
    app = create_app(
        settings=settings(),
        store=store,
        ingestion_service=service,
        start_scheduler=False,
    )
    with TestClient(app) as client:
        response = client.get("/v1/events/current")
    assert response.status_code == 503
    assert "has not been ingested" in response.json()["detail"]


def test_healthz_returns_schema_version() -> None:
    store = InMemoryStore()
    service = IngestionService(client=FakeClient(), store=store)
    app = create_app(
        settings=settings(),
        store=store,
        ingestion_service=service,
        start_scheduler=False,
    )
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["schema_version"] == 2


@pytest.mark.asyncio
async def test_rest_returns_normalised_events() -> None:
    store = InMemoryStore()
    service = IngestionService(client=FakeClient(), store=store)
    await service.ingest_reference_once()
    app = create_app(
        settings=settings(),
        store=store,
        ingestion_service=service,
        start_scheduler=False,
    )
    with TestClient(app) as client:
        response = client.get("/v1/events")
    assert response.status_code == 200
    assert response.json()[0]["id"] == 1


@pytest.mark.asyncio
async def test_entity_endpoints_return_normalised_data() -> None:
    store = InMemoryStore()
    service = IngestionService(client=FakeClient(), store=store)
    await service.ingest_reference_once()
    await service.ingest_live_once(target_event_id=None, fixture_id=None)
    app = create_app(
        settings=settings(),
        store=store,
        ingestion_service=service,
        start_scheduler=False,
    )
    with TestClient(app) as client:
        assert client.get("/v1/events/current").json()["id"] == 1
        assert client.get("/v1/events/1").json()["name"] == "Gameweek 1"
        assert client.get("/v1/phases").json() == []
        assert client.get("/v1/teams").json()[0]["short_name"] == "TST"
        assert client.get("/v1/teams/1").json()["name"] == "Team"
        assert client.get("/v1/element-types").json()[0]["id"] == 1
        assert client.get("/v1/elements").json()[0]["web_name"] == "Player"
        assert client.get("/v1/elements/1").json()["first_name"] == "First"
        assert client.get("/v1/fixtures").json()[0]["id"] == 1
        assert client.get("/v1/events/1/fixtures").json()[0]["event"] == 1
        assert client.get("/v1/event-status").json()["status"][0]["event"] == 1
        live_elements = client.get("/v1/events/1/live-elements").json()
        assert live_elements[0]["stats"]["total_points"] == 4
        live_element = client.get("/v1/events/1/live-elements/1").json()
        assert live_element["id"] == 1


@pytest.mark.parametrize(
    ("path", "detail"),
    [
        ("/v1/events/999", "Event not found."),
        ("/v1/teams/999", "Team not found."),
        ("/v1/elements/999", "Element not found."),
        ("/v1/events/1/live-elements/999", "Live element not found."),
    ],
)
def test_entity_endpoints_return_404_for_missing_rows(
    path: str,
    detail: str,
) -> None:
    store = InMemoryStore()
    service = IngestionService(client=FakeClient(), store=store)
    app = create_app(
        settings=settings(),
        store=store,
        ingestion_service=service,
        start_scheduler=False,
    )
    with TestClient(app) as client:
        response = client.get(path)
    assert response.status_code == 404
    assert response.json()["detail"] == detail


def test_change_events_endpoint_filters_by_after_id() -> None:
    store = InMemoryStore()
    timestamp = datetime(2026, 6, 20, tzinfo=UTC)
    store.events.append(
        ChangeEvent(
            id=1,
            resource_key=ResourceKey.BOOTSTRAP,
            event_name="bootstrap.updated",
            event_id=None,
            payload_hash="a" * 64,
            fetched_at=timestamp,
            created_at=timestamp,
        ),
    )
    service = IngestionService(client=FakeClient(), store=store)
    app = create_app(
        settings=settings(),
        store=store,
        ingestion_service=service,
        start_scheduler=False,
    )
    with TestClient(app) as client:
        response = client.get("/v1/change-events?after_id=0&limit=10")
    assert response.status_code == 200
    assert response.json()["events"][0]["event_name"] == "bootstrap.updated"


def test_encode_sse_event_contains_id_event_and_metadata() -> None:
    timestamp = datetime(2026, 6, 20, tzinfo=UTC)
    event = ChangeEvent(
        id=7,
        resource_key=ResourceKey.EVENT_LIVE,
        event_name="event_live.updated",
        event_id=3,
        payload_hash="b" * 64,
        fetched_at=timestamp,
        created_at=timestamp,
    )
    encoded = encode_sse_event(change_event=event)
    assert encoded.startswith("id: 7\nevent: event_live.updated\n")
    assert '"event_id":3' in encoded


def test_parse_last_event_id_validates_value() -> None:
    assert parse_last_event_id(last_event_id=None) == 0
    assert parse_last_event_id(last_event_id="12") == 12


@pytest.mark.parametrize("last_event_id", ["not-an-int", "-1"])
def test_parse_last_event_id_rejects_invalid_value(last_event_id: str) -> None:
    with pytest.raises(HTTPException):
        parse_last_event_id(last_event_id=last_event_id)


@pytest.mark.asyncio
async def test_sse_generator_replays_events_and_heartbeat() -> None:
    class ConnectedRequest:
        async def is_disconnected(self) -> bool:
            return False

    store = InMemoryStore()
    timestamp = datetime(2026, 6, 20, tzinfo=UTC)
    store.events.append(
        ChangeEvent(
            id=1,
            resource_key=ResourceKey.EVENT_STATUS,
            event_name="event_status.updated",
            event_id=1,
            payload_hash="c" * 64,
            fetched_at=timestamp,
            created_at=timestamp,
        ),
    )
    generator = cast(
        "AsyncGenerator[str]",
        sse_generator(
            request=ConnectedRequest(),
            store=store,
            after_id=0,
            heartbeat_seconds=1,
        ),
    )
    first = await anext(generator)
    second = await anext(generator)
    await generator.aclose()
    assert first.startswith("id: 1\nevent: event_status.updated")
    assert second == ": heartbeat\n\n"


def test_app_lifespan_starts_and_stops_scheduler() -> None:
    store = InMemoryStore()
    service = IngestionService(client=FakeClient(), store=store)
    app = create_app(
        settings=settings(),
        store=store,
        ingestion_service=service,
        start_scheduler=True,
    )
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert store.closed is True
