"""Boto3 implementation of infrastructure deployment operations."""

import json
import time
from collections.abc import Mapping
from typing import Protocol, cast

import boto3
from botocore.exceptions import ClientError
from pydantic import ValidationError

from fpl_data_relay.infrastructure_migrations.models import (
    AppliedMigrationRecord,
    ChangeSetPolicy,
)

MAPPING_WAIT_SECONDS = 180
MAPPING_POLL_SECONDS = 2
SOURCE_POLICY_NAME = "AssumeFplCollectorRole"


class AwsClient(Protocol):
    """Dynamic boto3 client operation."""

    def __getattr__(self, name: str) -> object: ...


class BotoInfrastructureAws:
    """Fail-fast, typed façade over deployment-related boto3 clients."""

    def __init__(self, *, region: str) -> None:
        session = boto3.Session(region_name=region)
        self._cloudformation = cast("AwsClient", session.client("cloudformation"))
        self._iam = cast("AwsClient", session.client("iam"))
        self._lambda = cast("AwsClient", session.client("lambda"))
        self._s3 = cast("AwsClient", session.client("s3"))
        self._sqs = cast("AwsClient", session.client("sqs"))
        self._ssm = cast("AwsClient", session.client("ssm"))
        self._sts = cast("AwsClient", session.client("sts"))

    def verify_identity(self, *, expected_account_id: str) -> None:
        """Reject credentials for any account other than production."""
        response = self._call(self._sts, "get_caller_identity")
        account = response.get("Account")
        if account != expected_account_id:
            raise RuntimeError(
                "AWS account mismatch: "
                f"expected {expected_account_id}, found {account!r}.",
            )

    def stack_exists(self, *, stack_name: str) -> bool:
        """Return whether CloudFormation can resolve the named stack."""
        try:
            self._call(
                self._cloudformation,
                "describe_stacks",
                StackName=stack_name,
            )
        except ClientError as error:
            if _is_missing_stack(error=error):
                return False
            raise
        return True

    def stack_outputs(self, *, stack_name: str) -> dict[str, str]:
        """Return strict string outputs for exactly one stack."""
        response = self._call(
            self._cloudformation,
            "describe_stacks",
            StackName=stack_name,
        )
        stacks = _required_list(response=response, key="Stacks")
        if len(stacks) != 1:
            raise RuntimeError(
                f"Expected one CloudFormation stack named {stack_name!r}.",
            )
        stack = _mapping(value=stacks[0], label="CloudFormation stack")
        raw_outputs = stack.get("Outputs")
        if not isinstance(raw_outputs, list):
            raise RuntimeError(f"Stack {stack_name!r} has no outputs.")
        outputs: dict[str, str] = {}
        for raw_output in raw_outputs:
            output = _mapping(value=raw_output, label="stack output")
            key = output.get("OutputKey")
            value = output.get("OutputValue")
            if not isinstance(key, str) or not isinstance(value, str):
                raise RuntimeError("CloudFormation returned an invalid stack output.")
            if key in outputs:
                raise RuntimeError(f"Stack output {key!r} is duplicated.")
            outputs[key] = value
        return outputs

    def stack_resource_id(
        self,
        *,
        stack_name: str,
        logical_resource_id: str,
    ) -> str:
        """Resolve a stack logical resource to its physical identifier."""
        response = self._call(
            self._cloudformation,
            "describe_stack_resource",
            StackName=stack_name,
            LogicalResourceId=logical_resource_id,
        )
        detail = _mapping(
            value=response.get("StackResourceDetail"),
            label="stack resource detail",
        )
        physical_id = detail.get("PhysicalResourceId")
        if not isinstance(physical_id, str) or not physical_id:
            raise RuntimeError(
                f"Stack resource {logical_resource_id!r} has no physical ID.",
            )
        return physical_id

    def queue_arn(self, *, queue_url: str) -> str:
        """Resolve one SQS queue URL to its ARN."""
        response = self._call(
            self._sqs,
            "get_queue_attributes",
            QueueUrl=queue_url,
            AttributeNames=["QueueArn"],
        )
        attributes = _mapping(
            value=response.get("Attributes"),
            label="SQS queue attributes",
        )
        queue_arn = attributes.get("QueueArn")
        if not isinstance(queue_arn, str) or not queue_arn:
            raise RuntimeError(f"Queue {queue_url!r} did not return QueueArn.")
        return queue_arn

    def disable_event_source_mappings(self, *, source_arn: str) -> None:
        """Disable every Lambda mapping from a protected fetch queue."""
        mappings = self._event_source_mappings(source_arn=source_arn)
        for mapping in mappings:
            uuid = _required_string(mapping=mapping, key="UUID")
            state = _required_string(mapping=mapping, key="State")
            if state not in {"Disabled", "Disabling"}:
                self._call(
                    self._lambda,
                    "update_event_source_mapping",
                    UUID=uuid,
                    Enabled=False,
                )
            self._wait_for_mapping_disabled(uuid=uuid)

    def assert_no_active_event_source_mappings(
        self,
        *,
        source_arn: str,
    ) -> None:
        """Require every mapping from the fetch queue to be disabled or absent."""
        active: list[str] = []
        for mapping in self._event_source_mappings(source_arn=source_arn):
            state = _required_string(mapping=mapping, key="State")
            if state != "Disabled":
                active.append(
                    f"{_required_string(mapping=mapping, key='UUID')}:{state}",
                )
        if active:
            raise RuntimeError(
                "Fetch queue still has active Lambda mappings: "
                + ", ".join(active),
            )

    def assert_single_enabled_event_source_mapping(
        self,
        *,
        source_arn: str,
        function_name: str,
    ) -> None:
        """Require one enabled result-queue mapping to the ingestion Lambda."""
        matching: list[Mapping[str, object]] = []
        for mapping in self._event_source_mappings(source_arn=source_arn):
            function_arn = _required_string(mapping=mapping, key="FunctionArn")
            if function_arn.rsplit(":", maxsplit=1)[-1] == function_name:
                matching.append(mapping)
        states = [
            _required_string(mapping=mapping, key="State") for mapping in matching
        ]
        if states != ["Enabled"]:
            raise RuntimeError(
                "Expected exactly one enabled result-queue mapping for "
                f"{function_name!r}; found states {states!r}.",
            )

    def assert_payload_bucket(
        self,
        *,
        bucket_name: str,
        payload_prefix: str,
    ) -> None:
        """Verify the payload bucket's required lifecycle and public block."""
        lifecycle = self._call(
            self._s3,
            "get_bucket_lifecycle_configuration",
            Bucket=bucket_name,
        )
        rules = _required_list(response=lifecycle, key="Rules")
        expected_prefix = f"{payload_prefix}/"
        valid_lifecycle = False
        for raw_rule in rules:
            rule = _mapping(value=raw_rule, label="S3 lifecycle rule")
            expiration = _mapping(
                value=rule.get("Expiration"),
                label="S3 lifecycle expiration",
            )
            if (
                rule.get("Status") == "Enabled"
                and rule.get("Prefix") == expected_prefix
                and expiration.get("Days") == 7
            ):
                valid_lifecycle = True
        if not valid_lifecycle:
            raise RuntimeError(
                f"Bucket {bucket_name!r} lacks the seven-day payload lifecycle.",
            )
        access = self._call(
            self._s3,
            "get_public_access_block",
            Bucket=bucket_name,
        )
        block = _mapping(
            value=access.get("PublicAccessBlockConfiguration"),
            label="S3 public access block",
        )
        required = {
            "BlockPublicAcls",
            "BlockPublicPolicy",
            "IgnorePublicAcls",
            "RestrictPublicBuckets",
        }
        if any(block.get(key) is not True for key in required):
            raise RuntimeError(
                f"Bucket {bucket_name!r} does not block every form of public access.",
            )
        encryption = self._call(
            self._s3,
            "get_bucket_encryption",
            Bucket=bucket_name,
        )
        encryption_configuration = _mapping(
            value=encryption.get("ServerSideEncryptionConfiguration"),
            label="S3 encryption configuration",
        )
        encryption_rules = encryption_configuration.get("Rules")
        if not isinstance(encryption_rules, list) or len(encryption_rules) != 1:
            raise RuntimeError(
                f"Bucket {bucket_name!r} has unexpected encryption rules.",
            )
        encryption_rule = _mapping(
            value=encryption_rules[0],
            label="S3 encryption rule",
        )
        encryption_default = _mapping(
            value=encryption_rule.get("ApplyServerSideEncryptionByDefault"),
            label="S3 encryption default",
        )
        if encryption_default.get("SSEAlgorithm") != "AES256":
            raise RuntimeError(
                f"Bucket {bucket_name!r} is not encrypted with AES256.",
            )
        ownership = self._call(
            self._s3,
            "get_bucket_ownership_controls",
            Bucket=bucket_name,
        )
        ownership_controls = _mapping(
            value=ownership.get("OwnershipControls"),
            label="S3 ownership controls",
        )
        if ownership_controls.get("Rules") != [
            {"ObjectOwnership": "BucketOwnerEnforced"},
        ]:
            raise RuntimeError(
                f"Bucket {bucket_name!r} does not enforce bucket ownership.",
            )

    def reconcile_collector_source_policy(
        self,
        *,
        source_user_name: str,
        source_user_arn: str,
        collector_role_arn: str,
    ) -> None:
        """Converge the source user's sole inline policy to the collector role."""
        self._assert_user_arn(
            source_user_name=source_user_name,
            source_user_arn=source_user_arn,
        )
        self._assert_role_trust(
            collector_role_arn=collector_role_arn,
            source_user_arn=source_user_arn,
        )
        self._call(
            self._iam,
            "put_user_policy",
            UserName=source_user_name,
            PolicyName=SOURCE_POLICY_NAME,
            PolicyDocument=json.dumps(
                _source_policy(collector_role_arn=collector_role_arn),
                separators=(",", ":"),
                sort_keys=True,
            ),
        )

    def assert_collector_source_policy(
        self,
        *,
        source_user_name: str,
        source_user_arn: str,
        collector_role_arn: str,
    ) -> None:
        """Verify the role trust and exact assume-role-only source policy."""
        self._assert_user_arn(
            source_user_name=source_user_name,
            source_user_arn=source_user_arn,
        )
        self._assert_role_trust(
            collector_role_arn=collector_role_arn,
            source_user_arn=source_user_arn,
        )
        response = self._call(
            self._iam,
            "get_user_policy",
            UserName=source_user_name,
            PolicyName=SOURCE_POLICY_NAME,
        )
        policy = response.get("PolicyDocument")
        if policy != _source_policy(collector_role_arn=collector_role_arn):
            raise RuntimeError(
                f"IAM user {source_user_name!r} has an unexpected source policy.",
            )
        inline = self._call(
            self._iam,
            "list_user_policies",
            UserName=source_user_name,
        )
        if inline.get("PolicyNames") != [SOURCE_POLICY_NAME]:
            raise RuntimeError(
                f"IAM user {source_user_name!r} has additional inline policies.",
            )
        attached = self._call(
            self._iam,
            "list_attached_user_policies",
            UserName=source_user_name,
        )
        if attached.get("AttachedPolicies") != []:
            raise RuntimeError(
                f"IAM user {source_user_name!r} has attached policies.",
            )
        groups = self._call(
            self._iam,
            "list_groups_for_user",
            UserName=source_user_name,
        )
        if groups.get("Groups") != []:
            raise RuntimeError(
                f"IAM user {source_user_name!r} inherits group policies.",
            )

    def read_migration_records(
        self,
        *,
        parameter_prefix: str,
    ) -> list[AppliedMigrationRecord]:
        """Load and validate every SSM migration record below the prefix."""
        records: list[AppliedMigrationRecord] = []
        next_token: str | None = None
        while True:
            arguments: dict[str, object] = {
                "Path": f"{parameter_prefix}/",
                "Recursive": False,
                "WithDecryption": False,
            }
            if next_token is not None:
                arguments["NextToken"] = next_token
            response = self._call(
                self._ssm,
                "get_parameters_by_path",
                **arguments,
            )
            for raw_parameter in _required_list(
                response=response,
                key="Parameters",
            ):
                parameter = _mapping(value=raw_parameter, label="SSM parameter")
                name = parameter.get("Name")
                value = parameter.get("Value")
                if not isinstance(name, str) or not isinstance(value, str):
                    raise RuntimeError("SSM returned an invalid migration parameter.")
                try:
                    record = AppliedMigrationRecord.model_validate_json(value)
                except ValidationError as error:
                    raise RuntimeError(
                        f"SSM migration record {name!r} is invalid.",
                    ) from error
                expected_name = (
                    f"{parameter_prefix}/{record.version:04d}_{record.name}"
                )
                if name != expected_name:
                    raise RuntimeError(
                        f"SSM migration parameter name mismatch: {name!r}.",
                    )
                records.append(record)
            raw_next_token = response.get("NextToken")
            if raw_next_token is None:
                break
            if not isinstance(raw_next_token, str):
                raise RuntimeError("SSM returned an invalid pagination token.")
            next_token = raw_next_token
        records.sort(key=lambda record: record.version)
        return records

    def write_migration_record(
        self,
        *,
        parameter_prefix: str,
        record: AppliedMigrationRecord,
    ) -> None:
        """Atomically create one immutable SSM migration record."""
        self._call(
            self._ssm,
            "put_parameter",
            Name=f"{parameter_prefix}/{record.version:04d}_{record.name}",
            Description="Applied FPL Data Relay infrastructure migration.",
            Value=record.model_dump_json(),
            Type="String",
            Overwrite=False,
        )

    def validate_change_set(
        self,
        *,
        change_set_arn: str,
        policy: ChangeSetPolicy,
    ) -> None:
        """Reject destructive changes to protected CloudFormation resources."""
        violations: list[str] = []
        next_token: str | None = None
        while True:
            arguments: dict[str, object] = {"ChangeSetName": change_set_arn}
            if next_token is not None:
                arguments["NextToken"] = next_token
            response = self._call(
                self._cloudformation,
                "describe_change_set",
                **arguments,
            )
            for raw_change in _required_list(response=response, key="Changes"):
                change = _mapping(value=raw_change, label="change-set change")
                resource = _mapping(
                    value=change.get("ResourceChange"),
                    label="change-set resource change",
                )
                logical_id = resource.get("LogicalResourceId")
                action = resource.get("Action")
                replacement = resource.get("Replacement")
                protected = policy is ChangeSetPolicy.DATA or (
                    policy is ChangeSetPolicy.APPLICATION
                    and logical_id == "IngestionQueue"
                )
                destructive = action == "Remove" or replacement in {
                    "True",
                    "Conditional",
                }
                if protected and destructive:
                    violations.append(
                        f"{logical_id}:{action}:replacement={replacement}",
                    )
            raw_next_token = response.get("NextToken")
            if raw_next_token is None:
                break
            if not isinstance(raw_next_token, str):
                raise RuntimeError(
                    "CloudFormation returned an invalid pagination token.",
                )
            next_token = raw_next_token
        if violations:
            raise RuntimeError(
                "CloudFormation change set contains protected destructive changes: "
                + ", ".join(violations),
            )

    def _event_source_mappings(
        self,
        *,
        source_arn: str,
    ) -> list[Mapping[str, object]]:
        mappings: list[Mapping[str, object]] = []
        marker: str | None = None
        while True:
            arguments: dict[str, object] = {"EventSourceArn": source_arn}
            if marker is not None:
                arguments["Marker"] = marker
            response = self._call(
                self._lambda,
                "list_event_source_mappings",
                **arguments,
            )
            mappings.extend(
                _mapping(value=value, label="Lambda event-source mapping")
                for value in _required_list(
                    response=response,
                    key="EventSourceMappings",
                )
            )
            raw_marker = response.get("NextMarker")
            if raw_marker is None:
                break
            if not isinstance(raw_marker, str):
                raise RuntimeError("Lambda returned an invalid pagination marker.")
            marker = raw_marker
        return mappings

    def _wait_for_mapping_disabled(self, *, uuid: str) -> None:
        deadline = time.monotonic() + MAPPING_WAIT_SECONDS
        while time.monotonic() < deadline:
            response = self._call(
                self._lambda,
                "get_event_source_mapping",
                UUID=uuid,
            )
            state = response.get("State")
            if state == "Disabled":
                return
            if state in {"CreateFailed", "DeleteFailed", "UpdateFailed"}:
                raise RuntimeError(
                    f"Lambda mapping {uuid!r} entered failure state {state!r}.",
                )
            time.sleep(MAPPING_POLL_SECONDS)
        raise TimeoutError(f"Timed out disabling Lambda mapping {uuid!r}.")

    def _assert_user_arn(
        self,
        *,
        source_user_name: str,
        source_user_arn: str,
    ) -> None:
        response = self._call(
            self._iam,
            "get_user",
            UserName=source_user_name,
        )
        user = _mapping(value=response.get("User"), label="IAM user")
        if user.get("Arn") != source_user_arn:
            raise RuntimeError(
                f"IAM user {source_user_name!r} has an unexpected ARN.",
            )

    def _assert_role_trust(
        self,
        *,
        collector_role_arn: str,
        source_user_arn: str,
    ) -> None:
        role_name = collector_role_arn.rsplit("/", maxsplit=1)[-1]
        response = self._call(self._iam, "get_role", RoleName=role_name)
        role = _mapping(value=response.get("Role"), label="IAM role")
        if role.get("Arn") != collector_role_arn:
            raise RuntimeError(f"Collector role {role_name!r} has an unexpected ARN.")
        trust = role.get("AssumeRolePolicyDocument")
        expected = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": source_user_arn},
                    "Action": "sts:AssumeRole",
                },
            ],
        }
        if trust != expected:
            raise RuntimeError(
                f"Collector role {role_name!r} has an unexpected trust policy.",
            )

    @staticmethod
    def _call(
        client: AwsClient,
        operation: str,
        **parameters: object,
    ) -> dict[str, object]:
        method = getattr(client, operation)
        if not callable(method):
            raise TypeError(f"AWS client operation {operation!r} is not callable.")
        response = method(**parameters)
        if not isinstance(response, dict):
            raise TypeError(f"AWS operation {operation!r} returned a non-object.")
        return cast("dict[str, object]", response)


def _mapping(*, value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"AWS returned an invalid {label}.")
    return cast("Mapping[str, object]", value)


def _required_list(*, response: Mapping[str, object], key: str) -> list[object]:
    value = response.get(key)
    if not isinstance(value, list):
        raise RuntimeError(f"AWS response lacks list field {key!r}.")
    return cast("list[object]", value)


def _required_string(*, mapping: Mapping[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"AWS response lacks string field {key!r}.")
    return value


def _source_policy(*, collector_role_arn: str) -> dict[str, object]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "sts:AssumeRole",
                "Resource": collector_role_arn,
            },
        ],
    }


def _is_missing_stack(*, error: ClientError) -> bool:
    details = error.response.get("Error", {})
    return (
        details.get("Code") == "ValidationError"
        and "does not exist" in str(details.get("Message", ""))
    )
