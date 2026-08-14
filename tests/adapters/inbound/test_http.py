import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fpl_data_relay.adapters.inbound.http.app import create_app
from fpl_data_relay.application.change_feed import ChangeFeed
from fpl_data_relay.application.community_queries import CommunityQueries
from fpl_data_relay.application.errors import (
    DatabaseUnavailableError,
    DatabaseWakingError,
    SchemaUnavailableError,
)
from fpl_data_relay.application.ingestion.service import IngestionService
from fpl_data_relay.application.live_queries import LiveQueries
from fpl_data_relay.application.reference_queries import ReferenceQueries
from fpl_data_relay.config import Settings
from fpl_data_relay.domain.changes import (
    ChangeEvent,
    ChangeKind,
    ChangeValue,
    EntityChange,
    EntityFamily,
    FieldChange,
    IngestionSourceKey,
)
from fpl_data_relay.domain.community import (
    CommunityReport,
    CommunityReportSummary,
    CommunityStrategySummary,
)
from tests.adapters.outbound.test_community_postgres import draft
from tests.conftest import FakeClient, InMemoryStore


class FakeCommunityQueries(CommunityQueries):
    def __init__(self, *, report: CommunityReport | None) -> None:
        self.report = report

    def list_strategies(self) -> list[CommunityStrategySummary]:
        return [
            CommunityStrategySummary(
                key="weekly-community-momentum-v1",
                name="Weekly momentum",
                description="Community topics",
                cadence="cron(0 6 * * ? *)",
                timezone="Europe/London",
                lookback_days=7,
                target_story_count=10,
            ),
        ]

    def has_strategy(self, *, strategy_key: str) -> bool:
        return strategy_key == "weekly-community-momentum-v1"

    async def latest(self, *, strategy_key: str) -> CommunityReport | None:
        del strategy_key
        return self.report

    async def get(self, *, report_id: int) -> CommunityReport | None:
        return (
            self.report
            if self.report is not None and report_id == self.report.id
            else None
        )

    async def recent(
        self,
        *,
        strategy_key: str,
        limit: int,
    ) -> list[CommunityReportSummary]:
        del strategy_key, limit
        return [] if self.report is None else [summary(report=self.report)]

    async def history(
        self,
        *,
        strategy_key: str,
        before_id: int,
        limit: int,
    ) -> list[CommunityReportSummary]:
        del strategy_key, before_id, limit
        return []


def community_report() -> CommunityReport:
    return CommunityReport(id=7, **draft().model_dump())


def summary(*, report: CommunityReport) -> CommunityReportSummary:
    return CommunityReportSummary(
        id=report.id,
        strategy_key=report.strategy_key,
        strategy_version=report.strategy_version,
        report_date=report.report_date,
        season_id=report.season_id,
        as_of_event_id=report.as_of_event_id,
        window_start=report.window_start,
        window_end=report.window_end,
        generated_at=report.generated_at,
        story_count=len(report.content.stories),
        successful_source_count=report.content.coverage.successful_source_count,
        failed_source_count=len(report.content.coverage.failed_sources),
    )


def settings() -> Settings:
    return Settings.model_validate(
        {
            "DATABASE_EXECUTOR": "asyncpg",
            "DATABASE_URL": "postgresql://relay:relay@localhost:5432/relay",
            "FPL_API_BASE_URL": "https://fantasy.premierleague.com/api",
            "FPL_CLIENT_USER_AGENT": "fpl-data-relay-tests",
            "HTTP_TIMEOUT_SECONDS": 10,
            "REFERENCE_POLL_SECONDS": 300,
            "LIVE_POLL_SECONDS": 15,
            "IDLE_POLL_SECONDS": 120,
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
    check_schema_on_startup: bool = True,
) -> FastAPI:
    """Build an API adapter from explicit application services."""
    async def shutdown() -> None:
        await store.close()

    runtime_settings = settings()
    return create_app(
        reference_queries=ReferenceQueries(repository=store),
        live_queries=LiveQueries(repository=store),
        change_feed=ChangeFeed(repository=store),
        community_queries=FakeCommunityQueries(report=community_report()),
        schema_manager=store,
        ingestion_service=ingestion_service,
        reference_poll_seconds=runtime_settings.reference_poll_seconds,
        live_poll_seconds=runtime_settings.live_poll_seconds,
        idle_poll_seconds=runtime_settings.idle_poll_seconds,
        start_scheduler=start_scheduler,
        check_schema_on_startup=check_schema_on_startup,
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


def test_community_endpoints_enforce_status_and_pagination_contracts() -> None:
    app = build_test_app()
    with TestClient(app) as client:
        strategies = client.get("/v1/community-strategies")
        latest = client.get(
            "/v1/community-reports/latest",
            params={"strategy_key": "weekly-community-momentum-v1"},
        )
        recent = client.get(
            "/v1/community-reports/recent",
            params={"strategy_key": "weekly-community-momentum-v1", "limit": 1},
        )
        history = client.get(
            "/v1/community-reports/history",
            params={
                "strategy_key": "weekly-community-momentum-v1",
                "before_id": 7,
                "limit": 10,
            },
        )
        historical = client.get("/v1/community-reports/7")
        missing_report = client.get("/v1/community-reports/99")
        unknown_strategy = client.get(
            "/v1/community-reports/latest",
            params={"strategy_key": "unknown"},
        )
    assert strategies.json()[0]["key"] == "weekly-community-momentum-v1"
    assert latest.json()["id"] == 7
    assert recent.json()["next_before_id"] == 7
    assert history.json() == {"items": [], "next_before_id": None}
    assert historical.status_code == 200
    assert missing_report.status_code == 404
    assert unknown_strategy.status_code == 404


def test_known_strategy_without_reports_returns_503() -> None:
    store = InMemoryStore()
    service = IngestionService(client=FakeClient(), repository=store)

    async def shutdown() -> None:
        await store.close()

    runtime_settings = settings()
    app = create_app(
        reference_queries=ReferenceQueries(repository=store),
        live_queries=LiveQueries(repository=store),
        change_feed=ChangeFeed(repository=store),
        community_queries=FakeCommunityQueries(report=None),
        schema_manager=store,
        ingestion_service=service,
        reference_poll_seconds=runtime_settings.reference_poll_seconds,
        live_poll_seconds=runtime_settings.live_poll_seconds,
        idle_poll_seconds=runtime_settings.idle_poll_seconds,
        start_scheduler=False,
        check_schema_on_startup=True,
        shutdown=shutdown,
    )
    with TestClient(app) as client:
        latest = client.get(
            "/v1/community-reports/latest",
            params={"strategy_key": "weekly-community-momentum-v1"},
        )
        recent = client.get(
            "/v1/community-reports/recent",
            params={"strategy_key": "weekly-community-momentum-v1", "limit": 10},
        )
        history = client.get(
            "/v1/community-reports/history",
            params={
                "strategy_key": "weekly-community-momentum-v1",
                "before_id": 7,
                "limit": 10,
            },
        )
    assert latest.status_code == 503
    assert recent.status_code == 503
    assert history.status_code == 503


@pytest.mark.parametrize(
    ("error", "code", "retry_after"),
    [
        (DatabaseWakingError("waking"), "database_waking", "5"),
        (
            DatabaseUnavailableError("database failed"),
            "database_unavailable",
            None,
        ),
        (
            SchemaUnavailableError("schema failed"),
            "schema_unavailable",
            None,
        ),
    ],
)
def test_readyz_returns_stable_database_errors(
    error: RuntimeError,
    code: str,
    retry_after: str | None,
) -> None:
    class FailingStore(InMemoryStore):
        async def check_schema_version(self, *, expected_version: int) -> None:
            del expected_version
            raise error

    store = FailingStore()
    service = IngestionService(client=FakeClient(), repository=store)
    app = create_test_app(
        store=store,
        ingestion_service=service,
        start_scheduler=False,
        check_schema_on_startup=False,
    )
    with TestClient(app) as client:
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["code"] == code
    assert response.headers.get("Retry-After") == retry_after


def test_documentation_endpoints_are_prefix_safe() -> None:
    app = build_test_app()
    with TestClient(app) as client:
        openapi_response = client.get("/openapi.json")
        swagger_response = client.get("/docs")
        redoc_response = client.get("/redoc")

    assert openapi_response.status_code == 200
    assert openapi_response.json()["openapi"] == "3.1.0"
    assert swagger_response.status_code == 200
    assert "SwaggerUIBundle" in swagger_response.text
    assert "url: './openapi.json'" in swagger_response.text
    assert '"deepLinking": true' in swagger_response.text
    assert "cdn.jsdelivr.net/npm/swagger-ui-dist@5" in swagger_response.text
    assert redoc_response.status_code == 200
    assert "FPL Data Relay - ReDoc" in redoc_response.text
    assert 'spec-url="./openapi.json"' in redoc_response.text


def test_openapi_documents_concrete_api_contracts() -> None:
    schema = build_test_app().openapi()
    paths = schema["paths"]
    components = schema["components"]["schemas"]

    assert schema["servers"] == [
        {
            "url": ".",
            "description": "The relay endpoint serving this OpenAPI document.",
        }
    ]

    expected_operation_ids = {
        "/healthz": "get_health",
        "/readyz": "get_readiness",
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
        "/v1/change-events/recent": "list_recent_change_events",
        "/v1/change-events/history": "list_change_event_history",
        "/v1/change-events/{change_event_id}/entity-changes": (
            "list_entity_changes"
        ),
        "/v1/ingestion-status": "get_ingestion_status",
        "/v1/community-strategies": "list_community_strategies",
        "/v1/community-reports/latest": "get_latest_community_report",
        "/v1/community-reports/recent": "list_recent_community_reports",
        "/v1/community-reports/history": "list_community_report_history",
        "/v1/community-reports/{report_id}": "get_community_report",
    }
    actual_operation_ids = {
        path: operation["get"]["operationId"] for path, operation in paths.items()
    }
    assert actual_operation_ids == expected_operation_ids
    expected_tags = {
        "/healthz": ["Service"],
        "/readyz": ["Service"],
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
        "/v1/change-events/recent": ["Change Events"],
        "/v1/change-events/history": ["Change Events"],
        "/v1/change-events/{change_event_id}/entity-changes": ["Change Events"],
        "/v1/ingestion-status": ["Change Events"],
        "/v1/community-strategies": ["Community Intelligence"],
        "/v1/community-reports/latest": ["Community Intelligence"],
        "/v1/community-reports/recent": ["Community Intelligence"],
        "/v1/community-reports/history": ["Community Intelligence"],
        "/v1/community-reports/{report_id}": ["Community Intelligence"],
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
        "Community Intelligence",
    ]
    assert "JsonValue" in components

    collection_models = {
        "/v1/seasons": "Season",
        "/v1/seasons/{season_id}/events": "Event",
        "/v1/seasons/{season_id}/phases": "Phase",
        "/v1/seasons/{season_id}/teams": "Team",
        "/v1/seasons/{season_id}/element-types": "ElementType",
    }
    for path, model_name in collection_models.items():
        response_schema = paths[path]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        assert response_schema["type"] == "array"
        assert response_schema["items"]["$ref"] == (
            f"#/components/schemas/{model_name}"
        )
    for path in [
        "/v1/seasons/{season_id}/elements",
        "/v1/seasons/{season_id}/fixtures",
        "/v1/seasons/{season_id}/events/{event_id}/fixtures",
        "/v1/seasons/{season_id}/events/{event_id}/live-elements",
    ]:
        response_schema = paths[path]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        component_name = response_schema["$ref"].rsplit("/", maxsplit=1)[-1]
        assert set(components[component_name]["properties"]) == {
            "items",
            "next_after_id",
        }

    entity_models = {
        "/healthz": "HealthResponse",
        "/readyz": "ReadyResponse",
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
    assert "default" not in change_parameters["after_id"]["schema"]
    assert change_parameters["limit"]["schema"]["minimum"] == 1
    assert change_parameters["limit"]["schema"]["maximum"] == 200
    assert "default" not in change_parameters["limit"]["schema"]
    assert "/v1/stream" not in paths


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
        elements = client.get(
            "/v1/seasons/2025-26/elements?after_id=0&limit=100",
        ).json()["items"]
        assert elements[0]["photo"] == "1.jpg"
        assert (
            client.get("/v1/seasons/2025-26/elements/1").json()["first_name"]
            == "First"
        )
        fixtures = client.get(
            "/v1/seasons/2025-26/fixtures?after_id=0&limit=100",
        ).json()["items"]
        assert fixtures[0]["id"] == 1
        assert (
            client.get(
                "/v1/seasons/2025-26/events/1/fixtures?after_id=0&limit=100",
            ).json()["items"][0]["event"]
            == 1
        )
        assert (
            client.get("/v1/seasons/2025-26/event-status").json()["status"][0][
                "event"
            ]
            == 1
        )
        live_elements = client.get(
            "/v1/seasons/2025-26/events/1/live-elements?after_id=0&limit=100",
        ).json()["items"]
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
            source_event_id=None,
            payload_hash="a" * 64,
            created_count=0,
            updated_count=1,
            deleted_count=0,
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
    assert response.json()["items"][0]["event_name"] == "bootstrap.updated"
    assert response.json()["items"][0]["season_id"] == "2025-26"


def test_change_event_endpoints_page_newest_older_and_forward() -> None:
    store = InMemoryStore()
    timestamp = datetime(2026, 6, 20, tzinfo=UTC)
    store.events.extend(
        ChangeEvent(
            id=event_id,
            season_id="2025-26",
            entity_family=EntityFamily.ELEMENTS,
            event_name="elements.updated",
            source_key=IngestionSourceKey.BOOTSTRAP,
            source_event_id=None,
            payload_hash=str(event_id) * 64,
            created_count=0,
            updated_count=1,
            deleted_count=0,
            fetched_at=timestamp,
            created_at=timestamp,
        )
        for event_id in range(1, 4)
    )
    service = IngestionService(client=FakeClient(), repository=store)
    app = create_test_app(
        store=store,
        ingestion_service=service,
        start_scheduler=False,
    )

    with TestClient(app) as client:
        recent = client.get("/v1/change-events/recent?limit=2").json()
        older = client.get(
            "/v1/change-events/history?before_id=3&limit=2",
        ).json()
        forward = client.get(
            "/v1/change-events?after_id=1&limit=1",
        ).json()

    assert [item["id"] for item in recent["items"]] == [3, 2]
    assert recent["next_before_id"] == 2
    assert [item["id"] for item in older["items"]] == [2, 1]
    assert older["next_before_id"] == 1
    assert [item["id"] for item in forward["items"]] == [2]
    assert forward["next_after_id"] == 2


def test_entity_change_endpoint_preserves_absent_and_json_null() -> None:
    store = InMemoryStore()
    timestamp = datetime(2026, 6, 20, tzinfo=UTC)
    store.entity_changes.append(
        EntityChange(
            id=1,
            change_event_id=7,
            entity_key="10",
            entity_label="Ada (10)",
            kind=ChangeKind.UPDATED,
            fields=[
                FieldChange(
                    field="news",
                    before=ChangeValue(present=False, value=None),
                    after=ChangeValue(present=True, value=None),
                ),
            ],
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
        response = client.get(
            "/v1/change-events/7/entity-changes?after_id=0&limit=1",
        )

    assert response.status_code == 200
    body = response.json()
    assert body["next_after_id"] == 1
    field = body["items"][0]["fields"][0]
    assert field["before"] == {"present": False, "value": None}
    assert field["after"] == {"present": True, "value": None}


def test_ingestion_status_initializes_before_first_snapshot() -> None:
    app = build_test_app()
    with TestClient(app) as client:
        response = client.get("/v1/ingestion-status")

    assert response.status_code == 200
    body = response.json()
    assert body["season_id"] is None
    assert body["reference"]["state"] == "initializing"
    assert body["live"]["state"] == "initializing"


@pytest.mark.asyncio
async def test_ingestion_status_reports_stale_reference_sources() -> None:
    store = InMemoryStore()
    service = IngestionService(client=FakeClient(), repository=store)
    await service.ingest_reference_once()
    stale_at = datetime.now(tz=UTC) - timedelta(minutes=11)
    store.source_statuses = {
        identity: status.model_copy(update={"checked_at": stale_at})
        for identity, status in store.source_statuses.items()
    }
    app = create_test_app(
        store=store,
        ingestion_service=service,
        start_scheduler=False,
    )

    with TestClient(app) as client:
        response = client.get("/v1/ingestion-status")

    assert response.status_code == 200
    reference = response.json()["reference"]
    assert reference["state"] == "stale"
    assert reference["expected_interval_seconds"] == 300
    assert reference["stale_after_seconds"] == 600


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
