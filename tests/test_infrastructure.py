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
    ):
        assert alarm in template

    for output in (
        "FetchQueueUrl:",
        "FetchDeadLetterQueueUrl:",
        "ResultQueueUrl:",
        "ResultDeadLetterQueueUrl:",
        "PayloadBucketName:",
        "PayloadPrefix:",
    ):
        assert output in template.split("Outputs:", maxsplit=1)[1]

    assert "CollectorRoleArn:" not in template
    assert "SqsManagedSseEnabled: true" in template
    assert "BucketOwnerEnforced" in template
    assert "BlockPublicPolicy: true" in template


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
        "LogicalResourceId/DatabaseInstance",
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
    assert "FPL_POSTGRES_MAINTENANCE_DATABASE_URL is required" in compose
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


def test_ci_validates_both_images_and_compose_files() -> None:
    workflow = (ROOT / ".github/workflows/ci.yaml").read_text()

    assert "docker compose --env-file .env.example config --quiet" in workflow
    assert "cp deploy/nas/.env.example deploy/nas/.env" in workflow
    assert "--file deploy/nas/compose.yaml" in workflow
    assert "--env-file deploy/nas/.env.example" in workflow
    assert "config --quiet" in workflow
    assert "docker build --file Dockerfile --tag fpl-relay:test ." in workflow
    assert (
        "docker build --file Dockerfile.collector --tag fpl-collector:test ."
        in workflow
    )
    assert 'find_spec("fastapi") is None' in workflow
