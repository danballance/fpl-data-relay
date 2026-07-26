from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from fpl_data_relay.infrastructure_migrations import cli
from fpl_data_relay.infrastructure_migrations.cli import app
from fpl_data_relay.infrastructure_migrations.models import (
    AppliedMigrationRecord,
    ChangeSetPolicy,
    MigrationBoundary,
    ProductionConfig,
)
from fpl_data_relay.infrastructure_migrations.runner import (
    MIGRATIONS,
    InfrastructureMigrationError,
    begin_migration_run,
    commit_migration_run,
    finalize_boundary,
    prepare_boundary,
    reconcile_collector_source_user,
    secure_failed_deployment,
    validate_migration_history,
    validate_registry,
    verify_all_migrations,
)
from fpl_data_relay.infrastructure_migrations.versions import (
    v0001_split_collector_ingestion as collector_split,
)

COMMIT_SHA = "a" * 40
FETCH_URL = "https://sqs.eu-west-2.amazonaws.com/757771412865/fetch"
FETCH_ARN = "arn:aws:sqs:eu-west-2:757771412865:fetch"
RESULT_URL = "https://sqs.eu-west-2.amazonaws.com/757771412865/result"
RESULT_ARN = "arn:aws:sqs:eu-west-2:757771412865:result"
ROLE_ARN = "arn:aws:iam::757771412865:role/fpl-relay-app-CollectorRole"


class FakeInfrastructureAws:
    def __init__(self) -> None:
        self.account_id = "757771412865"
        self.application_exists = True
        self.outputs = {"IngestionQueueUrl": FETCH_URL}
        self.queue_arns = {
            FETCH_URL: FETCH_ARN,
            RESULT_URL: RESULT_ARN,
        }
        self.active_sources = {FETCH_ARN}
        self.records: list[AppliedMigrationRecord] = []
        self.policy_reconciled = False
        self.bucket_checked = False
        self.result_mapping_checked = False
        self.change_set_checks: list[tuple[str, ChangeSetPolicy]] = []

    def verify_identity(self, *, expected_account_id: str) -> None:
        if expected_account_id != self.account_id:
            raise RuntimeError("wrong account")

    def stack_exists(self, *, stack_name: str) -> bool:
        assert stack_name == "fpl-relay-app"
        return self.application_exists

    def stack_outputs(self, *, stack_name: str) -> dict[str, str]:
        assert stack_name == "fpl-relay-app"
        return self.outputs.copy()

    def stack_resource_id(
        self,
        *,
        stack_name: str,
        logical_resource_id: str,
    ) -> str:
        assert stack_name == "fpl-relay-app"
        if logical_resource_id == "IngestionQueue":
            return FETCH_URL
        if logical_resource_id == "IngestionFunction":
            return "fpl-relay-app-IngestionFunction"
        raise AssertionError(logical_resource_id)

    def queue_arn(self, *, queue_url: str) -> str:
        return self.queue_arns[queue_url]

    def disable_event_source_mappings(self, *, source_arn: str) -> None:
        self.active_sources.discard(source_arn)

    def assert_no_active_event_source_mappings(
        self,
        *,
        source_arn: str,
    ) -> None:
        if source_arn in self.active_sources:
            raise RuntimeError("mapping remains active")

    def assert_single_enabled_event_source_mapping(
        self,
        *,
        source_arn: str,
        function_name: str,
    ) -> None:
        assert source_arn == RESULT_ARN
        assert function_name == "fpl-relay-app-IngestionFunction"
        self.result_mapping_checked = True

    def assert_payload_bucket(
        self,
        *,
        bucket_name: str,
        payload_prefix: str,
    ) -> None:
        assert bucket_name == "payload-bucket"
        assert payload_prefix == "payloads/v1"
        self.bucket_checked = True

    def reconcile_collector_source_policy(
        self,
        *,
        source_user_name: str,
        source_user_arn: str,
        collector_role_arn: str,
    ) -> None:
        assert source_user_name == "fpl-relay-nas-source"
        assert source_user_arn.endswith(":user/fpl-relay-nas-source")
        assert collector_role_arn == ROLE_ARN
        self.policy_reconciled = True

    def assert_collector_source_policy(
        self,
        *,
        source_user_name: str,
        source_user_arn: str,
        collector_role_arn: str,
    ) -> None:
        assert source_user_name == "fpl-relay-nas-source"
        assert source_user_arn.endswith(":user/fpl-relay-nas-source")
        assert collector_role_arn == ROLE_ARN
        if not self.policy_reconciled:
            raise RuntimeError("source policy has not converged")

    def read_migration_records(
        self,
        *,
        parameter_prefix: str,
    ) -> list[AppliedMigrationRecord]:
        assert parameter_prefix.endswith("/infrastructure-migrations")
        return self.records.copy()

    def write_migration_record(
        self,
        *,
        parameter_prefix: str,
        record: AppliedMigrationRecord,
    ) -> None:
        assert parameter_prefix.endswith("/infrastructure-migrations")
        if any(existing.version == record.version for existing in self.records):
            raise RuntimeError("overwrite attempted")
        self.records.append(record)

    def validate_change_set(
        self,
        *,
        change_set_arn: str,
        policy: ChangeSetPolicy,
    ) -> None:
        self.change_set_checks.append((change_set_arn, policy))


def production_config() -> ProductionConfig:
    return ProductionConfig(
        account_id="757771412865",
        region="eu-west-2",
        data_stack_name="fpl-relay-data",
        application_stack_name="fpl-relay-app",
        alert_email="relay@example.com",
        collector_source_user_name="fpl-relay-nas-source",
        payload_prefix="payloads/v1",
        migration_parameter_prefix=(
            "/fpl-data-relay/production/infrastructure-migrations"
        ),
        collector_image="ghcr.io/danballance/fpl-data-relay-collector",
    )


def upgraded_outputs() -> dict[str, str]:
    return {
        "FetchQueueUrl": FETCH_URL,
        "FetchDeadLetterQueueUrl": f"{FETCH_URL}-dlq",
        "CollectedPayloadQueueUrl": RESULT_URL,
        "CollectedPayloadDeadLetterQueueUrl": f"{RESULT_URL}-dlq",
        "CollectedPayloadBucketName": "payload-bucket",
        "CollectedPayloadPrefix": "payloads/v1",
        "CollectorRoleArn": ROLE_ARN,
    }


def applied_record(
    *,
    checksum: str,
    version: int = 1,
    name: str = "split_collector_ingestion",
) -> AppliedMigrationRecord:
    return AppliedMigrationRecord(
        version=version,
        name=name,
        checksum=checksum,
        applied_at=datetime(2026, 7, 26, 12, tzinfo=UTC),
        commit_sha=COMMIT_SHA,
        account_id="757771412865",
        region="eu-west-2",
        stack_name="fpl-relay-app",
    )


def test_production_config_loads_repository_file_and_forbids_unknown(
    tmp_path: Path,
) -> None:
    repository_config = ProductionConfig.from_toml(
        path=Path(__file__).parents[1] / "deploy/production.toml",
    )
    assert repository_config.account_id == "757771412865"
    assert repository_config.collector_source_user_arn.endswith(
        ":user/fpl-relay-nas-source",
    )
    invalid = tmp_path / "invalid.toml"
    invalid.write_text(
        (Path(__file__).parents[1] / "deploy/production.toml").read_text()
        + "\nunknown = true\n",
    )
    with pytest.raises(ValidationError, match="unknown"):
        ProductionConfig.from_toml(path=invalid)
    with pytest.raises(ValidationError, match="timezone-aware"):
        AppliedMigrationRecord(
            version=1,
            name="example",
            checksum="b" * 64,
            applied_at=datetime(2026, 7, 26),
            commit_sha=COMMIT_SHA,
            account_id="757771412865",
            region="eu-west-2",
            stack_name="fpl-relay-app",
        )


def test_full_migration_run_is_ordered_recorded_and_idempotent(
    tmp_path: Path,
) -> None:
    config = production_config()
    aws = FakeInfrastructureAws()
    state_path = tmp_path / "state.json"

    begin_migration_run(
        aws=aws,
        config=config,
        commit_sha=COMMIT_SHA,
        state_path=state_path,
    )
    prepare_boundary(
        aws=aws,
        config=config,
        boundary=MigrationBoundary.DATA_STACK,
        state_path=state_path,
    )
    finalize_boundary(
        aws=aws,
        config=config,
        boundary=MigrationBoundary.DATA_STACK,
        state_path=state_path,
    )
    prepare_boundary(
        aws=aws,
        config=config,
        boundary=MigrationBoundary.APPLICATION_STACK,
        state_path=state_path,
    )
    assert FETCH_ARN not in aws.active_sources
    aws.outputs = upgraded_outputs()
    reconcile_collector_source_user(aws=aws, config=config)
    finalize_boundary(
        aws=aws,
        config=config,
        boundary=MigrationBoundary.APPLICATION_STACK,
        state_path=state_path,
    )
    prepare_boundary(
        aws=aws,
        config=config,
        boundary=MigrationBoundary.POST_DEPLOYMENT,
        state_path=state_path,
    )
    finalize_boundary(
        aws=aws,
        config=config,
        boundary=MigrationBoundary.POST_DEPLOYMENT,
        state_path=state_path,
    )
    commit_migration_run(
        aws=aws,
        config=config,
        state_path=state_path,
        applied_at=datetime(2026, 7, 26, 12, tzinfo=UTC),
    )

    assert [record.version for record in aws.records] == [1]
    assert aws.records[0].checksum == MIGRATIONS[0].definition.checksum
    assert aws.bucket_checked
    assert aws.result_mapping_checked
    verify_all_migrations(aws=aws, config=config)

    begin_migration_run(
        aws=aws,
        config=config,
        commit_sha=COMMIT_SHA,
        state_path=state_path,
    )
    assert '"migrations":[]' in state_path.read_text().replace(" ", "").replace(
        "\n",
        "",
    )
    commit_migration_run(
        aws=aws,
        config=config,
        state_path=state_path,
        applied_at=datetime(2026, 7, 26, 13, tzinfo=UTC),
    )
    assert len(aws.records) == 1


def test_fresh_stack_and_interrupted_marker_write_recover(
    tmp_path: Path,
) -> None:
    config = production_config()
    aws = FakeInfrastructureAws()
    aws.application_exists = False
    state_path = tmp_path / "state.json"
    begin_migration_run(
        aws=aws,
        config=config,
        commit_sha=COMMIT_SHA,
        state_path=state_path,
    )
    prepare_boundary(
        aws=aws,
        config=config,
        boundary=MigrationBoundary.APPLICATION_STACK,
        state_path=state_path,
    )
    aws.application_exists = True
    aws.outputs = upgraded_outputs()
    aws.active_sources.clear()
    reconcile_collector_source_user(aws=aws, config=config)
    finalize_boundary(
        aws=aws,
        config=config,
        boundary=MigrationBoundary.APPLICATION_STACK,
        state_path=state_path,
    )
    commit_migration_run(
        aws=aws,
        config=config,
        state_path=state_path,
        applied_at=datetime.now(tz=UTC),
    )
    assert len(aws.records) == 1


def test_failure_guard_never_records_and_disables_fetch_mapping(
    tmp_path: Path,
) -> None:
    config = production_config()
    aws = FakeInfrastructureAws()
    state_path = tmp_path / "state.json"
    begin_migration_run(
        aws=aws,
        config=config,
        commit_sha=COMMIT_SHA,
        state_path=state_path,
    )
    with pytest.raises(InfrastructureMigrationError, match="not finalized"):
        commit_migration_run(
            aws=aws,
            config=config,
            state_path=state_path,
            applied_at=datetime.now(tz=UTC),
        )
    assert aws.records == []
    secure_failed_deployment(aws=aws, config=config)
    assert FETCH_ARN not in aws.active_sources


def test_finalize_rejects_fetch_queue_replacement(tmp_path: Path) -> None:
    config = production_config()
    aws = FakeInfrastructureAws()
    state_path = tmp_path / "state.json"
    begin_migration_run(
        aws=aws,
        config=config,
        commit_sha=COMMIT_SHA,
        state_path=state_path,
    )
    prepare_boundary(
        aws=aws,
        config=config,
        boundary=MigrationBoundary.APPLICATION_STACK,
        state_path=state_path,
    )
    aws.outputs = upgraded_outputs() | {"FetchQueueUrl": f"{FETCH_URL}-replacement"}
    aws.queue_arns[f"{FETCH_URL}-replacement"] = f"{FETCH_ARN}-replacement"
    with pytest.raises(RuntimeError, match="replaced"):
        finalize_boundary(
            aws=aws,
            config=config,
            boundary=MigrationBoundary.APPLICATION_STACK,
            state_path=state_path,
        )
    assert aws.records == []


@pytest.mark.parametrize(
    ("record", "message"),
    [
        (
            applied_record(checksum="b" * 64, version=99, name="unknown"),
            "unknown migration",
        ),
        (
            applied_record(checksum="b" * 64, name="renamed"),
            "name mismatch",
        ),
        (
            applied_record(checksum="b" * 64),
            "checksum mismatch",
        ),
    ],
)
def test_migration_history_rejects_unknown_renamed_and_modified_records(
    record: AppliedMigrationRecord,
    message: str,
) -> None:
    with pytest.raises(InfrastructureMigrationError, match=message):
        validate_migration_history(
            applied=[record],
            config=production_config(),
        )


def test_registry_and_cli_help_are_available() -> None:
    validate_registry()
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "infrastructure migrations" in result.stdout


def test_cli_commands_delegate_complete_migration_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = production_config()
    aws = FakeInfrastructureAws()
    config_path = tmp_path / "production.toml"
    config_path.write_text("unused")
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(cli, "_load", lambda *, config_path: (config, aws))

    cli.begin(
        config_path=config_path,
        commit_sha=COMMIT_SHA,
        state_path=state_path,
    )
    cli.prepare(
        config_path=config_path,
        boundary=MigrationBoundary.APPLICATION_STACK,
        state_path=state_path,
    )
    aws.outputs = upgraded_outputs()
    cli.reconcile_collector_user(config_path=config_path)
    cli.finalize(
        config_path=config_path,
        boundary=MigrationBoundary.APPLICATION_STACK,
        state_path=state_path,
    )
    cli.prepare(
        config_path=config_path,
        boundary=MigrationBoundary.POST_DEPLOYMENT,
        state_path=state_path,
    )
    cli.finalize(
        config_path=config_path,
        boundary=MigrationBoundary.POST_DEPLOYMENT,
        state_path=state_path,
    )
    cli.check_change_set(
        config_path=config_path,
        change_set_arn="change-set",
        policy=ChangeSetPolicy.APPLICATION,
    )
    cli.commit(config_path=config_path, state_path=state_path)
    cli.verify(config_path=config_path)
    aws.active_sources.add(FETCH_ARN)
    cli.secure_failure(config_path=config_path)

    assert len(aws.records) == 1
    assert aws.change_set_checks == [
        ("change-set", ChangeSetPolicy.APPLICATION),
    ]
    assert FETCH_ARN not in aws.active_sources


def test_runner_rejects_unprepared_naive_and_cross_environment_state(
    tmp_path: Path,
) -> None:
    config = production_config()
    aws = FakeInfrastructureAws()
    state_path = tmp_path / "state.json"
    begin_migration_run(
        aws=aws,
        config=config,
        commit_sha=COMMIT_SHA,
        state_path=state_path,
    )
    with pytest.raises(InfrastructureMigrationError, match="not prepared"):
        finalize_boundary(
            aws=aws,
            config=config,
            boundary=MigrationBoundary.APPLICATION_STACK,
            state_path=state_path,
        )
    with pytest.raises(InfrastructureMigrationError, match="timezone-aware"):
        commit_migration_run(
            aws=aws,
            config=config,
            state_path=state_path,
            applied_at=datetime(2026, 7, 26),
        )
    wrong_config = config.model_copy(update={"region": "eu-west-1"})
    with pytest.raises(InfrastructureMigrationError, match="another AWS"):
        prepare_boundary(
            aws=aws,
            config=wrong_config,
            boundary=MigrationBoundary.APPLICATION_STACK,
            state_path=state_path,
        )


def test_history_rejects_environment_stack_and_gap() -> None:
    definition = MIGRATIONS[0].definition
    config = production_config()
    wrong_environment = applied_record(checksum=definition.checksum).model_copy(
        update={"region": "eu-west-1"},
    )
    with pytest.raises(InfrastructureMigrationError, match="another AWS"):
        validate_migration_history(applied=[wrong_environment], config=config)
    wrong_stack = applied_record(checksum=definition.checksum).model_copy(
        update={"stack_name": "another-stack"},
    )
    with pytest.raises(InfrastructureMigrationError, match="stack-name"):
        validate_migration_history(applied=[wrong_stack], config=config)
    duplicate = applied_record(checksum=definition.checksum)
    with pytest.raises(InfrastructureMigrationError, match="contiguous prefix"):
        validate_migration_history(
            applied=[duplicate, duplicate],
            config=config,
        )


def test_collector_split_rejects_invalid_contracts_and_outputs(
    tmp_path: Path,
) -> None:
    del tmp_path
    config = production_config()
    aws = FakeInfrastructureAws()
    migration = MIGRATIONS[0]
    with pytest.raises(ValidationError, match="supplied together"):
        collector_split.CollectorSplitContext(
            fetch_queue_url=FETCH_URL,
            fetch_queue_arn=None,
        )
    aws.outputs = {
        "IngestionQueueUrl": FETCH_URL,
        "FetchQueueUrl": FETCH_URL,
    }
    with pytest.raises(RuntimeError, match="both legacy and upgraded"):
        migration.prepare(aws=aws, config=config)
    aws.outputs = upgraded_outputs() | {"CollectedPayloadPrefix": "wrong"}
    aws.active_sources.clear()
    aws.policy_reconciled = True
    with pytest.raises(RuntimeError, match="payload-prefix"):
        migration.verify(aws=aws, config=config)
    aws.outputs = {"FetchQueueUrl": FETCH_URL}
    with pytest.raises(RuntimeError, match="missing outputs"):
        migration.verify(aws=aws, config=config)
    aws.outputs = {}
    assert migration.prepare(aws=aws, config=config).startswith(
        '{"fetch_queue_url"',
    )


def test_failure_guard_is_noop_for_absent_application_stack() -> None:
    aws = FakeInfrastructureAws()
    aws.application_exists = False
    secure_failed_deployment(aws=aws, config=production_config())
    assert aws.active_sources == {FETCH_ARN}


def test_reconcile_requires_collector_role_output() -> None:
    aws = FakeInfrastructureAws()
    aws.outputs = {}
    with pytest.raises(RuntimeError, match="CollectorRoleArn"):
        reconcile_collector_source_user(
            aws=aws,
            config=production_config(),
        )
