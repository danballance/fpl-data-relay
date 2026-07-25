"""Pure domain rules for canonical payloads and ingestion decisions."""

import hashlib
import json
from collections.abc import Sequence
from typing import cast

from pydantic import BaseModel

from fpl_data_relay.domain.fixtures import Fixture
from fpl_data_relay.domain.reference import BootstrapStatic, Season
from fpl_data_relay.domain.types import JsonValue


def canonical_json(*, payload: object) -> str:
    """Serialize a payload with deterministic key order and separators."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def payload_sha256(*, payload: object) -> str:
    """Hash a payload using the relay's canonical JSON representation."""
    canonical_payload = canonical_json(payload=payload)
    return hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()


def parse_json_payload(*, payload: str) -> JsonValue:
    """Decode stored JSON text and verify it is JSON-compatible."""
    parsed_payload = json.loads(payload)
    if isinstance(parsed_payload, dict | list | str | int | float | bool):
        return parsed_payload
    if parsed_payload is None:
        return None
    raise TypeError(f"Decoded payload is not JSON-compatible: {type(parsed_payload)}")


def model_to_payload(*, model: BaseModel | Sequence[BaseModel]) -> JsonValue:
    """Convert validated Pydantic models to JSON-compatible values."""
    if isinstance(model, BaseModel):
        return cast("JsonValue", model.model_dump(mode="json"))
    return [cast("JsonValue", item.model_dump(mode="json")) for item in model]


def select_current_event_id(*, bootstrap: BootstrapStatic) -> int | None:
    """Select the current FPL event when the season has started."""
    current_events = [event for event in bootstrap.events if event.is_current]
    if not current_events:
        return None
    if len(current_events) > 1:
        count = len(current_events)
        raise ValueError(f"Expected at most one current FPL event, found {count}.")
    return current_events[0].id


def derive_season(*, bootstrap: BootstrapStatic) -> Season:
    """Derive the active FPL season from bootstrap event deadlines."""
    if not bootstrap.events:
        raise ValueError("Cannot derive FPL season without events.")
    missing_deadline_ids = [
        event.id for event in bootstrap.events if event.deadline_time is None
    ]
    if missing_deadline_ids:
        joined_ids = ", ".join(str(event_id) for event_id in missing_deadline_ids)
        raise ValueError(
            "Cannot derive FPL season: event deadline_time missing for "
            f"event id(s) {joined_ids}.",
        )
    deadlines = [
        event.deadline_time
        for event in bootstrap.events
        if event.deadline_time is not None
    ]
    first_deadline = min(deadlines)
    last_deadline = max(deadlines)
    if first_deadline.year + 1 != last_deadline.year:
        raise ValueError(
            "Cannot derive FPL season: first and last deadlines do not span "
            "exactly two adjacent years.",
        )
    start_year = first_deadline.year
    end_year = last_deadline.year
    return Season(
        id=f"{start_year}-{end_year % 100:02d}",
        start_year=start_year,
        end_year=end_year,
        first_deadline_time=first_deadline,
        last_deadline_time=last_deadline,
        is_current=True,
    )


def has_active_fixture(*, fixtures: list[Fixture]) -> bool:
    """Return whether any current fixture is started but not finished."""
    return any(fixture.started and not fixture.finished for fixture in fixtures)
