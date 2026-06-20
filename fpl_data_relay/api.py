from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Protocol

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from fpl_data_relay.config import Settings
from fpl_data_relay.ingestion import IngestionService, RelayScheduler
from fpl_data_relay.resources import ResourceKey
from fpl_data_relay.schemas import SCHEMA_VERSION
from fpl_data_relay.store import ChangeEvent, ResourceStore, StoredResource

ResourceGetter = Callable[[ResourceKey], Awaitable[JSONResponse]]


class DisconnectWatcher(Protocol):
    async def is_disconnected(self) -> bool: ...

RESOURCE_ROUTES: dict[str, ResourceKey] = {
    "/v1/bootstrap-static": ResourceKey.BOOTSTRAP,
    "/v1/fixtures": ResourceKey.FIXTURES,
    "/v1/events/current/fixtures": ResourceKey.CURRENT_FIXTURES,
    "/v1/event-status": ResourceKey.EVENT_STATUS,
    "/v1/events/current/live": ResourceKey.EVENT_LIVE,
}


def create_app(
    *,
    settings: Settings,
    store: ResourceStore,
    ingestion_service: IngestionService,
    start_scheduler: bool,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await store.check_schema_version(expected_version=SCHEMA_VERSION)
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
            await store.close()

    app = FastAPI(title="FPL Data Relay", version="0.1.0", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, int | str]:
        return {"status": "ok", "schema_version": SCHEMA_VERSION}

    async def get_resource(resource_key: ResourceKey) -> JSONResponse:
        stored_resource = await store.get_resource(resource_key=resource_key)
        if stored_resource is None:
            message = f"Resource {resource_key.value!r} has not been ingested yet."
            raise HTTPException(status_code=503, detail=message)
        return JSONResponse(
            content=stored_resource.payload,
            headers=resource_headers(stored_resource=stored_resource),
        )

    for route_path, resource_key in RESOURCE_ROUTES.items():
        app.add_api_route(
            path=route_path,
            endpoint=_resource_endpoint(
                resource_key=resource_key,
                get_resource=get_resource,
            ),
            methods=["GET"],
        )

    @app.get("/v1/change-events")
    async def change_events(
        after_id: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> dict[str, list[dict[str, int | str | None]]]:
        events = await store.list_change_events(after_id=after_id, limit=limit)
        return {"events": [event.to_public_dict() for event in events]}

    @app.get("/v1/stream")
    async def stream(
        request: Request,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        after_id = parse_last_event_id(last_event_id=last_event_id)
        generator = sse_generator(
            request=request,
            store=store,
            after_id=after_id,
            heartbeat_seconds=settings.sse_heartbeat_seconds,
        )
        return StreamingResponse(
            generator,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def _resource_endpoint(
    *,
    resource_key: ResourceKey,
    get_resource: ResourceGetter,
) -> Callable[[], Awaitable[JSONResponse]]:
    async def endpoint() -> JSONResponse:
        return await get_resource(resource_key)

    endpoint.__name__ = f"get_{resource_key.value}"
    return endpoint


def resource_headers(*, stored_resource: StoredResource) -> dict[str, str]:
    return {
        "ETag": f'"{stored_resource.payload_hash}"',
        "X-FPL-Relay-Fetched-At": stored_resource.fetched_at.isoformat(),
        "X-FPL-Relay-Checked-At": stored_resource.checked_at.isoformat(),
        "X-FPL-Relay-Resource-Key": stored_resource.resource_key.value,
    }


def parse_last_event_id(*, last_event_id: str | None) -> int:
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
    store: ResourceStore,
    after_id: int,
    heartbeat_seconds: int,
) -> AsyncIterator[str]:
    async for event in store.watch_change_events(
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
    data = change_event.model_dump_json()
    return (
        f"id: {change_event.id}\n"
        f"event: {change_event.event_name}\n"
        f"data: {data}\n\n"
    )
