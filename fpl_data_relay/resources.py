from enum import StrEnum


class ResourceKey(StrEnum):
    BOOTSTRAP = "bootstrap"
    FIXTURES = "fixtures"
    CURRENT_FIXTURES = "current_fixtures"
    EVENT_STATUS = "event_status"
    EVENT_LIVE = "event_live"


EVENT_NAMES: dict[ResourceKey, str] = {
    ResourceKey.BOOTSTRAP: "bootstrap.updated",
    ResourceKey.FIXTURES: "fixtures.updated",
    ResourceKey.CURRENT_FIXTURES: "current_fixtures.updated",
    ResourceKey.EVENT_STATUS: "event_status.updated",
    ResourceKey.EVENT_LIVE: "event_live.updated",
}

