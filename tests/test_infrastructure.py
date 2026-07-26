from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_application_template_wires_collector_boundary() -> None:
    template = (ROOT / "template-app.yaml").read_text()
    assert "CollectorPrincipalArn:" in template
    assert "CollectedPayloadBucket:" in template
    assert "ExpirationInDays: 7" in template
    assert "Queue: !GetAtt CollectedPayloadQueue.Arn" in template
    assert "Action: s3:PutObject" in template
    assert "Action: s3:GetObject" in template
    ingestion_environment = template.split("IngestionFunction:", maxsplit=1)[1].split(
        "Policies:",
        maxsplit=1,
    )[0]
    assert "FPL_API_BASE_URL" not in ingestion_environment
    assert "FPL_CLIENT_USER_AGENT" not in ingestion_environment
    collector_policy = template.split("CollectorRole:", maxsplit=1)[1].split(
        "LiveScheduleGroup:",
        maxsplit=1,
    )[0].split("Policies:", maxsplit=1)[1]
    actions = set(
        re.findall(
            r"(?:- |Action: )((?:sqs|s3):[A-Za-z]+)",
            collector_policy,
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
    assert "Resource: !GetAtt IngestionQueue.Arn" in collector_policy
    assert "Resource: !GetAtt CollectedPayloadQueue.Arn" in collector_policy
    assert (
        'Resource: !Sub "${CollectedPayloadBucket.Arn}/${PayloadPrefix}/*"'
        in collector_policy
    )
    for alarm in (
        "FetchDeadLetterAlarm:",
        "CollectedPayloadDeadLetterAlarm:",
        "FetchQueueAgeAlarm:",
        "CollectedPayloadQueueAgeAlarm:",
        "IngestionErrorAlarm:",
    ):
        assert alarm in template
    for output in (
        "FetchQueueUrl:",
        "CollectedPayloadQueueUrl:",
        "CollectedPayloadBucketName:",
        "CollectedPayloadPrefix:",
        "CollectorRoleArn:",
    ):
        assert output in template


def test_nas_compose_has_no_port_or_database_access() -> None:
    compose = (ROOT / "deploy/nas/compose.yaml").read_text()
    assert "ports:" not in compose
    assert "DATABASE_" not in compose
    assert "read_only: true" in compose
    assert "no-new-privileges:true" in compose
    assert "fpl-data-relay-collector" in compose


def test_production_deployment_is_manual_guarded_and_migration_aware() -> None:
    workflow = (ROOT / ".github/workflows/deploy-production.yaml").read_text()
    assert "workflow_dispatch:" in workflow
    assert "push:" not in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "Require successful CI for this commit" in workflow
    assert "sha-${GITHUB_SHA}" in workflow
    assert "check-change-set" in workflow
    assert "--policy data" in workflow
    assert "--policy application" in workflow
    assert "--boundary data-stack" in workflow
    assert "--boundary application-stack" in workflow
    assert "--boundary post-deployment" in workflow
    assert "reconcile-collector-user" in workflow
    assert "secure-failure" in workflow
    assert "update-termination-protection" in workflow
    assert "send-message" not in workflow
    assert "AWS_ACCESS_KEY_ID" in workflow
    assert "AWS_SECRET_ACCESS_KEY" in workflow


def test_production_config_replaces_stale_sam_configuration() -> None:
    config = (ROOT / "deploy/production.toml").read_text()
    assert 'data_stack_name = "fpl-relay-data"' in config
    assert 'application_stack_name = "fpl-relay-app"' in config
    assert 'payload_prefix = "payloads/v1"' in config
    assert "FplApiBaseUrl" not in config
    assert "FplClientUserAgent" not in config
    assert not (ROOT / "samconfig.toml").exists()
