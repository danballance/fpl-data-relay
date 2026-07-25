import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import cast

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from fpl_data_relay.adapters.inbound.http.app import (
    create_app,
    encode_sse_event,
    parse_last_event_id,
    sse_generator,
)
from fpl_data_relay.application.change_feed import ChangeFeed
from fpl_data_relay.application.ingestion.service import IngestionService
from fpl_data_relay.application.live_queries import LiveQueries
from fpl_data_relay.application.reference_queries import ReferenceQueries
from fpl_data_relay.config import Settings
from fpl_data_relay.domain.changes import (
    ChangeEvent,
    EntityFamily,
    IngestionSourceKey,
)
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


def build_test_app() -> FastAPI:
    """Build an API app with in-memory dependencies for schema tests."""
    store = InMemoryStore()
    service = IngestionService(client=FakeClient(), repository=store)
    return create_test_app(
        store=store,
        ingestion_service=service,
        start_scheduler=False,
    )


def create_test_app(
    *,
    store: InMemoryStore,
    ingestion_service: IngestionService,
    start_scheduler: bool,
) -> FastAPI:
    """Build an API adapter from explicit application services."""
    async def shutdown() -> None:
        await store.close()

    runtime_settings = settings()
    return create_app(
        reference_queries=ReferenceQueries(repository=store),
        live_queries=LiveQueries(repository=store),
        change_feed=ChangeFeed(repository=store),
        schema_manager=store,
        ingestion_service=ingestion_service,
        reference_poll_seconds=runtime_settings.reference_poll_seconds,
        live_poll_seconds=runtime_settings.live_poll_seconds,
        idle_poll_seconds=runtime_settings.idle_poll_seconds,
        sse_heartbeat_seconds=runtime_settings.sse_heartbeat_seconds,
        start_scheduler=start_scheduler,
        shutdown=shutdown,
    )


def test_rest_returns_503_before_first_successful_fetch() -> None:
    store = InMemoryStore()
    service = IngestionService(client=FakeClient(), repository=store)
    app = create_test_app(
        store=store,
        ingestion_service=service,
        start_scheduler=False,
    )
    with TestClient(app) as client:
        response = client.get("/v1/seasons/current")
    assert response.status_code == 503
    assert "has not been ingested" in response.json()["detail"]


def test_healthz_returns_schema_version() -> None:
    store = InMemoryStore()
    service = IngestionService(client=FakeClient(), repository=store)
    app = create_test_app(
        store=store,
        ingestion_service=service,
        start_scheduler=False,
    )
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["schema_version"] == 3


def test_default_documentation_endpoints_are_exposed() -> None:
    app = build_test_app()
    with TestClient(app) as client:
        openapi_response = client.get("/openapi.json")
        swagger_response = client.get("/docs")
        redoc_response = client.get("/redoc")

    assert openapi_response.status_code == 200
    assert openapi_response.json()["openapi"] == "3.1.0"
    assert swagger_response.status_code == 200
    assert "SwaggerUIBundle" in swagger_response.text
    assert "url: '/openapi.json'" in swagger_response.text
    assert "cdn.jsdelivr.net/npm/swagger-ui-dist@5" in swagger_response.text
    assert redoc_response.status_code == 200
    assert "FPL Data Relay - ReDoc" in redoc_response.text


def test_openapi_documents_concrete_api_contracts() -> None:
    schema = build_test_app().openapi()
    paths = schema["paths"]
    components = schema["components"]["schemas"]

    expected_operation_ids = {
        "/healthz": "get_health",
        "/v1/seasons": "list_seasons",
        "/v1/seasons/current": "get_current_season",
        "/v1/seasons/{season_id}": "get_season",
        "/v1/seasons/{season_id}/events": "list_events",
        "/v1/seasons/{season_id}/events/current": "get_current_event",
        "/v1/seasons/{season_id}/events/{event_id}": "get_event",
        "/v1/seasons/{season_id}/phases": "list_phases",
        "/v1/seasons/{season_id}/teams": "list_teams",
        "/v1/seasons/{season_id}/teams/{team_id}": "get_team",
        "/v1/seasons/{season_id}/element-types": "list_element_types",
        "/v1/seasons/{season_id}/elements": "list_elements",
        "/v1/seasons/{season_id}/elements/{element_id}": "get_element",
        "/v1/seasons/{season_id}/fixtures": "list_fixtures",
        "/v1/seasons/{season_id}/events/{event_id}/fixtures": (
            "list_event_fixtures"
        ),
        "/v1/seasons/{season_id}/event-status": "get_event_status",
        "/v1/seasons/{season_id}/events/{event_id}/live-elements": (
            "list_live_elements"
        ),
        "/v1/seasons/{season_id}/events/{event_id}/live-elements/{element_id}": (
            "get_live_element"
        ),
        "/v1/change-events": "list_change_events",
        "/v1/stream": "stream_change_events",
    }
    actual_operation_ids = {
        path: operation["get"]["operationId"] for path, operation in paths.items()
    }
    assert actual_operation_ids == expected_operation_ids
    expected_tags = {
        "/healthz": ["Service"],
        "/v1/seasons": ["Reference Data"],
        "/v1/seasons/current": ["Reference Data"],
        "/v1/seasons/{season_id}": ["Reference Data"],
        "/v1/seasons/{season_id}/events": ["Reference Data"],
        "/v1/seasons/{season_id}/events/current": ["Reference Data"],
        "/v1/seasons/{season_id}/events/{event_id}": ["Reference Data"],
        "/v1/seasons/{season_id}/phases": ["Reference Data"],
        "/v1/seasons/{season_id}/teams": ["Reference Data"],
        "/v1/seasons/{season_id}/teams/{team_id}": ["Reference Data"],
        "/v1/seasons/{season_id}/element-types": ["Reference Data"],
        "/v1/seasons/{season_id}/elements": ["Reference Data"],
        "/v1/seasons/{season_id}/elements/{element_id}": ["Reference Data"],
        "/v1/seasons/{season_id}/fixtures": ["Reference Data"],
        "/v1/seasons/{season_id}/events/{event_id}/fixtures": ["Reference Data"],
        "/v1/seasons/{season_id}/event-status": ["Live Data"],
        "/v1/seasons/{season_id}/events/{event_id}/live-elements": ["Live Data"],
        "/v1/seasons/{season_id}/events/{event_id}/live-elements/{element_id}": [
            "Live Data",
        ],
        "/v1/change-events": ["Change Events"],
        "/v1/stream": ["Change Events"],
    }
    actual_tags = {
        path: operation["get"]["tags"] for path, operation in paths.items()
    }
    assert actual_tags == expected_tags
    assert [tag["name"] for tag in schema["tags"]] == [
        "Service",
        "Reference Data",
        "Live Data",
        "Change Events",
    ]
    assert "JsonValue" not in components

    collection_models = {
        "/v1/seasons": "Season",
        "/v1/seasons/{season_id}/events": "Event",
        "/v1/seasons/{season_id}/phases": "Phase",
        "/v1/seasons/{season_id}/teams": "Team",
        "/v1/seasons/{season_id}/element-types": "ElementType",
        "/v1/seasons/{season_id}/elements": "Element",
        "/v1/seasons/{season_id}/fixtures": "Fixture",
        "/v1/seasons/{season_id}/events/{event_id}/fixtures": "Fixture",
        "/v1/seasons/{season_id}/events/{event_id}/live-elements": "LiveElement",
    }
    for path, model_name in collection_models.items():
        response_schema = paths[path]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        assert response_schema["type"] == "array"
        assert response_schema["items"]["$ref"] == (
            f"#/components/schemas/{model_name}"
        )

    entity_models = {
        "/healthz": "HealthResponse",
        "/v1/seasons/current": "Season",
        "/v1/seasons/{season_id}": "Season",
        "/v1/seasons/{season_id}/events/current": "Event",
        "/v1/seasons/{season_id}/events/{event_id}": "Event",
        "/v1/seasons/{season_id}/teams/{team_id}": "Team",
        "/v1/seasons/{season_id}/elements/{element_id}": "Element",
        "/v1/seasons/{season_id}/event-status": "EventStatusResponse",
        "/v1/seasons/{season_id}/events/{event_id}/live-elements/{element_id}": (
            "LiveElement"
        ),
        "/v1/change-events": "ChangeEventsResponse",
    }
    for path, model_name in entity_models.items():
        response_schema = paths[path]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        assert response_schema["$ref"] == f"#/components/schemas/{model_name}"

    event_operation = paths["/v1/seasons/{season_id}/events/{event_id}"]["get"]
    assert event_operation["parameters"][0]["schema"]["pattern"] == r"^\d{4}-\d{2}$"
    assert event_operation["parameters"][1]["schema"]["minimum"] == 1
    for path in [
        "/v1/seasons/{season_id}",
        "/v1/seasons/{season_id}/events/{event_id}",
        "/v1/seasons/{season_id}/teams/{team_id}",
        "/v1/seasons/{season_id}/elements/{element_id}",
        "/v1/seasons/{season_id}/events/{event_id}/live-elements/{element_id}",
    ]:
        assert paths[path]["get"]["responses"]["404"]["content"][
            "application/json"
        ]["schema"]["$ref"] == "#/components/schemas/ErrorResponse"
    for path in [
        "/v1/seasons/current",
        "/v1/seasons/{season_id}/events/current",
        "/v1/seasons/{season_id}/event-status",
    ]:
        assert paths[path]["get"]["responses"]["503"]["content"][
            "application/json"
        ]["schema"]["$ref"] == "#/components/schemas/ErrorResponse"

    change_parameters = {
        parameter["name"]: parameter
        for parameter in paths["/v1/change-events"]["get"]["parameters"]
    }
    assert change_parameters["after_id"]["schema"]["minimum"] == 0
    assert change_parameters["after_id"]["schema"]["default"] == 0
    assert change_parameters["limit"]["schema"]["minimum"] == 1
    assert change_parameters["limit"]["schema"]["maximum"] == 1000
    assert change_parameters["limit"]["schema"]["default"] == 100

    stream_operation = paths["/v1/stream"]["get"]
    assert stream_operation["parameters"][0]["name"] == "Last-Event-ID"
    assert set(stream_operation["responses"]["200"]["content"]) == {
        "text/event-stream",
    }
    assert stream_operation["responses"]["400"]["content"]["application/json"][
        "schema"
    ]["$ref"] == "#/components/schemas/ErrorResponse"


@pytest.mark.asyncio
async def test_rest_returns_normalised_events() -> None:
    store = InMemoryStore()
    service = IngestionService(client=FakeClient(), repository=store)
    await service.ingest_reference_once()
    app = create_test_app(
        store=store,
        ingestion_service=service,
        start_scheduler=False,
    )
    with TestClient(app) as client:
        response = client.get("/v1/seasons/2025-26/events")
    assert response.status_code == 200
    assert response.json()[0]["id"] == 1


@pytest.mark.asyncio
async def test_entity_endpoints_return_normalised_data() -> None:
    store = InMemoryStore()
    service = IngestionService(client=FakeClient(), repository=store)
    await service.ingest_reference_once()
    await service.ingest_live_once(target_event_id=None, fixture_id=None)
    app = create_test_app(
        store=store,
        ingestion_service=service,
        start_scheduler=False,
    )
    with TestClient(app) as client:
        assert client.get("/v1/seasons/current").json()["id"] == "2025-26"
        assert client.get("/v1/seasons/2025-26").json()["start_year"] == 2025
        assert client.get("/v1/seasons/2025-26/events/current").json()["id"] == 1
        assert (
            client.get("/v1/seasons/2025-26/events/1").json()["name"]
            == "Gameweek 1"
        )
        assert client.get("/v1/seasons/2025-26/phases").json() == []
        assert client.get("/v1/seasons/2025-26/teams").json()[0]["short_name"] == "TST"
        assert client.get("/v1/seasons/2025-26/teams/1").json()["name"] == "Team"
        assert client.get("/v1/seasons/2025-26/element-types").json()[0]["id"] == 1
        assert client.get("/v1/seasons/2025-26/elements").json()[0]["photo"] == "1.jpg"
        assert (
            client.get("/v1/seasons/2025-26/elements/1").json()["first_name"]
            == "First"
        )
        assert client.get("/v1/seasons/2025-26/fixtures").json()[0]["id"] == 1
        assert (
            client.get("/v1/seasons/2025-26/events/1/fixtures").json()[0]["event"]
            == 1
        )
        assert (
            client.get("/v1/seasons/2025-26/event-status").json()["status"][0][
                "event"
            ]
            == 1
        )
        live_elements = client.get(
            "/v1/seasons/2025-26/events/1/live-elements",
        ).json()
        assert live_elements[0]["stats"]["total_points"] == 4
        live_element = client.get(
            "/v1/seasons/2025-26/events/1/live-elements/1",
        ).json()
        assert live_element["id"] == 1


@pytest.mark.parametrize(
    ("path", "detail"),
    [
        ("/v1/seasons/2099-00", "Season not found."),
        ("/v1/seasons/2025-26/events/999", "Event not found."),
        ("/v1/seasons/2025-26/teams/999", "Team not found."),
        ("/v1/seasons/2025-26/elements/999", "Element not found."),
        (
            "/v1/seasons/2025-26/events/1/live-elements/999",
            "Live element not found.",
        ),
    ],
)
def test_entity_endpoints_return_404_for_missing_rows(
    path: str,
    detail: str,
) -> None:
    store = InMemoryStore()
    service = IngestionService(client=FakeClient(), repository=store)
    if path != "/v1/seasons/2099-00":
        asyncio.run(service.ingest_reference_once())
        asyncio.run(service.ingest_live_once(target_event_id=None, fixture_id=None))
    app = create_test_app(
        store=store,
        ingestion_service=service,
        start_scheduler=False,
    )
    with TestClient(app) as client:
        response = client.get(path)
    assert response.status_code == 404
    assert response.json()["detail"] == detail


@pytest.mark.parametrize(
    "path",
    [
        "/v1/seasons/not-a-season/events",
        "/v1/seasons/2025-26/events/0",
        "/v1/seasons/2025-26/teams/0",
        "/v1/seasons/2025-26/elements/0",
        "/v1/seasons/2025-26/events/0/fixtures",
        "/v1/seasons/2025-26/events/0/live-elements",
        "/v1/seasons/2025-26/events/1/live-elements/0",
    ],
)
def test_entity_endpoints_reject_non_positive_ids(path: str) -> None:
    app = build_test_app()
    with TestClient(app) as client:
        response = client.get(path)
    assert response.status_code == 422


def test_change_events_endpoint_filters_by_after_id() -> None:
    store = InMemoryStore()
    timestamp = datetime(2026, 6, 20, tzinfo=UTC)
    store.events.append(
        ChangeEvent(
            id=1,
            season_id="2025-26",
            entity_family=EntityFamily.EVENTS,
            event_name="bootstrap.updated",
            source_key=IngestionSourceKey.BOOTSTRAP,
            resource_key=IngestionSourceKey.BOOTSTRAP,
            event_id=None,
            payload_hash="a" * 64,
            fetched_at=timestamp,
            created_at=timestamp,
        ),
    )
    service = IngestionService(client=FakeClient(), repository=store)
    app = create_test_app(
        store=store,
        ingestion_service=service,
        start_scheduler=False,
    )
    with TestClient(app) as client:
        response = client.get("/v1/change-events?after_id=0&limit=10")
    assert response.status_code == 200
    assert response.json()["events"][0]["event_name"] == "bootstrap.updated"
    assert response.json()["events"][0]["season_id"] == "2025-26"


def test_encode_sse_event_contains_id_event_and_metadata() -> None:
    timestamp = datetime(2026, 6, 20, tzinfo=UTC)
    event = ChangeEvent(
        id=7,
        season_id="2025-26",
        entity_family=EntityFamily.EVENT_LIVE,
        event_name="event_live.updated",
        source_key=IngestionSourceKey.EVENT_LIVE,
        resource_key=IngestionSourceKey.EVENT_LIVE,
        event_id=3,
        payload_hash="b" * 64,
        fetched_at=timestamp,
        created_at=timestamp,
    )
    encoded = encode_sse_event(change_event=event)
    assert encoded.startswith("id: 7\nevent: event_live.updated\n")
    assert '"season_id":"2025-26"' in encoded
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
            season_id="2025-26",
            entity_family=EntityFamily.EVENT_STATUS,
            event_name="event_status.updated",
            source_key=IngestionSourceKey.EVENT_STATUS,
            resource_key=IngestionSourceKey.EVENT_STATUS,
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
            change_feed=ChangeFeed(repository=store),
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
    service = IngestionService(client=FakeClient(), repository=store)
    app = create_test_app(
        store=store,
        ingestion_service=service,
        start_scheduler=True,
    )
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert store.closed is True
