"""Long-running NAS collector for upstream FPL payloads."""

import asyncio
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, TypedDict
from uuid import uuid4

from pydantic import TypeAdapter

from fpl_data_relay.application.bundles import (
    MAX_COLLECTED_PAYLOAD_BYTES,
    PAYLOAD_VERSION,
    CollectedPayloadMessage,
    LivePayloadBundle,
    PayloadBundle,
    ReferencePayloadBundle,
    canonical_bundle_bytes,
    payload_sha256_bytes,
)
from fpl_data_relay.application.jobs import (
    INGESTION_JOB_ADAPTER,
    LiveJob,
    ReferenceJob,
)
from fpl_data_relay.config import CollectorSettings
from fpl_data_relay.domain.types import JsonValue

LOGGER = logging.getLogger(__name__)
SQS_LONG_POLL_SECONDS = 20
SQS_VISIBILITY_TIMEOUT_SECONDS = 240


class SqsMessage(TypedDict):
    """Required fields from one received SQS message."""

    MessageId: str
    ReceiptHandle: str
    Body: str


class SqsClient(Protocol):
    """SQS calls required by the collection worker."""

    def get_queue_attributes(self, **parameters: object) -> dict[str, object]: ...

    def receive_message(self, **parameters: object) -> dict[str, object]: ...

    def send_message(self, **parameters: object) -> dict[str, object]: ...

    def delete_message(self, **parameters: object) -> dict[str, object]: ...


class S3Client(Protocol):
    """S3 calls required by the collection worker."""

    def put_object(self, **parameters: object) -> dict[str, object]: ...


class StsClient(Protocol):
    """STS startup check required by the collection worker."""

    def get_caller_identity(self) -> dict[str, object]: ...


class RawFplGateway(Protocol):
    """Raw upstream operations required by the collection worker."""

    async def fetch_bootstrap_static(self) -> JsonValue: ...

    async def fetch_fixtures(self) -> JsonValue: ...

    async def fetch_current_fixtures(self, *, event_id: int) -> JsonValue: ...

    async def fetch_event_status(self) -> JsonValue: ...

    async def fetch_event_live(self, *, event_id: int) -> JsonValue: ...

    async def close(self) -> None: ...


class CollectorWorker:
    """Long-poll SQS and publish immutable payload pointers."""

    def __init__(
        self,
        *,
        settings: CollectorSettings,
        client: RawFplGateway,
        sqs: SqsClient,
        s3: S3Client,
        sts: StsClient,
    ) -> None:
        self._settings = settings
        self._client = client
        self._sqs = sqs
        self._s3 = s3
        self._sts = sts

    async def run(self) -> None:
        """Validate AWS access and collect jobs until the process is stopped."""
        await self._check_aws_access()
        self._touch_heartbeat()
        while True:
            response = self._sqs.receive_message(
                QueueUrl=str(self._settings.fetch_queue_url),
                MaxNumberOfMessages=1,
                WaitTimeSeconds=SQS_LONG_POLL_SECONDS,
                VisibilityTimeout=SQS_VISIBILITY_TIMEOUT_SECONDS,
                AttributeNames=["ApproximateReceiveCount"],
            )
            self._touch_heartbeat()
            await asyncio.sleep(0)
            for message in received_messages(response=response):
                started_at = time.perf_counter()
                try:
                    await self.process_message(message=message)
                except Exception:
                    job_kind = failed_job_kind(body=message["Body"])
                    LOGGER.exception(
                        "collector_job_failed",
                        extra={
                            "message_id": message["MessageId"],
                            "job_kind": job_kind,
                            "elapsed_ms": elapsed_ms(started_at=started_at),
                        },
                    )

    async def close(self) -> None:
        """Close the owned upstream HTTP client."""
        await self._client.close()

    async def process_message(self, *, message: SqsMessage) -> None:
        """Collect, upload, forward, then acknowledge exactly one job."""
        started_at = time.perf_counter()
        job = INGESTION_JOB_ADAPTER.validate_json(message["Body"])
        collection_started_at = time.perf_counter()
        bundle = await self._collect(job=job)
        collection_ms = elapsed_ms(started_at=collection_started_at)
        payload = canonical_bundle_bytes(bundle=bundle)
        if len(payload) > MAX_COLLECTED_PAYLOAD_BYTES:
            raise RuntimeError(
                f"Collected payload is {len(payload)} bytes; "
                f"maximum is {MAX_COLLECTED_PAYLOAD_BYTES}.",
            )
        payload_hash = payload_sha256_bytes(payload=payload)
        key = payload_key(
            prefix=self._settings.payload_prefix,
            kind=job.kind,
            fetched_at=bundle.fetched_at,
        )
        upload_started_at = time.perf_counter()
        self._s3.put_object(
            Bucket=self._settings.payload_bucket,
            Key=key,
            Body=payload,
            ContentType="application/json",
            ChecksumAlgorithm="SHA256",
        )
        upload_ms = elapsed_ms(started_at=upload_started_at)
        result = CollectedPayloadMessage(
            version=PAYLOAD_VERSION,
            job=job,
            bucket=self._settings.payload_bucket,
            key=key,
            size_bytes=len(payload),
            sha256=payload_hash,
        )
        publish_started_at = time.perf_counter()
        self._sqs.send_message(
            QueueUrl=str(self._settings.result_queue_url),
            MessageBody=result.model_dump_json(),
        )
        publish_ms = elapsed_ms(started_at=publish_started_at)
        self._sqs.delete_message(
            QueueUrl=str(self._settings.fetch_queue_url),
            ReceiptHandle=message["ReceiptHandle"],
        )
        LOGGER.info(
            "collector_job_completed",
            extra={
                "message_id": message["MessageId"],
                "job_kind": job.kind,
                "payload_key": key,
                "payload_bytes": len(payload),
                "payload_sha256": payload_hash,
                "collection_ms": collection_ms,
                "upload_ms": upload_ms,
                "publish_ms": publish_ms,
                "elapsed_ms": elapsed_ms(started_at=started_at),
            },
        )

    async def _collect(
        self,
        *,
        job: ReferenceJob | LiveJob,
    ) -> PayloadBundle:
        fetched_at = datetime.now(tz=UTC)
        if isinstance(job, ReferenceJob):
            bootstrap, fixtures = await asyncio.gather(
                self._client.fetch_bootstrap_static(),
                self._client.fetch_fixtures(),
            )
            return ReferencePayloadBundle(
                version=PAYLOAD_VERSION,
                kind="reference",
                job=job,
                fetched_at=fetched_at,
                bootstrap_static=bootstrap,
                fixtures=fixtures,
            )
        event_status, current_fixtures, event_live = await asyncio.gather(
            self._client.fetch_event_status(),
            self._client.fetch_current_fixtures(event_id=job.event_id),
            self._client.fetch_event_live(event_id=job.event_id),
        )
        return LivePayloadBundle(
            version=PAYLOAD_VERSION,
            kind="live",
            job=job,
            fetched_at=fetched_at,
            event_status=event_status,
            current_fixtures=current_fixtures,
            event_live=event_live,
        )

    async def _check_aws_access(self) -> None:
        identity = self._sts.get_caller_identity()
        self._sqs.get_queue_attributes(
            QueueUrl=str(self._settings.fetch_queue_url),
            AttributeNames=["QueueArn"],
        )
        arn = identity.get("Arn")
        if not isinstance(arn, str) or arn.strip() == "":
            raise RuntimeError("STS GetCallerIdentity returned no caller ARN.")
        LOGGER.info("collector_aws_identity_validated", extra={"caller_arn": arn})

    def _touch_heartbeat(self) -> None:
        path = Path(self._settings.heartbeat_path)
        path.touch()


def received_messages(*, response: dict[str, object]) -> list[SqsMessage]:
    """Validate the small subset of ReceiveMessage used by the worker."""
    raw_messages = response.get("Messages", [])
    return TypeAdapter(list[SqsMessage]).validate_python(raw_messages)


def payload_key(*, prefix: str, kind: str, fetched_at: datetime) -> str:
    """Build a unique, date-partitioned payload object key."""
    cleaned_prefix = prefix.strip("/")
    date_path = fetched_at.astimezone(UTC).strftime("%Y/%m/%d")
    return f"{cleaned_prefix}/v1/{kind}/{date_path}/{uuid4()}.json"


def elapsed_ms(*, started_at: float) -> int:
    """Return elapsed monotonic time in whole milliseconds."""
    return round((time.perf_counter() - started_at) * 1000)


def failed_job_kind(*, body: str) -> str:
    """Return the job kind for failure logs without masking parse failures."""
    try:
        return INGESTION_JOB_ADAPTER.validate_json(body).kind
    except ValueError:
        return "invalid"
