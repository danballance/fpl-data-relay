"""Ingestion source keys and logical FPL entity-family change names."""

from enum import StrEnum


class IngestionSourceKey(StrEnum):
    """Upstream source identifiers used for latest ingestion metadata."""

    BOOTSTRAP = "bootstrap-static"
    FIXTURES = "fixtures"
    CURRENT_FIXTURES = "fixtures-current-event"
    EVENT_STATUS = "event-status"
    EVENT_LIVE = "event-live"


class EntityFamily(StrEnum):
    """Normalised entity families exposed by change events."""

    EVENTS = "events"
    PHASES = "phases"
    TEAMS = "teams"
    ELEMENT_TYPES = "element_types"
    ELEMENT_STATS = "element_stats"
    ELEMENTS = "elements"
    FIXTURES = "fixtures"
    EVENT_STATUS = "event_status"
    EVENT_LIVE = "event_live"


# Backwards-compatible alias for older tests/imports. The implementation now uses
# IngestionSourceKey plus EntityFamily instead of opaque resource payload keys.
ResourceKey = IngestionSourceKey

EVENT_NAMES: dict[EntityFamily | ResourceKey, str] = {
    EntityFamily.EVENTS: "events.updated",
    EntityFamily.PHASES: "phases.updated",
    EntityFamily.TEAMS: "teams.updated",
    EntityFamily.ELEMENT_TYPES: "element_types.updated",
    EntityFamily.ELEMENT_STATS: "element_stats.updated",
    EntityFamily.ELEMENTS: "elements.updated",
    EntityFamily.FIXTURES: "fixtures.updated",
    EntityFamily.EVENT_STATUS: "event_status.updated",
    EntityFamily.EVENT_LIVE: "event_live.updated",
    ResourceKey.BOOTSTRAP: "bootstrap.updated",
    ResourceKey.FIXTURES: "fixtures.updated",
    ResourceKey.CURRENT_FIXTURES: "current_fixtures.updated",
    ResourceKey.EVENT_STATUS: "event_status.updated",
    ResourceKey.EVENT_LIVE: "event_live.updated",
}
