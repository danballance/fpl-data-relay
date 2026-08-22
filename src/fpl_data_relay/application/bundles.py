"""Strict wire contracts for payloads collected outside AWS."""

import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from fpl_data_relay.application.jobs import IngestionJob, LiveJob, ReferenceJob
from fpl_data_relay.domain.types import JsonValue

MAX_COLLECTED_PAYLOAD_BYTES = 20 * 1024 * 1024
PAYLOAD_VERSION = 2


class CollectedBundle(BaseModel):
    """Common immutable metadata for one collected upstream snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    version: Literal[2]
    fetched_at: datetime

    @field_validator("fetched_at")
    @classmethod
    def fetched_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        """Reject ambiguous timestamps at the wire boundary."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware.")
        return value


class ReferencePayloadBundle(CollectedBundle):
    """Raw reference documents collected together on the NAS."""

    kind: Literal["reference"]
    job: ReferenceJob
    bootstrap_static: JsonValue
    fixtures: JsonValue
    event_status: JsonValue


class LivePayloadBundle(CollectedBundle):
    """Raw event-scoped documents collected together on the NAS."""

    kind: Literal["live"]
    job: LiveJob
    event_status: JsonValue
    current_fixtures: JsonValue
    event_live: JsonValue


PayloadBundle = Annotated[
    ReferencePayloadBundle | LivePayloadBundle,
    Field(discriminator="kind"),
]
PAYLOAD_BUNDLE_ADAPTER = TypeAdapter(PayloadBundle)


class CollectedPayloadMessage(BaseModel):
    """Small SQS message pointing at one immutable S3 payload bundle."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    version: Literal[2]
    job: IngestionJob
    bucket: str = Field(min_length=3)
    key: str = Field(min_length=1)
    size_bytes: int = Field(gt=0, le=MAX_COLLECTED_PAYLOAD_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def canonical_bundle_bytes(*, bundle: PayloadBundle) -> bytes:
    """Serialize a bundle deterministically for hashing and transport."""
    payload = bundle.model_dump(mode="json")
    return json.dumps(
        payload,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def payload_sha256_bytes(*, payload: bytes) -> str:
    """Return the lowercase SHA-256 for exact uploaded bytes."""
    return hashlib.sha256(payload).hexdigest()
