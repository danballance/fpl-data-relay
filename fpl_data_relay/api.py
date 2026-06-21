"""HTTP application and SSE endpoints for normalised FPL relay data."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, NoReturn, Protocol, cast

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from fpl_data_relay.config import Settings
from fpl_data_relay.fpl_client import model_to_payload
from fpl_data_relay.fpl_models import (
    Element,
    ElementType,
    Event,
    EventLiveResponse,
    EventStatusResponse,
    Fixture,
    LiveElement,
    Phase,
    Team,
)
from fpl_data_relay.ingestion import IngestionService, RelayScheduler
from fpl_data_relay.json_types import JsonValue
from fpl_data_relay.schemas import SCHEMA_VERSION
from fpl_data_relay.store import ChangeEvent, FplStore


class DisconnectWatcher(Protocol):
    """Minimal request interface needed by the SSE stream."""

    async def is_disconnected(self) -> bool:
        """Return whether the client has closed the response stream."""
        ...


def create_app(
    *,
    settings: Settings,
    store: Any,
    ingestion_service: IngestionService,
    start_scheduler: bool,
) -> FastAPI:
    """Build the FastAPI app around an injected store and ingestion service."""
    typed_store = cast("FplStore", store)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        """Validate dependencies, start optional polling, and close resources."""
        await typed_store.check_schema_version(expected_version=SCHEMA_VERSION)
        scheduler = RelayScheduler(
            ingestion_service=ingestion_service,
            reference_poll_seconds=settings.reference_poll_seconds,
            live_poll_seconds=settings.live_poll_seconds,
            idle_poll_seconds=settings.idle_poll_seconds,
        )
        scheduler_task = scheduler.start() if start_scheduler else None
        try:
            yield
        finally:
            if scheduler_task is not None:
                await scheduler.stop(task=scheduler_task)
            await ingestion_service.close()
            await typed_store.close()

    app = FastAPI(title="FPL Data Relay", version="0.1.0", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, int | str]:
        """Report service liveness and the schema version expected by the app."""
        return {"status": "ok", "schema_version": SCHEMA_VERSION}

    @app.get("/v1/events")
    async def list_events() -> JsonValue:
        """Return all stored FPL events."""
        return model_to_payload(model=await typed_store.list_events())

    @app.get("/v1/events/current")
    async def current_event() -> JsonValue:
        """Return the single current FPL event."""
        event = await typed_store.get_current_event()
        if event is None:
            raise_not_ingested(entity="current event")
        return model_to_payload(model=event)

    @app.get("/v1/events/{event_id}")
    async def get_event(event_id: int) -> JsonValue:
        """Return one stored FPL event."""
        event = await typed_store.get_event(event_id=event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="Event not found.")
        return model_to_payload(model=event)

    @app.get("/v1/phases")
    async def list_phases() -> JsonValue:
        """Return all stored FPL phases."""
        return model_to_payload(model=await typed_store.list_phases())

    @app.get("/v1/teams")
    async def list_teams() -> JsonValue:
        """Return all stored FPL teams."""
        return model_to_payload(model=await typed_store.list_teams())

    @app.get("/v1/teams/{team_id}")
    async def get_team(team_id: int) -> JsonValue:
        """Return one stored FPL team."""
        team = await typed_store.get_team(team_id=team_id)
        if team is None:
            raise HTTPException(status_code=404, detail="Team not found.")
        return model_to_payload(model=team)

    @app.get("/v1/element-types")
    async def list_element_types() -> JsonValue:
        """Return all stored FPL element types."""
        return model_to_payload(model=await typed_store.list_element_types())

    @app.get("/v1/elements")
    async def list_elements() -> JsonValue:
        """Return all stored FPL elements."""
        return model_to_payload(model=await typed_store.list_elements())

    @app.get("/v1/elements/{element_id}")
    async def get_element(element_id: int) -> JsonValue:
        """Return one stored FPL element."""
        element = await typed_store.get_element(element_id=element_id)
        if element is None:
            raise HTTPException(status_code=404, detail="Element not found.")
        return model_to_payload(model=element)

    @app.get("/v1/fixtures")
    async def list_fixtures() -> JsonValue:
        """Return all stored FPL fixtures."""
        return model_to_payload(model=await typed_store.list_fixtures(event_id=None))

    @app.get("/v1/events/{event_id}/fixtures")
    async def list_event_fixtures(event_id: int) -> JsonValue:
        """Return fixtures for one FPL event."""
        return model_to_payload(
            model=await typed_store.list_fixtures(event_id=event_id),
        )

    @app.get("/v1/event-status")
    async def event_status() -> JsonValue:
        """Return latest event-status response from normalised rows."""
        status = await typed_store.get_event_status()
        if status is None:
            raise_not_ingested(entity="event status")
        return model_to_payload(model=status)

    @app.get("/v1/events/{event_id}/live-elements")
    async def list_live_elements(event_id: int) -> JsonValue:
        """Return live element rows for one FPL event."""
        return model_to_payload(
            model=await typed_store.list_live_elements(event_id=event_id),
        )

    @app.get("/v1/events/{event_id}/live-elements/{element_id}")
    async def get_live_element(event_id: int, element_id: int) -> JsonValue:
        """Return one live element row for one FPL event."""
        live_element = await typed_store.get_live_element(
            event_id=event_id,
            element_id=element_id,
        )
        if live_element is None:
            raise HTTPException(status_code=404, detail="Live element not found.")
        return model_to_payload(model=live_element)

    @app.get("/v1/change-events")
    async def change_events(
        after_id: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> dict[str, list[dict[str, int | str | None]]]:
        """List stored change-event metadata after a known event id."""
        events = await typed_store.list_change_events(after_id=after_id, limit=limit)
        return {"events": [event.to_public_dict() for event in events]}

    @app.get("/v1/stream")
    async def stream(
        request: Request,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        """Stream change events as Server-Sent Events with replay support."""
        after_id = parse_last_event_id(last_event_id=last_event_id)
        generator = sse_generator(
            request=request,
            store=typed_store,
            after_id=after_id,
            heartbeat_seconds=settings.sse_heartbeat_seconds,
        )
        return StreamingResponse(
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
    store: Any,
    after_id: int,
    heartbeat_seconds: int,
) -> AsyncIterator[str]:
    """Yield SSE frames from stored change events and heartbeat intervals."""
    typed_store = cast("FplStore", store)
    async for event in typed_store.watch_change_events(
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


__all__ = [
    "Element",
    "ElementType",
    "Event",
    "EventLiveResponse",
    "EventStatusResponse",
    "Fixture",
    "LiveElement",
    "Phase",
    "Team",
    "create_app",
    "encode_sse_event",
    "parse_last_event_id",
    "sse_generator",
]
