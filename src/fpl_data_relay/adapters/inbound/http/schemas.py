"""Pydantic contracts for relay-specific HTTP responses."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from fpl_data_relay.domain.changes import (
    ChangeEvent,
    ChangeKind,
    ChangeValue,
    EntityChange,
    EntityFamily,
    FieldChange,
    IngestionSourceKey,
)
from fpl_data_relay.domain.types import JsonValue


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
    """Public summary of accurate changes within one entity family."""

    id: int
    season_id: str
    entity_family: EntityFamily
    event_name: str
    source_key: IngestionSourceKey
    source_event_id: int | None
    payload_hash: str
    created_count: int
    updated_count: int
    deleted_count: int
    fetched_at: datetime
    created_at: datetime


class ChangeEventsResponse(ApiResponse):
    """Forward cursor page of change-event summaries."""

    items: list[ChangeEventResponse]
    next_after_id: int | None


class ChangeEventHistoryResponse(ApiResponse):
    """Newest-first page of change-event summaries."""

    items: list[ChangeEventResponse]
    next_before_id: int | None


class ChangeValueResponse(ApiResponse):
    """Public field value that distinguishes absence from JSON null."""

    present: bool
    value: JsonValue


class FieldChangeResponse(ApiResponse):
    """Public before and after values for one top-level field."""

    field: str
    before: ChangeValueResponse
    after: ChangeValueResponse


class EntityChangeResponse(ApiResponse):
    """Public field-level changes for one logical entity."""

    id: int
    change_event_id: int
    entity_key: str
    entity_label: str
    kind: ChangeKind
    fields: list[FieldChangeResponse]
    created_at: datetime


class EntityChangesResponse(ApiResponse):
    """Cursor page of entity changes under one family event."""

    items: list[EntityChangeResponse]
    next_after_id: int | None


class PipelineStatusResponse(ApiResponse):
    """Freshness status for one automatic ingestion stream."""

    state: Literal["initializing", "healthy", "stale", "idle", "polling"]
    expected_interval_seconds: int
    stale_after_seconds: int
    last_checked_at: datetime | None
    last_changed_at: datetime | None
    current_window_end: datetime | None
    next_window_start: datetime | None


class IngestionStatusResponse(ApiResponse):
    """Reference and live automatic-ingestion freshness."""

    season_id: str | None
    checked_at: datetime
    reference: PipelineStatusResponse
    live: PipelineStatusResponse


class CursorPage[ItemT](ApiResponse):
    """Cursor page for an entity with an integer id."""

    items: list[ItemT]
    next_after_id: int | None


def public_change_event(*, change_event: ChangeEvent) -> ChangeEventResponse:
    """Convert an internal change event to its explicit public contract."""
    return ChangeEventResponse.model_validate(change_event, from_attributes=True)


def public_entity_change(*, entity_change: EntityChange) -> EntityChangeResponse:
    """Convert an internal entity change to its explicit public contract."""
    return EntityChangeResponse(
        id=entity_change.id,
        change_event_id=entity_change.change_event_id,
        entity_key=entity_change.entity_key,
        entity_label=entity_change.entity_label,
        kind=entity_change.kind,
        fields=[public_field_change(field=field) for field in entity_change.fields],
        created_at=entity_change.created_at,
    )


def public_field_change(*, field: FieldChange) -> FieldChangeResponse:
    """Convert one internal field diff to its explicit public contract."""
    return FieldChangeResponse(
        field=field.field,
        before=public_change_value(value=field.before),
        after=public_change_value(value=field.after),
    )


def public_change_value(*, value: ChangeValue) -> ChangeValueResponse:
    """Convert one presence-aware field value."""
    return ChangeValueResponse(present=value.present, value=value.value)
