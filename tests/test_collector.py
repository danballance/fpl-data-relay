import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import httpx
import pytest
from pytest import MonkeyPatch
from typer.testing import CliRunner

from fpl_data_relay import collector_cli
from fpl_data_relay.adapters.outbound.fpl.client import RawFplClient
from fpl_data_relay.application.bundles import (
    PAYLOAD_BUNDLE_ADAPTER,
    CollectedPayloadMessage,
)
from fpl_data_relay.application.jobs import LiveJob, ReferenceJob
from fpl_data_relay.collector import (
    CollectorWorker,
    S3Client,
    SqsClient,
    SqsMessage,
    StsClient,
    failed_job_kind,
    payload_key,
    received_messages,
)
from fpl_data_relay.collector_cli import app
from fpl_data_relay.config import CollectorSettings, FplSettings
from fpl_data_relay.domain.types import JsonValue
from tests.conftest import bootstrap_payload, fixture_payload


class FakeSqs:
    def __init__(self, *, fail_send: bool) -> None:
        self.fail_send = fail_send
        self.actions: list[tuple[str, dict[str, object]]] = []

    def get_queue_attributes(self, **parameters: object) -> dict[str, object]:
        self.actions.append(("get", parameters))
        return {"Attributes": {"QueueArn": "arn:queue"}}

    def receive_message(self, **parameters: object) -> dict[str, object]:
        self.actions.append(("receive", parameters))
        return {}

    def send_message(self, **parameters: object) -> dict[str, object]:
        self.actions.append(("send", parameters))
        if self.fail_send:
            raise RuntimeError("send failed")
        return {"MessageId": "result"}

    def delete_message(self, **parameters: object) -> dict[str, object]:
        self.actions.append(("delete", parameters))
        return {}


class FakeS3:
    def __init__(self, *, fail_put: bool = False) -> None:
        self.fail_put = fail_put
        self.puts: list[dict[str, object]] = []

    def put_object(self, **parameters: object) -> dict[str, object]:
        self.puts.append(parameters)
        if self.fail_put:
            raise RuntimeError("upload failed")
        return {"ETag": "etag"}


class FakeSts:
    def get_caller_identity(self) -> dict[str, object]:
        return {"Arn": "arn:aws:sts::123456789012:assumed-role/collector/nas"}


class PollingSqs(FakeSqs):
    def __init__(self, *, message: SqsMessage) -> None:
        super().__init__(fail_send=False)
        self._message: SqsMessage | None = message

    def receive_message(self, **parameters: object) -> dict[str, object]:
        self.actions.append(("receive", parameters))
        if self._message is not None:
            message = self._message
            self._message = None
            return {"Messages": [message]}
        return {}


class ConcurrentRawClient:
    def __init__(self, *, expected_requests: int) -> None:
        self.expected_requests = expected_requests
        self.active_requests = 0
        self.peak_requests = 0
        self.release = asyncio.Event()

    async def _fetch(self, *, payload: JsonValue) -> JsonValue:
        self.active_requests += 1
        self.peak_requests = max(self.peak_requests, self.active_requests)
        if self.active_requests == self.expected_requests:
            self.release.set()
        await self.release.wait()
        self.active_requests -= 1
        return payload

    async def fetch_bootstrap_static(self) -> JsonValue:
        return await self._fetch(payload={"events": []})

    async def fetch_fixtures(self) -> JsonValue:
        return await self._fetch(payload=[])

    async def fetch_event_status(self) -> JsonValue:
        return await self._fetch(payload={"status": []})

    async def fetch_current_fixtures(self, *, event_id: int) -> JsonValue:
        del event_id
        return await self._fetch(payload=[])

    async def fetch_event_live(self, *, event_id: int) -> JsonValue:
        del event_id
        return await self._fetch(payload={"elements": []})

    async def close(self) -> None:
        return None


def settings(*, heartbeat_path: Path) -> CollectorSettings:
    return CollectorSettings.model_validate(
        {
            "aws_region": "eu-west-2",
            "fetch_queue_url": "https://sqs.eu-west-2.amazonaws.com/1/fetch",
            "result_queue_url": "https://sqs.eu-west-2.amazonaws.com/1/result",
            "payload_bucket": "payload-bucket",
            "payload_prefix": "payloads",
            "heartbeat_path": str(heartbeat_path),
            "fpl": FplSettings.model_validate(
                {
                    "base_url": "https://fantasy.premierleague.com/api",
                    "user_agent": "tests",
                    "timeout_seconds": 10,
                },
            ),
        },
    )


def raw_client() -> RawFplClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/bootstrap-static/"):
            return httpx.Response(200, json=bootstrap_payload(current_ids=[1]))
        if request.url.path.endswith("/fixtures/"):
            return httpx.Response(
                200,
                json=[
                    fixture_payload(
                        fixture_id=1,
                        event=1,
                        started=True,
                        finished=False,
                    ),
                ],
            )
        if request.url.path.endswith("/event-status/"):
            return httpx.Response(200, json={"status": []})
        if request.url.path.endswith("/event/1/live/"):
            return httpx.Response(200, json={"elements": []})
        return httpx.Response(404)

    client = RawFplClient(
        base_url="https://fantasy.premierleague.com/api",
        user_agent="tests",
        timeout_seconds=10,
    )
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://fantasy.premierleague.com/api",
    )
    return client


def worker(
    *,
    heartbeat_path: Path,
    sqs: FakeSqs,
    s3: FakeS3,
) -> CollectorWorker:
    return CollectorWorker(
        settings=settings(heartbeat_path=heartbeat_path),
        client=raw_client(),
        sqs=cast("SqsClient", sqs),
        s3=cast("S3Client", s3),
        sts=cast("StsClient", FakeSts()),
    )


@pytest.mark.asyncio
async def test_reference_collection_uploads_sends_then_deletes(
    tmp_path: Path,
) -> None:
    sqs = FakeSqs(fail_send=False)
    s3 = FakeS3()
    collector = worker(
        heartbeat_path=tmp_path / "heartbeat",
        sqs=sqs,
        s3=s3,
    )
    message: SqsMessage = {
        "MessageId": "source",
        "ReceiptHandle": "receipt",
        "Body": ReferenceJob(version=1, kind="reference").model_dump_json(),
    }
    try:
        await collector.process_message(message=message)
    finally:
        await collector.close()
    assert len(s3.puts) == 1
    uploaded = cast("bytes", s3.puts[0]["Body"])
    bundle = PAYLOAD_BUNDLE_ADAPTER.validate_json(uploaded)
    assert bundle.kind == "reference"
    action_names = [name for name, _ in sqs.actions]
    assert action_names == ["send", "delete"]
    result = CollectedPayloadMessage.model_validate_json(
        cast("str", sqs.actions[0][1]["MessageBody"]),
    )
    assert result.size_bytes == len(uploaded)
    assert result.key.startswith("payloads/v2/reference/")


@pytest.mark.asyncio
async def test_live_collection_does_not_delete_when_forwarding_fails(
    tmp_path: Path,
) -> None:
    sqs = FakeSqs(fail_send=True)
    s3 = FakeS3()
    collector = worker(
        heartbeat_path=tmp_path / "heartbeat",
        sqs=sqs,
        s3=s3,
    )
    now = datetime(2026, 7, 26, 12, tzinfo=UTC)
    job = LiveJob(
        version=1,
        kind="live",
        season_id="2026-27",
        event_id=1,
        window_start=now,
        window_end=now + timedelta(hours=3),
    )
    try:
        with pytest.raises(RuntimeError, match="send failed"):
            await collector.process_message(
                message={
                    "MessageId": "source",
                    "ReceiptHandle": "receipt",
                    "Body": job.model_dump_json(),
                },
            )
    finally:
        await collector.close()
    assert len(s3.puts) == 1
    assert [name for name, _ in sqs.actions] == ["send"]


@pytest.mark.asyncio
async def test_collection_does_not_delete_when_upload_fails(tmp_path: Path) -> None:
    sqs = FakeSqs(fail_send=False)
    collector = worker(
        heartbeat_path=tmp_path / "heartbeat",
        sqs=sqs,
        s3=FakeS3(fail_put=True),
    )
    try:
        with pytest.raises(RuntimeError, match="upload failed"):
            await collector.process_message(
                message={
                    "MessageId": "source",
                    "ReceiptHandle": "receipt",
                    "Body": ReferenceJob(version=1, kind="reference").model_dump_json(),
                },
            )
    finally:
        await collector.close()
    assert sqs.actions == []


@pytest.mark.parametrize(
    ("job", "expected_requests"),
    [
        (ReferenceJob(version=1, kind="reference"), 3),
        (
            LiveJob(
                version=1,
                kind="live",
                season_id="2026-27",
                event_id=1,
                window_start=datetime(2026, 7, 26, 12, tzinfo=UTC),
                window_end=datetime(2026, 7, 26, 15, tzinfo=UTC),
            ),
            3,
        ),
    ],
)
@pytest.mark.asyncio
async def test_collector_fetches_each_bundle_concurrently(
    tmp_path: Path,
    job: ReferenceJob | LiveJob,
    expected_requests: int,
) -> None:
    client = ConcurrentRawClient(expected_requests=expected_requests)
    collector = CollectorWorker(
        settings=settings(heartbeat_path=tmp_path / "heartbeat"),
        client=client,
        sqs=cast("SqsClient", FakeSqs(fail_send=False)),
        s3=cast("S3Client", FakeS3()),
        sts=cast("StsClient", FakeSts()),
    )
    try:
        await collector.process_message(
            message={
                "MessageId": "source",
                "ReceiptHandle": "receipt",
                "Body": job.model_dump_json(),
            },
        )
    finally:
        await collector.close()
    assert client.peak_requests == expected_requests


@pytest.mark.asyncio
async def test_collector_startup_check_and_heartbeat(tmp_path: Path) -> None:
    heartbeat = tmp_path / "heartbeat"
    sqs = FakeSqs(fail_send=False)
    collector = worker(heartbeat_path=heartbeat, sqs=sqs, s3=FakeS3())
    try:
        await collector._check_aws_access()
        collector._touch_heartbeat()
    finally:
        await collector.close()
    assert heartbeat.is_file()
    assert sqs.actions[0][0] == "get"


@pytest.mark.asyncio
async def test_collector_run_long_polls_and_processes_one_job(
    tmp_path: Path,
) -> None:
    message: SqsMessage = {
        "MessageId": "source",
        "ReceiptHandle": "receipt",
        "Body": ReferenceJob(version=1, kind="reference").model_dump_json(),
    }
    sqs = PollingSqs(message=message)
    collector = worker(
        heartbeat_path=tmp_path / "heartbeat",
        sqs=sqs,
        s3=FakeS3(),
    )
    task = asyncio.create_task(collector.run())
    while not any(name == "delete" for name, _ in sqs.actions):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await collector.close()
    receive = next(parameters for name, parameters in sqs.actions if name == "receive")
    assert receive["WaitTimeSeconds"] == 20
    assert receive["VisibilityTimeout"] == 240


def test_receive_shape_and_payload_key_are_strict() -> None:
    message = {
        "MessageId": "id",
        "ReceiptHandle": "receipt",
        "Body": '{"version":1,"kind":"reference"}',
    }
    assert received_messages(response={"Messages": [message]}) == [message]
    with pytest.raises(ValueError):
        received_messages(response={"Messages": [{"Body": "{}"}]})
    key = payload_key(
        prefix="/payloads/",
        kind="reference",
        fetched_at=datetime(2026, 7, 26, tzinfo=UTC),
    )
    assert key.startswith("payloads/v2/reference/2026/07/26/")
    assert failed_job_kind(body=message["Body"]) == "reference"
    assert failed_job_kind(body="{}") == "invalid"


def test_healthcheck_accepts_current_and_rejects_missing_heartbeat(
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    heartbeat = tmp_path / "heartbeat"
    missing = runner.invoke(
        app,
        [
            "healthcheck",
            "--heartbeat-path",
            str(heartbeat),
            "--max-age-seconds",
            "90",
        ],
    )
    assert missing.exit_code == 1
    heartbeat.touch()
    healthy = runner.invoke(
        app,
        [
            "healthcheck",
            "--heartbeat-path",
            str(heartbeat),
            "--max-age-seconds",
            "90",
        ],
    )
    assert healthy.exit_code == 0


def test_collector_cli_run_builds_worker_from_explicit_settings(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    configured = settings(heartbeat_path=tmp_path / "heartbeat")
    clients: list[str] = []
    workers: list[object] = []

    class FakeSession:
        def client(self, service_name: str) -> object:
            clients.append(service_name)
            return object()

    class FakeWorker:
        def __init__(self, **parameters: object) -> None:
            workers.append(parameters)

    async def fake_run_worker(*, worker: object) -> None:
        workers.append(worker)

    monkeypatch.setattr(
        collector_cli,
        "load_collector_settings_from_environment",
        lambda: configured,
    )
    monkeypatch.setattr(
        collector_cli.boto3,
        "Session",
        lambda **parameters: FakeSession(),
    )
    monkeypatch.setattr(collector_cli, "CollectorWorker", FakeWorker)
    monkeypatch.setattr(collector_cli, "run_worker", fake_run_worker)
    result = CliRunner().invoke(app, ["run"])
    assert result.exit_code == 0
    assert clients == ["sqs", "s3", "sts"]
    assert len(workers) == 2
