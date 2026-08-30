from pathlib import Path

import pytest
from pydantic import ValidationError

from fpl_data_relay.config import (
    CollectorSettings,
    Settings,
    load_admin_settings,
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


def test_load_admin_settings_requires_one_strict_explicit_file(
    tmp_path: Path,
) -> None:
    config = tmp_path / "admin.env"
    config.write_text(
        "\n".join(
            [
                "# non-secret production administration",
                "FPL_ADMIN_AWS_PROFILE=admin",
                "FPL_ADMIN_AWS_REGION=eu-west-2",
                "FPL_ADMIN_DATA_STACK_NAME=data",
                "FPL_ADMIN_APP_STACK_NAME=app",
                "FPL_ADMIN_NAS_SSH_TARGET=relay@nas",
                "FPL_ADMIN_NAS_STACK_DIRECTORY=/stack",
                "FPL_ADMIN_NAS_COMPOSE_EXECUTABLE=/compose",
                "FPL_ADMIN_NAS_DOCKER_EXECUTABLE=/docker",
                "FPL_ADMIN_NAS_SSH_CONNECT_TIMEOUT_SECONDS=10",
                "FPL_ADMIN_DRAIN_TIMEOUT_SECONDS=20",
                "FPL_ADMIN_DRAIN_POLL_SECONDS=2",
                "FPL_ADMIN_DRAIN_STABLE_SECONDS=4",
                "FPL_ADMIN_NAS_HEALTH_ATTEMPTS=2",
                "FPL_ADMIN_NAS_HEALTH_INTERVAL_SECONDS=1",
                "FPL_ADMIN_NAS_LOG_TAIL_LINES=10",
                "",
            ],
        ),
    )
    loaded = load_admin_settings(path=config)
    assert loaded.aws_profile == "admin"
    assert loaded.nas_stack_directory == "/stack"
    config.write_text(
        config.read_text() + "FPL_ADMIN_UNKNOWN=value\n",
    )
    with pytest.raises(ValidationError, match="unknown"):
        load_admin_settings(path=config)
    with pytest.raises(RuntimeError, match="does not exist"):
        load_admin_settings(path=tmp_path / "missing")


def test_load_admin_settings_rejects_impossible_drain_timings(
    tmp_path: Path,
) -> None:
    config = tmp_path / "admin.env"
    config.write_text(
        Path(".admin.env.example")
        .read_text()
        .replace(
            "FPL_ADMIN_DRAIN_STABLE_SECONDS=120",
            "FPL_ADMIN_DRAIN_STABLE_SECONDS=7200",
        ),
    )
    with pytest.raises(ValidationError, match="stable period"):
        load_admin_settings(path=config)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("INVALID", "Invalid administration config line"),
        ("FPL_ADMIN_A=1\nFPL_ADMIN_A=2", "Duplicate administration config key"),
        ("WRONG_KEY=value", "Unexpected administration config keys"),
    ],
)
def test_load_admin_settings_rejects_malformed_files(
    tmp_path: Path,
    content: str,
    message: str,
) -> None:
    config = tmp_path / "admin.env"
    config.write_text(content)
    with pytest.raises(RuntimeError, match=message):
        load_admin_settings(path=config)
