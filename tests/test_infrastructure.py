from __future__ import annotations

import json
import re
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
    assert "ScheduleExpression: cron(0/15 * * * ? *)" in template
    assert "MaximumEventAgeInSeconds: 900" in template
    assert "MaximumRetryAttempts: 3" in template
    assert "DeadLetterConfig:" in template
    assert "MetricName: InvocationDroppedCount" in template


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


def test_makefile_is_the_newcomer_and_ci_control_surface() -> None:
    makefile = (ROOT / "Makefile").read_text()
    workflow = (ROOT / ".github/workflows/ci.yaml").read_text()

    assert ".DEFAULT_GOAL := help" in makefile
    for target in (
        "help",
        "doctor",
        "install",
        "setup",
        "dev",
        "up",
        "client",
        "logs",
        "ps",
        "down",
        "db-status",
        "db-apply",
        "lint",
        "test",
        "check",
        "infra",
        "images",
        "ci",
        "deploy",
        "deploy-status",
    ):
        assert re.search(rf"^{re.escape(target)}:", makefile, re.MULTILINE)

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
    assert "gh workflow run deploy-production.yaml --ref main" in makefile
    assert "sam deploy" not in makefile

    down_target = makefile.split("down: require-root-env", maxsplit=1)[1].split(
        "db-status:",
        maxsplit=1,
    )[0]
    assert "--volumes" not in down_target

    assert "build-ApiFunction: build-python" in makefile
    assert "build-IngestionFunction: build-python" in makefile
    assert "make install" in workflow
    assert "make ci" in workflow
    assert "docker build" not in workflow
