from collections.abc import Sequence
from typing import cast

import httpx
from pydantic import BaseModel, ValidationError

from fpl_data_relay.json_types import JsonValue
from fpl_data_relay.upstream_models import (
    BootstrapStatic,
    EventLiveResponse,
    EventStatusResponse,
    Fixture,
)


class UpstreamFetchError(RuntimeError):
    pass


class FplClient:
    def __init__(
        self,
        *,
        base_url: str,
        user_agent: str,
        timeout_seconds: float,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"User-Agent": user_agent},
            timeout=timeout_seconds,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch_bootstrap_static(self) -> BootstrapStatic:
        payload = await self._fetch_json(path="/bootstrap-static/")
        return BootstrapStatic.model_validate(payload)

    async def fetch_fixtures(self) -> list[Fixture]:
        payload = await self._fetch_json(path="/fixtures/")
        return validate_model_list(model=Fixture, payload=payload)

    async def fetch_current_fixtures(self, *, event_id: int) -> list[Fixture]:
        payload = await self._fetch_json_with_params(
            path="/fixtures/",
            params={"event": event_id},
        )
        return validate_model_list(model=Fixture, payload=payload)

    async def fetch_event_status(self) -> EventStatusResponse:
        payload = await self._fetch_json(path="/event-status/")
        return EventStatusResponse.model_validate(payload)

    async def fetch_event_live(self, *, event_id: int) -> EventLiveResponse:
        payload = await self._fetch_json(path=f"/event/{event_id}/live/")
        return EventLiveResponse.model_validate(payload)

    async def _fetch_json(self, *, path: str) -> JsonValue:
        response = await self._client.get(path)
        return response_json(response=response, path=path)

    async def _fetch_json_with_params(
        self,
        *,
        path: str,
        params: dict[str, int],
    ) -> JsonValue:
        response = await self._client.get(path, params=params)
        return response_json(response=response, path=path)


def response_json(*, response: httpx.Response, path: str) -> JsonValue:
    if response.status_code != httpx.codes.OK:
        snippet = response.text[:200]
        message = f"FPL request failed for {path}: {response.status_code} {snippet}"
        raise UpstreamFetchError(message)
    payload = response.json()
    return cast("JsonValue", payload)


def validate_model_list[ModelT: BaseModel](
    *,
    model: type[ModelT],
    payload: JsonValue,
) -> list[ModelT]:
    if not isinstance(payload, list):
        raise ValidationError.from_exception_data(
            title=f"{model.__name__}List",
            line_errors=[
                {
                    "type": "list_type",
                    "loc": (),
                    "input": payload,
                    "ctx": {},
                },
            ],
        )
    return [model.model_validate(item) for item in payload]


def model_to_payload(*, model: BaseModel | Sequence[BaseModel]) -> JsonValue:
    if isinstance(model, BaseModel):
        return cast("JsonValue", model.model_dump(mode="json"))
    return [cast("JsonValue", item.model_dump(mode="json")) for item in model]
