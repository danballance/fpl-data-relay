from typing import cast

import httpx
import pytest
from pydantic import ValidationError

from fpl_data_relay.fpl_client import (
    FplClient,
    UpstreamFetchError,
    model_to_payload,
    response_json,
    validate_model_list,
)
from fpl_data_relay.upstream_models import (
    BootstrapStatic,
    EventStatusResponse,
    Fixture,
)
from tests.conftest import bootstrap_payload, fixture_payload


def test_response_json_fails_for_non_200() -> None:
    response = httpx.Response(status_code=429, text="rate limited")
    with pytest.raises(UpstreamFetchError, match="429"):
        response_json(response=response, path="/bootstrap-static/")


def test_upstream_models_preserve_unknown_fields() -> None:
    model = BootstrapStatic.model_validate(bootstrap_payload(current_ids=[1]))
    dumped = model.model_dump(mode="json")
    event = dumped["events"][0]
    assert event["unknown_event_field"] == "kept"


def test_upstream_models_fail_on_missing_required_core_fields() -> None:
    payload = bootstrap_payload(current_ids=[1])
    del payload["elements"]
    with pytest.raises(ValidationError, match="elements"):
        BootstrapStatic.model_validate(payload)


@pytest.mark.asyncio
async def test_fpl_client_fetches_all_core_documents() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/bootstrap-static/":
            return httpx.Response(200, json=bootstrap_payload(current_ids=[1]))
        if request.url.path == "/api/fixtures/":
            return httpx.Response(
                200,
                json=[fixture_payload(fixture_id=1, started=True, finished=False)],
            )
        if request.url.path == "/api/event-status/":
            return httpx.Response(
                200,
                json={
                    "status": [
                        {
                            "event": 1,
                            "bonus_added": False,
                            "date": "2026-06-20",
                            "leagues_updated": False,
                        },
                    ],
                },
            )
        if request.url.path == "/api/event/1/live/":
            return httpx.Response(
                200,
                json={
                    "elements": [
                        {
                            "id": 1,
                            "stats": {"total_points": 2},
                            "explain": [
                                {
                                    "fixture": 1,
                                    "stats": [
                                        {"identifier": "minutes", "points": 2},
                                    ],
                                },
                            ],
                        },
                    ],
                },
            )
        return httpx.Response(404, text="missing")

    client = FplClient(
        base_url="https://fantasy.premierleague.com/api",
        user_agent="tests",
        timeout_seconds=10,
    )
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://fantasy.premierleague.com/api",
    )
    try:
        bootstrap = await client.fetch_bootstrap_static()
        fixtures = await client.fetch_fixtures()
        current_fixtures = await client.fetch_current_fixtures(event_id=1)
        event_status = await client.fetch_event_status()
        event_live = await client.fetch_event_live(event_id=1)
    finally:
        await client.close()
    assert bootstrap.events[0].id == 1
    assert fixtures[0].started is True
    assert current_fixtures[0].id == 1
    assert event_status.status[0].event == 1
    assert event_live.elements[0].stats.total_points == 2


def test_validate_model_list_rejects_non_list_payload() -> None:
    with pytest.raises(ValidationError):
        validate_model_list(model=Fixture, payload={"id": 1})


def test_event_status_accepts_current_upstream_shape() -> None:
    status = EventStatusResponse.model_validate(
        {
            "status": [
                {
                    "event": 38,
                    "bonus_added": True,
                    "date": "2026-06-20",
                    "points": "r",
                },
            ],
        },
    )
    dumped = status.model_dump(mode="json")
    assert dumped["status"][0]["points"] == "r"


def test_model_to_payload_handles_model_and_model_lists() -> None:
    status = EventStatusResponse.model_validate(
        {
            "status": [
                {
                    "event": 1,
                    "bonus_added": False,
                    "date": "2026-06-20",
                    "leagues_updated": False,
                },
            ],
        },
    )
    fixtures = [
        Fixture.model_validate(
            fixture_payload(fixture_id=1, started=False, finished=False),
        ),
    ]
    status_payload = cast("dict[str, object]", model_to_payload(model=status))
    fixture_payloads = cast("list[dict[str, object]]", model_to_payload(model=fixtures))
    assert status_payload["status"] == [
        {
            "event": 1,
            "bonus_added": False,
            "date": "2026-06-20",
            "leagues_updated": False,
        },
    ]
    assert fixture_payloads[0]["id"] == 1
