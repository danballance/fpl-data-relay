"""SQS Lambda composition for reference and live ingestion."""

import asyncio
import logging
import os
from datetime import UTC, datetime
from typing import Protocol, TypedDict, cast

import boto3
from pydantic import BaseModel, ConfigDict

from fpl_data_relay.adapters.outbound.fpl.client import FplClient
from fpl_data_relay.adapters.outbound.postgres.connection import PoolProtocol
from fpl_data_relay.adapters.outbound.postgres.database import (
    IngestionLockError,
    PostgresDatabase,
)
from fpl_data_relay.adapters.outbound.postgres.ingestion import (
    PostgresIngestionRepository,
)
from fpl_data_relay.adapters.outbound.rds_data import create_rds_data_pool
from fpl_data_relay.application.errors import DatabaseWakingError
from fpl_data_relay.application.ingestion.service import IngestionService
from fpl_data_relay.application.jobs import (
    INGESTION_JOB_ADAPTER,
    LiveJob,
    MatchWindow,
    ReferenceJob,
    build_match_windows,
    next_live_delay,
)
from fpl_data_relay.config import (
    load_fpl_settings_from_environment,
    load_rds_data_settings_from_environment,
)
from fpl_data_relay.domain.fixtures import Fixture

LOGGER = logging.getLogger(__name__)
PAGE_SIZE = 200


class SqsClient(Protocol):
    """SQS operation required by ingestion."""

    def send_message(self, **parameters: object) -> dict[str, object]: ...


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
FPL_SETTINGS = load_fpl_settings_from_environment()
QUEUE_URL = os.environ["INGESTION_QUEUE_URL"]
SCHEDULE_GROUP_NAME = os.environ["LIVE_SCHEDULE_GROUP_NAME"]
SCHEDULE_TARGET_ROLE_ARN = os.environ["SCHEDULE_TARGET_ROLE_ARN"]
QUEUE_ARN = os.environ["INGESTION_QUEUE_ARN"]

POOL = create_rds_data_pool(
    resource_arn=RDS_SETTINGS.resource_arn,
    secret_arn=RDS_SETTINGS.secret_arn,
    database_name=RDS_SETTINGS.database_name,
)
DATABASE = PostgresDatabase(pool=cast("PoolProtocol", POOL))
REPOSITORY = PostgresIngestionRepository(database=DATABASE)
FPL_CLIENT = FplClient(
    base_url=str(FPL_SETTINGS.base_url),
    user_agent=FPL_SETTINGS.user_agent,
    timeout_seconds=FPL_SETTINGS.timeout_seconds,
)
INGESTION = IngestionService(client=FPL_CLIENT, repository=REPOSITORY)
SQS = cast("SqsClient", boto3.client("sqs"))
SCHEDULER = cast("SchedulerClient", boto3.client("scheduler"))


def handler(event: dict[str, object], context: object) -> LambdaResult:
    """Validate and process exactly one SQS ingestion job."""
    del context
    parsed_event = SqsEvent.model_validate(event)
    if len(parsed_event.Records) != 1:
        raise ValueError("Ingestion Lambda requires exactly one SQS record.")
    job = INGESTION_JOB_ADAPTER.validate_json(parsed_event.Records[0].body)
    try:
        return asyncio.run(process_job(job=job, now=datetime.now(tz=UTC)))
    except IngestionLockError:
        LOGGER.info("ingestion_skipped_lock_held", extra={"job_kind": job.kind})
        return {"status": "skipped_lock_held", "job_kind": job.kind}


async def process_job(
    *,
    job: ReferenceJob | LiveJob,
    now: datetime,
) -> LambdaResult:
    """Run a reference or live job and schedule its next action."""
    if isinstance(job, ReferenceJob):
        await INGESTION.ingest_reference_once()
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
        await reconcile_schedules(windows=windows)
        return {"status": "reference_ingested", "job_kind": job.kind}
    try:
        result = await INGESTION.ingest_live_once(
            target_event_id=job.event_id,
            fixture_id=None,
        )
    except DatabaseWakingError:
        delay = next_live_delay(
            job=job,
            now=now,
            has_active_fixture=None,
            database_waking=True,
        )
        if delay is not None:
            await requeue_live_job(job=job, delay_seconds=delay)
        return {"status": "database_waking", "job_kind": job.kind}
    delay = next_live_delay(
        job=job,
        now=now,
        has_active_fixture=result.has_active_fixture,
        database_waking=False,
    )
    if delay is not None:
        await requeue_live_job(job=job, delay_seconds=delay)
    return {"status": "live_ingested", "job_kind": job.kind}


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
    await asyncio.to_thread(
        SQS.send_message,
        QueueUrl=QUEUE_URL,
        DelaySeconds=delay_seconds,
        MessageBody=job.model_dump_json(),
    )


async def reconcile_schedules(*, windows: list[MatchWindow]) -> None:
    """Create desired one-time schedules and prune obsolete relay schedules."""
    existing = await list_live_schedule_names()
    desired = {window.schedule_name: window for window in windows}
    for obsolete_name in sorted(existing - desired.keys()):
        await asyncio.to_thread(
            SCHEDULER.delete_schedule,
            GroupName=SCHEDULE_GROUP_NAME,
            Name=obsolete_name,
        )
    for name, window in desired.items():
        parameters = schedule_parameters(window=window)
        operation = (
            SCHEDULER.update_schedule if name in existing else SCHEDULER.create_schedule
        )
        await asyncio.to_thread(operation, **parameters)


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
        response = await asyncio.to_thread(SCHEDULER.list_schedules, **parameters)
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
            "Arn": QUEUE_ARN,
            "RoleArn": SCHEDULE_TARGET_ROLE_ARN,
            "Input": window.job().model_dump_json(),
        },
    }
