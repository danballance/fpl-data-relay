"""Preserve the fetch queue while moving ingestion behind the NAS collector."""

from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, model_validator

from fpl_data_relay.infrastructure_migrations.interfaces import InfrastructureAws
from fpl_data_relay.infrastructure_migrations.models import (
    MigrationBoundary,
    MigrationDefinition,
    ProductionConfig,
)

SOURCE_PATH = Path(__file__)


class CollectorSplitContext(BaseModel):
    """Fetch queue identity captured before the application-stack update."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fetch_queue_url: str | None
    fetch_queue_arn: str | None

    @model_validator(mode="after")
    def require_complete_queue_identity(self) -> CollectorSplitContext:
        """Require both queue values for an existing stack, or neither."""
        if (self.fetch_queue_url is None) != (self.fetch_queue_arn is None):
            raise ValueError("fetch queue URL and ARN must be supplied together")
        return self


class SplitCollectorIngestionMigration:
    """One-time transition from direct Lambda fetches to collected payloads."""

    source_path = SOURCE_PATH
    definition = MigrationDefinition(
        version=1,
        name="split_collector_ingestion",
        boundary=MigrationBoundary.APPLICATION_STACK,
        checksum=sha256(SOURCE_PATH.read_bytes()).hexdigest(),
    )

    def prepare(
        self,
        *,
        aws: InfrastructureAws,
        config: ProductionConfig,
    ) -> str:
        """Capture and protect the existing physical fetch queue."""
        if not aws.stack_exists(stack_name=config.application_stack_name):
            return CollectorSplitContext(
                fetch_queue_url=None,
                fetch_queue_arn=None,
            ).model_dump_json()
        fetch_queue_url = _fetch_queue_url(aws=aws, config=config)
        fetch_queue_arn = aws.queue_arn(queue_url=fetch_queue_url)
        aws.disable_event_source_mappings(source_arn=fetch_queue_arn)
        aws.assert_no_active_event_source_mappings(source_arn=fetch_queue_arn)
        return CollectorSplitContext(
            fetch_queue_url=fetch_queue_url,
            fetch_queue_arn=fetch_queue_arn,
        ).model_dump_json()

    def finalize(
        self,
        *,
        aws: InfrastructureAws,
        config: ProductionConfig,
        context_json: str,
    ) -> None:
        """Verify the upgraded stack before permitting an applied marker."""
        context = CollectorSplitContext.model_validate_json(context_json)
        outputs = _required_upgraded_outputs(aws=aws, config=config)
        if (
            context.fetch_queue_url is not None
            and outputs["FetchQueueUrl"] != context.fetch_queue_url
        ):
            raise RuntimeError(
                "The application update replaced the existing physical fetch queue.",
            )
        self.verify(aws=aws, config=config)

    def verify(
        self,
        *,
        aws: InfrastructureAws,
        config: ProductionConfig,
    ) -> None:
        """Verify the collector/result boundary and its security invariants."""
        outputs = _required_upgraded_outputs(aws=aws, config=config)
        if outputs["CollectedPayloadPrefix"] != config.payload_prefix:
            raise RuntimeError(
                "Application payload-prefix output does not match production config.",
            )
        fetch_queue_arn = aws.queue_arn(queue_url=outputs["FetchQueueUrl"])
        result_queue_arn = aws.queue_arn(
            queue_url=outputs["CollectedPayloadQueueUrl"],
        )
        aws.assert_no_active_event_source_mappings(source_arn=fetch_queue_arn)
        function_name = aws.stack_resource_id(
            stack_name=config.application_stack_name,
            logical_resource_id="IngestionFunction",
        )
        aws.assert_single_enabled_event_source_mapping(
            source_arn=result_queue_arn,
            function_name=function_name,
        )
        aws.assert_payload_bucket(
            bucket_name=outputs["CollectedPayloadBucketName"],
            payload_prefix=config.payload_prefix,
        )
        aws.assert_collector_source_policy(
            source_user_name=config.collector_source_user_name,
            source_user_arn=config.collector_source_user_arn,
            collector_role_arn=outputs["CollectorRoleArn"],
        )

    def secure_failure(
        self,
        *,
        aws: InfrastructureAws,
        config: ProductionConfig,
    ) -> None:
        """Ensure an interrupted update cannot restore direct FPL ingestion."""
        if not aws.stack_exists(stack_name=config.application_stack_name):
            return
        fetch_queue_url = _fetch_queue_url(aws=aws, config=config)
        fetch_queue_arn = aws.queue_arn(queue_url=fetch_queue_url)
        aws.disable_event_source_mappings(source_arn=fetch_queue_arn)
        aws.assert_no_active_event_source_mappings(source_arn=fetch_queue_arn)


def _fetch_queue_url(
    *,
    aws: InfrastructureAws,
    config: ProductionConfig,
) -> str:
    outputs = aws.stack_outputs(stack_name=config.application_stack_name)
    legacy_url = outputs.get("IngestionQueueUrl")
    upgraded_url = outputs.get("FetchQueueUrl")
    if legacy_url is not None and upgraded_url is not None:
        raise RuntimeError(
            "Application stack exposes both legacy and upgraded fetch outputs.",
        )
    if legacy_url is not None:
        return legacy_url
    if upgraded_url is not None:
        return upgraded_url
    return aws.stack_resource_id(
        stack_name=config.application_stack_name,
        logical_resource_id="IngestionQueue",
    )


def _required_upgraded_outputs(
    *,
    aws: InfrastructureAws,
    config: ProductionConfig,
) -> dict[str, str]:
    outputs = aws.stack_outputs(stack_name=config.application_stack_name)
    required = {
        "FetchQueueUrl",
        "FetchDeadLetterQueueUrl",
        "CollectedPayloadQueueUrl",
        "CollectedPayloadDeadLetterQueueUrl",
        "CollectedPayloadBucketName",
        "CollectedPayloadPrefix",
        "CollectorRoleArn",
    }
    missing = sorted(required.difference(outputs))
    if missing:
        raise RuntimeError(
            "Upgraded application stack is missing outputs: " + ", ".join(missing),
        )
    return outputs


MIGRATION = SplitCollectorIngestionMigration()
