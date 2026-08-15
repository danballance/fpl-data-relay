"""Generic SQS Lambda for community dispatch and strategy workloads."""

import asyncio
import logging
import os
from datetime import UTC, datetime
from typing import Protocol, TypedDict, cast

import boto3
import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict

from fpl_data_relay.adapters.outbound.aws_community import (
    SqsCommunityJobQueue,
    load_community_credentials_from_secret,
)
from fpl_data_relay.adapters.outbound.community_sources import (
    CommunityHttpSourceGateway,
)
from fpl_data_relay.adapters.outbound.openai_community import (
    OpenAICommunityAnalyzer,
)
from fpl_data_relay.adapters.outbound.postgres.community import (
    PostgresCommunityReportRepository,
)
from fpl_data_relay.adapters.outbound.postgres.community_cache import (
    PostgresCommunityExtractionCacheRepository,
)
from fpl_data_relay.adapters.outbound.postgres.connection import PoolProtocol
from fpl_data_relay.adapters.outbound.postgres.database import PostgresDatabase
from fpl_data_relay.adapters.outbound.postgres.reference import (
    PostgresReferenceRepository,
)
from fpl_data_relay.adapters.outbound.rds_data import create_rds_data_pool
from fpl_data_relay.application.community_jobs import (
    COMMUNITY_JOB_ADAPTER,
    CommunityDispatchJob,
    CommunityStrategyJob,
    build_strategy_jobs,
)
from fpl_data_relay.application.community_ranking import (
    CommunityMomentumRankingPolicy,
)
from fpl_data_relay.application.community_service import CommunityService
from fpl_data_relay.application.community_strategies import load_strategy_registry
from fpl_data_relay.config import load_rds_data_settings_from_environment

LOGGER = logging.getLogger(__name__)


class SqsClient(Protocol):
    def send_message(self, **parameters: object) -> dict[str, object]: ...


class SecretsClient(Protocol):
    def get_secret_value(self, **parameters: object) -> dict[str, object]: ...


class SqsRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    body: str


class SqsEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    Records: list[SqsRecord]


class LambdaResult(TypedDict):
    status: str
    job_kind: str
    report_id: int | None


RDS_SETTINGS = load_rds_data_settings_from_environment()
COMMUNITY_QUEUE_URL = os.environ["COMMUNITY_QUEUE_URL"]
COMMUNITY_CREDENTIAL_SECRET_ARN = os.environ["COMMUNITY_CREDENTIAL_SECRET_ARN"]

POOL = create_rds_data_pool(
    resource_arn=RDS_SETTINGS.resource_arn,
    secret_arn=RDS_SETTINGS.secret_arn,
    database_name=RDS_SETTINGS.database_name,
)
DATABASE = PostgresDatabase(pool=cast("PoolProtocol", POOL))
REPORTS = PostgresCommunityReportRepository(database=DATABASE)
CACHE = PostgresCommunityExtractionCacheRepository(database=DATABASE)
REFERENCES = PostgresReferenceRepository(database=DATABASE)
REGISTRY = load_strategy_registry()
SQS = cast("SqsClient", boto3.client("sqs"))
SECRETS = cast("SecretsClient", boto3.client("secretsmanager"))


def handler(event: dict[str, object], context: object) -> LambdaResult:
    """Validate and process exactly one community SQS message."""
    del context
    parsed_event = SqsEvent.model_validate(event)
    if len(parsed_event.Records) != 1:
        raise ValueError("Community Lambda requires exactly one SQS record.")
    job = COMMUNITY_JOB_ADAPTER.validate_json(parsed_event.Records[0].body)
    return asyncio.run(process_job(job=job))


async def process_job(
    *,
    job: CommunityDispatchJob | CommunityStrategyJob,
) -> LambdaResult:
    if isinstance(job, CommunityDispatchJob):
        queue = SqsCommunityJobQueue(client=SQS, queue_url=COMMUNITY_QUEUE_URL)
        jobs = build_strategy_jobs(
            registry=REGISTRY,
            scheduled_at=job.scheduled_at,
        )
        for strategy_job in jobs:
            await queue.send(message_body=strategy_job.model_dump_json())
        LOGGER.info(
            "community_dispatch_completed",
            extra={"strategy_job_count": len(jobs)},
        )
        return {
            "status": "strategies_enqueued",
            "job_kind": job.kind,
            "report_id": None,
        }
    existing = await REPORTS.get_report_for_date(
        strategy_key=job.strategy_key,
        report_date=job.report_date,
    )
    if existing is not None:
        return {
            "status": "duplicate_returned",
            "job_kind": job.kind,
            "report_id": existing.id,
        }
    credentials = await load_community_credentials_from_secret(
        client=SECRETS,
        secret_arn=COMMUNITY_CREDENTIAL_SECRET_ARN,
    )
    gateway = CommunityHttpSourceGateway(
        credentials=credentials,
        client=httpx.AsyncClient(),
    )
    analyzer = OpenAICommunityAnalyzer(
        client=AsyncOpenAI(api_key=credentials.openai_api_key),
    )
    try:
        report = await CommunityService(
            registry=REGISTRY,
            source_gateway=gateway,
            analyzer=analyzer,
            ranking_policy=CommunityMomentumRankingPolicy(),
            reports=REPORTS,
            extraction_cache=CACHE,
            references=REFERENCES,
            clock=lambda: datetime.now(tz=UTC),
        ).run(job=job)
    finally:
        try:
            await analyzer.close()
        finally:
            await gateway.close()
    LOGGER.info(
        "community_report_completed",
        extra={
            "strategy_key": job.strategy_key,
            "report_date": job.report_date.isoformat(),
            "report_id": report.id,
            "story_count": len(report.content.stories),
            "failed_source_count": len(report.content.coverage.failed_sources),
            "cache_eligible_document_count": (
                report.content.extraction_cache.eligible_document_count
            ),
            "cache_hit_count": report.content.extraction_cache.hit_count,
            "cache_miss_count": report.content.extraction_cache.miss_count,
            "cache_write_count": report.content.extraction_cache.write_count,
            "cache_expired_entry_count": (
                report.content.extraction_cache.expired_entry_count
            ),
            "input_tokens": report.content.model_usage.input_tokens,
            "output_tokens": report.content.model_usage.output_tokens,
        },
    )
    return {
        "status": "report_published",
        "job_kind": job.kind,
        "report_id": report.id,
    }
