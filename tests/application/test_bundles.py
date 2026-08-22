import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from fpl_data_relay.application.bundles import (
    MAX_COLLECTED_PAYLOAD_BYTES,
    PAYLOAD_BUNDLE_ADAPTER,
    CollectedPayloadMessage,
    LivePayloadBundle,
    ReferencePayloadBundle,
    canonical_bundle_bytes,
    payload_sha256_bytes,
)
from fpl_data_relay.application.jobs import LiveJob, ReferenceJob


def reference_bundle() -> ReferencePayloadBundle:
    return ReferencePayloadBundle(
        version=2,
        kind="reference",
        job=ReferenceJob(version=1, kind="reference"),
        fetched_at=datetime(2026, 7, 26, 12, tzinfo=UTC),
        bootstrap_static={"events": []},
        fixtures=[],
        event_status={"status": []},
    )


def test_bundle_serialization_is_canonical_and_round_trips() -> None:
    bundle = reference_bundle()
    payload = canonical_bundle_bytes(bundle=bundle)
    parsed = PAYLOAD_BUNDLE_ADAPTER.validate_json(payload)
    assert parsed == bundle
    assert payload == canonical_bundle_bytes(bundle=parsed)
    assert len(payload_sha256_bytes(payload=payload)) == 64


def test_bundle_contract_rejects_naive_time_and_unknown_fields() -> None:
    payload = reference_bundle().model_dump(mode="json")
    payload["fetched_at"] = "2026-07-26T12:00:00"
    with pytest.raises(ValidationError, match="timezone-aware"):
        PAYLOAD_BUNDLE_ADAPTER.validate_json(json.dumps(payload))
    payload["fetched_at"] = "2026-07-26T12:00:00Z"
    payload["unexpected"] = True
    with pytest.raises(ValidationError, match="extra"):
        PAYLOAD_BUNDLE_ADAPTER.validate_json(json.dumps(payload))


def test_collected_message_enforces_hash_size_and_strict_fields() -> None:
    job = ReferenceJob(version=1, kind="reference")
    message = CollectedPayloadMessage(
        version=2,
        job=job,
        bucket="payload-bucket",
        key="payloads/v2/reference/key.json",
        size_bytes=10,
        sha256="a" * 64,
    )
    assert message.job == job
    with pytest.raises(ValidationError):
        CollectedPayloadMessage.model_validate(
            {
                **message.model_dump(),
                "sha256": "invalid",
            },
        )
    with pytest.raises(ValidationError):
        CollectedPayloadMessage.model_validate(
            {
                **message.model_dump(),
                "extra": True,
            },
        )
    with pytest.raises(ValidationError):
        CollectedPayloadMessage.model_validate(
            {
                **message.model_dump(),
                "size_bytes": str(message.size_bytes),
            },
        )
    with pytest.raises(ValidationError):
        CollectedPayloadMessage(
            version=2,
            job=job,
            bucket="payload-bucket",
            key="payloads/v2/reference/key.json",
            size_bytes=MAX_COLLECTED_PAYLOAD_BYTES + 1,
            sha256="a" * 64,
        )


def test_live_bundle_rejects_naive_or_reversed_window() -> None:
    start = datetime(2026, 7, 26, 12, tzinfo=UTC)
    with pytest.raises(ValidationError, match="timezone-aware"):
        LiveJob(
            version=1,
            kind="live",
            season_id="2025-26",
            event_id=1,
            window_start=start.replace(tzinfo=None),
            window_end=start + timedelta(hours=1),
        )
    with pytest.raises(ValidationError, match="after window_start"):
        LiveJob(
            version=1,
            kind="live",
            season_id="2025-26",
            event_id=1,
            window_start=start,
            window_end=start,
        )
    job = LiveJob(
        version=1,
        kind="live",
        season_id="2025-26",
        event_id=1,
        window_start=start,
        window_end=start + timedelta(hours=1),
    )
    bundle = LivePayloadBundle(
        version=2,
        kind="live",
        job=job,
        fetched_at=start,
        event_status={},
        current_fixtures=[],
        event_live={},
    )
    assert PAYLOAD_BUNDLE_ADAPTER.validate_json(
        canonical_bundle_bytes(bundle=bundle),
    ) == bundle
