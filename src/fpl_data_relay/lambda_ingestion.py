"""SQS Lambda composition for collected-payload ingestion."""

import asyncio
import hashlib
import hmac
import logging
import os
import re
from datetime import UTC, datetime
from typing import Protocol, TypedDict, cast

import boto3
from pydantic import BaseModel, ConfigDict

from fpl_data_relay.adapters.outbound.fpl.validation import (
    validate_fpl_model,
    validate_fpl_model_list,
)
from fpl_data_relay.adapters.outbound.postgres.connection import PoolProtocol
from fpl_data_relay.adapters.outbound.postgres.database import (
    IngestionLockError,
    PostgresDatabase,
)
from fpl_data_relay.adapters.outbound.postgres.ingestion import (
    PostgresIngestionRepository,
)
from fpl_data_relay.adapters.outbound.rds_data import create_rds_data_pool
from fpl_data_relay.application.bundles import (
    MAX_COLLECTED_PAYLOAD_BYTES,
    PAYLOAD_BUNDLE_ADAPTER,
    CollectedPayloadMessage,
    LivePayloadBundle,
    ReferencePayloadBundle,
)
from fpl_data_relay.application.errors import DatabaseWakingError
from fpl_data_relay.application.ingestion.service import (
    IngestionResult,
    IngestionService,
)
from fpl_data_relay.application.jobs import (
    LiveJob,
    MatchWindow,
    build_match_windows,
    next_live_delay,
)
from fpl_data_relay.config import load_rds_data_settings_from_environment
from fpl_data_relay.domain.fixtures import Fixture
from fpl_data_relay.domain.live import EventLiveResponse, EventStatusResponse
from fpl_data_relay.domain.reference import BootstrapStatic

LOGGER = logging.getLogger(__name__)
PAGE_SIZE = 200


class SqsClient(Protocol):
    """SQS operation required by ingestion."""

    def send_message(self, **parameters: object) -> dict[str, object]: ...


class S3Client(Protocol):
    """S3 operation required by collected-payload ingestion."""

    def get_object(self, **parameters: object) -> dict[str, object]: ...


class ReadableBody(Protocol):
    """Readable byte stream returned by S3 GetObject."""

    def read(self) -> bytes: ...


class SchedulerClient(Protocol):
    """EventBridge Scheduler operations required by fixture planning."""

    def list_schedules(self, **parameters: object) -> dict[str, object]: ...

    def create_schedule(self, **parameters: object) -> dict[str, object]: ...

    def update_schedule(self, **parameters: object) -> dict[str, object]: ...

    def delete_schedule(self, **parameters: object) -> dict[str, object]: ...


class SqsRecord(BaseModel):
    """One Lambda SQS record."""

    model_config = ConfigDict(extra="ignore")

    body: str


class SqsEvent(BaseModel):
    """Lambda SQS event constrained to the configured batch size."""

    model_config = ConfigDict(extra="ignore")

    Records: list[SqsRecord]


class LambdaResult(TypedDict):
    """Small observable result returned by direct Lambda invocation."""

    status: str
    job_kind: str


RDS_SETTINGS = load_rds_data_settings_from_environment()
FETCH_QUEUE_URL = os.environ["FETCH_QUEUE_URL"]
SCHEDULE_GROUP_NAME = os.environ["LIVE_SCHEDULE_GROUP_NAME"]
SCHEDULE_TARGET_ROLE_ARN = os.environ["SCHEDULE_TARGET_ROLE_ARN"]
SCHEDULE_DEAD_LETTER_QUEUE_ARN = os.environ["SCHEDULE_DEAD_LETTER_QUEUE_ARN"]
FETCH_QUEUE_ARN = os.environ["FETCH_QUEUE_ARN"]
PAYLOAD_BUCKET = os.environ["PAYLOAD_BUCKET"]
PAYLOAD_PREFIX = os.environ["PAYLOAD_PREFIX"].strip("/")

POOL = create_rds_data_pool(
    resource_arn=RDS_SETTINGS.resource_arn,
    secret_arn=RDS_SETTINGS.secret_arn,
    database_name=RDS_SETTINGS.database_name,
)
DATABASE = PostgresDatabase(pool=cast("PoolProtocol", POOL))
REPOSITORY = PostgresIngestionRepository(database=DATABASE)
SQS = cast("SqsClient", boto3.client("sqs"))
S3 = cast("S3Client", boto3.client("s3"))
SCHEDULER = cast("SchedulerClient", boto3.client("scheduler"))


def handler(event: dict[str, object], context: object) -> LambdaResult:
    """Validate and process exactly one SQS ingestion job."""
    del context
    parsed_event = SqsEvent.model_validate(event)
    if len(parsed_event.Records) != 1:
        raise ValueError("Ingestion Lambda requires exactly one SQS record.")
    message = CollectedPayloadMessage.model_validate_json(
        parsed_event.Records[0].body,
    )
    try:
        return asyncio.run(
            process_collected_payload(
                message=message,
                now=datetime.now(tz=UTC),
            ),
        )
    except IngestionLockError:
        LOGGER.info(
            "ingestion_skipped_lock_held",
            extra={"job_kind": message.job.kind},
        )
        return {"status": "skipped_lock_held", "job_kind": message.job.kind}


async def process_collected_payload(
    *,
    message: CollectedPayloadMessage,
    now: datetime,
) -> LambdaResult:
    """Verify, validate, persist, and schedule one collected payload."""
    bundle = await load_payload_bundle(message=message)
    if bundle.job != message.job:
        raise ValueError("Collected payload job does not match its SQS message.")
    ingestion = IngestionService(client=UnavailableFplGateway(), repository=REPOSITORY)
    if isinstance(bundle, ReferencePayloadBundle):
        bootstrap = validate_fpl_model(
            model=BootstrapStatic,
            payload=bundle.bootstrap_static,
        )
        fixtures = validate_fpl_model_list(
            model=Fixture,
            payload=bundle.fixtures,
        )
        result = await ingestion.ingest_reference_payload(
            bootstrap=bootstrap,
            fixtures=fixtures,
            fetched_at=bundle.fetched_at,
        )
        season = await REPOSITORY.get_current_season()
        if season is None:
            raise RuntimeError("Reference ingestion did not create a current season.")
        fixtures = await read_all_fixtures(season_id=season.id)
        windows, missing_ids = build_match_windows(
            season_id=season.id,
            fixtures=fixtures,
            now=now,
        )
        for fixture_id in missing_ids:
            LOGGER.warning(
                "fixture_missing_schedule_data",
                extra={"fixture_id": fixture_id, "season_id": season.id},
            )
        schedule_result = await reconcile_schedules(windows=windows)
        LOGGER.info(
            "ingestion_completed",
            extra={
                "job_kind": bundle.job.kind,
                "source": bundle.job.kind,
                "sources": ["bootstrap-static", "fixtures"],
                "season_id": result.season_id,
                "event_id": result.current_event_id,
                "changed_count": result.changed_count,
                "unchanged_count": result.unchanged_count,
                "changed_entity_counts": changed_entity_counts(result=result),
                "schedules_created": schedule_result.created_count,
                "schedules_updated": schedule_result.updated_count,
                "schedules_deleted": schedule_result.deleted_count,
            },
        )
        return {"status": "reference_ingested", "job_kind": bundle.job.kind}
    event_status = validate_fpl_model(
        model=EventStatusResponse,
        payload=bundle.event_status,
    )
    current_fixtures = validate_fpl_model_list(
        model=Fixture,
        payload=bundle.current_fixtures,
    )
    event_live = validate_fpl_model(
        model=EventLiveResponse,
        payload=bundle.event_live,
    )
    try:
        result = await ingestion.ingest_live_payload(
            season_id=bundle.job.season_id,
            event_id=bundle.job.event_id,
            event_status=event_status,
            current_fixtures=current_fixtures,
            event_live=event_live,
            fetched_at=bundle.fetched_at,
        )
    except DatabaseWakingError:
        delay = next_live_delay(
            job=bundle.job,
            now=now,
            has_active_fixture=None,
            database_waking=True,
        )
        if delay is not None:
            await requeue_live_job(job=bundle.job, delay_seconds=delay)
        return {"status": "database_waking", "job_kind": bundle.job.kind}
    delay = next_live_delay(
        job=bundle.job,
        now=now,
        has_active_fixture=result.has_active_fixture,
        database_waking=False,
    )
    if delay is not None:
        await requeue_live_job(job=bundle.job, delay_seconds=delay)
    LOGGER.info(
        "ingestion_completed",
        extra={
            "job_kind": bundle.job.kind,
            "source": bundle.job.kind,
            "sources": [
                "event-status",
                "fixtures-current-event",
                "event-live",
            ],
            "season_id": result.season_id,
            "event_id": result.current_event_id,
            "changed_count": result.changed_count,
            "unchanged_count": result.unchanged_count,
            "changed_entity_counts": changed_entity_counts(result=result),
            "has_active_fixture": result.has_active_fixture,
            "next_delay_seconds": delay,
            "schedules_created": 0,
            "schedules_updated": 0,
            "schedules_deleted": 0,
        },
    )
    return {"status": "live_ingested", "job_kind": bundle.job.kind}


def changed_entity_counts(*, result: IngestionResult) -> dict[str, dict[str, int]]:
    """Return JSON-log-safe operation totals keyed by entity family."""
    return {
        family.value: {
            "created": counts.created,
            "updated": counts.updated,
            "deleted": counts.deleted,
        }
        for family, counts in result.entity_change_counts.items()
    }


class UnavailableFplGateway:
    """Fail fast if collected-payload ingestion attempts an upstream fetch."""

    async def fetch_bootstrap_static(self) -> BootstrapStatic:
        raise RuntimeError("Collected-payload ingestion cannot fetch upstream.")

    async def fetch_fixtures(self) -> list[Fixture]:
        raise RuntimeError("Collected-payload ingestion cannot fetch upstream.")

    async def fetch_current_fixtures(self, *, event_id: int) -> list[Fixture]:
        del event_id
        raise RuntimeError("Collected-payload ingestion cannot fetch upstream.")

    async def fetch_event_status(self) -> EventStatusResponse:
        raise RuntimeError("Collected-payload ingestion cannot fetch upstream.")

    async def fetch_event_live(self, *, event_id: int) -> EventLiveResponse:
        del event_id
        raise RuntimeError("Collected-payload ingestion cannot fetch upstream.")


async def load_payload_bundle(
    *,
    message: CollectedPayloadMessage,
) -> ReferencePayloadBundle | LivePayloadBundle:
    """Load exact S3 bytes and enforce the configured trust boundary."""
    if message.bucket != PAYLOAD_BUCKET:
        raise ValueError("Collected payload references an unexpected S3 bucket.")
    expected_key = re.compile(
        rf"{re.escape(PAYLOAD_PREFIX)}/v1/{message.job.kind}/"
        r"\d{4}/\d{2}/\d{2}/"
        r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
        r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\.json",
    )
    if expected_key.fullmatch(message.key) is None:
        raise ValueError("Collected payload references an unexpected S3 key.")
    response = S3.get_object(
        Bucket=message.bucket,
        Key=message.key,
    )
    body = response.get("Body")
    if not hasattr(body, "read"):
        raise RuntimeError("S3 GetObject response did not contain a readable body.")
    raw_payload = cast("ReadableBody", body).read()
    if len(raw_payload) != message.size_bytes:
        raise ValueError("Collected payload size does not match its SQS message.")
    if len(raw_payload) > MAX_COLLECTED_PAYLOAD_BYTES:
        raise ValueError("Collected payload exceeds the maximum size.")
    actual_hash = hashlib.sha256(raw_payload).hexdigest()
    if not hmac.compare_digest(actual_hash, message.sha256):
        raise ValueError("Collected payload checksum does not match its SQS message.")
    return PAYLOAD_BUNDLE_ADAPTER.validate_json(raw_payload)


async def read_all_fixtures(*, season_id: str) -> list[Fixture]:
    """Read bounded fixture pages for schedule planning."""
    fixtures: list[Fixture] = []
    after_id = 0
    while True:
        page = await DATABASE.list_fixtures(
            season_id=season_id,
            event_id=None,
            after_id=after_id,
            limit=PAGE_SIZE,
        )
        fixtures.extend(page)
        if len(page) < PAGE_SIZE:
            return fixtures
        next_id = page[-1].id
        if next_id <= after_id:
            raise RuntimeError("Fixture pagination did not advance.")
        after_id = next_id


async def requeue_live_job(*, job: LiveJob, delay_seconds: int) -> None:
    """Send a bounded-delay continuation to the ingestion queue."""
    SQS.send_message(
        QueueUrl=FETCH_QUEUE_URL,
        DelaySeconds=delay_seconds,
        MessageBody=job.model_dump_json(),
    )


class ScheduleReconciliationResult(BaseModel):
    """Counts from one desired-versus-existing schedule reconciliation."""

    model_config = ConfigDict(frozen=True)

    created_count: int
    updated_count: int
    deleted_count: int


async def reconcile_schedules(
    *,
    windows: list[MatchWindow],
) -> ScheduleReconciliationResult:
    """Create desired one-time schedules and prune obsolete relay schedules."""
    existing = await list_live_schedule_names()
    desired = {window.schedule_name: window for window in windows}
    obsolete = existing - desired.keys()
    for obsolete_name in sorted(obsolete):
        SCHEDULER.delete_schedule(
            GroupName=SCHEDULE_GROUP_NAME,
            Name=obsolete_name,
        )
    for name, window in desired.items():
        parameters = schedule_parameters(window=window)
        operation = (
            SCHEDULER.update_schedule if name in existing else SCHEDULER.create_schedule
        )
        operation(**parameters)
    return ScheduleReconciliationResult(
        created_count=len(desired.keys() - existing),
        updated_count=len(desired.keys() & existing),
        deleted_count=len(obsolete),
    )


async def list_live_schedule_names() -> set[str]:
    """List all relay-owned schedules in the dedicated schedule group."""
    names: set[str] = set()
    next_token: str | None = None
    while True:
        parameters: dict[str, object] = {
            "GroupName": SCHEDULE_GROUP_NAME,
            "NamePrefix": "fpl-live-",
        }
        if next_token is not None:
            parameters["NextToken"] = next_token
        response = SCHEDULER.list_schedules(**parameters)
        schedules = response.get("Schedules", [])
        if not isinstance(schedules, list):
            raise RuntimeError("Scheduler returned an invalid schedule list.")
        for schedule in schedules:
            if isinstance(schedule, dict):
                name = schedule.get("Name")
                if isinstance(name, str):
                    names.add(name)
        raw_token = response.get("NextToken")
        if raw_token is None:
            return names
        if not isinstance(raw_token, str):
            raise RuntimeError("Scheduler returned an invalid pagination token.")
        next_token = raw_token


def schedule_parameters(*, window: MatchWindow) -> dict[str, object]:
    """Build a complete create/update request for a one-time schedule."""
    utc_start = window.start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    return {
        "Name": window.schedule_name,
        "GroupName": SCHEDULE_GROUP_NAME,
        "ScheduleExpression": f"at({utc_start})",
        "ScheduleExpressionTimezone": "UTC",
        "FlexibleTimeWindow": {"Mode": "OFF"},
        "ActionAfterCompletion": "DELETE",
        "State": "ENABLED",
        "Target": {
            "Arn": FETCH_QUEUE_ARN,
            "RoleArn": SCHEDULE_TARGET_ROLE_ARN,
            "Input": window.job().model_dump_json(),
            "DeadLetterConfig": {"Arn": SCHEDULE_DEAD_LETTER_QUEUE_ARN},
            "RetryPolicy": {
                "MaximumEventAgeInSeconds": 900,
                "MaximumRetryAttempts": 3,
            },
        },
    }
