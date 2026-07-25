"""Inbound HTTP adapter for normalised FPL relay data."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, NoReturn, Protocol

from fastapi import FastAPI, Header, HTTPException, Path, Query, Request
from fastapi.responses import StreamingResponse

from fpl_data_relay.adapters.inbound.http.schemas import (
    ChangeEventsResponse,
    ErrorResponse,
    HealthResponse,
    public_change_event,
)
from fpl_data_relay.adapters.inbound.scheduler import RelayScheduler
from fpl_data_relay.application.change_feed import ChangeFeed
from fpl_data_relay.application.database import SCHEMA_VERSION
from fpl_data_relay.application.live_queries import LiveQueries
from fpl_data_relay.application.ports.administration import SchemaManager
from fpl_data_relay.application.ports.inbound import IngestionRunner
from fpl_data_relay.application.reference_queries import ReferenceQueries
from fpl_data_relay.domain.changes import ChangeEvent
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
        "description": "Change-event replay and Server-Sent Events streaming.",
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
INVALID_LAST_EVENT_ID_RESPONSE: dict[int | str, dict[str, object]] = {
    400: {
        "description": "The Last-Event-ID header is not a non-negative integer.",
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
        },
    },
}


class DisconnectWatcher(Protocol):
    """Minimal request interface needed by the SSE stream."""

    async def is_disconnected(self) -> bool:
        """Return whether the client has closed the response stream."""
        ...


class EventStreamResponse(StreamingResponse):
    """Streaming response whose documented media type is SSE."""

    media_type = "text/event-stream"


def create_app(
    *,
    reference_queries: ReferenceQueries,
    live_queries: LiveQueries,
    change_feed: ChangeFeed,
    schema_manager: SchemaManager,
    ingestion_service: IngestionRunner,
    reference_poll_seconds: int,
    live_poll_seconds: int,
    idle_poll_seconds: int,
    sse_heartbeat_seconds: int,
    start_scheduler: bool,
    shutdown: Callable[[], Awaitable[None]],
) -> FastAPI:
    """Build the FastAPI app around injected application use cases."""

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        """Validate dependencies, start optional polling, and close resources."""
        await schema_manager.check_schema_version(expected_version=SCHEMA_VERSION)
        scheduler = RelayScheduler(
            ingestion_service=ingestion_service,
            reference_poll_seconds=reference_poll_seconds,
            live_poll_seconds=live_poll_seconds,
            idle_poll_seconds=idle_poll_seconds,
        )
        scheduler_task = scheduler.start() if start_scheduler else None
        try:
            yield
        finally:
            if scheduler_task is not None:
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
    ) -> list[Element]:
        """Return all stored FPL elements for one season."""
        await require_season(season_id=season_id)
        return await reference_queries.list_elements(season_id=season_id)

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
    ) -> list[Fixture]:
        """Return all stored FPL fixtures for one season."""
        await require_season(season_id=season_id)
        return await reference_queries.list_fixtures(
            season_id=season_id,
            event_id=None,
        )

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
    ) -> list[Fixture]:
        """Return fixtures for one FPL event in one season."""
        await require_season(season_id=season_id)
        return await reference_queries.list_fixtures(
            season_id=season_id,
            event_id=event_id,
        )

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
    ) -> list[LiveElement]:
        """Return live element rows for one FPL event in one season."""
        await require_season(season_id=season_id)
        return await live_queries.list_live_elements(
            season_id=season_id,
            event_id=event_id,
        )

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
        ] = 0,
        limit: Annotated[
            int,
            Query(ge=1, le=1000, description="Maximum number of events to return."),
        ] = 100,
    ) -> ChangeEventsResponse:
        """List stored change-event metadata after a known event id."""
        events = await change_feed.list_events(after_id=after_id, limit=limit)
        return ChangeEventsResponse(
            events=[public_change_event(change_event=event) for event in events],
        )

    @app.get(
        "/v1/stream",
        tags=["Change Events"],
        summary="Stream change events",
        description=(
            "Open a Server-Sent Events stream. Supply `Last-Event-ID` to replay "
            "events after a previously received identifier. Each update contains "
            "`id`, `event`, and JSON `data` fields; idle connections receive "
            "heartbeat comments. Swagger UI cannot complete this long-lived request."
        ),
        response_model=None,
        response_class=EventStreamResponse,
        responses={
            200: {
                "description": "A long-lived Server-Sent Events stream.",
                "content": {
                    "text/event-stream": {
                        "schema": {"type": "string"},
                        "example": (
                            "id: 7\n"
                            "event: event_live.updated\n"
                            'data: {"id":7,"event_name":"event_live.updated"}\n\n'
                        ),
                    },
                },
            },
            **INVALID_LAST_EVENT_ID_RESPONSE,
        },
        operation_id="stream_change_events",
    )
    async def stream(
        request: Request,
        last_event_id: Annotated[
            str | None,
            Header(
                alias="Last-Event-ID",
                description=(
                    "Replay events whose identifiers are greater than this "
                    "non-negative integer."
                ),
            ),
        ] = None,
    ) -> EventStreamResponse:
        """Stream change events as Server-Sent Events with replay support."""
        after_id = parse_last_event_id(last_event_id=last_event_id)
        generator = sse_generator(
            request=request,
            change_feed=change_feed,
            after_id=after_id,
            heartbeat_seconds=sse_heartbeat_seconds,
        )
        return EventStreamResponse(
            generator,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def raise_not_ingested(*, entity: str) -> NoReturn:
    """Raise a consistent not-yet-ingested response."""
    message = f"FPL {entity} data has not been ingested yet."
    raise HTTPException(status_code=503, detail=message)


def parse_last_event_id(*, last_event_id: str | None) -> int:
    """Parse and validate the SSE Last-Event-ID header."""
    if last_event_id is None or last_event_id == "":
        return 0
    try:
        parsed_event_id = int(last_event_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="Last-Event-ID must be an integer.",
        ) from exc
    if parsed_event_id < 0:
        raise HTTPException(status_code=400, detail="Last-Event-ID must be >= 0.")
    return parsed_event_id


async def sse_generator(
    *,
    request: DisconnectWatcher,
    change_feed: ChangeFeed,
    after_id: int,
    heartbeat_seconds: int,
) -> AsyncIterator[str]:
    """Yield SSE frames from stored change events and heartbeat intervals."""
    async for event in change_feed.watch_events(
        after_id=after_id,
        heartbeat_seconds=heartbeat_seconds,
    ):
        if await request.is_disconnected():
            break
        if event is None:
            yield ": heartbeat\n\n"
            continue
        yield encode_sse_event(change_event=event)


def encode_sse_event(*, change_event: ChangeEvent) -> str:
    """Encode one change-event model as an SSE event frame."""
    data = change_event.model_dump_json()
    return (
        f"id: {change_event.id}\n"
        f"event: {change_event.event_name}\n"
        f"data: {data}\n\n"
    )
