"""Change-event domain types and event-name rules."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


EVENT_NAMES: dict[EntityFamily, str] = {
    EntityFamily.EVENTS: "events.updated",
    EntityFamily.PHASES: "phases.updated",
    EntityFamily.TEAMS: "teams.updated",
    EntityFamily.ELEMENT_TYPES: "element_types.updated",
    EntityFamily.ELEMENT_STATS: "element_stats.updated",
    EntityFamily.ELEMENTS: "elements.updated",
    EntityFamily.FIXTURES: "fixtures.updated",
    EntityFamily.EVENT_STATUS: "event_status.updated",
    EntityFamily.EVENT_LIVE: "event_live.updated",
}


class IngestionMetadata(BaseModel):
    """Metadata for one upstream source fetch."""

    model_config = ConfigDict(frozen=True)

    season_id: str
    source_key: IngestionSourceKey
    event_id: int | None
    payload_hash: str
    fetched_at: datetime
    checked_at: datetime

    @field_validator("payload_hash")
    @classmethod
    def payload_hash_must_be_sha256(cls, value: str) -> str:
        """Validate that stored payload hashes look like SHA-256 digests."""
        if len(value) != 64:
            raise ValueError("payload_hash must be a SHA-256 hex digest.")
        return value


class ChangeEvent(BaseModel):
    """Metadata describing a changed normalised entity family."""

    model_config = ConfigDict(frozen=True)

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

    def to_public_dict(self) -> dict[str, int | str | None]:
        """Serialize change-event metadata for the public API."""
        return {
            "id": self.id,
            "season_id": self.season_id,
            "entity_family": self.entity_family.value,
            "event_name": self.event_name,
            "source_key": None if self.source_key is None else self.source_key.value,
            "resource_key": (
                None if self.resource_key is None else self.resource_key.value
            ),
            "event_id": self.event_id,
            "payload_hash": self.payload_hash,
            "fetched_at": self.fetched_at.isoformat(),
            "created_at": self.created_at.isoformat(),
        }


class UpsertOutcome(BaseModel):
    """Result of attempting to upsert one source payload."""

    model_config = ConfigDict(frozen=True)

    changed: bool
    change_events: list[ChangeEvent] = Field(default_factory=list)
