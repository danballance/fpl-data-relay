"""Environment-backed runtime configuration for the relay."""

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, PositiveFloat, PositiveInt
from pydantic_settings import BaseSettings, SettingsConfigDict

POSTGRES_MAINTENANCE_DATABASE_URL = "POSTGRES_MAINTENANCE_DATABASE_URL"

REQUIRED_ENV_VARS = [
    "DATABASE_EXECUTOR",
    "DATABASE_URL",
    "FPL_API_BASE_URL",
    "FPL_CLIENT_USER_AGENT",
    "HTTP_TIMEOUT_SECONDS",
    "REFERENCE_POLL_SECONDS",
    "LIVE_POLL_SECONDS",
    "IDLE_POLL_SECONDS",
]

RDS_DATA_REQUIRED_ENV_VARS = [
    "DATABASE_EXECUTOR",
    "DATABASE_RESOURCE_ARN",
    "DATABASE_SECRET_ARN",
    "DATABASE_NAME",
]

FPL_REQUIRED_ENV_VARS = [
    "FPL_API_BASE_URL",
    "FPL_CLIENT_USER_AGENT",
    "HTTP_TIMEOUT_SECONDS",
]

COLLECTOR_REQUIRED_ENV_VARS = [
    *FPL_REQUIRED_ENV_VARS,
    "AWS_REGION",
    "FETCH_QUEUE_URL",
    "RESULT_QUEUE_URL",
    "PAYLOAD_BUCKET",
    "PAYLOAD_PREFIX",
    "COLLECTOR_HEARTBEAT_PATH",
]

COMMUNITY_CREDENTIAL_ENV_VARS = [
    "OPENAI_API_KEY",
    "X_BEARER_TOKEN",
    "YOUTUBE_API_KEY",
    "SUPADATA_API_KEY",
]


class Settings(BaseSettings):
    """Validated application settings loaded from explicit environment names."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_executor: Literal["asyncpg"] = Field(
        validation_alias="DATABASE_EXECUTOR",
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


class RdsDataSettings(BaseModel):
    """Explicit Aurora Data API connection settings."""

    model_config = ConfigDict(frozen=True)

    database_executor: Literal["rds_data"]
    resource_arn: str
    secret_arn: str
    database_name: str


class FplSettings(BaseModel):
    """Explicit upstream FPL client settings."""

    model_config = ConfigDict(frozen=True)

    base_url: HttpUrl
    user_agent: str
    timeout_seconds: PositiveFloat


class CollectorSettings(BaseModel):
    """Explicit configuration for the NAS collection worker."""

    model_config = ConfigDict(frozen=True)

    aws_region: str = Field(min_length=1)
    fetch_queue_url: HttpUrl
    result_queue_url: HttpUrl
    payload_bucket: str = Field(min_length=3)
    payload_prefix: str = Field(min_length=1)
    heartbeat_path: str = Field(min_length=1)
    fpl: FplSettings


class CommunityCredentials(BaseModel):
    """Exactly the four credentials consumed by the community worker."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    openai_api_key: str = Field(min_length=1)
    x_bearer_token: str = Field(min_length=1)
    youtube_api_key: str = Field(min_length=1)
    supadata_api_key: str = Field(min_length=1)


def load_settings_from_environment() -> Settings:
    """Load settings after checking that every required variable is present."""
    missing_names = [name for name in REQUIRED_ENV_VARS if name not in os.environ]
    if missing_names:
        joined_names = ", ".join(missing_names)
        raise RuntimeError(f"Missing required environment variables: {joined_names}")
    env_values = {name: os.environ[name] for name in REQUIRED_ENV_VARS}
    return Settings.model_validate(env_values)


def load_rds_data_settings_from_environment() -> RdsDataSettings:
    """Load and validate the Data API connection settings."""
    missing_names = [
        name for name in RDS_DATA_REQUIRED_ENV_VARS if name not in os.environ
    ]
    if missing_names:
        joined_names = ", ".join(missing_names)
        raise RuntimeError(f"Missing required environment variables: {joined_names}")
    env_values = {
        "database_executor": os.environ["DATABASE_EXECUTOR"],
        "resource_arn": os.environ["DATABASE_RESOURCE_ARN"],
        "secret_arn": os.environ["DATABASE_SECRET_ARN"],
        "database_name": os.environ["DATABASE_NAME"],
    }
    return RdsDataSettings.model_validate(env_values)


def load_fpl_settings_from_environment() -> FplSettings:
    """Load the settings required by production ingestion."""
    missing_names = [name for name in FPL_REQUIRED_ENV_VARS if name not in os.environ]
    if missing_names:
        joined_names = ", ".join(missing_names)
        raise RuntimeError(f"Missing required environment variables: {joined_names}")
    return FplSettings.model_validate(
        {
            "base_url": os.environ["FPL_API_BASE_URL"],
            "user_agent": os.environ["FPL_CLIENT_USER_AGENT"],
            "timeout_seconds": os.environ["HTTP_TIMEOUT_SECONDS"],
        },
    )


def load_collector_settings_from_environment() -> CollectorSettings:
    """Load every explicit setting required by the NAS collector."""
    missing_names = [
        name for name in COLLECTOR_REQUIRED_ENV_VARS if name not in os.environ
    ]
    if missing_names:
        joined_names = ", ".join(missing_names)
        raise RuntimeError(f"Missing required environment variables: {joined_names}")
    return CollectorSettings.model_validate(
        {
            "aws_region": os.environ["AWS_REGION"],
            "fetch_queue_url": os.environ["FETCH_QUEUE_URL"],
            "result_queue_url": os.environ["RESULT_QUEUE_URL"],
            "payload_bucket": os.environ["PAYLOAD_BUCKET"],
            "payload_prefix": os.environ["PAYLOAD_PREFIX"],
            "heartbeat_path": os.environ["COLLECTOR_HEARTBEAT_PATH"],
            "fpl": {
                "base_url": os.environ["FPL_API_BASE_URL"],
                "user_agent": os.environ["FPL_CLIENT_USER_AGENT"],
                "timeout_seconds": os.environ["HTTP_TIMEOUT_SECONDS"],
            },
        },
    )


def load_postgres_maintenance_database_url_from_environment() -> str:
    """Load the maintenance database URL required by drop-and-create."""
    if POSTGRES_MAINTENANCE_DATABASE_URL not in os.environ:
        raise RuntimeError(
            "Missing required environment variable: "
            f"{POSTGRES_MAINTENANCE_DATABASE_URL}",
        )
    value = os.environ[POSTGRES_MAINTENANCE_DATABASE_URL]
    if value.strip() == "":
        raise RuntimeError(
            "Environment variable must not be empty: "
            f"{POSTGRES_MAINTENANCE_DATABASE_URL}",
        )
    return value


def load_community_credentials_from_environment() -> CommunityCredentials:
    """Load the explicit local equivalents of the production secret keys."""
    missing_names = [
        name for name in COMMUNITY_CREDENTIAL_ENV_VARS if name not in os.environ
    ]
    if missing_names:
        joined_names = ", ".join(missing_names)
        raise RuntimeError(f"Missing required environment variables: {joined_names}")
    return CommunityCredentials.model_validate(
        {
            "openai_api_key": os.environ["OPENAI_API_KEY"],
            "x_bearer_token": os.environ["X_BEARER_TOKEN"],
            "youtube_api_key": os.environ["YOUTUBE_API_KEY"],
            "supadata_api_key": os.environ["SUPADATA_API_KEY"],
        },
    )
