from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

import pytest
from botocore.exceptions import ClientError

from fpl_data_relay.infrastructure_migrations import aws as aws_module
from fpl_data_relay.infrastructure_migrations.aws import (
    BotoInfrastructureAws,
)
from fpl_data_relay.infrastructure_migrations.models import (
    AppliedMigrationRecord,
    ChangeSetPolicy,
)

ACCOUNT_ID = "757771412865"
USER_ARN = f"arn:aws:iam::{ACCOUNT_ID}:user/fpl-relay-nas-source"
ROLE_ARN = f"arn:aws:iam::{ACCOUNT_ID}:role/fpl-relay-app-CollectorRole"
QUEUE_ARN = f"arn:aws:sqs:eu-west-2:{ACCOUNT_ID}:fetch"


class FakeBotoClient:
    def __init__(
        self,
        *,
        responses: dict[str, list[dict[str, object] | Exception]],
    ) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []

    def __getattr__(
        self,
        name: str,
    ) -> Callable[..., dict[str, object]]:
        def operation(**parameters: object) -> dict[str, object]:
            self.calls.append((name, parameters))
            responses = self.responses.get(name, [])
            if not responses:
                return {}
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        return operation


def boto_facade(
    *,
    cloudformation: FakeBotoClient | None = None,
    iam: FakeBotoClient | None = None,
    lambda_client: FakeBotoClient | None = None,
    s3: FakeBotoClient | None = None,
    sqs: FakeBotoClient | None = None,
    ssm: FakeBotoClient | None = None,
    sts: FakeBotoClient | None = None,
) -> BotoInfrastructureAws:
    aws = object.__new__(BotoInfrastructureAws)
    aws._cloudformation = cloudformation or FakeBotoClient(responses={})
    aws._iam = iam or FakeBotoClient(responses={})
    aws._lambda = lambda_client or FakeBotoClient(responses={})
    aws._s3 = s3 or FakeBotoClient(responses={})
    aws._sqs = sqs or FakeBotoClient(responses={})
    aws._ssm = ssm or FakeBotoClient(responses={})
    aws._sts = sts or FakeBotoClient(responses={})
    return aws


def client_error(*, code: str, message: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": message}},
        "Example",
    )


def migration_record() -> AppliedMigrationRecord:
    return AppliedMigrationRecord(
        version=1,
        name="split_collector_ingestion",
        checksum="a" * 64,
        applied_at=datetime(2026, 7, 26, 12, tzinfo=UTC),
        commit_sha="b" * 40,
        account_id=ACCOUNT_ID,
        region="eu-west-2",
        stack_name="fpl-relay-app",
    )


def test_boto_stack_identity_output_resource_and_queue_operations() -> None:
    cloudformation = FakeBotoClient(
        responses={
            "describe_stacks": [
                {
                    "Stacks": [
                        {
                            "Outputs": [
                                {"OutputKey": "Queue", "OutputValue": "url"},
                            ],
                        },
                    ],
                },
                client_error(
                    code="ValidationError",
                    message="Stack with id missing does not exist",
                ),
            ],
            "describe_stack_resource": [
                {"StackResourceDetail": {"PhysicalResourceId": "physical"}},
            ],
        },
    )
    sqs = FakeBotoClient(
        responses={
            "get_queue_attributes": [{"Attributes": {"QueueArn": QUEUE_ARN}}],
        },
    )
    sts = FakeBotoClient(
        responses={"get_caller_identity": [{"Account": ACCOUNT_ID}]},
    )
    aws = boto_facade(cloudformation=cloudformation, sqs=sqs, sts=sts)

    aws.verify_identity(expected_account_id=ACCOUNT_ID)
    assert aws.stack_outputs(stack_name="stack") == {"Queue": "url"}
    assert not aws.stack_exists(stack_name="missing")
    assert (
        aws.stack_resource_id(
            stack_name="stack",
            logical_resource_id="Logical",
        )
        == "physical"
    )
    assert aws.queue_arn(queue_url="url") == QUEUE_ARN
    with pytest.raises(RuntimeError, match="account mismatch"):
        boto_facade(
            sts=FakeBotoClient(
                responses={"get_caller_identity": [{"Account": "000000000000"}]},
            ),
        ).verify_identity(expected_account_id=ACCOUNT_ID)


def test_boto_disables_and_verifies_lambda_mappings() -> None:
    lambda_client = FakeBotoClient(
        responses={
            "list_event_source_mappings": [
                {
                    "EventSourceMappings": [
                        {
                            "UUID": "old",
                            "State": "Enabled",
                            "FunctionArn": (
                                f"arn:aws:lambda:eu-west-2:{ACCOUNT_ID}:"
                                "function:legacy"
                            ),
                        },
                    ],
                },
                {
                    "EventSourceMappings": [
                        {
                            "UUID": "old",
                            "State": "Disabled",
                            "FunctionArn": (
                                f"arn:aws:lambda:eu-west-2:{ACCOUNT_ID}:"
                                "function:legacy"
                            ),
                        },
                    ],
                },
                {
                    "EventSourceMappings": [
                        {
                            "UUID": "result",
                            "State": "Enabled",
                            "FunctionArn": (
                                f"arn:aws:lambda:eu-west-2:{ACCOUNT_ID}:"
                                "function:ingestion"
                            ),
                        },
                    ],
                },
            ],
            "update_event_source_mapping": [{}],
            "get_event_source_mapping": [{"State": "Disabled"}],
        },
    )
    aws = boto_facade(lambda_client=lambda_client)
    aws.disable_event_source_mappings(source_arn=QUEUE_ARN)
    aws.assert_no_active_event_source_mappings(source_arn=QUEUE_ARN)
    aws.assert_single_enabled_event_source_mapping(
        source_arn=f"{QUEUE_ARN}-result",
        function_name="ingestion",
    )
    assert (
        "update_event_source_mapping",
        {"UUID": "old", "Enabled": False},
    ) in lambda_client.calls


def test_boto_rejects_active_or_incorrect_result_mappings() -> None:
    active = FakeBotoClient(
        responses={
            "list_event_source_mappings": [
                {
                    "EventSourceMappings": [
                        {
                            "UUID": "old",
                            "State": "Enabled",
                            "FunctionArn": "arn:function:legacy",
                        },
                    ],
                },
            ],
        },
    )
    with pytest.raises(RuntimeError, match="active Lambda mappings"):
        boto_facade(
            lambda_client=active,
        ).assert_no_active_event_source_mappings(source_arn=QUEUE_ARN)

    missing = FakeBotoClient(
        responses={"list_event_source_mappings": [{"EventSourceMappings": []}]},
    )
    with pytest.raises(RuntimeError, match="exactly one enabled"):
        boto_facade(
            lambda_client=missing,
        ).assert_single_enabled_event_source_mapping(
            source_arn=QUEUE_ARN,
            function_name="ingestion",
        )


def test_boto_verifies_payload_bucket() -> None:
    valid_s3 = FakeBotoClient(
        responses={
            "get_bucket_lifecycle_configuration": [
                {
                    "Rules": [
                        {
                            "Status": "Enabled",
                            "Prefix": "payloads/v1/",
                            "Expiration": {"Days": 7},
                        },
                    ],
                },
            ],
            "get_public_access_block": [
                {
                    "PublicAccessBlockConfiguration": {
                        "BlockPublicAcls": True,
                        "BlockPublicPolicy": True,
                        "IgnorePublicAcls": True,
                        "RestrictPublicBuckets": True,
                    },
                },
            ],
            "get_bucket_encryption": [
                {
                    "ServerSideEncryptionConfiguration": {
                        "Rules": [
                            {
                                "ApplyServerSideEncryptionByDefault": {
                                    "SSEAlgorithm": "AES256",
                                },
                            },
                        ],
                    },
                },
            ],
            "get_bucket_ownership_controls": [
                {
                    "OwnershipControls": {
                        "Rules": [
                            {"ObjectOwnership": "BucketOwnerEnforced"},
                        ],
                    },
                },
            ],
        },
    )
    boto_facade(s3=valid_s3).assert_payload_bucket(
        bucket_name="payloads",
        payload_prefix="payloads/v1",
    )

    invalid_s3 = FakeBotoClient(
        responses={
            "get_bucket_lifecycle_configuration": [{"Rules": []}],
        },
    )
    with pytest.raises(RuntimeError, match="seven-day"):
        boto_facade(s3=invalid_s3).assert_payload_bucket(
            bucket_name="payloads",
            payload_prefix="payloads/v1",
        )


def iam_responses() -> dict[str, list[dict[str, object] | Exception]]:
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"AWS": USER_ARN},
                "Action": "sts:AssumeRole",
            },
        ],
    }
    source_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "sts:AssumeRole",
                "Resource": ROLE_ARN,
            },
        ],
    }
    return {
        "get_user": [
            {"User": {"Arn": USER_ARN}},
            {"User": {"Arn": USER_ARN}},
        ],
        "get_role": [
            {"Role": {"Arn": ROLE_ARN, "AssumeRolePolicyDocument": trust}},
            {"Role": {"Arn": ROLE_ARN, "AssumeRolePolicyDocument": trust}},
        ],
        "put_user_policy": [{}],
        "get_user_policy": [{"PolicyDocument": source_policy}],
        "list_user_policies": [
            {"PolicyNames": ["AssumeFplCollectorRole"]},
        ],
        "list_attached_user_policies": [{"AttachedPolicies": []}],
        "list_groups_for_user": [{"Groups": []}],
    }


def test_boto_reconciles_and_verifies_exact_collector_policy() -> None:
    iam = FakeBotoClient(responses=iam_responses())
    aws = boto_facade(iam=iam)
    aws.reconcile_collector_source_policy(
        source_user_name="fpl-relay-nas-source",
        source_user_arn=USER_ARN,
        collector_role_arn=ROLE_ARN,
    )
    aws.assert_collector_source_policy(
        source_user_name="fpl-relay-nas-source",
        source_user_arn=USER_ARN,
        collector_role_arn=ROLE_ARN,
    )
    operation, parameters = next(
        call for call in iam.calls if call[0] == "put_user_policy"
    )
    assert operation == "put_user_policy"
    assert parameters["PolicyName"] == "AssumeFplCollectorRole"
    assert '"Resource":"' + ROLE_ARN + '"' in cast(
        "str",
        parameters["PolicyDocument"],
    )


def test_boto_reads_and_atomically_writes_ssm_records() -> None:
    record = migration_record()
    parameter_prefix = "/fpl-data-relay/production/infrastructure-migrations"
    ssm = FakeBotoClient(
        responses={
            "get_parameters_by_path": [
                {
                    "Parameters": [
                        {
                            "Name": (
                                f"{parameter_prefix}/"
                                "0001_split_collector_ingestion"
                            ),
                            "Value": record.model_dump_json(),
                        },
                    ],
                },
            ],
            "put_parameter": [{}],
        },
    )
    aws = boto_facade(ssm=ssm)
    assert aws.read_migration_records(parameter_prefix=parameter_prefix) == [record]
    aws.write_migration_record(
        parameter_prefix=parameter_prefix,
        record=record,
    )
    operation, parameters = ssm.calls[-1]
    assert operation == "put_parameter"
    assert parameters["Overwrite"] is False
    assert parameters["Type"] == "String"


def test_boto_change_set_guard_blocks_protected_changes_only() -> None:
    destructive_data = FakeBotoClient(
        responses={
            "describe_change_set": [
                {
                    "Changes": [
                        {
                            "ResourceChange": {
                                "LogicalResourceId": "DatabaseCluster",
                                "Action": "Modify",
                                "Replacement": "Conditional",
                            },
                        },
                    ],
                },
            ],
        },
    )
    with pytest.raises(RuntimeError, match="protected destructive"):
        boto_facade(
            cloudformation=destructive_data,
        ).validate_change_set(
            change_set_arn="change-set",
            policy=ChangeSetPolicy.DATA,
        )

    safe_application = FakeBotoClient(
        responses={
            "describe_change_set": [
                {
                    "Changes": [
                        {
                            "ResourceChange": {
                                "LogicalResourceId": "CollectedPayloadQueue",
                                "Action": "Remove",
                                "Replacement": "False",
                            },
                        },
                        {
                            "ResourceChange": {
                                "LogicalResourceId": "IngestionQueue",
                                "Action": "Modify",
                                "Replacement": "False",
                            },
                        },
                    ],
                },
            ],
        },
    )
    boto_facade(cloudformation=safe_application).validate_change_set(
        change_set_arn="change-set",
        policy=ChangeSetPolicy.APPLICATION,
    )

    destructive_application = FakeBotoClient(
        responses={
            "describe_change_set": [
                {
                    "Changes": [
                        {
                            "ResourceChange": {
                                "LogicalResourceId": "IngestionQueue",
                                "Action": "Remove",
                                "Replacement": "False",
                            },
                        },
                    ],
                    "NextToken": "page-2",
                },
                {"Changes": []},
            ],
        },
    )
    with pytest.raises(RuntimeError, match="IngestionQueue"):
        boto_facade(
            cloudformation=destructive_application,
        ).validate_change_set(
            change_set_arn="change-set",
            policy=ChangeSetPolicy.APPLICATION,
        )


def test_boto_constructor_creates_all_regional_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[str] = []

    class FakeSession:
        def __init__(self, *, region_name: str) -> None:
            assert region_name == "eu-west-2"

        def client(self, service_name: str) -> FakeBotoClient:
            created.append(service_name)
            return FakeBotoClient(responses={})

    monkeypatch.setattr(aws_module.boto3, "Session", FakeSession)
    BotoInfrastructureAws(region="eu-west-2")
    assert created == [
        "cloudformation",
        "iam",
        "lambda",
        "s3",
        "sqs",
        "ssm",
        "sts",
    ]


def test_boto_validates_stack_and_aws_response_shapes() -> None:
    assert boto_facade(
        cloudformation=FakeBotoClient(
            responses={"describe_stacks": [{"Stacks": [{}]}]},
        ),
    ).stack_exists(stack_name="stack")
    unexpected_error = client_error(code="AccessDenied", message="denied")
    with pytest.raises(ClientError):
        boto_facade(
            cloudformation=FakeBotoClient(
                responses={"describe_stacks": [unexpected_error]},
            ),
        ).stack_exists(stack_name="stack")
    with pytest.raises(RuntimeError, match="duplicated"):
        boto_facade(
            cloudformation=FakeBotoClient(
                responses={
                    "describe_stacks": [
                        {
                            "Stacks": [
                                {
                                    "Outputs": [
                                        {"OutputKey": "A", "OutputValue": "one"},
                                        {"OutputKey": "A", "OutputValue": "two"},
                                    ],
                                },
                            ],
                        },
                    ],
                },
            ),
        ).stack_outputs(stack_name="stack")
    with pytest.raises(RuntimeError, match="QueueArn"):
        boto_facade(
            sqs=FakeBotoClient(
                responses={"get_queue_attributes": [{"Attributes": {}}]},
            ),
        ).queue_arn(queue_url="url")


def test_boto_mapping_wait_rejects_failure_state() -> None:
    lambda_client = FakeBotoClient(
        responses={
            "list_event_source_mappings": [
                {
                    "EventSourceMappings": [
                        {
                            "UUID": "old",
                            "State": "Disabling",
                            "FunctionArn": "arn:function:legacy",
                        },
                    ],
                },
            ],
            "get_event_source_mapping": [{"State": "UpdateFailed"}],
        },
    )
    with pytest.raises(RuntimeError, match="failure state"):
        boto_facade(
            lambda_client=lambda_client,
        ).disable_event_source_mappings(source_arn=QUEUE_ARN)


def test_boto_rejects_incomplete_public_block_and_iam_policy() -> None:
    invalid_s3 = FakeBotoClient(
        responses={
            "get_bucket_lifecycle_configuration": [
                {
                    "Rules": [
                        {
                            "Status": "Enabled",
                            "Prefix": "payloads/v1/",
                            "Expiration": {"Days": 7},
                        },
                    ],
                },
            ],
            "get_public_access_block": [
                {
                    "PublicAccessBlockConfiguration": {
                        "BlockPublicAcls": True,
                        "BlockPublicPolicy": False,
                        "IgnorePublicAcls": True,
                        "RestrictPublicBuckets": True,
                    },
                },
            ],
            "get_bucket_encryption": [],
            "get_bucket_ownership_controls": [],
        },
    )
    with pytest.raises(RuntimeError, match="public access"):
        boto_facade(s3=invalid_s3).assert_payload_bucket(
            bucket_name="payloads",
            payload_prefix="payloads/v1",
        )

    responses = iam_responses()
    responses["get_user_policy"] = [{"PolicyDocument": {"Version": "wrong"}}]
    iam = FakeBotoClient(responses=responses)
    aws = boto_facade(iam=iam)
    aws.reconcile_collector_source_policy(
        source_user_name="fpl-relay-nas-source",
        source_user_arn=USER_ARN,
        collector_role_arn=ROLE_ARN,
    )
    with pytest.raises(RuntimeError, match="unexpected source policy"):
        aws.assert_collector_source_policy(
            source_user_name="fpl-relay-nas-source",
            source_user_arn=USER_ARN,
            collector_role_arn=ROLE_ARN,
        )


def test_boto_rejects_invalid_ssm_records_and_parameter_names() -> None:
    prefix = "/fpl-data-relay/production/infrastructure-migrations"
    invalid_json = FakeBotoClient(
        responses={
            "get_parameters_by_path": [
                {
                    "Parameters": [
                        {
                            "Name": f"{prefix}/0001_split_collector_ingestion",
                            "Value": "{}",
                        },
                    ],
                },
            ],
        },
    )
    with pytest.raises(RuntimeError, match="is invalid"):
        boto_facade(ssm=invalid_json).read_migration_records(
            parameter_prefix=prefix,
        )
    wrong_name = FakeBotoClient(
        responses={
            "get_parameters_by_path": [
                {
                    "Parameters": [
                        {
                            "Name": f"{prefix}/wrong",
                            "Value": migration_record().model_dump_json(),
                        },
                    ],
                },
            ],
        },
    )
    with pytest.raises(RuntimeError, match="name mismatch"):
        boto_facade(ssm=wrong_name).read_migration_records(
            parameter_prefix=prefix,
        )


def test_boto_call_rejects_non_callable_and_non_object_operations() -> None:
    class NonCallableClient:
        value = 1

    with pytest.raises(TypeError, match="not callable"):
        BotoInfrastructureAws._call(
            cast("aws_module.AwsClient", NonCallableClient()),
            "value",
        )
    client = FakeBotoClient(responses={"operation": []})
    client.responses["operation"] = [cast("dict[str, object]", "wrong")]
    with pytest.raises(TypeError, match="non-object"):
        BotoInfrastructureAws._call(client, "operation")
