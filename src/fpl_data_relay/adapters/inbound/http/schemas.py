"""Pydantic contracts for relay-specific HTTP responses."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from fpl_data_relay.domain.changes import (
    ChangeEvent,
    EntityFamily,
    IngestionSourceKey,
)


class ApiResponse(BaseModel):
    """Immutable base model for relay-owned response contracts."""

    model_config = ConfigDict(frozen=True)


class HealthResponse(ApiResponse):
    """Service liveness and expected database schema version."""

    status: Literal["ok"]
    schema_version: int


class ReadyResponse(ApiResponse):
    """Database readiness and applied schema version."""

    status: Literal["ready"]
    schema_version: int


class ErrorResponse(ApiResponse):
    """Standard FastAPI error payload returned by relay endpoints."""

    detail: str


class ServiceErrorResponse(ErrorResponse):
    """Stable machine-readable service availability error."""

    code: Literal[
        "database_waking",
        "database_unavailable",
        "schema_unavailable",
    ]
    retry_after_seconds: int | None


class ChangeEventResponse(ApiResponse):
    """Public metadata describing one changed entity family."""

    id: int
    season_id: str | None
    entity_family: EntityFamily
    event_name: str
    source_key: IngestionSourceKey | None
    resource_key: IngestionSourceKey | None
    event_id: int | None
    payload_hash: str
    fetched_at: datetime
    created_at: datetime


class ChangeEventsResponse(ApiResponse):
    """Page of change events after a caller-supplied event identifier."""

    items: list[ChangeEventResponse]
    next_after_id: int | None


class CursorPage[ItemT](ApiResponse):
    """Cursor page for an entity with an integer id."""

    items: list[ItemT]
    next_after_id: int | None


def public_change_event(*, change_event: ChangeEvent) -> ChangeEventResponse:
    """Convert an internal change event to its explicit public contract."""
    return ChangeEventResponse(
        id=change_event.id,
        season_id=change_event.season_id,
        entity_family=change_event.entity_family,
        event_name=change_event.event_name,
        source_key=change_event.source_key,
        resource_key=change_event.resource_key,
        event_id=change_event.event_id,
        payload_hash=change_event.payload_hash,
        fetched_at=change_event.fetched_at,
        created_at=change_event.created_at,
    )
