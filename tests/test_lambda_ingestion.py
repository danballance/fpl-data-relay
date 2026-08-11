import importlib
import io
import logging
import sys
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import boto3
import pytest

from fpl_data_relay.application.bundles import (
    CollectedPayloadMessage,
    LivePayloadBundle,
    ReferencePayloadBundle,
    canonical_bundle_bytes,
    payload_sha256_bytes,
)
from fpl_data_relay.application.errors import DatabaseWakingError
from fpl_data_relay.application.ingestion.service import IngestionService
from fpl_data_relay.application.jobs import LiveJob, MatchWindow, ReferenceJob
from fpl_data_relay.domain.types import JsonValue
from tests.conftest import FakeClient, InMemoryStore, bootstrap_payload, fixture_payload


class FakeAwsClient:
    def __init__(self) -> None:
        self.payload = b""

    def get_object(self, **parameters: object) -> dict[str, object]:
        del parameters
        return {"Body": io.BytesIO(self.payload)}


@pytest.fixture
def lambda_module(
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    values = {
        "DATABASE_EXECUTOR": "rds_data",
        "DATABASE_RESOURCE_ARN": "cluster",
        "DATABASE_SECRET_ARN": "secret",
        "DATABASE_NAME": "relay",
        "FETCH_QUEUE_URL": "https://sqs.eu-west-2.amazonaws.com/1/fetch",
        "FETCH_QUEUE_ARN": "arn:aws:sqs:eu-west-2:1:fetch",
        "LIVE_SCHEDULE_GROUP_NAME": "live",
        "SCHEDULE_TARGET_ROLE_ARN": "arn:aws:iam::1:role/scheduler",
        "SCHEDULE_DEAD_LETTER_QUEUE_ARN": "arn:aws:sqs:eu-west-2:1:schedule-dlq",
        "PAYLOAD_BUCKET": "payload-bucket",
        "PAYLOAD_PREFIX": "payloads",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    def client(service_name: str, **parameters: object) -> FakeAwsClient:
        del service_name
        del parameters
        return FakeAwsClient()

    monkeypatch.setattr(boto3, "client", client)
    sys.modules.pop("fpl_data_relay.lambda_ingestion", None)
    module = importlib.import_module("fpl_data_relay.lambda_ingestion")
    return module


def test_live_schedule_parameters_include_retry_and_dead_letter_policy(
    lambda_module: Any,
) -> None:
    start = datetime(2026, 8, 15, 12, tzinfo=UTC)
    parameters = lambda_module.schedule_parameters(
        window=MatchWindow(
            season_id="2026-27",
            event_id=1,
            start=start,
            end=start + timedelta(hours=3),
        ),
    )
    target = cast("dict[str, object]", parameters["Target"])
    assert target["DeadLetterConfig"] == {
        "Arn": "arn:aws:sqs:eu-west-2:1:schedule-dlq",
    }
    assert target["RetryPolicy"] == {
        "MaximumEventAgeInSeconds": 900,
        "MaximumRetryAttempts": 3,
    }


def reference_bundle() -> ReferencePayloadBundle:
    return ReferencePayloadBundle(
        version=1,
        kind="reference",
        job=ReferenceJob(version=1, kind="reference"),
        fetched_at=datetime(2026, 7, 26, 12, tzinfo=UTC),
        bootstrap_static=cast("JsonValue", bootstrap_payload(current_ids=[1])),
        fixtures=cast(
            "JsonValue",
            [
                fixture_payload(
                    fixture_id=1,
                    event=1,
                    started=False,
                    finished=False,
                ),
            ],
        ),
    )


def collected_message(
    *,
    bundle: ReferencePayloadBundle | LivePayloadBundle,
) -> tuple[CollectedPayloadMessage, bytes]:
    payload = canonical_bundle_bytes(bundle=bundle)
    return (
        CollectedPayloadMessage(
            version=1,
            job=bundle.job,
            bucket="payload-bucket",
            key=(
                f"payloads/v1/{bundle.kind}/2026/07/26/"
                "12345678-1234-4123-8123-123456789abc.json"
            ),
            size_bytes=len(payload),
            sha256=payload_sha256_bytes(payload=payload),
        ),
        payload,
    )


@pytest.mark.asyncio
async def test_lambda_loads_only_expected_verified_payload(
    lambda_module: Any,
) -> None:
    bundle = reference_bundle()
    message, payload = collected_message(bundle=bundle)
    lambda_module.S3.payload = payload
    assert await lambda_module.load_payload_bundle(message=message) == bundle
    with pytest.raises(ValueError, match="bucket"):
        await lambda_module.load_payload_bundle(
            message=message.model_copy(update={"bucket": "other-bucket"}),
        )
    with pytest.raises(ValueError, match="key"):
        await lambda_module.load_payload_bundle(
            message=message.model_copy(
                update={
                    "key": (
                        "payloads/v1/live/2026/07/26/"
                        "12345678-1234-4123-8123-123456789abc.json"
                    ),
                },
            ),
        )
    lambda_module.S3.payload = payload + b" "
    with pytest.raises(ValueError, match="size"):
        await lambda_module.load_payload_bundle(message=message)
    lambda_module.S3.payload = payload
    with pytest.raises(ValueError, match="checksum"):
        await lambda_module.load_payload_bundle(
            message=message.model_copy(update={"sha256": "0" * 64}),
        )


@pytest.mark.asyncio
async def test_lambda_persists_reference_and_reconciles_schedules(
    lambda_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = InMemoryStore()
    bundle = reference_bundle()
    message, payload = collected_message(bundle=bundle)
    lambda_module.S3.payload = payload
    lambda_module.REPOSITORY = store
    reconciled: list[list[object]] = []

    async def read_all_fixtures(*, season_id: str) -> list[object]:
        assert season_id == "2025-26"
        return list(store.fixtures.values())

    async def reconcile_schedules(*, windows: list[object]) -> object:
        reconciled.append(windows)
        return lambda_module.ScheduleReconciliationResult(
            created_count=0,
            updated_count=0,
            deleted_count=0,
        )

    monkeypatch.setattr(lambda_module, "read_all_fixtures", read_all_fixtures)
    monkeypatch.setattr(lambda_module, "reconcile_schedules", reconcile_schedules)
    caplog.set_level(logging.INFO, logger=lambda_module.__name__)
    result = await lambda_module.process_collected_payload(
        message=message,
        now=datetime(2025, 8, 1, tzinfo=UTC),
    )
    assert result == {"status": "reference_ingested", "job_kind": "reference"}
    assert await store.get_current_season() is not None
    assert reconciled == [[]]
    completed = next(
        record for record in caplog.records if record.message == "ingestion_completed"
    )
    assert completed.__dict__["source"] == "reference"
    assert completed.__dict__["sources"] == ["bootstrap-static", "fixtures"]
    assert completed.__dict__["changed_entity_counts"] == {}
    assert completed.__dict__["schedules_created"] == 0


@pytest.mark.asyncio
async def test_lambda_persists_live_and_enqueues_continuation(
    lambda_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryStore()
    await IngestionService(
        client=FakeClient(),
        repository=store,
    ).ingest_reference_once()
    now = datetime(2026, 5, 1, 12, tzinfo=UTC)
    job = LiveJob(
        version=1,
        kind="live",
        season_id="2025-26",
        event_id=1,
        window_start=now - timedelta(minutes=10),
        window_end=now + timedelta(hours=3),
    )
    bundle = LivePayloadBundle(
        version=1,
        kind="live",
        job=job,
        fetched_at=now,
        event_status={
            "status": [
                {
                    "event": 1,
                    "bonus_added": False,
                    "date": "2026-05-01",
                    "leagues_updated": False,
                },
            ],
        },
        current_fixtures=cast(
            "JsonValue",
            [
                fixture_payload(
                    fixture_id=1,
                    event=1,
                    started=True,
                    finished=False,
                ),
            ],
        ),
        event_live={"elements": []},
    )
    message, payload = collected_message(bundle=bundle)
    lambda_module.S3.payload = payload
    lambda_module.REPOSITORY = store
    delays: list[int] = []

    async def requeue_live_job(*, job: object, delay_seconds: int) -> None:
        del job
        delays.append(delay_seconds)

    monkeypatch.setattr(lambda_module, "requeue_live_job", requeue_live_job)
    result = await lambda_module.process_collected_payload(
        message=message,
        now=now,
    )
    assert result == {"status": "live_ingested", "job_kind": "live"}
    assert delays == [15]


@pytest.mark.asyncio
async def test_lambda_requeues_live_job_while_aurora_wakes(
    lambda_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 5, 1, 12, tzinfo=UTC)
    job = LiveJob(
        version=1,
        kind="live",
        season_id="2025-26",
        event_id=1,
        window_start=now - timedelta(minutes=10),
        window_end=now + timedelta(hours=3),
    )
    bundle = LivePayloadBundle(
        version=1,
        kind="live",
        job=job,
        fetched_at=now,
        event_status={"status": []},
        current_fixtures=[],
        event_live={"elements": []},
    )
    message, payload = collected_message(bundle=bundle)
    lambda_module.S3.payload = payload
    delays: list[int] = []

    class WakingIngestion:
        def __init__(self, **parameters: object) -> None:
            del parameters

        async def ingest_live_payload(self, **parameters: object) -> None:
            del parameters
            raise DatabaseWakingError("waking")

    async def requeue_live_job(*, job: object, delay_seconds: int) -> None:
        del job
        delays.append(delay_seconds)

    monkeypatch.setattr(lambda_module, "IngestionService", WakingIngestion)
    monkeypatch.setattr(lambda_module, "requeue_live_job", requeue_live_job)
    result = await lambda_module.process_collected_payload(
        message=message,
        now=now,
    )
    assert result == {"status": "database_waking", "job_kind": "live"}
    assert delays == [15]


@pytest.mark.asyncio
async def test_lambda_rejects_message_and_bundle_job_mismatch(
    lambda_module: Any,
) -> None:
    bundle = reference_bundle()
    message, _ = collected_message(bundle=bundle)
    now = datetime(2026, 5, 1, 12, tzinfo=UTC)
    live_job = LiveJob(
        version=1,
        kind="live",
        season_id="2025-26",
        event_id=1,
        window_start=now,
        window_end=now + timedelta(hours=1),
    )

    async def load_payload_bundle(
        *,
        message: CollectedPayloadMessage,
    ) -> ReferencePayloadBundle:
        del message
        return bundle

    lambda_module.load_payload_bundle = load_payload_bundle
    with pytest.raises(ValueError, match="does not match"):
        await lambda_module.process_collected_payload(
            message=message.model_copy(update={"job": live_job}),
            now=now,
        )
