import importlib
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import pytest

from fpl_data_relay.application.community_jobs import (
    CommunityDispatchJob,
    CommunityStrategyJob,
)
from fpl_data_relay.config import CommunityCredentials
from fpl_data_relay.domain.community import CommunityReport
from tests.adapters.outbound.test_community_postgres import draft


class FakeAwsClient:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    def send_message(self, **parameters: object) -> dict[str, object]:
        self.messages.append(parameters)
        return {"MessageId": "1"}


@pytest.fixture
def lambda_module(monkeypatch: pytest.MonkeyPatch) -> Any:
    values = {
        "DATABASE_EXECUTOR": "rds_data",
        "DATABASE_RESOURCE_ARN": "cluster",
        "DATABASE_SECRET_ARN": "database-secret",
        "DATABASE_NAME": "relay",
        "COMMUNITY_QUEUE_URL": "https://sqs.eu-west-2.amazonaws.com/1/community",
        "COMMUNITY_CREDENTIAL_SECRET_ARN": "community-secret",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    aws_client = FakeAwsClient()

    def client(service_name: str, **parameters: object) -> FakeAwsClient:
        del service_name
        del parameters
        return aws_client

    monkeypatch.setattr(boto3, "client", client)
    sys.modules.pop("fpl_data_relay.lambda_community", None)
    module = importlib.import_module("fpl_data_relay.lambda_community")
    module.SQS = aws_client
    return module


@pytest.mark.asyncio
async def test_dispatch_enqueues_each_expanded_strategy_job(
    lambda_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Queue:
        def __init__(self, *, client: FakeAwsClient, queue_url: str) -> None:
            self._client = client
            self._queue_url = queue_url

        async def send(self, *, message_body: str) -> None:
            self._client.send_message(
                QueueUrl=self._queue_url,
                MessageBody=message_body,
            )

    scheduled_at = datetime(2026, 8, 13, 5, tzinfo=UTC)
    strategy_job = CommunityStrategyJob(
        version=1,
        kind="community_strategy",
        strategy_key="weekly-community-momentum-v1",
        strategy_version=1,
        report_date=scheduled_at.date(),
        window_start=scheduled_at - timedelta(days=7),
        window_end=scheduled_at,
    )
    monkeypatch.setattr(
        lambda_module,
        "build_strategy_jobs",
        lambda **parameters: [strategy_job],
    )
    monkeypatch.setattr(lambda_module, "SqsCommunityJobQueue", Queue)

    result = await lambda_module.process_job(
        job=CommunityDispatchJob(
            version=1,
            kind="community_dispatch",
            scheduled_at=scheduled_at,
        ),
    )

    assert result == {
        "status": "strategies_enqueued",
        "job_kind": "community_dispatch",
        "report_id": None,
    }
    assert lambda_module.SQS.messages == [
        {
            "QueueUrl": "https://sqs.eu-west-2.amazonaws.com/1/community",
            "MessageBody": strategy_job.model_dump_json(),
        },
    ]


@pytest.mark.asyncio
async def test_duplicate_strategy_delivery_returns_before_external_work(
    lambda_module: Any,
) -> None:
    stored = CommunityReport.model_validate({"id": 7, **draft().model_dump()})

    class Reports:
        async def get_report_for_date(self, **parameters: object) -> CommunityReport:
            assert parameters == {
                "strategy_key": stored.strategy_key,
                "report_date": stored.report_date,
            }
            return stored

    lambda_module.REPORTS = Reports()

    def unexpected_pacer(**parameters: object) -> object:
        del parameters
        raise AssertionError("Duplicate jobs must not construct a provider pacer.")

    lambda_module.EvenlySpacedRequestPacer = unexpected_pacer
    result = await lambda_module.process_job(
        job=CommunityStrategyJob(
            version=1,
            kind="community_strategy",
            strategy_key=stored.strategy_key,
            strategy_version=stored.strategy_version,
            report_date=stored.report_date,
            window_start=stored.window_start,
            window_end=stored.window_end,
        ),
    )
    assert result == {
        "status": "duplicate_returned",
        "job_kind": "community_strategy",
        "report_id": 7,
    }


@pytest.mark.asyncio
async def test_strategy_job_wires_configured_supadata_pacer(
    lambda_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Reports:
        async def get_report_for_date(self, **parameters: object) -> None:
            del parameters
            return None

    class WiringObserved(Exception):
        pass

    captured: dict[str, object] = {}

    async def load_credentials(
        *,
        client: object,
        secret_arn: str,
    ) -> CommunityCredentials:
        del client, secret_arn
        return CommunityCredentials(
            openai_api_key="openai",
            x_bearer_token="x",
            youtube_api_key="youtube",
            supadata_api_key="supadata",
        )

    def build_pacer(
        *,
        requests_per_second: int,
        monotonic_clock: object,
        sleeper: object,
    ) -> object:
        captured.update(
            requests_per_second=requests_per_second,
            monotonic_clock=monotonic_clock,
            sleeper=sleeper,
        )
        return object()

    def build_gateway(
        *,
        credentials: object,
        client: object,
        supadata_pacer: object,
    ) -> object:
        captured.update(
            credentials=credentials,
            client=client,
            supadata_pacer=supadata_pacer,
        )
        raise WiringObserved

    lambda_module.REPORTS = Reports()
    monkeypatch.setattr(
        lambda_module,
        "load_community_credentials_from_secret",
        load_credentials,
    )
    monkeypatch.setattr(lambda_module, "EvenlySpacedRequestPacer", build_pacer)
    monkeypatch.setattr(lambda_module, "CommunityHttpSourceGateway", build_gateway)
    monkeypatch.setattr(lambda_module.httpx, "AsyncClient", lambda: object())
    scheduled_at = datetime(2026, 8, 13, 5, tzinfo=UTC)

    with pytest.raises(WiringObserved):
        await lambda_module.process_job(
            job=CommunityStrategyJob(
                version=1,
                kind="community_strategy",
                strategy_key="weekly-community-momentum-v1",
                strategy_version=1,
                report_date=scheduled_at.date(),
                window_start=scheduled_at - timedelta(days=7),
                window_end=scheduled_at,
            ),
        )

    assert captured["requests_per_second"] == 8
    assert captured["monotonic_clock"] is lambda_module.monotonic
    assert captured["sleeper"] is lambda_module.asyncio.sleep
    assert captured["supadata_pacer"] is not None


def test_handler_requires_exactly_one_strict_sqs_record(lambda_module: Any) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        lambda_module.handler({"Records": []}, object())
    with pytest.raises(ValueError, match="exactly one"):
        lambda_module.handler(
            {"Records": [{"body": "{}"}, {"body": "{}"}]},
            object(),
        )
    with pytest.raises(ValueError):
        lambda_module.handler({"Records": [{"body": "{}"}]}, object())
