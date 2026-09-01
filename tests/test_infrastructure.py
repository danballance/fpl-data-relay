from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_application_template_uses_final_collector_topology() -> None:
    template = (ROOT / "template-app.yaml").read_text()

    for logical_id in (
        "FetchQueue:",
        "FetchDeadLetterQueue:",
        "ResultQueue:",
        "ResultDeadLetterQueue:",
        "ScheduleDeadLetterQueue:",
        "PayloadBucket:",
        "CollectorUserPolicy:",
    ):
        assert logical_id in template

    for legacy_name in (
        "CollectorPrincipalArn",
        "CollectorRole",
        "IngestionQueue",
        "CollectedPayloadQueue",
        "CollectedPayloadBucket",
    ):
        assert legacy_name not in template

    assert "CollectorUserName:" in template
    assert "UserName: !Ref CollectorUserName" in template
    assert "ExpirationInDays: 7" in template
    assert 'Prefix: !Sub "${PayloadPrefix}/"' in template
    assert "Queue: !GetAtt ResultQueue.Arn" in template

    ingestion_environment = template.split("IngestionFunction:", maxsplit=1)[
        1
    ].split("Policies:", maxsplit=1)[0]
    assert "FPL_API_BASE_URL" not in ingestion_environment
    assert "FPL_CLIENT_USER_AGENT" not in ingestion_environment
    assert "LogFormat: JSON" in ingestion_environment
    assert "ApplicationLogLevel: INFO" in ingestion_environment


def test_collector_user_policy_is_exact_and_resource_scoped() -> None:
    template = (ROOT / "template-app.yaml").read_text()
    policy = template.split("CollectorUserPolicy:", maxsplit=1)[1].split(
        "LiveScheduleGroup:",
        maxsplit=1,
    )[0]
    actions = set(
        re.findall(
            r"(?:- |Action: )((?:sqs|s3):[A-Za-z]+)",
            policy,
        ),
    )

    assert actions == {
        "sqs:ChangeMessageVisibility",
        "sqs:DeleteMessage",
        "sqs:GetQueueAttributes",
        "sqs:ReceiveMessage",
        "sqs:SendMessage",
        "s3:PutObject",
    }
    assert "Resource: !GetAtt FetchQueue.Arn" in policy
    assert "Resource: !GetAtt ResultQueue.Arn" in policy
    assert 'Resource: !Sub "${PayloadBucket.Arn}/${PayloadPrefix}/*"' in policy
    assert "sts:" not in policy
    assert 'Resource: "*"' not in policy


def test_application_template_keeps_operational_protections_and_outputs() -> None:
    template = (ROOT / "template-app.yaml").read_text()
    lambda_api = (ROOT / "src/fpl_data_relay/lambda_api.py").read_text()

    for alarm in (
        "FetchDeadLetterAlarm:",
        "ResultDeadLetterAlarm:",
        "FetchQueueAgeAlarm:",
        "ResultQueueAgeAlarm:",
        "IngestionErrorAlarm:",
        "ScheduleDeadLetterAlarm:",
        "ReferenceScheduleMissedAlarm:",
        "ReferenceScheduleErrorAlarm:",
        "LiveScheduleErrorAlarm:",
        "ScheduleDroppedAlarm:",
    ):
        assert alarm in template

    for output in (
        "FetchQueueUrl:",
        "FetchDeadLetterQueueUrl:",
        "ResultQueueUrl:",
        "ResultDeadLetterQueueUrl:",
        "ScheduleDeadLetterQueueUrl:",
        "PayloadBucketName:",
        "PayloadPrefix:",
    ):
        assert output in template.split("Outputs:", maxsplit=1)[1]

    assert "CollectorRoleArn:" not in template
    assert "SqsManagedSseEnabled: true" in template
    assert "BucketOwnerEnforced" in template
    assert "BlockPublicPolicy: true" in template
    assert 'Name: !Sub "${AWS::StackName}-reference-hourly"' in template
    assert "ScheduleExpression: cron(0 * * * ? *)" in template
    assert "State: !Ref ReferenceScheduleState" in template
    assert "ReferenceScheduleGroupName:" in template
    assert "LiveScheduleGroupName:" in template
    assert "DeployedRevision:" in template
    assert "MaximumEventAgeInSeconds: 900" in template
    assert "MaximumRetryAttempts: 3" in template
    assert "scheduler:GetSchedule" in template
    assert "DeadLetterConfig:" in template
    assert "MetricName: InvocationDroppedCount" in template
    missed_alarm = template.split("ReferenceScheduleMissedAlarm:", maxsplit=1)[
        1
    ].split("ReferenceScheduleErrorAlarm:", maxsplit=1)[0]
    assert "Period: 3600" in missed_alarm
    assert "EvaluationPeriods: 2" in missed_alarm
    assert "reference_poll_seconds=3600" in lambda_api


def test_application_template_has_disabled_isolated_community_runtime() -> None:
    template = (ROOT / "template-app.yaml").read_text()
    workflow = (ROOT / ".github/workflows/deploy-production.yaml").read_text()
    makefile = (ROOT / "Makefile").read_text()

    for logical_id in (
        "CommunityQueue:",
        "CommunityDeadLetterQueue:",
        "CommunityFunction:",
        "CommunitySchedule:",
        "CommunityLogGroup:",
        "CommunityErrorAlarm:",
        "CommunityQueueAgeAlarm:",
        "CommunityDeadLetterAlarm:",
        "CommunityScheduleErrorAlarm:",
        "CommunityScheduleDroppedAlarm:",
        "CommunityScheduleMissedAlarm:",
    ):
        assert logical_id in template

    community_function = template.split("CommunityFunction:", maxsplit=1)[1].split(
        "ApiLogGroup:",
        maxsplit=1,
    )[0]
    assert "Timeout: 840" in community_function
    assert "ReservedConcurrentExecutions: 1" in community_function
    assert "BatchSize: 1" in community_function
    assert "ReadCommunityCredentialSecret" in community_function
    assert "rds-data:ExecuteStatement" in community_function
    assert "rds-data:BeginTransaction" not in community_function
    assert "rds-data:BatchExecuteStatement" not in community_function
    assert 'Resource: "*"' not in community_function
    assert "ScheduleExpression: cron(0 6 * * ? *)" in template
    assert "ScheduleExpressionTimezone: Europe/London" in template
    assert "State: !Ref CommunityScheduleState" in template
    assert "CommunityScheduleEnabled: !Equals" in template
    assert "Condition: CommunityScheduleEnabled" in template
    assert "VisibilityTimeout: 900" in template
    assert "RetentionInDays: 14" in template
    assert "COMMUNITY_SCHEDULE_STATE: DISABLED" in workflow
    assert '"CommunityScheduleState=${COMMUNITY_SCHEDULE_STATE}"' in workflow
    assert '"ReferenceScheduleState=${REFERENCE_SCHEDULE_STATE}"' in workflow
    assert '"DeployedRevision=${GITHUB_SHA}"' in workflow
    assert "Preserve operational schedule states" in workflow
    assert "ReferenceScheduleGroupName" in workflow
    assert "ReferenceScheduleName" in workflow
    assert "reference-quarter-hour" not in workflow
    assert "--group community" in makefile


def test_data_stack_has_layered_database_protection() -> None:
    template = (ROOT / "template-data.yaml").read_text()
    assert "DeletionPolicy: Snapshot" in template
    assert "UpdateReplacePolicy: Snapshot" in template
    assert "DeletionProtection: true" in template

    policy = json.loads((ROOT / "deploy/data-stack-policy.json").read_text())
    deny = next(
        statement
        for statement in policy["Statement"]
        if statement["Effect"] == "Deny"
    )
    assert set(deny["Action"]) == {"Update:Delete", "Update:Replace"}
    assert set(deny["Resource"]) == {
        "LogicalResourceId/DatabaseCluster",
        "LogicalResourceId/DatabaseWriter",
    }


def test_production_deployment_is_one_direct_manual_workflow() -> None:
    workflow = (ROOT / ".github/workflows/deploy-production.yaml").read_text()

    assert "workflow_dispatch:" in workflow
    assert "push:" not in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "Require successful CI for this commit" in workflow
    assert "sha-${GITHUB_SHA}" in workflow
    assert workflow.count("sam deploy") == 2
    assert "update-termination-protection" in workflow
    assert "set-stack-policy" in workflow
    assert "CAPABILITY_NAMED_IAM" in workflow
    assert '"CollectorUserName=${COLLECTOR_USER_NAME}"' in workflow
    assert "PAYLOAD_PREFIX: payloads" in workflow
    assert "uv run fpl-relay db wait-ready" in workflow
    assert "--attempts 12" in workflow
    assert "--interval-seconds 5" in workflow
    assert "AWS_ACCESS_KEY_ID" in workflow
    assert "AWS_SECRET_ACCESS_KEY" in workflow
    assert "${{ vars.COMMUNITY_CREDENTIAL_SECRET_ARN }}" in workflow
    assert "${{ secrets.COMMUNITY_CREDENTIAL_SECRET_ARN }}" not in workflow
    assert "Validate deployment variables" in workflow
    assert 'app_stack="$(aws cloudformation describe-stacks' in workflow
    assert 'grep -q "does not exist"' in workflow
    assert "Preserve operational schedule states" in workflow
    assert '2>/dev/null; then' not in workflow

    for removed_concept in (
        "fpl-infrastructure-migrate",
        "check-change-set",
        "infrastructure_migrations",
        "MIGRATION_STATE",
        "--boundary",
        "reconcile-collector-user",
        "secure-failure",
        "production.toml",
    ):
        assert removed_concept not in workflow


def test_local_compose_is_explicit_and_does_not_restart_stale_services() -> None:
    compose = (ROOT / "docker-compose.yml").read_text()
    dockerfile = (ROOT / "Dockerfile").read_text()
    example = (ROOT / ".env.example").read_text()

    assert "postgres:17.7-alpine" in compose
    assert "postgres-17-data:" in compose
    assert "DATABASE_EXECUTOR: asyncpg" in compose
    assert "FPL_POSTGRES_MAINTENANCE_DATABASE_URL is required" in compose
    assert "http://127.0.0.1:8000/readyz" in compose
    assert "restart:" not in compose
    assert 'ENV PATH="/app/.venv/bin:$PATH"' in dockerfile
    assert 'CMD ["fpl-relay", "serve"]' in dockerfile
    assert "FPL_DATABASE_URL=" in example
    assert "FPL_POSTGRES_MAINTENANCE_DATABASE_URL=" in example


def test_nas_compose_is_hardened_and_uses_direct_immutable_identity() -> None:
    compose = (ROOT / "deploy/nas/compose.yaml").read_text()
    example = (ROOT / "deploy/nas/.env.example").read_text()
    aws_config = (ROOT / "deploy/nas/aws-config.example").read_text()

    assert "ports:" not in compose
    assert "DATABASE_" not in compose
    assert "pull_policy:" not in compose
    assert "read_only: true" in compose
    assert "no-new-privileges:true" in compose
    assert "cap_drop:" in compose
    assert "restart: unless-stopped" in compose
    assert "fpl-data-relay-collector" in compose
    assert "COLLECTOR_IMAGE_TAG=sha-" in example
    assert "AWS_PROFILE=fpl-collector-source" in example
    assert "[profile fpl-collector-source]" in aws_config
    assert "role_arn" not in aws_config
    assert "source_profile" not in aws_config


def test_administration_configuration_is_nonsecret_and_externally_managed() -> None:
    example = (ROOT / ".admin.env.example").read_text()
    guide = (ROOT / "docs/administration.md").read_text()
    normalized_guide = " ".join(guide.split())
    helper = (
        ROOT / "src/fpl_data_relay/adapters/outbound/nas_admin.sh"
    ).read_text()

    assert "FPL_ADMIN_AWS_PROFILE=default" in example
    assert "FPL_ADMIN_NAS_SSH_TARGET=fpl-nas" in example
    assert "FPL_ADMIN_AWS_ACCOUNT_ID" not in example
    assert "SECRET_ACCESS_KEY" not in example
    assert not (ROOT / "deploy/admin-policy.example.json").exists()
    assert "configured and authenticated externally" in normalized_guide
    assert "never create, edit, log in to, log out of, or" in normalized_guide
    assert "make aws-profile-" not in guide
    assert "COLLECTOR_IMAGE_TAG" in helper
    assert "backup" in helper


def test_makefile_separates_local_and_ci_control_surfaces() -> None:
    makefile = (ROOT / "Makefile").read_text()
    workflow = (ROOT / ".github/workflows/ci.yaml").read_text()

    assert ".DEFAULT_GOAL := help" in makefile
    for target in (
        "help",
        "tui",
        "doctor",
        "install",
        "setup",
        "local-dev",
        "local-up",
        "local-client",
        "local-logs",
        "local-ps",
        "local-down",
        "local-db-status",
        "local-db-migrate",
        "aws-doctor",
        "aws-status",
        "aws-app-revision",
        "aws-db-status",
        "aws-db-migrate",
        "aws-queues-status",
        "aws-queues-drain",
        "aws-dlqs-status",
        "aws-dlq-peek",
        "aws-send-reference",
        "aws-send-live",
        "aws-send-community",
        "aws-schedules-status",
        "aws-schedules-bootstrap-pause",
        "aws-schedules-bootstrap-restore",
        "aws-maintenance-status",
        "aws-schedules-pause",
        "aws-schedules-restore",
        "aws-rebaseline-current",
        "nas-doctor",
        "nas-status",
        "nas-start",
        "nas-stop",
        "nas-logs",
        "nas-update",
        "nas-rollback",
        "prod-doctor",
        "prod-status",
        "prod-maintenance-begin",
        "prod-maintenance-end",
        "prod-rebaseline-current",
        "lint",
        "test",
        "check",
        "infra",
        "images",
        "ci",
    ):
        assert re.search(rf"^{re.escape(target)}:", makefile, re.MULTILINE)

    for removed_target in (
        "dev",
        "up",
        "client",
        "logs",
        "ps",
        "down",
        "db-status",
        "db-apply",
        "deploy",
        "deploy-status",
        "aws-profile-bootstrap",
        "aws-profile-setup",
        "aws-profile-onboard",
        "aws-profile-login",
        "aws-profile-status",
        "aws-profile-logout",
        "prepare-admin-env",
    ):
        assert not re.search(
            rf"^{re.escape(removed_target)}:",
            makefile,
            re.MULTILINE,
        )

    assert "test ! -e .env" in makefile
    assert "test ! -e client/.env.local" in makefile
    assert "cp .env.example .env" in makefile
    assert "cp client/.env.example client/.env.local" in makefile
    assert "mktemp -d" in makefile
    assert "docker compose --env-file .env.example config --quiet" in makefile
    assert "docker build --file Dockerfile --tag fpl-relay:test ." in makefile
    assert (
        "docker build --file Dockerfile.collector --tag fpl-collector:test ."
        in makefile
    )
    assert 'find_spec("fastapi") is None' in makefile
    assert "local-*" in makefile
    assert "aws-*" in makefile
    assert "nas-*" in makefile
    assert "prod-*" in makefile
    assert "fpl-admin --config .admin.env" in makefile
    assert "AWS CLI" in makefile
    assert "AWS CLI >= 2.32" not in makefile
    assert "uv run aws --version" in makefile
    assert "aws-profile-" not in makefile
    assert "prepare-admin-env" not in makefile
    public_targets = re.findall(
        r"^[a-zA-Z0-9_-]+:.*## ",
        makefile,
        re.MULTILINE,
    )
    assert len(public_targets) == 50
    assert "GitHub Actions" in makefile
    assert "gh workflow run" not in makefile
    assert "sam deploy" not in makefile

    bootstrap_script = ROOT / "scripts/bootstrap-migration-0005.sh"
    bootstrap = bootstrap_script.read_text()
    administration_guide = (ROOT / "docs/administration.md").read_text()
    gitignore = (ROOT / ".gitignore").read_text()
    assert bootstrap_script.stat().st_mode & 0o111
    assert "set -Eeuo pipefail" in bootstrap
    assert "prod-maintenance-begin" in bootstrap
    assert "prod-rebaseline-current" in bootstrap
    assert "aws-schedules-bootstrap-pause" in bootstrap
    assert "aws-schedules-bootstrap-restore" in bootstrap
    assert "gh workflow run" not in bootstrap
    assert "aws-db-migrate" not in bootstrap
    assert "scripts/bootstrap-migration-0005.sh prepare" in administration_guide
    assert "scripts/bootstrap-migration-0005.sh complete" in administration_guide
    assert ".admin-state/" in gitignore

    down_target = makefile.split(
        "local-down: require-local-env",
        maxsplit=1,
    )[1].split(
        "local-db-status:",
        maxsplit=1,
    )[0]
    assert "--volumes" not in down_target

    assert "build-ApiFunction: build-python" in makefile
    assert "build-IngestionFunction: build-python" in makefile
    assert "make install" in workflow
    assert "make ci" in workflow
    assert "docker build" not in workflow


def test_aws_commands_require_but_never_create_admin_config(
    tmp_path: Path,
) -> None:
    command = [
        "make",
        "--file",
        str(ROOT / "Makefile"),
        "aws-doctor",
        "ADMIN=true",
    ]

    missing = subprocess.run(
        command,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert missing.returncode != 0
    assert ".admin.env is required" in missing.stderr
    assert not (tmp_path / ".admin.env").exists()

    existing = "FPL_ADMIN_AWS_PROFILE=default\n"
    (tmp_path / ".admin.env").write_text(existing)
    configured = subprocess.run(
        command,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert configured.returncode == 0, configured.stderr
    assert (tmp_path / ".admin.env").read_text() == existing
