"""AWS Secrets Manager and SQS adapters for community jobs."""

import asyncio
import json
from typing import Protocol

from fpl_data_relay.config import CommunityCredentials


class SecretsManagerClient(Protocol):
    def get_secret_value(self, **parameters: object) -> dict[str, object]: ...


class SqsClient(Protocol):
    def send_message(self, **parameters: object) -> dict[str, object]: ...


async def load_community_credentials_from_secret(
    *,
    client: SecretsManagerClient,
    secret_arn: str,
) -> CommunityCredentials:
    """Load and strictly validate the pre-existing four-key JSON secret."""
    response = await asyncio.to_thread(
        client.get_secret_value,
        SecretId=secret_arn,
    )
    secret_string = response.get("SecretString")
    if not isinstance(secret_string, str):
        raise RuntimeError(
            "Community credential secret must contain SecretString JSON.",
        )
    payload = json.loads(secret_string)
    return CommunityCredentials.model_validate(payload)


class SqsCommunityJobQueue:
    """Send versioned jobs to the worker's own encrypted queue."""

    def __init__(self, *, client: SqsClient, queue_url: str) -> None:
        self._client = client
        self._queue_url = queue_url

    async def send(self, *, message_body: str) -> None:
        await asyncio.to_thread(
            self._client.send_message,
            QueueUrl=self._queue_url,
            MessageBody=message_body,
        )
