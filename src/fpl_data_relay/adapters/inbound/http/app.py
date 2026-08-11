"""Inbound HTTP adapter for normalised FPL relay data."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, NoReturn

from fastapi import FastAPI, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse

from fpl_data_relay.adapters.inbound.http.schemas import (
    ChangeEventHistoryResponse,
    ChangeEventsResponse,
    CursorPage,
    EntityChangesResponse,
    ErrorResponse,
    HealthResponse,
    IngestionStatusResponse,
    PipelineStatusResponse,
    ReadyResponse,
    ServiceErrorResponse,
    public_change_event,
    public_entity_change,
)
from fpl_data_relay.adapters.inbound.scheduler import RelayScheduler
from fpl_data_relay.application.change_feed import ChangeFeed
from fpl_data_relay.application.database import SCHEMA_VERSION
from fpl_data_relay.application.errors import (
    DatabaseUnavailableError,
    DatabaseWakingError,
    SchemaUnavailableError,
)
from fpl_data_relay.application.jobs import (
    WINDOW_AFTER_KICKOFF,
    WINDOW_BEFORE_KICKOFF,
)
from fpl_data_relay.application.live_queries import LiveQueries
from fpl_data_relay.application.ports.administration import SchemaManager
from fpl_data_relay.application.ports.inbound import IngestionRunner
from fpl_data_relay.application.reference_queries import ReferenceQueries
from fpl_data_relay.domain.changes import (
    IngestionSourceKey,
    IngestionSourceStatus,
)
from fpl_data_relay.domain.fixtures import Fixture
from fpl_data_relay.domain.live import (
    EventStatusResponse,
    LiveElement,
)
from fpl_data_relay.domain.reference import (
    Element,
    ElementType,
    Event,
    Phase,
    Season,
    Team,
)

OPENAPI_TAGS = [
    {
        "name": "Service",
        "description": "Service health and runtime metadata.",
    },
    {
        "name": "Reference Data",
        "description": "Stored FPL events, teams, players, and fixtures.",
    },
    {
        "name": "Live Data",
        "description": "Stored event status and live player data.",
    },
    {
        "name": "Change Events",
        "description": "Cursor-based change-event replay.",
    },
]

NOT_FOUND_RESPONSE: dict[int | str, dict[str, object]] = {
    404: {
        "model": ErrorResponse,
        "description": "The requested entity does not exist.",
    },
}
NOT_INGESTED_RESPONSE: dict[int | str, dict[str, object]] = {
    503: {
        "model": ErrorResponse,
        "description": "The requested data has not been ingested yet.",
    },
}
def create_app(
    *,
    reference_queries: ReferenceQueries,
    live_queries: LiveQueries,
    change_feed: ChangeFeed,
    schema_manager: SchemaManager,
    ingestion_service: IngestionRunner | None,
    reference_poll_seconds: int,
    live_poll_seconds: int,
    idle_poll_seconds: int,
    start_scheduler: bool,
    check_schema_on_startup: bool,
    shutdown: Callable[[], Awaitable[None]],
) -> FastAPI:
    """Build the FastAPI app around injected application use cases."""

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        """Validate dependencies, start optional polling, and close resources."""
        if check_schema_on_startup:
            await schema_manager.check_schema_version(
                expected_version=SCHEMA_VERSION,
            )
        if start_scheduler and ingestion_service is None:
            raise RuntimeError("Local scheduler requires an ingestion service.")
        scheduler = (
            RelayScheduler(
                ingestion_service=ingestion_service,
                reference_poll_seconds=reference_poll_seconds,
                live_poll_seconds=live_poll_seconds,
                idle_poll_seconds=idle_poll_seconds,
            )
            if ingestion_service is not None
            else None
        )
        scheduler_task = (
            scheduler.start() if start_scheduler and scheduler is not None else None
        )
        try:
            yield
        finally:
            if scheduler_task is not None:
                if scheduler is None:
                    raise RuntimeError("Scheduler task exists without a scheduler.")
                await scheduler.stop(task=scheduler_task)
            await shutdown()

    app = FastAPI(
        title="FPL Data Relay",
        summary="Stored Fantasy Premier League data and change notifications.",
        description=(
            "Read the latest normalised FPL data stored by the relay. "
            "Requests never proxy the upstream FPL API."
        ),
        version="0.1.0",
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
    )

    @app.exception_handler(DatabaseWakingError)
    async def database_waking(
        _: Request,
        exception: DatabaseWakingError,
    ) -> JSONResponse:
        del exception
        body = ServiceErrorResponse(
            code="database_waking",
            detail="The database is waking from idle. Retry shortly.",
            retry_after_seconds=5,
        )
        return JSONResponse(
            status_code=503,
            headers={"Retry-After": "5"},
            content=body.model_dump(mode="json"),
        )

    @app.exception_handler(DatabaseUnavailableError)
    async def database_unavailable(
        _: Request,
        exception: DatabaseUnavailableError,
    ) -> JSONResponse:
        body = ServiceErrorResponse(
            code="database_unavailable",
            detail=str(exception),
            retry_after_seconds=None,
        )
        return JSONResponse(status_code=503, content=body.model_dump(mode="json"))

    @app.exception_handler(SchemaUnavailableError)
    async def schema_unavailable(
        _: Request,
        exception: SchemaUnavailableError,
    ) -> JSONResponse:
        body = ServiceErrorResponse(
            code="schema_unavailable",
            detail=str(exception),
            retry_after_seconds=None,
        )
        return JSONResponse(status_code=503, content=body.model_dump(mode="json"))

    @app.get(
        "/healthz",
        tags=["Service"],
        summary="Check service health",
        response_description="Service liveness and expected schema version.",
        operation_id="get_health",
    )
    async def healthz() -> HealthResponse:
        """Report service liveness and the schema version expected by the app."""
        return HealthResponse(status="ok", schema_version=SCHEMA_VERSION)

    @app.get(
        "/readyz",
        tags=["Service"],
        summary="Check database readiness",
        response_description="Database readiness and applied schema version.",
        responses={503: {"model": ServiceErrorResponse}},
        operation_id="get_readiness",
    )
    async def readyz() -> ReadyResponse:
        """Verify that the database is awake and has the expected schema."""
        await schema_manager.check_schema_version(expected_version=SCHEMA_VERSION)
        return ReadyResponse(status="ready", schema_version=SCHEMA_VERSION)

    async def require_season(*, season_id: str) -> Season:
        """Return a season or raise the standard not-found response."""
        season = await reference_queries.get_season(season_id=season_id)
        if season is None:
            raise HTTPException(status_code=404, detail="Season not found.")
        return season

    @app.get(
        "/v1/seasons",
        tags=["Reference Data"],
        summary="List seasons",
        response_description="All stored FPL seasons.",
        operation_id="list_seasons",
    )
    async def list_seasons() -> list[Season]:
        """Return all stored FPL seasons."""
        return await reference_queries.list_seasons()

    @app.get(
        "/v1/seasons/current",
        tags=["Reference Data"],
        summary="Get the current season",
        response_description="The season marked as current by relay ingestion.",
        responses=NOT_INGESTED_RESPONSE,
        operation_id="get_current_season",
    )
    async def current_season() -> Season:
        """Return the single current FPL season."""
        season = await reference_queries.get_current_season()
        if season is None:
            raise_not_ingested(entity="current season")
        return season

    @app.get(
        "/v1/seasons/{season_id}",
        tags=["Reference Data"],
        summary="Get a season",
        response_description="The requested FPL season.",
        responses=NOT_FOUND_RESPONSE,
        operation_id="get_season",
    )
    async def get_season(
        season_id: Annotated[
            str,
            Path(pattern=r"^\d{4}-\d{2}$", description="FPL season id."),
        ],
    ) -> Season:
        """Return one stored FPL season."""
        return await require_season(season_id=season_id)

    @app.get(
        "/v1/seasons/{season_id}/events",
        tags=["Reference Data"],
        summary="List season events",
        response_description="All stored FPL events for the requested season.",
        operation_id="list_events",
    )
    async def list_events(
        season_id: Annotated[
            str,
            Path(pattern=r"^\d{4}-\d{2}$", description="FPL season id."),
        ],
    ) -> list[Event]:
        """Return all stored FPL events for one season."""
        await require_season(season_id=season_id)
        return await reference_queries.list_events(season_id=season_id)

    @app.get(
        "/v1/seasons/{season_id}/events/current",
        tags=["Reference Data"],
        summary="Get the current season event",
        response_description="The event marked as current for the requested season.",
        responses=NOT_INGESTED_RESPONSE,
        operation_id="get_current_event",
    )
    async def current_event(
        season_id: Annotated[
            str,
            Path(pattern=r"^\d{4}-\d{2}$", description="FPL season id."),
        ],
    ) -> Event:
        """Return the single current FPL event for one season."""
        await require_season(season_id=season_id)
        event = await reference_queries.get_current_event(season_id=season_id)
        if event is None:
            raise_not_ingested(entity="current event")
        return event

    @app.get(
        "/v1/seasons/{season_id}/events/{event_id}",
        tags=["Reference Data"],
        summary="Get a season event",
        response_description="The requested FPL event.",
        responses=NOT_FOUND_RESPONSE,
        operation_id="get_event",
    )
    async def get_event(
        season_id: Annotated[
            str,
            Path(pattern=r"^\d{4}-\d{2}$", description="FPL season id."),
        ],
        event_id: Annotated[
            int,
            Path(ge=1, description="FPL event identifier."),
        ],
    ) -> Event:
        """Return one stored FPL event for one season."""
        await require_season(season_id=season_id)
        event = await reference_queries.get_event(
            season_id=season_id,
            event_id=event_id,
        )
        if event is None:
            raise HTTPException(status_code=404, detail="Event not found.")
        return event

    @app.get(
        "/v1/seasons/{season_id}/phases",
        tags=["Reference Data"],
        summary="List season phases",
        response_description="All stored FPL phases for the requested season.",
        operation_id="list_phases",
    )
    async def list_phases(
        season_id: Annotated[
            str,
            Path(pattern=r"^\d{4}-\d{2}$", description="FPL season id."),
        ],
    ) -> list[Phase]:
        """Return all stored FPL phases for one season."""
        await require_season(season_id=season_id)
        return await reference_queries.list_phases(season_id=season_id)

    @app.get(
        "/v1/seasons/{season_id}/teams",
        tags=["Reference Data"],
        summary="List season teams",
        response_description="All stored Premier League teams for the season.",
        operation_id="list_teams",
    )
    async def list_teams(
        season_id: Annotated[
            str,
            Path(pattern=r"^\d{4}-\d{2}$", description="FPL season id."),
        ],
    ) -> list[Team]:
        """Return all stored FPL teams for one season."""
        await require_season(season_id=season_id)
        return await reference_queries.list_teams(season_id=season_id)

    @app.get(
        "/v1/seasons/{season_id}/teams/{team_id}",
        tags=["Reference Data"],
        summary="Get a season team",
        response_description="The requested Premier League team.",
        responses=NOT_FOUND_RESPONSE,
        operation_id="get_team",
    )
    async def get_team(
        season_id: Annotated[
            str,
            Path(pattern=r"^\d{4}-\d{2}$", description="FPL season id."),
        ],
        team_id: Annotated[
            int,
            Path(ge=1, description="FPL team identifier."),
        ],
    ) -> Team:
        """Return one stored FPL team for one season."""
        await require_season(season_id=season_id)
        team = await reference_queries.get_team(
            season_id=season_id,
            team_id=team_id,
        )
        if team is None:
            raise HTTPException(status_code=404, detail="Team not found.")
        return team

    @app.get(
        "/v1/seasons/{season_id}/element-types",
        tags=["Reference Data"],
        summary="List season element types",
        response_description="All stored FPL player position definitions.",
        operation_id="list_element_types",
    )
    async def list_element_types(
        season_id: Annotated[
            str,
            Path(pattern=r"^\d{4}-\d{2}$", description="FPL season id."),
        ],
    ) -> list[ElementType]:
        """Return all stored FPL element types for one season."""
        await require_season(season_id=season_id)
        return await reference_queries.list_element_types(season_id=season_id)

    @app.get(
        "/v1/seasons/{season_id}/elements",
        tags=["Reference Data"],
        summary="List season elements",
        response_description="All stored FPL players for the requested season.",
        operation_id="list_elements",
    )
    async def list_elements(
        season_id: Annotated[
            str,
            Path(pattern=r"^\d{4}-\d{2}$", description="FPL season id."),
        ],
        after_id: Annotated[int, Query(ge=0)],
        limit: Annotated[int, Query(ge=1, le=200)],
    ) -> CursorPage[Element]:
        """Return a cursor page of stored FPL elements."""
        await require_season(season_id=season_id)
        items = await reference_queries.list_elements(
            season_id=season_id,
            after_id=after_id,
            limit=limit,
        )
        return cursor_page(items=items, limit=limit)

    @app.get(
        "/v1/seasons/{season_id}/elements/{element_id}",
        tags=["Reference Data"],
        summary="Get a season element",
        response_description="The requested FPL player.",
        responses=NOT_FOUND_RESPONSE,
        operation_id="get_element",
    )
    async def get_element(
        season_id: Annotated[
            str,
            Path(pattern=r"^\d{4}-\d{2}$", description="FPL season id."),
        ],
        element_id: Annotated[
            int,
            Path(ge=1, description="FPL element/player identifier."),
        ],
    ) -> Element:
        """Return one stored FPL element for one season."""
        await require_season(season_id=season_id)
        element = await reference_queries.get_element(
            season_id=season_id,
            element_id=element_id,
        )
        if element is None:
            raise HTTPException(status_code=404, detail="Element not found.")
        return element

    @app.get(
        "/v1/seasons/{season_id}/fixtures",
        tags=["Reference Data"],
        summary="List season fixtures",
        response_description="All stored FPL fixtures for the requested season.",
        operation_id="list_fixtures",
    )
    async def list_fixtures(
        season_id: Annotated[
            str,
            Path(pattern=r"^\d{4}-\d{2}$", description="FPL season id."),
        ],
        after_id: Annotated[int, Query(ge=0)],
        limit: Annotated[int, Query(ge=1, le=200)],
    ) -> CursorPage[Fixture]:
        """Return a cursor page of stored FPL fixtures for one season."""
        await require_season(season_id=season_id)
        items = await reference_queries.list_fixtures(
            season_id=season_id,
            event_id=None,
            after_id=after_id,
            limit=limit,
        )
        return cursor_page(items=items, limit=limit)

    @app.get(
        "/v1/seasons/{season_id}/events/{event_id}/fixtures",
        tags=["Reference Data"],
        summary="List season event fixtures",
        response_description="Stored fixtures assigned to the requested FPL event.",
        operation_id="list_event_fixtures",
    )
    async def list_event_fixtures(
        season_id: Annotated[
            str,
            Path(pattern=r"^\d{4}-\d{2}$", description="FPL season id."),
        ],
        event_id: Annotated[
            int,
            Path(ge=1, description="FPL event identifier."),
        ],
        after_id: Annotated[int, Query(ge=0)],
        limit: Annotated[int, Query(ge=1, le=200)],
    ) -> CursorPage[Fixture]:
        """Return a cursor page of fixtures for one event."""
        await require_season(season_id=season_id)
        items = await reference_queries.list_fixtures(
            season_id=season_id,
            event_id=event_id,
            after_id=after_id,
            limit=limit,
        )
        return cursor_page(items=items, limit=limit)

    @app.get(
        "/v1/seasons/{season_id}/event-status",
        tags=["Live Data"],
        summary="Get season event status",
        response_description="The latest stored FPL event-status response.",
        responses=NOT_INGESTED_RESPONSE,
        operation_id="get_event_status",
    )
    async def event_status(
        season_id: Annotated[
            str,
            Path(pattern=r"^\d{4}-\d{2}$", description="FPL season id."),
        ],
    ) -> EventStatusResponse:
        """Return latest event-status response from normalised rows."""
        await require_season(season_id=season_id)
        status = await live_queries.get_event_status(season_id=season_id)
        if status is None:
            raise_not_ingested(entity="event status")
        return status

    @app.get(
        "/v1/seasons/{season_id}/events/{event_id}/live-elements",
        tags=["Live Data"],
        summary="List season live elements",
        response_description="Live player rows for the requested FPL event.",
        operation_id="list_live_elements",
    )
    async def list_live_elements(
        season_id: Annotated[
            str,
            Path(pattern=r"^\d{4}-\d{2}$", description="FPL season id."),
        ],
        event_id: Annotated[
            int,
            Path(ge=1, description="FPL event identifier."),
        ],
        after_id: Annotated[int, Query(ge=0)],
        limit: Annotated[int, Query(ge=1, le=200)],
    ) -> CursorPage[LiveElement]:
        """Return a cursor page of live elements."""
        await require_season(season_id=season_id)
        items = await live_queries.list_live_elements(
            season_id=season_id,
            event_id=event_id,
            after_id=after_id,
            limit=limit,
        )
        return cursor_page(items=items, limit=limit)

    @app.get(
        "/v1/seasons/{season_id}/events/{event_id}/live-elements/{element_id}",
        tags=["Live Data"],
        summary="Get a season live element",
        response_description="The requested player's live row for an FPL event.",
        responses=NOT_FOUND_RESPONSE,
        operation_id="get_live_element",
    )
    async def get_live_element(
        season_id: Annotated[
            str,
            Path(pattern=r"^\d{4}-\d{2}$", description="FPL season id."),
        ],
        event_id: Annotated[
            int,
            Path(ge=1, description="FPL event identifier."),
        ],
        element_id: Annotated[
            int,
            Path(ge=1, description="FPL element/player identifier."),
        ],
    ) -> LiveElement:
        """Return one live element row for one FPL event in one season."""
        await require_season(season_id=season_id)
        live_element = await live_queries.get_live_element(
            season_id=season_id,
            event_id=event_id,
            element_id=element_id,
        )
        if live_element is None:
            raise HTTPException(status_code=404, detail="Live element not found.")
        return live_element

    @app.get(
        "/v1/change-events/recent",
        tags=["Change Events"],
        summary="List recent change events",
        description="Return the newest bounded change-event summaries.",
        operation_id="list_recent_change_events",
    )
    async def recent_change_events(
        limit: Annotated[
            int,
            Query(ge=1, le=200, description="Maximum events to return."),
        ],
    ) -> ChangeEventHistoryResponse:
        events = await change_feed.list_recent_events(limit=limit)
        return ChangeEventHistoryResponse(
            items=[public_change_event(change_event=event) for event in events],
            next_before_id=events[-1].id if len(events) == limit else None,
        )

    @app.get(
        "/v1/change-events/history",
        tags=["Change Events"],
        summary="List older change events",
        description="Return a newest-first page before a known event id.",
        operation_id="list_change_event_history",
    )
    async def change_event_history(
        before_id: Annotated[
            int,
            Query(ge=1, description="Return events older than this id."),
        ],
        limit: Annotated[
            int,
            Query(ge=1, le=200, description="Maximum events to return."),
        ],
    ) -> ChangeEventHistoryResponse:
        events = await change_feed.list_events_before(
            before_id=before_id,
            limit=limit,
        )
        return ChangeEventHistoryResponse(
            items=[public_change_event(change_event=event) for event in events],
            next_before_id=events[-1].id if len(events) == limit else None,
        )

    @app.get(
        "/v1/change-events",
        tags=["Change Events"],
        summary="List change events",
        description=(
            "Return change-event metadata with identifiers greater than `after_id`, "
            "ordered by identifier. Use the last returned identifier to request the "
            "next page."
        ),
        response_description="A page of stored change events.",
        operation_id="list_change_events",
    )
    async def change_events(
        after_id: Annotated[
            int,
            Query(
                ge=0,
                description="Return events after this change-event identifier.",
            ),
        ],
        limit: Annotated[
            int,
            Query(ge=1, le=200, description="Maximum number of events to return."),
        ],
    ) -> ChangeEventsResponse:
        """List stored change-event metadata after a known event id."""
        events = await change_feed.list_events(after_id=after_id, limit=limit)
        return ChangeEventsResponse(
            items=[public_change_event(change_event=event) for event in events],
            next_after_id=events[-1].id if len(events) == limit else None,
        )

    @app.get(
        "/v1/change-events/{change_event_id}/entity-changes",
        tags=["Change Events"],
        summary="List changed entities",
        description="Return field-level entity changes under one family event.",
        operation_id="list_entity_changes",
    )
    async def entity_changes(
        change_event_id: Annotated[
            int,
            Path(ge=1, description="Stored change-event identifier."),
        ],
        after_id: Annotated[
            int,
            Query(ge=0, description="Return entity changes after this id."),
        ],
        limit: Annotated[
            int,
            Query(ge=1, le=100, description="Maximum changes to return."),
        ],
    ) -> EntityChangesResponse:
        changes = await change_feed.list_entity_changes(
            change_event_id=change_event_id,
            after_id=after_id,
            limit=limit,
        )
        return EntityChangesResponse(
            items=[public_entity_change(entity_change=change) for change in changes],
            next_after_id=changes[-1].id if len(changes) == limit else None,
        )

    @app.get(
        "/v1/ingestion-status",
        tags=["Change Events"],
        summary="Inspect automatic ingestion freshness",
        operation_id="get_ingestion_status",
    )
    async def ingestion_status() -> IngestionStatusResponse:
        now = datetime.now(tz=UTC)
        season = await reference_queries.get_current_season()
        if season is None:
            return empty_ingestion_status(
                now=now,
                reference_poll_seconds=reference_poll_seconds,
                idle_poll_seconds=idle_poll_seconds,
            )
        statuses = await change_feed.list_source_statuses(season_id=season.id)
        fixtures = await read_all_status_fixtures(
            reference_queries=reference_queries,
            season_id=season.id,
        )
        return build_ingestion_status(
            season_id=season.id,
            fixtures=fixtures,
            statuses=statuses,
            now=now,
            reference_poll_seconds=reference_poll_seconds,
            live_poll_seconds=live_poll_seconds,
            idle_poll_seconds=idle_poll_seconds,
        )

    return app


async def read_all_status_fixtures(
    *,
    reference_queries: ReferenceQueries,
    season_id: str,
) -> list[Fixture]:
    """Read all fixtures through the existing bounded query port."""
    fixtures: list[Fixture] = []
    after_id = 0
    while True:
        page = await reference_queries.list_fixtures(
            season_id=season_id,
            event_id=None,
            after_id=after_id,
            limit=200,
        )
        fixtures.extend(page)
        if len(page) < 200:
            return fixtures
        next_id = page[-1].id
        if next_id <= after_id:
            raise RuntimeError("Fixture status pagination did not advance.")
        after_id = next_id


def build_ingestion_status(
    *,
    season_id: str,
    fixtures: list[Fixture],
    statuses: list[IngestionSourceStatus],
    now: datetime,
    reference_poll_seconds: int,
    live_poll_seconds: int,
    idle_poll_seconds: int,
) -> IngestionStatusResponse:
    """Derive clear reference/live freshness from stored successful checks."""
    reference_sources = select_statuses(
        statuses=statuses,
        keys={IngestionSourceKey.BOOTSTRAP, IngestionSourceKey.FIXTURES},
        event_id=None,
    )
    reference_last_checked = oldest_checked_at(statuses=reference_sources)
    reference_stale_after = reference_poll_seconds * 2
    reference_state: str = "initializing"
    if len(reference_sources) == 2 and reference_last_checked is not None:
        reference_state = (
            "stale"
            if (now - reference_last_checked).total_seconds()
            > reference_stale_after
            else "healthy"
        )

    active_ends: list[datetime] = []
    next_starts: list[datetime] = []
    active_event_ids: set[int] = set()
    for fixture in fixtures:
        if fixture.event is None or fixture.kickoff_time is None:
            continue
        start = fixture.kickoff_time - WINDOW_BEFORE_KICKOFF
        end = fixture.kickoff_time + WINDOW_AFTER_KICKOFF
        if start <= now < end:
            active_ends.append(end)
            active_event_ids.add(fixture.event)
        elif start > now:
            next_starts.append(start)
    live_sources = [
        status
        for status in statuses
        if status.source_key
        in {
            IngestionSourceKey.CURRENT_FIXTURES,
            IngestionSourceKey.EVENT_STATUS,
            IngestionSourceKey.EVENT_LIVE,
        }
        and (not active_event_ids or status.event_id in active_event_ids)
    ]
    live_last_checked = oldest_checked_at(statuses=live_sources)
    live_stale_after = (
        live_poll_seconds if active_ends else idle_poll_seconds
    ) * 2
    live_state: str = "idle"
    if active_ends:
        required_keys = {status.source_key for status in live_sources}
        if required_keys != {
            IngestionSourceKey.CURRENT_FIXTURES,
            IngestionSourceKey.EVENT_STATUS,
            IngestionSourceKey.EVENT_LIVE,
        }:
            live_state = "initializing"
        elif live_last_checked is None or (
            now - live_last_checked
        ).total_seconds() > live_stale_after:
            live_state = "stale"
        else:
            live_state = "polling"

    return IngestionStatusResponse(
        season_id=season_id,
        checked_at=now,
        reference=PipelineStatusResponse(
            state=reference_state,
            expected_interval_seconds=reference_poll_seconds,
            stale_after_seconds=reference_stale_after,
            last_checked_at=reference_last_checked,
            last_changed_at=newest_changed_at(statuses=reference_sources),
            current_window_end=None,
            next_window_start=None,
        ),
        live=PipelineStatusResponse(
            state=live_state,
            expected_interval_seconds=(
                live_poll_seconds if active_ends else idle_poll_seconds
            ),
            stale_after_seconds=live_stale_after,
            last_checked_at=live_last_checked,
            last_changed_at=newest_changed_at(statuses=live_sources),
            current_window_end=max(active_ends) if active_ends else None,
            next_window_start=min(next_starts) if next_starts else None,
        ),
    )


def empty_ingestion_status(
    *,
    now: datetime,
    reference_poll_seconds: int,
    idle_poll_seconds: int,
) -> IngestionStatusResponse:
    """Return explicit initializing state before the first reference snapshot."""
    return IngestionStatusResponse(
        season_id=None,
        checked_at=now,
        reference=PipelineStatusResponse(
            state="initializing",
            expected_interval_seconds=reference_poll_seconds,
            stale_after_seconds=reference_poll_seconds * 2,
            last_checked_at=None,
            last_changed_at=None,
            current_window_end=None,
            next_window_start=None,
        ),
        live=PipelineStatusResponse(
            state="initializing",
            expected_interval_seconds=idle_poll_seconds,
            stale_after_seconds=idle_poll_seconds * 2,
            last_checked_at=None,
            last_changed_at=None,
            current_window_end=None,
            next_window_start=None,
        ),
    )


def select_statuses(
    *,
    statuses: list[IngestionSourceStatus],
    keys: set[IngestionSourceKey],
    event_id: int | None,
) -> list[IngestionSourceStatus]:
    return [
        status
        for status in statuses
        if status.source_key in keys and status.event_id == event_id
    ]


def oldest_checked_at(
    *,
    statuses: list[IngestionSourceStatus],
) -> datetime | None:
    values = [status.checked_at for status in statuses]
    return min(values) if values else None


def newest_changed_at(
    *,
    statuses: list[IngestionSourceStatus],
) -> datetime | None:
    values = [
        status.last_changed_at
        for status in statuses
        if status.last_changed_at is not None
    ]
    return max(values) if values else None


def raise_not_ingested(*, entity: str) -> NoReturn:
    """Raise a consistent not-yet-ingested response."""
    message = f"FPL {entity} data has not been ingested yet."
    raise HTTPException(status_code=503, detail=message)


def cursor_page[ItemT: Element | Fixture | LiveElement](
    *,
    items: list[ItemT],
    limit: int,
) -> CursorPage[ItemT]:
    """Build a page whose continuation cursor is the last returned id."""
    return CursorPage[ItemT](
        items=items,
        next_after_id=items[-1].id if len(items) == limit else None,
    )
