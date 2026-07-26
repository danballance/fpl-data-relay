import pytest
from pydantic import ValidationError

from fpl_data_relay.config import (
    CollectorSettings,
    Settings,
    load_collector_settings_from_environment,
    load_fpl_settings_from_environment,
    load_postgres_maintenance_database_url_from_environment,
    load_rds_data_settings_from_environment,
    load_settings_from_environment,
)


def valid_settings_payload() -> dict[str, object]:
    return {
        "DATABASE_EXECUTOR": "asyncpg",
        "DATABASE_URL": "postgresql://relay:relay@localhost:5432/relay",
        "FPL_API_BASE_URL": "https://fantasy.premierleague.com/api",
        "FPL_CLIENT_USER_AGENT": "fpl-data-relay-tests",
        "HTTP_TIMEOUT_SECONDS": 10,
        "REFERENCE_POLL_SECONDS": 300,
        "LIVE_POLL_SECONDS": 15,
        "IDLE_POLL_SECONDS": 120,
    }


def test_settings_require_all_values() -> None:
    payload = valid_settings_payload()
    payload.pop("DATABASE_URL")
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        Settings.model_validate(payload)


def test_settings_reject_invalid_intervals() -> None:
    payload = valid_settings_payload()
    payload["LIVE_POLL_SECONDS"] = 0
    with pytest.raises(ValidationError, match="LIVE_POLL_SECONDS"):
        Settings.model_validate(payload)


def test_environment_loader_requires_every_explicit_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in valid_settings_payload():
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        load_settings_from_environment()


def test_environment_loader_returns_validated_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in valid_settings_payload().items():
        monkeypatch.setenv(name, str(value))
    settings = load_settings_from_environment()
    assert settings.database_url.endswith("/relay")
    assert settings.live_poll_seconds == 15


@pytest.mark.parametrize("value", [None, "", "   "])
def test_maintenance_database_url_must_be_present_and_nonempty(
    monkeypatch: pytest.MonkeyPatch,
    value: str | None,
) -> None:
    if value is None:
        monkeypatch.delenv("POSTGRES_MAINTENANCE_DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("POSTGRES_MAINTENANCE_DATABASE_URL", value)
    with pytest.raises(RuntimeError):
        load_postgres_maintenance_database_url_from_environment()


def test_maintenance_database_url_is_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "postgresql://relay:relay@localhost:5432/postgres"
    monkeypatch.setenv("POSTGRES_MAINTENANCE_DATABASE_URL", url)
    assert load_postgres_maintenance_database_url_from_environment() == url


def test_rds_data_environment_loader_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "DATABASE_EXECUTOR": "rds_data",
        "DATABASE_RESOURCE_ARN": "cluster",
        "DATABASE_SECRET_ARN": "secret",
        "DATABASE_NAME": "relay",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    settings = load_rds_data_settings_from_environment()
    assert settings.resource_arn == "cluster"
    monkeypatch.delenv("DATABASE_SECRET_ARN")
    with pytest.raises(RuntimeError, match="DATABASE_SECRET_ARN"):
        load_rds_data_settings_from_environment()


def test_fpl_environment_loader_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "FPL_API_BASE_URL": "https://fantasy.premierleague.com/api",
        "FPL_CLIENT_USER_AGENT": "tests",
        "HTTP_TIMEOUT_SECONDS": "10",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    settings = load_fpl_settings_from_environment()
    assert settings.user_agent == "tests"
    monkeypatch.delenv("FPL_CLIENT_USER_AGENT")
    with pytest.raises(RuntimeError, match="FPL_CLIENT_USER_AGENT"):
        load_fpl_settings_from_environment()


def test_collector_environment_loader_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "FPL_API_BASE_URL": "https://fantasy.premierleague.com/api",
        "FPL_CLIENT_USER_AGENT": "tests",
        "HTTP_TIMEOUT_SECONDS": "10",
        "AWS_REGION": "eu-west-2",
        "FETCH_QUEUE_URL": "https://sqs.eu-west-2.amazonaws.com/1/fetch",
        "RESULT_QUEUE_URL": "https://sqs.eu-west-2.amazonaws.com/1/result",
        "PAYLOAD_BUCKET": "payload-bucket",
        "PAYLOAD_PREFIX": "payloads",
        "COLLECTOR_HEARTBEAT_PATH": "/tmp/heartbeat",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    settings = load_collector_settings_from_environment()
    assert isinstance(settings, CollectorSettings)
    assert settings.aws_region == "eu-west-2"
    monkeypatch.delenv("RESULT_QUEUE_URL")
    with pytest.raises(RuntimeError, match="RESULT_QUEUE_URL"):
        load_collector_settings_from_environment()
