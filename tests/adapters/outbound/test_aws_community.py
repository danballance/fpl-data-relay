import json

import pytest
from pydantic import ValidationError

from fpl_data_relay.adapters.outbound.aws_community import (
    SqsCommunityJobQueue,
    load_community_credentials_from_secret,
)
from fpl_data_relay.config import load_community_credentials_from_environment


class Secrets:
    def __init__(self, *, value: object) -> None:
        self.value = value

    def get_secret_value(self, **parameters: object) -> dict[str, object]:
        assert parameters == {"SecretId": "arn:secret"}
        return {"SecretString": self.value}


class Sqs:
    def __init__(self) -> None:
        self.parameters: dict[str, object] = {}

    def send_message(self, **parameters: object) -> dict[str, object]:
        self.parameters = parameters
        return {"MessageId": "1"}


def secret_payload() -> dict[str, str]:
    return {
        "openai_api_key": "openai",
        "x_bearer_token": "x",
        "youtube_api_key": "youtube",
        "supadata_api_key": "supadata",
    }


@pytest.mark.asyncio
async def test_secret_loader_requires_exact_string_json_contract() -> None:
    credentials = await load_community_credentials_from_secret(
        client=Secrets(value=json.dumps(secret_payload())),
        secret_arn="arn:secret",
    )
    assert credentials.youtube_api_key == "youtube"
    with pytest.raises(RuntimeError, match="SecretString"):
        await load_community_credentials_from_secret(
            client=Secrets(value=1),
            secret_arn="arn:secret",
        )
    invalid = {**secret_payload(), "unexpected": "no"}
    with pytest.raises(ValidationError, match="unexpected"):
        await load_community_credentials_from_secret(
            client=Secrets(value=json.dumps(invalid)),
            secret_arn="arn:secret",
        )


@pytest.mark.asyncio
async def test_sqs_queue_sends_exact_body_to_own_queue() -> None:
    client = Sqs()
    queue = SqsCommunityJobQueue(client=client, queue_url="https://queue")
    await queue.send(message_body='{"version":1}')
    assert client.parameters == {
        "QueueUrl": "https://queue",
        "MessageBody": '{"version":1}',
    }


def test_local_community_credentials_require_every_explicit_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = {
        "OPENAI_API_KEY": "openai",
        "X_BEARER_TOKEN": "x",
        "YOUTUBE_API_KEY": "youtube",
        "SUPADATA_API_KEY": "supadata",
    }
    for name, value in names.items():
        monkeypatch.setenv(name, value)
    assert load_community_credentials_from_environment().x_bearer_token == "x"
    monkeypatch.delenv("X_BEARER_TOKEN")
    with pytest.raises(RuntimeError, match="X_BEARER_TOKEN"):
        load_community_credentials_from_environment()
