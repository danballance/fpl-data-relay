"""Environment-backed runtime configuration for the relay."""

import os

from pydantic import Field, HttpUrl, PositiveFloat, PositiveInt
from pydantic_settings import BaseSettings, SettingsConfigDict

REQUIRED_ENV_VARS = [
    "DATABASE_URL",
    "FPL_API_BASE_URL",
    "FPL_CLIENT_USER_AGENT",
    "HTTP_TIMEOUT_SECONDS",
    "REFERENCE_POLL_SECONDS",
    "LIVE_POLL_SECONDS",
    "IDLE_POLL_SECONDS",
    "SSE_HEARTBEAT_SECONDS",
]


class Settings(BaseSettings):
    """Validated application settings loaded from explicit environment names."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(validation_alias="DATABASE_URL")
    fpl_api_base_url: HttpUrl = Field(validation_alias="FPL_API_BASE_URL")
    fpl_client_user_agent: str = Field(validation_alias="FPL_CLIENT_USER_AGENT")
    http_timeout_seconds: PositiveFloat = Field(validation_alias="HTTP_TIMEOUT_SECONDS")
    reference_poll_seconds: PositiveInt = Field(
        validation_alias="REFERENCE_POLL_SECONDS",
    )
    live_poll_seconds: PositiveInt = Field(validation_alias="LIVE_POLL_SECONDS")
    idle_poll_seconds: PositiveInt = Field(validation_alias="IDLE_POLL_SECONDS")
    sse_heartbeat_seconds: PositiveInt = Field(
        validation_alias="SSE_HEARTBEAT_SECONDS",
    )


def load_settings_from_environment() -> Settings:
    """Load settings after checking that every required variable is present."""
    missing_names = [name for name in REQUIRED_ENV_VARS if name not in os.environ]
    if missing_names:
        joined_names = ", ".join(missing_names)
        raise RuntimeError(f"Missing required environment variables: {joined_names}")
    env_values = {name: os.environ[name] for name in REQUIRED_ENV_VARS}
    return Settings.model_validate(env_values)
