from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import boto3
import pytest

from fpl_data_relay.adapters.outbound.aws_administration import (
    AwsBotoAdministration,
    dead_letter_urls,
    require_integer,
    require_mapping,
    require_nonnegative_int_string,
    require_output,
    require_string,
)
from fpl_data_relay.application.jobs import LiveJob
from fpl_data_relay.application.ports.administration import ScheduleState
from fpl_data_relay.config import AdminSettings


def settings() -> AdminSettings:
    return AdminSettings.model_validate(
        {
            "aws_profile": "admin",
            "aws_region": "eu-west-2",
            "aws_account_id": "123456789012",
            "data_stack_name": "data",
            "app_stack_name": "app",
            "nas_ssh_target": "nas",
            "nas_stack_directory": "/stack",
            "nas_compose_executable": "/docker-compose",
            "nas_docker_executable": "/docker",
            "nas_ssh_connect_timeout_seconds": 10,
            "drain_timeout_seconds": 20,
            "drain_poll_seconds": 2,
            "drain_stable_seconds": 4,
            "nas_health_attempts": 2,
            "nas_health_interval_seconds": 1,
            "nas_log_tail_lines": 10,
        },
    )


def app_outputs() -> dict[str, str]:
    return {
        "FetchQueueUrl": "https://sqs/fetch",
        "FetchDeadLetterQueueUrl": "https://sqs/fetch-dlq",
        "ResultQueueUrl": "https://sqs/result",
        "ResultDeadLetterQueueUrl": "https://sqs/result-dlq",
        "ScheduleDeadLetterQueueUrl": "https://sqs/schedule-dlq",
        "CommunityQueueUrl": "https://sqs/community",
        "CommunityDeadLetterQueueUrl": "https://sqs/community-dlq",
    }


class FakeSts:
    account = "123456789012"

    def get_caller_identity(self) -> dict[str, object]:
        return {"Account": self.account, "Arn": "arn:operator"}


class FakeCloudFormation:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.invalid = False
        self.revision = "a" * 40

    def describe_stacks(self, **parameters: object) -> dict[str, object]:
        stack_name = str(parameters["StackName"])
        self.calls.append(stack_name)
        if self.invalid:
            return {"Stacks": []}
        values = (
            {
                "DatabaseClusterArn": "arn:db",
                "DatabaseSecretArn": "arn:secret",
                "DatabaseName": "relay",
            }
            if stack_name == "data"
            else app_outputs() | {"DeployedRevision": self.revision}
        )
        return {
            "Stacks": [
                {
                    "Outputs": [
                        {"OutputKey": key, "OutputValue": value}
                        for key, value in values.items()
                    ],
                },
            ],
        }


class FakeSqs:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []
        self.received_parameters: dict[str, object] | None = None
        self.messages: object = [{"Body": "failure"}]

    def get_queue_attributes(self, **parameters: object) -> dict[str, object]:
        del parameters
        return {
            "Attributes": {
                "ApproximateNumberOfMessages": "1",
                "ApproximateNumberOfMessagesNotVisible": "2",
                "ApproximateNumberOfMessagesDelayed": "3",
            },
        }

    def send_message(self, **parameters: object) -> dict[str, object]:
        self.sent.append(parameters)
        return {"MessageId": f"message-{len(self.sent)}"}

    def receive_message(self, **parameters: object) -> dict[str, object]:
        self.received_parameters = parameters
        return {"Messages": self.messages}


class FakeScheduler:
    def __init__(self) -> None:
        self.updates: list[dict[str, object]] = []

    def list_schedules(self, **parameters: object) -> dict[str, object]:
        assert parameters["GroupName"] == "app-live"
        return {"Schedules": [{"Name": "fpl-live-window"}]}

    def get_schedule(self, **parameters: object) -> dict[str, object]:
        name = str(parameters["Name"])
        live = name.startswith("fpl-live-")
        body = (
            LiveJob(
                version=1,
                kind="live",
                season_id="2026-27",
                event_id=1,
                window_start=datetime(2026, 8, 24, 11, tzinfo=UTC),
                window_end=datetime(2026, 8, 24, 15, tzinfo=UTC),
            ).model_dump_json()
            if live
            else '{"version":1,"kind":"reference"}'
        )
        response: dict[str, object] = {
            "Name": name,
            "GroupName": parameters["GroupName"],
            "State": "ENABLED",
            "ScheduleExpression": (
                "at(2026-08-24T11:00:00)"
                if live
                else "cron(0/15 * * * ? *)"
            ),
            "ScheduleExpressionTimezone": "UTC",
            "FlexibleTimeWindow": {"Mode": "OFF"},
            "Description": "schedule",
            "Target": {
                "Arn": "arn:queue",
                "RoleArn": "arn:role",
                "Input": body,
                "DeadLetterConfig": {"Arn": "arn:dlq"},
                "RetryPolicy": {
                    "MaximumEventAgeInSeconds": 900,
                    "MaximumRetryAttempts": 3,
                },
            },
        }
        if live:
            response["ActionAfterCompletion"] = "NONE"
        return response

    def update_schedule(self, **parameters: object) -> dict[str, object]:
        self.updates.append(parameters)
        return {"ScheduleArn": "arn:schedule"}


class FakeSession:
    def __init__(self) -> None:
        self.sts = FakeSts()
        self.cloudformation = FakeCloudFormation()
        self.sqs = FakeSqs()
        self.scheduler = FakeScheduler()
        self.rds_data = object()

    def client(self, service_name: str, **parameters: object) -> object:
        assert "config" in parameters
        return {
            "sts": self.sts,
            "cloudformation": self.cloudformation,
            "sqs": self.sqs,
            "scheduler": self.scheduler,
            "rds-data": self.rds_data,
        }[service_name]


def adapter(
    *,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[AwsBotoAdministration, FakeSession]:
    fake_session = FakeSession()

    def session_factory(**parameters: object) -> FakeSession:
        assert parameters == {
            "profile_name": "admin",
            "region_name": "eu-west-2",
        }
        return fake_session

    monkeypatch.setattr(boto3, "Session", session_factory)
    return AwsBotoAdministration(settings=settings()), fake_session


def test_aws_adapter_resolves_identity_resources_queues_and_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    administration, session = adapter(monkeypatch=monkeypatch)
    assert administration.identity().arn == "arn:operator"
    resources = administration.resources()
    assert resources.database_name == "relay"
    assert resources.reference_schedule_group_name == "app-reference"
    assert resources.reference_schedule_name == "app-reference-quarter-hour"
    assert resources.live_schedule_group_name == "app-live"
    assert resources.community_schedule_group_name == "app-community"
    assert resources.community_schedule_name == "app-community-daily"
    assert administration.resources() is resources
    assert session.cloudformation.calls == ["data", "app"]
    assert administration.app_deployed_revision() == "a" * 40
    assert len(administration.queue_depths(include_dead_letters=False)) == 3
    all_depths = administration.queue_depths(include_dead_letters=True)
    assert len(all_depths) == 7
    assert all_depths[0].total == 6
    assert administration.database() is not None
    urls = dead_letter_urls(resources=resources)
    assert set(urls) == {"fetch", "result", "schedule", "community"}

    session.sts.account = "999999999999"
    with pytest.raises(RuntimeError, match="account mismatch"):
        administration.identity()

    session.cloudformation.revision = "not-a-revision"
    with pytest.raises(RuntimeError, match="full lowercase Git SHA"):
        administration.app_deployed_revision()


def test_aws_adapter_snapshots_updates_sends_and_peeks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    administration, session = adapter(monkeypatch=monkeypatch)
    schedules = administration.schedule_snapshots()
    assert len(schedules) == 3
    live = schedules[-1]
    assert live.action_after_completion == "NONE"
    administration.set_schedule_state(
        schedule=live,
        state=ScheduleState.DISABLED,
        schedule_expression=live.schedule_expression,
    )
    update = session.scheduler.updates[0]
    assert update["State"] == "DISABLED"
    assert update["ActionAfterCompletion"] == "NONE"
    assert update["Description"] == "schedule"

    assert administration.send_fetch_job(message_body="fetch") == "message-1"
    assert administration.send_community_job(message_body="community") == "message-2"
    assert session.sqs.sent[0]["QueueUrl"] == "https://sqs/fetch"
    assert administration.peek_dead_letters(
        queue_name="fetch",
        max_messages=10,
    ) == ["failure"]
    assert session.sqs.received_parameters is not None
    assert session.sqs.received_parameters["VisibilityTimeout"] == 0
    with pytest.raises(ValueError, match="between 1 and 10"):
        administration.peek_dead_letters(queue_name="fetch", max_messages=11)
    with pytest.raises(ValueError, match="Unknown DLQ"):
        administration.peek_dead_letters(queue_name="wrong", max_messages=1)

    session.sqs.messages = []
    assert administration.peek_dead_letters(
        queue_name="community",
        max_messages=1,
    ) == []


def test_aws_response_validation_helpers_fail_fast() -> None:
    assert require_output(outputs={"A": "value"}, key="A") == "value"
    assert require_string(response={"A": "value"}, key="A") == "value"
    assert require_mapping(response={"A": {"B": 1}}, key="A") == {"B": 1}
    assert require_integer(response={"A": 1}, key="A") == 1
    assert require_nonnegative_int_string(values={"A": "2"}, key="A") == 2
    with pytest.raises(RuntimeError, match="missing"):
        require_output(outputs={}, key="A")
    with pytest.raises(RuntimeError, match="nonempty"):
        require_string(response={"A": 1}, key="A")
    with pytest.raises(RuntimeError, match="object"):
        require_mapping(response={"A": []}, key="A")
    with pytest.raises(RuntimeError, match="integer"):
        require_integer(response={"A": "1"}, key="A")
    with pytest.raises(RuntimeError, match="nonnegative"):
        require_nonnegative_int_string(values={"A": "-1"}, key="A")


def test_aws_adapter_rejects_invalid_stack_and_sdk_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    administration, session = adapter(monkeypatch=monkeypatch)
    session.cloudformation.invalid = True
    with pytest.raises(RuntimeError, match="exactly one stack"):
        administration.resources()

    administration, session = adapter(monkeypatch=monkeypatch)
    session.sqs.messages = "wrong"
    with pytest.raises(RuntimeError, match="Messages"):
        administration.peek_dead_letters(queue_name="fetch", max_messages=1)

    session.sqs.messages = [cast_mapping({"Body": 1})]
    with pytest.raises(RuntimeError, match="string body"):
        administration.peek_dead_letters(queue_name="fetch", max_messages=1)


def cast_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return value
