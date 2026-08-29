"""IAM bootstrap for the dedicated local AWS administration profile."""

import json
from collections.abc import Mapping
from typing import Literal, cast
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from fpl_data_relay.adapters.outbound.aws_profile import (
    MINIMUM_AWS_CLI_VERSION,
    SIGN_IN_POLICY_ARN,
    VERSION_PATTERN,
    AwsCliIoMode,
    AwsCliResult,
    AwsCliRunner,
    CallerIdentityPayload,
    failure_detail,
)
from fpl_data_relay.application.errors import AwsProfileError
from fpl_data_relay.application.ports.administration import (
    AwsIamPrincipalType,
    AwsManagedPolicyState,
    AwsProfileBootstrapStatus,
)
from fpl_data_relay.config import AdminSettings

RELAY_POLICY_NAME = "FplRelayAdministrator"
RELAY_POLICY_PATH = "/fpl-data-relay/"
RELAY_POLICY_DESCRIPTION = (
    "Generated permissions for local FPL Data Relay production administration."
)
RELAY_POLICY_MARKER_SID = "VerifyRelayAdministratorIdentity"
IAM_NO_SUCH_ENTITY = "NoSuchEntity"
IAM_POLICY_VERSION_LIMIT = 5


class AwsProfileBootstrapRequest(BaseModel):
    """Validated operator input for one IAM bootstrap."""

    model_config = ConfigDict(frozen=True)

    bootstrap_profile: str = Field(pattern=r"^[A-Za-z0-9_.@+-]+$")
    principal_type: AwsIamPrincipalType
    principal_name: str = Field(pattern=r"^[A-Za-z0-9_+=,.@-]+$")


class AwsStackOutput(BaseModel):
    """One CloudFormation output used to scope the generated policy."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    key: str = Field(alias="OutputKey", min_length=1)
    value: str = Field(alias="OutputValue", min_length=1)


class AwsStackDescription(BaseModel):
    """Validated subset of one CloudFormation stack."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    outputs: list[AwsStackOutput] = Field(alias="Outputs")


class AwsDescribeStacksPayload(BaseModel):
    """Validated CloudFormation DescribeStacks response."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    stacks: list[AwsStackDescription] = Field(
        alias="Stacks",
        min_length=1,
        max_length=1,
    )


class RelayDataStackOutputs(BaseModel):
    """Data-stack outputs required by relay administration."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    database_resource_arn: str = Field(alias="DatabaseClusterArn", min_length=1)
    database_secret_arn: str = Field(alias="DatabaseSecretArn", min_length=1)


class RelayAppStackOutputs(BaseModel):
    """Application-stack queue outputs required by relay administration."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    fetch_queue_url: str = Field(alias="FetchQueueUrl", min_length=1)
    fetch_dead_letter_queue_url: str = Field(
        alias="FetchDeadLetterQueueUrl",
        min_length=1,
    )
    result_queue_url: str = Field(alias="ResultQueueUrl", min_length=1)
    result_dead_letter_queue_url: str = Field(
        alias="ResultDeadLetterQueueUrl",
        min_length=1,
    )
    schedule_dead_letter_queue_url: str = Field(
        alias="ScheduleDeadLetterQueueUrl",
        min_length=1,
    )
    community_queue_url: str = Field(alias="CommunityQueueUrl", min_length=1)
    community_dead_letter_queue_url: str = Field(
        alias="CommunityDeadLetterQueueUrl",
        min_length=1,
    )


class IamPolicyStatement(BaseModel):
    """Canonical IAM policy statement emitted and compared by the toolkit."""

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
    )

    sid: str = Field(alias="Sid", min_length=1)
    effect: Literal["Allow"] = Field(alias="Effect")
    action: list[str] = Field(alias="Action", min_length=1)
    resource: list[str] = Field(alias="Resource", min_length=1)

    @field_validator("action", "resource", mode="before")
    @classmethod
    def normalize_scalar_or_list(cls, value: object) -> object:
        """Normalize AWS's scalar shorthand before semantic comparison."""
        return [value] if isinstance(value, str) else value

    @field_validator("action", "resource", mode="after")
    @classmethod
    def sort_unique_values(cls, value: list[str]) -> list[str]:
        """Canonicalize order-insensitive IAM values."""
        if len(set(value)) != len(value):
            raise ValueError("IAM policy values must be unique.")
        return sorted(value)


class IamPolicyDocument(BaseModel):
    """Canonical relay administrator policy document."""

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
    )

    version: Literal["2012-10-17"] = Field(alias="Version")
    statements: list[IamPolicyStatement] = Field(alias="Statement", min_length=1)

    @field_validator("statements", mode="after")
    @classmethod
    def sort_unique_statements(
        cls,
        value: list[IamPolicyStatement],
    ) -> list[IamPolicyStatement]:
        """Give statements stable ordering and reject ambiguous duplicate Sids."""
        sids = [statement.sid for statement in value]
        if len(set(sids)) != len(sids):
            raise ValueError("IAM policy statement Sids must be unique.")
        return sorted(value, key=lambda statement: statement.sid)

    def compact_json(self) -> str:
        """Return stable JSON accepted directly by the AWS CLI."""
        return json.dumps(
            self.model_dump(by_alias=True),
            separators=(",", ":"),
            sort_keys=True,
        )


class IamManagedPolicySummary(BaseModel):
    """Validated metadata for the generated customer-managed policy."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    policy_name: str = Field(alias="PolicyName", min_length=1)
    policy_arn: str = Field(alias="Arn", min_length=1)
    path: str = Field(alias="Path", min_length=1)
    default_version_id: str = Field(
        alias="DefaultVersionId",
        pattern=r"^v[1-9][0-9]*$",
    )


class IamGetPolicyPayload(BaseModel):
    """Validated IAM GetPolicy response."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    policy: IamManagedPolicySummary = Field(alias="Policy")


class IamManagedPolicyVersion(BaseModel):
    """Current managed-policy version including its document."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    document: IamPolicyDocument = Field(alias="Document")


class IamGetPolicyVersionPayload(BaseModel):
    """Validated IAM GetPolicyVersion response."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    policy_version: IamManagedPolicyVersion = Field(alias="PolicyVersion")


class IamPolicyVersionSummary(BaseModel):
    """Deletable metadata for one customer-managed policy version."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    version_id: str = Field(alias="VersionId", pattern=r"^v[1-9][0-9]*$")
    is_default: bool = Field(alias="IsDefaultVersion")


class IamListPolicyVersionsPayload(BaseModel):
    """Validated non-paginated IAM ListPolicyVersions response."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    versions: list[IamPolicyVersionSummary] = Field(
        alias="Versions",
        min_length=1,
        max_length=IAM_POLICY_VERSION_LIMIT,
    )
    is_truncated: bool = Field(alias="IsTruncated")


def build_relay_administrator_policy(
    *,
    settings: AdminSettings,
    data: RelayDataStackOutputs,
    app: RelayAppStackOutputs,
) -> IamPolicyDocument:
    """Generate account-specific least-privilege relay administration access."""
    queue_urls = {
        "fetch": app.fetch_queue_url,
        "fetch_dlq": app.fetch_dead_letter_queue_url,
        "result": app.result_queue_url,
        "result_dlq": app.result_dead_letter_queue_url,
        "schedule_dlq": app.schedule_dead_letter_queue_url,
        "community": app.community_queue_url,
        "community_dlq": app.community_dead_letter_queue_url,
    }
    queue_arns = {
        name: queue_url_to_arn(
            queue_url=queue_url,
            region=settings.aws_region,
            account_id=settings.aws_account_id,
        )
        for name, queue_url in queue_urls.items()
    }
    scheduler_prefix = (
        f"arn:aws:scheduler:{settings.aws_region}:"
        f"{settings.aws_account_id}:schedule"
    )
    reference_schedule_group_name = f"{settings.app_stack_name}-reference"
    reference_schedule_name = (
        f"{settings.app_stack_name}-reference-quarter-hour"
    )
    community_schedule_group_name = f"{settings.app_stack_name}-community"
    community_schedule_name = f"{settings.app_stack_name}-community-daily"
    live_schedule_group_name = f"{settings.app_stack_name}-live"
    schedule_arns = [
        (
            f"{scheduler_prefix}/{reference_schedule_group_name}/"
            f"{reference_schedule_name}"
        ),
        (
            f"{scheduler_prefix}/{community_schedule_group_name}/"
            f"{community_schedule_name}"
        ),
        f"{scheduler_prefix}/{live_schedule_group_name}/fpl-live-*",
    ]
    stack_arns = [
        (
            f"arn:aws:cloudformation:{settings.aws_region}:"
            f"{settings.aws_account_id}:stack/{settings.data_stack_name}/*"
        ),
        (
            f"arn:aws:cloudformation:{settings.aws_region}:"
            f"{settings.aws_account_id}:stack/{settings.app_stack_name}/*"
        ),
    ]
    return IamPolicyDocument(
        Version="2012-10-17",
        Statement=[
            IamPolicyStatement(
                Sid=RELAY_POLICY_MARKER_SID,
                Effect="Allow",
                Action=["sts:GetCallerIdentity"],
                Resource=["*"],
            ),
            IamPolicyStatement(
                Sid="DiscoverRelayStacks",
                Effect="Allow",
                Action=["cloudformation:DescribeStacks"],
                Resource=stack_arns,
            ),
            IamPolicyStatement(
                Sid="InspectRelayQueues",
                Effect="Allow",
                Action=["sqs:GetQueueAttributes"],
                Resource=list(queue_arns.values()),
            ),
            IamPolicyStatement(
                Sid="SendRelayJobs",
                Effect="Allow",
                Action=["sqs:SendMessage"],
                Resource=[queue_arns["fetch"], queue_arns["community"]],
            ),
            IamPolicyStatement(
                Sid="PeekRelayDeadLetters",
                Effect="Allow",
                Action=["sqs:ReceiveMessage"],
                Resource=[
                    queue_arns["fetch_dlq"],
                    queue_arns["result_dlq"],
                    queue_arns["schedule_dlq"],
                    queue_arns["community_dlq"],
                ],
            ),
            IamPolicyStatement(
                Sid="ListRelaySchedules",
                Effect="Allow",
                Action=["scheduler:ListSchedules"],
                Resource=["*"],
            ),
            IamPolicyStatement(
                Sid="OperateRelaySchedules",
                Effect="Allow",
                Action=[
                    "scheduler:GetSchedule",
                    "scheduler:UpdateSchedule",
                ],
                Resource=schedule_arns,
            ),
            IamPolicyStatement(
                Sid="AdministerRelayDatabase",
                Effect="Allow",
                Action=[
                    "rds-data:BatchExecuteStatement",
                    "rds-data:BeginTransaction",
                    "rds-data:CommitTransaction",
                    "rds-data:ExecuteStatement",
                    "rds-data:RollbackTransaction",
                ],
                Resource=[data.database_resource_arn],
            ),
            IamPolicyStatement(
                Sid="ReadRelayDatabaseSecret",
                Effect="Allow",
                Action=["secretsmanager:GetSecretValue"],
                Resource=[data.database_secret_arn],
            ),
        ],
    )


def queue_url_to_arn(*, queue_url: str, region: str, account_id: str) -> str:
    """Convert and strictly validate one same-account standard SQS URL."""
    parsed = urlparse(queue_url)
    expected_host = f"sqs.{region}.amazonaws.com"
    path_parts = parsed.path.strip("/").split("/")
    if (
        parsed.scheme != "https"
        or parsed.hostname != expected_host
        or parsed.query
        or parsed.fragment
        or len(path_parts) != 2
        or path_parts[0] != account_id
        or not path_parts[1]
    ):
        raise AwsProfileError(
            f"CloudFormation returned invalid SQS URL {queue_url!r}; expected "
            f"https://{expected_host}/{account_id}/QUEUE_NAME.",
        )
    return f"arn:aws:sqs:{region}:{account_id}:{path_parts[1]}"


class AwsIamProfileBootstrapAdministration:
    """Create and attach generated access using an existing trusted profile."""

    def __init__(
        self,
        *,
        settings: AdminSettings,
        runner: AwsCliRunner,
    ) -> None:
        self._settings = settings
        self._runner = runner

    def instructions(self) -> str:
        """Explain the root-of-trust step no unauthenticated CLI can perform."""
        return (
            "The target profile cannot grant its own initial AWS authority.\n"
            "Before continuing, an AWS account administrator must provide a "
            "separately authenticated AWS CLI profile with access to account "
            f"{self._settings.aws_account_id}. That bootstrap identity needs:\n"
            "  - sts:GetCallerIdentity and cloudformation:DescribeStacks\n"
            "  - iam:GetUser, iam:GetGroup, and iam:GetRole as applicable\n"
            "  - iam:GetPolicy, iam:GetPolicyVersion, and "
            "iam:ListPolicyVersions\n"
            "  - iam:CreatePolicy, iam:CreatePolicyVersion, and "
            "iam:DeletePolicyVersion\n"
            "  - iam:AttachUserPolicy, iam:AttachGroupPolicy, or "
            "iam:AttachRolePolicy as applicable\n"
            "Configure or log in to that identity under a separate named "
            "profile, verify it with:\n"
            "  aws sts get-caller-identity --profile PROFILE_NAME\n"
            "Then rerun this command. No credentials belong in .admin.env. "
            "The bootstrap profile is used but never modified. The command "
            "will generate the relay policy and attach it together with "
            "AWS's SignInLocalDevelopmentAccess managed policy.\n"
        )

    def bootstrap(
        self,
        *,
        bootstrap_profile: str,
        principal_type: AwsIamPrincipalType,
        principal_name: str,
    ) -> AwsProfileBootstrapStatus:
        """Generate, version, and attach both required policies idempotently."""
        try:
            request = AwsProfileBootstrapRequest(
                bootstrap_profile=bootstrap_profile,
                principal_type=principal_type,
                principal_name=principal_name,
            )
        except ValidationError as error:
            raise AwsProfileError(
                "Bootstrap profile and IAM principal names must use AWS-safe "
                "characters without whitespace.",
            ) from error
        self._require_supported_cli()
        identity = self._bootstrap_identity(profile=request.bootstrap_profile)
        self._require_principal(request=request)
        data_values = self._stack_outputs(
            profile=request.bootstrap_profile,
            stack_name=self._settings.data_stack_name,
        )
        app_values = self._stack_outputs(
            profile=request.bootstrap_profile,
            stack_name=self._settings.app_stack_name,
        )
        try:
            data = RelayDataStackOutputs.model_validate(data_values)
            app = RelayAppStackOutputs.model_validate(app_values)
        except ValidationError as error:
            raise AwsProfileError(
                "Relay CloudFormation stacks do not expose every output "
                "required to generate the administrator policy.",
            ) from error
        policy = build_relay_administrator_policy(
            settings=self._settings,
            data=data,
            app=app,
        )
        relay_policy_arn, policy_state = self._ensure_relay_policy(
            profile=request.bootstrap_profile,
            document=policy,
        )
        self._attach_policy(
            request=request,
            policy_arn=SIGN_IN_POLICY_ARN,
        )
        self._attach_policy(
            request=request,
            policy_arn=relay_policy_arn,
        )
        return AwsProfileBootstrapStatus(
            bootstrap_profile=request.bootstrap_profile,
            bootstrap_arn=identity.arn,
            principal_type=request.principal_type,
            principal_name=request.principal_name,
            sign_in_policy_arn=SIGN_IN_POLICY_ARN,
            relay_policy_arn=relay_policy_arn,
            relay_policy_state=policy_state,
        )

    def _require_supported_cli(self) -> None:
        result = self._runner.run(
            command=["aws", "--version"],
            io_mode=AwsCliIoMode.CAPTURE,
        )
        self._require_success(
            result=result,
            operation="read the AWS CLI version",
            include_instructions=False,
        )
        match = VERSION_PATTERN.search(f"{result.stdout}\n{result.stderr}")
        if match is None:
            raise AwsProfileError("AWS CLI returned an unrecognised version string.")
        version = tuple(int(value) for value in match.groups())
        if version < MINIMUM_AWS_CLI_VERSION:
            minimum = ".".join(str(value) for value in MINIMUM_AWS_CLI_VERSION)
            actual = ".".join(str(value) for value in version)
            raise AwsProfileError(
                f"AWS CLI {minimum} or newer is required; found {actual}.",
            )

    def _bootstrap_identity(self, *, profile: str) -> CallerIdentityPayload:
        result = self._run_aws(
            profile,
            "sts",
            "get-caller-identity",
        )
        self._require_success(
            result=result,
            operation=f"authenticate bootstrap profile {profile!r}",
            include_instructions=True,
        )
        try:
            identity = CallerIdentityPayload.model_validate(
                self._json_object(result=result, operation="read caller identity"),
            )
        except ValidationError as error:
            raise AwsProfileError(
                "Bootstrap STS returned an invalid caller identity.",
            ) from error
        if identity.account_id != self._settings.aws_account_id:
            raise AwsProfileError(
                "Bootstrap AWS account mismatch: "
                f"expected {self._settings.aws_account_id}, "
                f"found {identity.account_id}.",
            )
        return identity

    def _require_principal(self, *, request: AwsProfileBootstrapRequest) -> None:
        if request.principal_type is AwsIamPrincipalType.USER:
            arguments = ("iam", "get-user", "--user-name", request.principal_name)
        elif request.principal_type is AwsIamPrincipalType.GROUP:
            arguments = (
                "iam",
                "get-group",
                "--group-name",
                request.principal_name,
                "--max-items",
                "1",
            )
        else:
            arguments = ("iam", "get-role", "--role-name", request.principal_name)
        self._require_success(
            result=self._run_aws(request.bootstrap_profile, *arguments),
            operation=(
                f"find IAM {request.principal_type.value} "
                f"{request.principal_name!r}"
            ),
            include_instructions=True,
        )

    def _stack_outputs(self, *, profile: str, stack_name: str) -> dict[str, str]:
        result = self._run_aws(
            profile,
            "cloudformation",
            "describe-stacks",
            "--stack-name",
            stack_name,
        )
        self._require_success(
            result=result,
            operation=f"describe CloudFormation stack {stack_name!r}",
            include_instructions=True,
        )
        try:
            payload = AwsDescribeStacksPayload.model_validate(
                self._json_object(
                    result=result,
                    operation=f"read CloudFormation stack {stack_name!r}",
                ),
            )
        except ValidationError as error:
            raise AwsProfileError(
                f"CloudFormation returned an invalid stack {stack_name!r}.",
            ) from error
        outputs: dict[str, str] = {}
        for output in payload.stacks[0].outputs:
            if output.key in outputs:
                raise AwsProfileError(
                    f"CloudFormation stack {stack_name!r} repeats output "
                    f"{output.key!r}.",
                )
            outputs[output.key] = output.value
        return outputs

    def _ensure_relay_policy(
        self,
        *,
        profile: str,
        document: IamPolicyDocument,
    ) -> tuple[str, AwsManagedPolicyState]:
        policy_arn = self._relay_policy_arn()
        result = self._run_aws(
            profile,
            "iam",
            "get-policy",
            "--policy-arn",
            policy_arn,
        )
        if result.returncode != 0:
            if IAM_NO_SUCH_ENTITY not in failure_detail(result=result):
                self._require_success(
                    result=result,
                    operation=f"inspect relay policy {policy_arn}",
                    include_instructions=True,
                )
            self._create_relay_policy(
                profile=profile,
                document=document,
            )
            return policy_arn, AwsManagedPolicyState.CREATED
        try:
            summary = IamGetPolicyPayload.model_validate(
                self._json_object(result=result, operation="read relay policy"),
            ).policy
        except ValidationError as error:
            raise AwsProfileError(
                "IAM returned invalid relay policy metadata.",
            ) from error
        if (
            summary.policy_name != RELAY_POLICY_NAME
            or summary.policy_arn != policy_arn
            or summary.path != RELAY_POLICY_PATH
        ):
            raise AwsProfileError(
                "Existing relay policy metadata does not match the toolkit-owned "
                "policy; refusing to overwrite it.",
            )
        current = self._policy_document(
            profile=profile,
            policy_arn=policy_arn,
            version_id=summary.default_version_id,
        )
        if current == document:
            return policy_arn, AwsManagedPolicyState.UNCHANGED
        if RELAY_POLICY_MARKER_SID not in {
            statement.sid for statement in current.statements
        }:
            raise AwsProfileError(
                "Existing relay policy lacks the toolkit ownership marker; "
                "refusing to overwrite it.",
            )
        self._make_policy_version_room(profile=profile, policy_arn=policy_arn)
        self._require_success(
            result=self._run_aws(
                profile,
                "iam",
                "create-policy-version",
                "--policy-arn",
                policy_arn,
                "--policy-document",
                document.compact_json(),
                "--set-as-default",
            ),
            operation="update the generated relay policy",
            include_instructions=True,
        )
        return policy_arn, AwsManagedPolicyState.UPDATED

    def _create_relay_policy(
        self,
        *,
        profile: str,
        document: IamPolicyDocument,
    ) -> None:
        self._require_success(
            result=self._run_aws(
                profile,
                "iam",
                "create-policy",
                "--policy-name",
                RELAY_POLICY_NAME,
                "--path",
                RELAY_POLICY_PATH,
                "--description",
                RELAY_POLICY_DESCRIPTION,
                "--policy-document",
                document.compact_json(),
            ),
            operation="create the generated relay policy",
            include_instructions=True,
        )

    def _policy_document(
        self,
        *,
        profile: str,
        policy_arn: str,
        version_id: str,
    ) -> IamPolicyDocument:
        result = self._run_aws(
            profile,
            "iam",
            "get-policy-version",
            "--policy-arn",
            policy_arn,
            "--version-id",
            version_id,
        )
        self._require_success(
            result=result,
            operation="read the generated relay policy version",
            include_instructions=True,
        )
        try:
            return IamGetPolicyVersionPayload.model_validate(
                self._json_object(
                    result=result,
                    operation="read relay policy version",
                ),
            ).policy_version.document
        except ValidationError as error:
            raise AwsProfileError(
                "IAM returned an invalid relay policy document.",
            ) from error

    def _make_policy_version_room(self, *, profile: str, policy_arn: str) -> None:
        result = self._run_aws(
            profile,
            "iam",
            "list-policy-versions",
            "--policy-arn",
            policy_arn,
        )
        self._require_success(
            result=result,
            operation="list generated relay policy versions",
            include_instructions=True,
        )
        try:
            payload = IamListPolicyVersionsPayload.model_validate(
                self._json_object(
                    result=result,
                    operation="read relay policy versions",
                ),
            )
        except ValidationError as error:
            raise AwsProfileError(
                "IAM returned an invalid relay policy version list.",
            ) from error
        if payload.is_truncated:
            raise AwsProfileError(
                "IAM unexpectedly paginated a maximum-five policy version list.",
            )
        if len(payload.versions) < IAM_POLICY_VERSION_LIMIT:
            return
        candidates = [version for version in payload.versions if not version.is_default]
        if not candidates:
            raise AwsProfileError(
                "IAM returned no removable non-default relay policy version.",
            )
        oldest = min(candidates, key=lambda version: int(version.version_id[1:]))
        self._require_success(
            result=self._run_aws(
                profile,
                "iam",
                "delete-policy-version",
                "--policy-arn",
                policy_arn,
                "--version-id",
                oldest.version_id,
            ),
            operation=f"delete old relay policy version {oldest.version_id}",
            include_instructions=True,
        )

    def _attach_policy(
        self,
        *,
        request: AwsProfileBootstrapRequest,
        policy_arn: str,
    ) -> None:
        if request.principal_type is AwsIamPrincipalType.USER:
            arguments = (
                "iam",
                "attach-user-policy",
                "--user-name",
                request.principal_name,
            )
        elif request.principal_type is AwsIamPrincipalType.GROUP:
            arguments = (
                "iam",
                "attach-group-policy",
                "--group-name",
                request.principal_name,
            )
        else:
            arguments = (
                "iam",
                "attach-role-policy",
                "--role-name",
                request.principal_name,
            )
        self._require_success(
            result=self._run_aws(
                request.bootstrap_profile,
                *arguments,
                "--policy-arn",
                policy_arn,
            ),
            operation=(
                f"attach {policy_arn} to IAM {request.principal_type.value} "
                f"{request.principal_name!r}"
            ),
            include_instructions=True,
        )

    def _relay_policy_arn(self) -> str:
        return (
            f"arn:aws:iam::{self._settings.aws_account_id}:policy"
            f"{RELAY_POLICY_PATH}{RELAY_POLICY_NAME}"
        )

    def _run_aws(self, profile: str, *arguments: str) -> AwsCliResult:
        return self._runner.run(
            command=[
                "aws",
                *arguments,
                "--profile",
                profile,
                "--region",
                self._settings.aws_region,
                "--output",
                "json",
                "--no-cli-pager",
            ],
            io_mode=AwsCliIoMode.CAPTURE,
        )

    @staticmethod
    def _json_object(
        *,
        result: AwsCliResult,
        operation: str,
    ) -> Mapping[str, object]:
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise AwsProfileError(
                f"AWS CLI returned invalid JSON while {operation}.",
            ) from error
        if not isinstance(payload, Mapping):
            raise AwsProfileError(
                f"AWS CLI returned a non-object JSON value while {operation}.",
            )
        return cast("Mapping[str, object]", payload)

    def _require_success(
        self,
        *,
        result: AwsCliResult,
        operation: str,
        include_instructions: bool,
    ) -> None:
        if result.returncode == 0:
            return
        detail = failure_detail(result=result)
        suffix = f": {detail}" if detail else ""
        guidance = f"\n\n{self.instructions()}" if include_instructions else ""
        raise AwsProfileError(f"Failed to {operation}{suffix}{guidance}")
