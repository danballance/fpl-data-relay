import pytest
from pydantic import ValidationError

from fpl_data_relay.config import Settings


def valid_settings_payload() -> dict[str, object]:
    return {
        "DATABASE_URL": "postgresql://relay:relay@localhost:5432/relay",
        "FPL_API_BASE_URL": "https://fantasy.premierleague.com/api",
        "FPL_CLIENT_USER_AGENT": "fpl-data-relay-tests",
        "HTTP_TIMEOUT_SECONDS": 10,
        "REFERENCE_POLL_SECONDS": 300,
        "LIVE_POLL_SECONDS": 15,
        "IDLE_POLL_SECONDS": 120,
        "SSE_HEARTBEAT_SECONDS": 5,
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


def test_settings_accept_required_values() -> None:
    settings = Settings.model_validate(valid_settings_payload())
    assert settings.database_url.startswith("postgresql://")
    assert settings.live_poll_seconds == 15

