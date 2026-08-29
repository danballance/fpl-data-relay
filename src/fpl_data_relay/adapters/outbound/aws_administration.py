"""Boto3-backed production administration adapter."""

import re
from collections.abc import Mapping
from typing import Protocol, cast

import boto3
from botocore.config import Config

from fpl_data_relay.adapters.outbound.postgres.connection import PoolProtocol
from fpl_data_relay.adapters.outbound.postgres.database import PostgresDatabase
from fpl_data_relay.adapters.outbound.rds_data import RdsDataClient, RdsDataPool
from fpl_data_relay.application.ports.administration import (
    AwsIdentity,
    AwsResources,
    QueueDepth,
    ScheduleSnapshot,
    ScheduleState,
    ScheduleTargetSnapshot,
)
from fpl_data_relay.config import AdminSettings


class StsClient(Protocol):
    def get_caller_identity(self) -> dict[str, object]: ...


class CloudFormationClient(Protocol):
    def describe_stacks(self, **parameters: object) -> dict[str, object]: ...


class SqsClient(Protocol):
    def get_queue_attributes(self, **parameters: object) -> dict[str, object]: ...

    def send_message(self, **parameters: object) -> dict[str, object]: ...

    def receive_message(self, **parameters: object) -> dict[str, object]: ...


class SchedulerClient(Protocol):
    def list_schedules(self, **parameters: object) -> dict[str, object]: ...

    def get_schedule(self, **parameters: object) -> dict[str, object]: ...

    def update_schedule(self, **parameters: object) -> dict[str, object]: ...


class AwsBotoAdministration:
    """Resolve stack resources and operate only the relay control plane."""

    def __init__(self, *, settings: AdminSettings) -> None:
        self._settings = settings
        self._session = boto3.Session(
            profile_name=settings.aws_profile,
            region_name=settings.aws_region,
        )
        retry_config = Config(
            retries={"mode": "standard", "total_max_attempts": 3},
        )
        self._sts = cast("StsClient", self._session.client("sts", config=retry_config))
        self._cloudformation = cast(
            "CloudFormationClient",
            self._session.client("cloudformation", config=retry_config),
        )
        self._sqs = cast("SqsClient", self._session.client("sqs", config=retry_config))
        self._scheduler = cast(
            "SchedulerClient",
            self._session.client("scheduler", config=retry_config),
        )
        self._rds_data = cast(
            "RdsDataClient",
            self._session.client("rds-data", config=retry_config),
        )
        self._resources: AwsResources | None = None

    def identity(self) -> AwsIdentity:
        """Validate the active profile against the configured account."""
        response = self._sts.get_caller_identity()
        account_id = require_string(response=response, key="Account")
        arn = require_string(response=response, key="Arn")
        if account_id != self._settings.aws_account_id:
            raise RuntimeError(
                "AWS account mismatch: "
                f"expected {self._settings.aws_account_id}, found {account_id}.",
            )
        return AwsIdentity(account_id=account_id, arn=arn)

    def resources(self) -> AwsResources:
        """Resolve every required output and reject incomplete stacks."""
        if self._resources is not None:
            return self._resources
        data = self._stack_outputs(stack_name=self._settings.data_stack_name)
        app = self._stack_outputs(stack_name=self._settings.app_stack_name)
        self._resources = AwsResources(
            database_resource_arn=require_output(
                outputs=data,
                key="DatabaseClusterArn",
            ),
            database_secret_arn=require_output(
                outputs=data,
                key="DatabaseSecretArn",
            ),
            database_name=require_output(outputs=data, key="DatabaseName"),
            fetch_queue_url=require_output(outputs=app, key="FetchQueueUrl"),
            fetch_dead_letter_queue_url=require_output(
                outputs=app,
                key="FetchDeadLetterQueueUrl",
            ),
            result_queue_url=require_output(outputs=app, key="ResultQueueUrl"),
            result_dead_letter_queue_url=require_output(
                outputs=app,
                key="ResultDeadLetterQueueUrl",
            ),
            schedule_dead_letter_queue_url=require_output(
                outputs=app,
                key="ScheduleDeadLetterQueueUrl",
            ),
            community_queue_url=require_output(
                outputs=app,
                key="CommunityQueueUrl",
            ),
            community_dead_letter_queue_url=require_output(
                outputs=app,
                key="CommunityDeadLetterQueueUrl",
            ),
            reference_schedule_group_name=(
                f"{self._settings.app_stack_name}-reference"
            ),
            reference_schedule_name=(
                f"{self._settings.app_stack_name}-reference-quarter-hour"
            ),
            live_schedule_group_name=f"{self._settings.app_stack_name}-live",
            community_schedule_group_name=(
                f"{self._settings.app_stack_name}-community"
            ),
            community_schedule_name=(
                f"{self._settings.app_stack_name}-community-daily"
            ),
        )
        return self._resources

    def database(self) -> PostgresDatabase:
        """Build a database facade using this adapter's explicit AWS session."""
        resources = self.resources()
        return PostgresDatabase(
            pool=cast(
                "PoolProtocol",
                RdsDataPool(
                    client=self._rds_data,
                    resource_arn=resources.database_resource_arn,
                    secret_arn=resources.database_secret_arn,
                    database_name=resources.database_name,
                ),
            ),
        )

    def app_deployed_revision(self) -> str:
        """Return the exact revision exposed by the deployed application stack."""
        revision = require_output(
            outputs=self._stack_outputs(
                stack_name=self._settings.app_stack_name,
            ),
            key="DeployedRevision",
        )
        if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
            raise RuntimeError(
                "CloudFormation output DeployedRevision must be a full "
                "lowercase Git SHA.",
            )
        return revision

    def queue_depths(self, *, include_dead_letters: bool) -> list[QueueDepth]:
        """Return complete depth samples for working queues and optional DLQs."""
        resources = self.resources()
        queues = [
            ("fetch", resources.fetch_queue_url),
            ("result", resources.result_queue_url),
            ("community", resources.community_queue_url),
        ]
        if include_dead_letters:
            queues.extend(
                [
                    ("fetch-dead-letter", resources.fetch_dead_letter_queue_url),
                    ("result-dead-letter", resources.result_dead_letter_queue_url),
                    (
                        "schedule-dead-letter",
                        resources.schedule_dead_letter_queue_url,
                    ),
                    (
                        "community-dead-letter",
                        resources.community_dead_letter_queue_url,
                    ),
                ],
            )
        return [self._queue_depth(name=name, url=url) for name, url in queues]

    def schedule_snapshots(self) -> list[ScheduleSnapshot]:
        """Return fixed and dynamic relay schedule definitions."""
        resources = self.resources()
        identities = [
            (
                resources.reference_schedule_group_name,
                resources.reference_schedule_name,
            ),
            (
                resources.community_schedule_group_name,
                resources.community_schedule_name,
            ),
        ]
        next_token: str | None = None
        while True:
            parameters: dict[str, object] = {
                "GroupName": resources.live_schedule_group_name,
                "NamePrefix": "fpl-live-",
            }
            if next_token is not None:
                parameters["NextToken"] = next_token
            response = self._scheduler.list_schedules(**parameters)
            schedules = response.get("Schedules")
            if not isinstance(schedules, list):
                raise RuntimeError("Scheduler returned an invalid schedule list.")
            for raw_schedule in schedules:
                if not isinstance(raw_schedule, Mapping):
                    raise RuntimeError("Scheduler returned an invalid schedule item.")
                name = raw_schedule.get("Name")
                if not isinstance(name, str):
                    raise RuntimeError("Scheduler schedule has no string name.")
                identities.append((resources.live_schedule_group_name, name))
            raw_token = response.get("NextToken")
            if raw_token is None:
                break
            if not isinstance(raw_token, str):
                raise RuntimeError("Scheduler returned an invalid pagination token.")
            next_token = raw_token
        return [
            self._schedule_snapshot(group_name=group_name, name=name)
            for group_name, name in identities
        ]

    def set_schedule_state(
        self,
        *,
        schedule: ScheduleSnapshot,
        state: ScheduleState,
        schedule_expression: str,
    ) -> None:
        """Apply a state change without clearing optional schedule fields."""
        target: dict[str, object] = {
            "Arn": schedule.target.arn,
            "RoleArn": schedule.target.role_arn,
            "Input": schedule.target.input,
            "DeadLetterConfig": {"Arn": schedule.target.dead_letter_arn},
            "RetryPolicy": {
                "MaximumEventAgeInSeconds": (
                    schedule.target.maximum_event_age_seconds
                ),
                "MaximumRetryAttempts": schedule.target.maximum_retry_attempts,
            },
        }
        parameters: dict[str, object] = {
            "Name": schedule.name,
            "GroupName": schedule.group_name,
            "ScheduleExpression": schedule_expression,
            "ScheduleExpressionTimezone": schedule.schedule_expression_timezone,
            "FlexibleTimeWindow": {"Mode": schedule.flexible_window_mode},
            "State": state.value,
            "Target": target,
        }
        if schedule.action_after_completion is not None:
            parameters["ActionAfterCompletion"] = schedule.action_after_completion
        if schedule.description is not None:
            parameters["Description"] = schedule.description
        self._scheduler.update_schedule(**parameters)

    def send_fetch_job(self, *, message_body: str) -> str:
        """Send one validated job body to the fetch queue."""
        return self._send_message(
            queue_url=self.resources().fetch_queue_url,
            message_body=message_body,
        )

    def send_community_job(self, *, message_body: str) -> str:
        """Send one validated job body to the community queue."""
        return self._send_message(
            queue_url=self.resources().community_queue_url,
            message_body=message_body,
        )

    def peek_dead_letters(
        self,
        *,
        queue_name: str,
        max_messages: int,
    ) -> list[str]:
        """Receive DLQ messages with zero visibility timeout and no deletion."""
        if max_messages < 1 or max_messages > 10:
            raise ValueError("DLQ peek message count must be between 1 and 10.")
        urls = dead_letter_urls(resources=self.resources())
        if queue_name not in urls:
            allowed = ", ".join(sorted(urls))
            raise ValueError(f"Unknown DLQ {queue_name!r}; expected one of {allowed}.")
        response = self._sqs.receive_message(
            QueueUrl=urls[queue_name],
            MaxNumberOfMessages=max_messages,
            VisibilityTimeout=0,
            WaitTimeSeconds=0,
            AttributeNames=["All"],
            MessageAttributeNames=["All"],
        )
        messages = response.get("Messages", [])
        if not isinstance(messages, list):
            raise RuntimeError("SQS returned an invalid Messages value.")
        bodies: list[str] = []
        for message in messages:
            if not isinstance(message, Mapping):
                raise RuntimeError("SQS returned an invalid message.")
            body = message.get("Body")
            if not isinstance(body, str):
                raise RuntimeError("SQS message has no string body.")
            bodies.append(body)
        return bodies

    def _stack_outputs(self, *, stack_name: str) -> dict[str, str]:
        response = self._cloudformation.describe_stacks(StackName=stack_name)
        stacks = response.get("Stacks")
        if not isinstance(stacks, list) or len(stacks) != 1:
            raise RuntimeError(f"Expected exactly one stack named {stack_name}.")
        stack = stacks[0]
        if not isinstance(stack, Mapping):
            raise RuntimeError(f"Stack {stack_name} has an invalid response.")
        raw_outputs = stack.get("Outputs")
        if not isinstance(raw_outputs, list):
            raise RuntimeError(f"Stack {stack_name} has no outputs.")
        outputs: dict[str, str] = {}
        for raw_output in raw_outputs:
            if not isinstance(raw_output, Mapping):
                raise RuntimeError(f"Stack {stack_name} has an invalid output.")
            key = raw_output.get("OutputKey")
            value = raw_output.get("OutputValue")
            if not isinstance(key, str) or not isinstance(value, str):
                raise RuntimeError(f"Stack {stack_name} has a malformed output.")
            if key in outputs:
                raise RuntimeError(f"Stack {stack_name} repeats output {key}.")
            outputs[key] = value
        return outputs

    def _queue_depth(self, *, name: str, url: str) -> QueueDepth:
        response = self._sqs.get_queue_attributes(
            QueueUrl=url,
            AttributeNames=[
                "ApproximateNumberOfMessages",
                "ApproximateNumberOfMessagesNotVisible",
                "ApproximateNumberOfMessagesDelayed",
            ],
        )
        attributes = response.get("Attributes")
        if not isinstance(attributes, Mapping):
            raise RuntimeError(f"SQS returned no attributes for {name}.")
        typed_attributes = cast("Mapping[str, object]", attributes)
        return QueueDepth(
            name=name,
            url=url,
            visible=require_nonnegative_int_string(
                values=typed_attributes,
                key="ApproximateNumberOfMessages",
            ),
            in_flight=require_nonnegative_int_string(
                values=typed_attributes,
                key="ApproximateNumberOfMessagesNotVisible",
            ),
            delayed=require_nonnegative_int_string(
                values=typed_attributes,
                key="ApproximateNumberOfMessagesDelayed",
            ),
        )

    def _schedule_snapshot(self, *, group_name: str, name: str) -> ScheduleSnapshot:
        response = self._scheduler.get_schedule(GroupName=group_name, Name=name)
        flexible = require_mapping(response=response, key="FlexibleTimeWindow")
        target = require_mapping(response=response, key="Target")
        dead_letter = require_mapping(response=target, key="DeadLetterConfig")
        retry = require_mapping(response=target, key="RetryPolicy")
        action = response.get("ActionAfterCompletion")
        description = response.get("Description")
        if action is not None and not isinstance(action, str):
            raise RuntimeError(f"Schedule {name} has an invalid completion action.")
        if description is not None and not isinstance(description, str):
            raise RuntimeError(f"Schedule {name} has an invalid description.")
        return ScheduleSnapshot(
            name=require_string(response=response, key="Name"),
            group_name=require_string(response=response, key="GroupName"),
            state=ScheduleState(require_string(response=response, key="State")),
            schedule_expression=require_string(
                response=response,
                key="ScheduleExpression",
            ),
            schedule_expression_timezone=require_string(
                response=response,
                key="ScheduleExpressionTimezone",
            ),
            flexible_window_mode=require_string(
                response=flexible,
                key="Mode",
            ),
            action_after_completion=action,
            description=description,
            target=ScheduleTargetSnapshot(
                arn=require_string(response=target, key="Arn"),
                role_arn=require_string(response=target, key="RoleArn"),
                input=require_string(response=target, key="Input"),
                dead_letter_arn=require_string(response=dead_letter, key="Arn"),
                maximum_event_age_seconds=require_integer(
                    response=retry,
                    key="MaximumEventAgeInSeconds",
                ),
                maximum_retry_attempts=require_integer(
                    response=retry,
                    key="MaximumRetryAttempts",
                ),
            ),
        )

    def _send_message(self, *, queue_url: str, message_body: str) -> str:
        response = self._sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=message_body,
        )
        return require_string(response=response, key="MessageId")


def dead_letter_urls(*, resources: AwsResources) -> dict[str, str]:
    """Return the supported DLQ selector mapping."""
    return {
        "fetch": resources.fetch_dead_letter_queue_url,
        "result": resources.result_dead_letter_queue_url,
        "schedule": resources.schedule_dead_letter_queue_url,
        "community": resources.community_dead_letter_queue_url,
    }


def require_output(*, outputs: Mapping[str, str], key: str) -> str:
    """Require one nonempty stack output."""
    value = outputs.get(key)
    if value is None or value.strip() == "":
        raise RuntimeError(f"Required CloudFormation output is missing: {key}")
    return value


def require_string(*, response: Mapping[str, object], key: str) -> str:
    """Require one string from an AWS SDK response."""
    value = response.get(key)
    if not isinstance(value, str) or value == "":
        raise RuntimeError(f"AWS response field {key} must be a nonempty string.")
    return value


def require_mapping(
    *,
    response: Mapping[str, object],
    key: str,
) -> Mapping[str, object]:
    """Require one mapping from an AWS SDK response."""
    value = response.get(key)
    if not isinstance(value, Mapping):
        raise RuntimeError(f"AWS response field {key} must be an object.")
    return cast("Mapping[str, object]", value)


def require_integer(*, response: Mapping[str, object], key: str) -> int:
    """Require one integer from an AWS SDK response."""
    value = response.get(key)
    if not isinstance(value, int):
        raise RuntimeError(f"AWS response field {key} must be an integer.")
    return value


def require_nonnegative_int_string(
    *,
    values: Mapping[str, object],
    key: str,
) -> int:
    """Parse one nonnegative SQS approximate-count attribute."""
    raw_value = values.get(key)
    if not isinstance(raw_value, str) or not raw_value.isdigit():
        raise RuntimeError(f"SQS attribute {key} must be a nonnegative integer.")
    return int(raw_value)
