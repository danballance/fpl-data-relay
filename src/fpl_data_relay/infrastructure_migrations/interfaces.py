"""Interfaces shared by the infrastructure migration runner and versions."""

from pathlib import Path
from typing import Protocol

from fpl_data_relay.infrastructure_migrations.models import (
    AppliedMigrationRecord,
    ChangeSetPolicy,
    MigrationDefinition,
    ProductionConfig,
)


class InfrastructureAws(Protocol):
    """AWS operations required by migrations and deployment reconciliation."""

    def verify_identity(self, *, expected_account_id: str) -> None: ...

    def stack_exists(self, *, stack_name: str) -> bool: ...

    def stack_outputs(self, *, stack_name: str) -> dict[str, str]: ...

    def stack_resource_id(
        self,
        *,
        stack_name: str,
        logical_resource_id: str,
    ) -> str: ...

    def queue_arn(self, *, queue_url: str) -> str: ...

    def disable_event_source_mappings(self, *, source_arn: str) -> None: ...

    def assert_no_active_event_source_mappings(
        self,
        *,
        source_arn: str,
    ) -> None: ...

    def assert_single_enabled_event_source_mapping(
        self,
        *,
        source_arn: str,
        function_name: str,
    ) -> None: ...

    def assert_payload_bucket(
        self,
        *,
        bucket_name: str,
        payload_prefix: str,
    ) -> None: ...

    def reconcile_collector_source_policy(
        self,
        *,
        source_user_name: str,
        source_user_arn: str,
        collector_role_arn: str,
    ) -> None: ...

    def assert_collector_source_policy(
        self,
        *,
        source_user_name: str,
        source_user_arn: str,
        collector_role_arn: str,
    ) -> None: ...

    def read_migration_records(
        self,
        *,
        parameter_prefix: str,
    ) -> list[AppliedMigrationRecord]: ...

    def write_migration_record(
        self,
        *,
        parameter_prefix: str,
        record: AppliedMigrationRecord,
    ) -> None: ...

    def validate_change_set(
        self,
        *,
        change_set_arn: str,
        policy: ChangeSetPolicy,
    ) -> None: ...


class InfrastructureMigration(Protocol):
    """One immutable, retry-safe infrastructure migration."""

    definition: MigrationDefinition
    source_path: Path

    def prepare(
        self,
        *,
        aws: InfrastructureAws,
        config: ProductionConfig,
    ) -> str: ...

    def finalize(
        self,
        *,
        aws: InfrastructureAws,
        config: ProductionConfig,
        context_json: str,
    ) -> None: ...

    def verify(
        self,
        *,
        aws: InfrastructureAws,
        config: ProductionConfig,
    ) -> None: ...

    def secure_failure(
        self,
        *,
        aws: InfrastructureAws,
        config: ProductionConfig,
    ) -> None: ...
