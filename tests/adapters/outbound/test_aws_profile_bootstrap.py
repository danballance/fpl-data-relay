import json

import pytest

from fpl_data_relay.adapters.outbound.aws_profile import (
    SIGN_IN_POLICY_ARN,
    AwsCliIoMode,
    AwsCliResult,
)
from fpl_data_relay.adapters.outbound.aws_profile_bootstrap import (
    RELAY_POLICY_NAME,
    RELAY_POLICY_PATH,
    AwsIamProfileBootstrapAdministration,
    IamPolicyDocument,
)
from fpl_data_relay.application.errors import AwsProfileError
from fpl_data_relay.application.ports.administration import (
    AwsIamPrincipalType,
    AwsManagedPolicyState,
)
from fpl_data_relay.config import AdminSettings
from tests.adapters.inbound.test_admin_cli import admin_settings


def queue_url(*, settings: AdminSettings, name: str) -> str:
    return (
        f"https://sqs.{settings.aws_region}.amazonaws.com/"
        f"{settings.aws_account_id}/{name}"
    )


def data_outputs(*, settings: AdminSettings) -> dict[str, str]:
    return {
        "DatabaseClusterArn": (
            f"arn:aws:rds:{settings.aws_region}:{settings.aws_account_id}:"
            "cluster:relay-cluster"
        ),
        "DatabaseSecretArn": (
            f"arn:aws:secretsmanager:{settings.aws_region}:"
            f"{settings.aws_account_id}:secret:relay-secret-AbCdEf"
        ),
    }


def app_outputs(*, settings: AdminSettings) -> dict[str, str]:
    return {
        "FetchQueueUrl": queue_url(settings=settings, name="app-fetch"),
        "FetchDeadLetterQueueUrl": queue_url(
            settings=settings,
            name="app-fetch-dlq",
        ),
        "ResultQueueUrl": queue_url(settings=settings, name="app-result"),
        "ResultDeadLetterQueueUrl": queue_url(
            settings=settings,
            name="app-result-dlq",
        ),
        "ScheduleDeadLetterQueueUrl": queue_url(
            settings=settings,
            name="app-schedule-dlq",
        ),
        "CommunityQueueUrl": queue_url(settings=settings, name="app-community"),
        "CommunityDeadLetterQueueUrl": queue_url(
            settings=settings,
            name="app-community-dlq",
        ),
    }


def command_value(*, arguments: list[str], option: str) -> str:
    return arguments[arguments.index(option) + 1]


class FakeBootstrapAwsCliRunner:
    def __init__(self, *, settings: AdminSettings) -> None:
        self.settings = settings
        self.version = "aws-cli/2.34.24 Python/3.13 Linux/6 source/x86_64"
        self.account_id = settings.aws_account_id
        self.arn = f"arn:aws:iam::{settings.aws_account_id}:user/bootstrap"
        self.data = data_outputs(settings=settings)
        self.app = app_outputs(settings=settings)
        self.principals: dict[AwsIamPrincipalType, set[str]] = {
            AwsIamPrincipalType.USER: {"relay-user"},
            AwsIamPrincipalType.GROUP: {"relay-group"},
            AwsIamPrincipalType.ROLE: {"relay-role"},
        }
        self.policy_versions: dict[str, dict[str, object]] = {}
        self.default_version: str | None = None
        self.attachments: list[tuple[str, str, str]] = []
        self.commands: list[tuple[list[str], AwsCliIoMode]] = []
        self.failures: dict[tuple[str, str], AwsCliResult] = {}

    def run(
        self,
        *,
        command: list[str],
        io_mode: AwsCliIoMode,
    ) -> AwsCliResult:
        self.commands.append((command, io_mode))
        assert io_mode is AwsCliIoMode.CAPTURE
        arguments = command[1:]
        if arguments == ["--version"]:
            return success(stdout=self.version)
        operation = (arguments[0], arguments[1])
        if operation in self.failures:
            return self.failures[operation]
        if operation == ("sts", "get-caller-identity"):
            return json_success(
                {"Account": self.account_id, "Arn": self.arn},
            )
        if operation == ("cloudformation", "describe-stacks"):
            stack_name = command_value(arguments=arguments, option="--stack-name")
            values = (
                self.data
                if stack_name == self.settings.data_stack_name
                else self.app
            )
            return json_success(
                {
                    "Stacks": [
                        {
                            "Outputs": [
                                {"OutputKey": key, "OutputValue": value}
                                for key, value in values.items()
                            ],
                        },
                    ],
                },
            )
        if operation in {
            ("iam", "get-user"),
            ("iam", "get-group"),
            ("iam", "get-role"),
        }:
            return self._get_principal(arguments=arguments, operation=operation[1])
        if operation == ("iam", "get-policy"):
            if self.default_version is None:
                return failure(stderr="An error occurred (NoSuchEntity)")
            return json_success(
                {
                    "Policy": {
                        "PolicyName": RELAY_POLICY_NAME,
                        "Arn": self.policy_arn,
                        "Path": RELAY_POLICY_PATH,
                        "DefaultVersionId": self.default_version,
                    },
                },
            )
        if operation == ("iam", "create-policy"):
            document = self._policy_argument(arguments=arguments)
            self.policy_versions = {"v1": document}
            self.default_version = "v1"
            return json_success({"Policy": {"Arn": self.policy_arn}})
        if operation == ("iam", "get-policy-version"):
            version_id = command_value(arguments=arguments, option="--version-id")
            return json_success(
                {
                    "PolicyVersion": {
                        "Document": self.policy_versions[version_id],
                    },
                },
            )
        if operation == ("iam", "list-policy-versions"):
            return json_success(
                {
                    "Versions": [
                        {
                            "VersionId": version_id,
                            "IsDefaultVersion": version_id == self.default_version,
                        }
                        for version_id in sorted(
                            self.policy_versions,
                            key=lambda value: int(value[1:]),
                        )
                    ],
                    "IsTruncated": False,
                },
            )
        if operation == ("iam", "delete-policy-version"):
            version_id = command_value(arguments=arguments, option="--version-id")
            del self.policy_versions[version_id]
            return success()
        if operation == ("iam", "create-policy-version"):
            document = self._policy_argument(arguments=arguments)
            next_number = max(
                [int(value[1:]) for value in self.policy_versions] + [0],
            ) + 1
            version_id = f"v{next_number}"
            self.policy_versions[version_id] = document
            self.default_version = version_id
            return json_success({"PolicyVersion": {"VersionId": version_id}})
        if operation in {
            ("iam", "attach-user-policy"),
            ("iam", "attach-group-policy"),
            ("iam", "attach-role-policy"),
        }:
            name_option = {
                "attach-user-policy": "--user-name",
                "attach-group-policy": "--group-name",
                "attach-role-policy": "--role-name",
            }[operation[1]]
            self.attachments.append(
                (
                    operation[1],
                    command_value(arguments=arguments, option=name_option),
                    command_value(arguments=arguments, option="--policy-arn"),
                ),
            )
            return success()
        raise AssertionError(f"Unexpected command: {command}")

    @property
    def policy_arn(self) -> str:
        return (
            f"arn:aws:iam::{self.settings.aws_account_id}:policy"
            f"{RELAY_POLICY_PATH}{RELAY_POLICY_NAME}"
        )

    def _get_principal(
        self,
        *,
        arguments: list[str],
        operation: str,
    ) -> AwsCliResult:
        principal_type, option = {
            "get-user": (AwsIamPrincipalType.USER, "--user-name"),
            "get-group": (AwsIamPrincipalType.GROUP, "--group-name"),
            "get-role": (AwsIamPrincipalType.ROLE, "--role-name"),
        }[operation]
        name = command_value(arguments=arguments, option=option)
        if name not in self.principals[principal_type]:
            return failure(stderr="An error occurred (NoSuchEntity)")
        return json_success({principal_type.value.title(): {"Name": name}})

    @staticmethod
    def _policy_argument(*, arguments: list[str]) -> dict[str, object]:
        raw = command_value(arguments=arguments, option="--policy-document")
        payload = json.loads(raw)
        assert isinstance(payload, dict)
        return payload


def success(*, stdout: str = "") -> AwsCliResult:
    return AwsCliResult(returncode=0, stdout=stdout, stderr="")


def json_success(payload: object) -> AwsCliResult:
    return success(stdout=json.dumps(payload))


def failure(*, stderr: str) -> AwsCliResult:
    return AwsCliResult(returncode=255, stdout="", stderr=stderr)


def bootstrap_adapter(
    *,
    settings: AdminSettings,
    runner: FakeBootstrapAwsCliRunner,
) -> AwsIamProfileBootstrapAdministration:
    return AwsIamProfileBootstrapAdministration(settings=settings, runner=runner)


def test_bootstrap_creates_scoped_policy_and_attaches_both_policies() -> None:
    settings = admin_settings()
    runner = FakeBootstrapAwsCliRunner(settings=settings)

    status = bootstrap_adapter(settings=settings, runner=runner).bootstrap(
        bootstrap_profile="existing-admin",
        principal_type=AwsIamPrincipalType.USER,
        principal_name="relay-user",
    )

    assert status.relay_policy_state is AwsManagedPolicyState.CREATED
    assert status.bootstrap_arn == runner.arn
    assert runner.attachments == [
        ("attach-user-policy", "relay-user", SIGN_IN_POLICY_ARN),
        ("attach-user-policy", "relay-user", runner.policy_arn),
    ]
    document = IamPolicyDocument.model_validate(runner.policy_versions["v1"])
    resources = {
        resource
        for statement in document.statements
        for resource in statement.resource
    }
    assert data_outputs(settings=settings)["DatabaseClusterArn"] in resources
    assert data_outputs(settings=settings)["DatabaseSecretArn"] in resources
    assert (
        f"arn:aws:sqs:{settings.aws_region}:{settings.aws_account_id}:app-fetch"
        in resources
    )
    statements = {statement.sid: statement for statement in document.statements}
    assert statements["SendRelayJobs"].resource == sorted(
        [
            (
                f"arn:aws:sqs:{settings.aws_region}:"
                f"{settings.aws_account_id}:app-fetch"
            ),
            (
                f"arn:aws:sqs:{settings.aws_region}:"
                f"{settings.aws_account_id}:app-community"
            ),
        ],
    )
    assert all(
        resource.endswith("-dlq")
        for resource in statements["PeekRelayDeadLetters"].resource
    )
    scheduler_prefix = (
        f"arn:aws:scheduler:{settings.aws_region}:"
        f"{settings.aws_account_id}:schedule"
    )
    assert statements["OperateRelaySchedules"].resource == sorted(
        [
            f"{scheduler_prefix}/app-community/app-community-daily",
            f"{scheduler_prefix}/app-live/fpl-live-*",
            (
                f"{scheduler_prefix}/app-reference/"
                "app-reference-quarter-hour"
            ),
        ],
    )
    assert all("REPLACE_ACCOUNT_ID" not in resource for resource in resources)
    assert not any("configure" in command for command, _mode in runner.commands)


@pytest.mark.parametrize(
    ("principal_type", "principal_name", "attachment_operation"),
    [
        (AwsIamPrincipalType.GROUP, "relay-group", "attach-group-policy"),
        (AwsIamPrincipalType.ROLE, "relay-role", "attach-role-policy"),
    ],
)
def test_bootstrap_supports_group_and_role_targets(
    principal_type: AwsIamPrincipalType,
    principal_name: str,
    attachment_operation: str,
) -> None:
    settings = admin_settings()
    runner = FakeBootstrapAwsCliRunner(settings=settings)

    bootstrap_adapter(settings=settings, runner=runner).bootstrap(
        bootstrap_profile="existing-admin",
        principal_type=principal_type,
        principal_name=principal_name,
    )

    assert [attachment[0] for attachment in runner.attachments] == [
        attachment_operation,
        attachment_operation,
    ]


def test_bootstrap_is_idempotent_when_generated_policy_is_unchanged() -> None:
    settings = admin_settings()
    runner = FakeBootstrapAwsCliRunner(settings=settings)
    profile = bootstrap_adapter(settings=settings, runner=runner)

    first = profile.bootstrap(
        bootstrap_profile="existing-admin",
        principal_type=AwsIamPrincipalType.USER,
        principal_name="relay-user",
    )
    second = profile.bootstrap(
        bootstrap_profile="existing-admin",
        principal_type=AwsIamPrincipalType.USER,
        principal_name="relay-user",
    )

    assert first.relay_policy_state is AwsManagedPolicyState.CREATED
    assert second.relay_policy_state is AwsManagedPolicyState.UNCHANGED
    assert set(runner.policy_versions) == {"v1"}


def test_bootstrap_updates_policy_before_reaching_version_limit() -> None:
    settings = admin_settings()
    runner = FakeBootstrapAwsCliRunner(settings=settings)
    profile = bootstrap_adapter(settings=settings, runner=runner)
    profile.bootstrap(
        bootstrap_profile="existing-admin",
        principal_type=AwsIamPrincipalType.USER,
        principal_name="relay-user",
    )
    runner.app["CommunityQueueUrl"] = queue_url(
        settings=settings,
        name="community-replacement",
    )

    status = profile.bootstrap(
        bootstrap_profile="existing-admin",
        principal_type=AwsIamPrincipalType.USER,
        principal_name="relay-user",
    )

    assert status.relay_policy_state is AwsManagedPolicyState.UPDATED
    assert set(runner.policy_versions) == {"v1", "v2"}


def test_bootstrap_updates_policy_and_removes_oldest_version_at_limit() -> None:
    settings = admin_settings()
    runner = FakeBootstrapAwsCliRunner(settings=settings)
    profile = bootstrap_adapter(settings=settings, runner=runner)
    profile.bootstrap(
        bootstrap_profile="existing-admin",
        principal_type=AwsIamPrincipalType.USER,
        principal_name="relay-user",
    )
    original = runner.policy_versions["v1"]
    runner.policy_versions = {
        f"v{number}": original for number in range(1, 6)
    }
    runner.default_version = "v5"
    runner.app["FetchQueueUrl"] = queue_url(
        settings=settings,
        name="app-fetch-replacement",
    )

    status = profile.bootstrap(
        bootstrap_profile="existing-admin",
        principal_type=AwsIamPrincipalType.USER,
        principal_name="relay-user",
    )

    assert status.relay_policy_state is AwsManagedPolicyState.UPDATED
    assert runner.default_version == "v6"
    assert set(runner.policy_versions) == {"v2", "v3", "v4", "v5", "v6"}


def test_bootstrap_failure_preserves_error_and_printable_external_steps() -> None:
    settings = admin_settings()
    runner = FakeBootstrapAwsCliRunner(settings=settings)
    runner.failures[("sts", "get-caller-identity")] = failure(
        stderr="The config profile (missing-admin) could not be found",
    )
    profile = bootstrap_adapter(settings=settings, runner=runner)

    with pytest.raises(AwsProfileError) as captured:
        profile.bootstrap(
            bootstrap_profile="missing-admin",
            principal_type=AwsIamPrincipalType.USER,
            principal_name="relay-user",
        )

    message = str(captured.value)
    assert "could not be found" in message
    assert "cannot grant its own initial AWS authority" in message
    assert "aws sts get-caller-identity --profile PROFILE_NAME" in message
    assert ".admin.env" in message


def test_bootstrap_rejects_old_cli_wrong_account_and_unsafe_names() -> None:
    settings = admin_settings()
    runner = FakeBootstrapAwsCliRunner(settings=settings)
    runner.version = "aws-cli/2.31.0 Python/3.13 Linux/6 source/x86_64"
    profile = bootstrap_adapter(settings=settings, runner=runner)
    with pytest.raises(AwsProfileError, match=r"2\.32\.0"):
        profile.bootstrap(
            bootstrap_profile="existing-admin",
            principal_type=AwsIamPrincipalType.USER,
            principal_name="relay-user",
        )

    runner.version = "aws-cli/2.34.24 Python/3.13 Linux/6 source/x86_64"
    runner.account_id = "999999999999"
    with pytest.raises(AwsProfileError, match="account mismatch"):
        profile.bootstrap(
            bootstrap_profile="existing-admin",
            principal_type=AwsIamPrincipalType.USER,
            principal_name="relay-user",
        )
    with pytest.raises(AwsProfileError, match="AWS-safe"):
        profile.bootstrap(
            bootstrap_profile="invalid profile",
            principal_type=AwsIamPrincipalType.USER,
            principal_name="relay-user",
        )

    runner.version = "not-an-aws-version"
    with pytest.raises(AwsProfileError, match="unrecognised version"):
        profile.bootstrap(
            bootstrap_profile="existing-admin",
            principal_type=AwsIamPrincipalType.USER,
            principal_name="relay-user",
        )


def test_bootstrap_rejects_invalid_stack_output_and_unowned_policy() -> None:
    settings = admin_settings()
    runner = FakeBootstrapAwsCliRunner(settings=settings)
    profile = bootstrap_adapter(settings=settings, runner=runner)
    runner.app["FetchQueueUrl"] = "https://example.com/not-sqs"
    with pytest.raises(AwsProfileError, match="invalid SQS URL"):
        profile.bootstrap(
            bootstrap_profile="existing-admin",
            principal_type=AwsIamPrincipalType.USER,
            principal_name="relay-user",
        )

    runner.app = app_outputs(settings=settings)
    profile.bootstrap(
        bootstrap_profile="existing-admin",
        principal_type=AwsIamPrincipalType.USER,
        principal_name="relay-user",
    )
    runner.policy_versions["v1"] = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "UnrelatedPolicy",
                "Effect": "Allow",
                "Action": "s3:ListAllMyBuckets",
                "Resource": "*",
            },
        ],
    }
    runner.app["FetchQueueUrl"] = queue_url(settings=settings, name="changed")
    with pytest.raises(AwsProfileError, match="ownership marker"):
        profile.bootstrap(
            bootstrap_profile="existing-admin",
            principal_type=AwsIamPrincipalType.USER,
            principal_name="relay-user",
        )


def test_bootstrap_rejects_missing_stack_output_and_policy_lookup_denial() -> None:
    settings = admin_settings()
    runner = FakeBootstrapAwsCliRunner(settings=settings)
    profile = bootstrap_adapter(settings=settings, runner=runner)
    del runner.data["DatabaseSecretArn"]
    with pytest.raises(AwsProfileError, match="do not expose every output"):
        profile.bootstrap(
            bootstrap_profile="existing-admin",
            principal_type=AwsIamPrincipalType.USER,
            principal_name="relay-user",
        )

    runner.data = data_outputs(settings=settings)
    runner.failures[("iam", "get-policy")] = failure(
        stderr="AccessDenied: iam:GetPolicy",
    )
    with pytest.raises(AwsProfileError) as captured:
        profile.bootstrap(
            bootstrap_profile="existing-admin",
            principal_type=AwsIamPrincipalType.USER,
            principal_name="relay-user",
        )
    assert "inspect relay policy" in str(captured.value)
    assert "iam:GetPolicy" in str(captured.value)
    assert "cannot grant its own initial AWS authority" in str(captured.value)


def test_bootstrap_iam_denial_includes_required_external_permissions() -> None:
    settings = admin_settings()
    runner = FakeBootstrapAwsCliRunner(settings=settings)
    runner.failures[("iam", "get-user")] = failure(
        stderr="AccessDenied: iam:GetUser",
    )

    with pytest.raises(AwsProfileError) as captured:
        bootstrap_adapter(settings=settings, runner=runner).bootstrap(
            bootstrap_profile="existing-admin",
            principal_type=AwsIamPrincipalType.USER,
            principal_name="relay-user",
        )

    assert "iam:GetUser" in str(captured.value)
    assert "iam:AttachUserPolicy" in str(captured.value)
