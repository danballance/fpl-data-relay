"""Typed HTTP client for the public Fantasy Premier League API."""

from collections.abc import Sequence
from typing import cast

import httpx
from pydantic import BaseModel

from fpl_data_relay.fpl_models import (
    BootstrapStatic,
    EventLiveResponse,
    EventStatusResponse,
    Fixture,
)
from fpl_data_relay.fpl_validation import validate_fpl_model, validate_fpl_model_list
from fpl_data_relay.json_types import JsonValue


class UpstreamFetchError(RuntimeError):
    """Raised when the upstream FPL API returns a non-success response."""

    pass


class FplClient:
    """Fetch and validate the upstream FPL resources used by the relay."""

    def __init__(
        self,
        *,
        base_url: str,
        user_agent: str,
        timeout_seconds: float,
    ) -> None:
        """Create the underlying async HTTP client with explicit settings."""
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"User-Agent": user_agent},
            timeout=timeout_seconds,
        )

    async def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._client.aclose()

    async def fetch_bootstrap_static(self) -> BootstrapStatic:
        """Fetch the season bootstrap document."""
        payload = await self._fetch_json(path="/bootstrap-static/")
        return validate_fpl_model(
            model=BootstrapStatic,
            payload=payload,
            endpoint="/bootstrap-static/",
        )

    async def fetch_fixtures(self) -> list[Fixture]:
        """Fetch all season fixtures."""
        payload = await self._fetch_json(path="/fixtures/")
        return validate_fpl_model_list(
            model=Fixture,
            payload=payload,
            endpoint="/fixtures/",
        )

    async def fetch_current_fixtures(self, *, event_id: int) -> list[Fixture]:
        """Fetch fixtures for the supplied gameweek event id."""
        payload = await self._fetch_json_with_params(
            path="/fixtures/",
            params={"event": event_id},
        )
        return validate_fpl_model_list(
            model=Fixture,
            payload=payload,
            endpoint="/fixtures/?event={event_id}",
        )

    async def fetch_event_status(self) -> EventStatusResponse:
        """Fetch event status metadata from the upstream API."""
        payload = await self._fetch_json(path="/event-status/")
        return validate_fpl_model(
            model=EventStatusResponse,
            payload=payload,
            endpoint="/event-status/",
        )

    async def fetch_event_live(self, *, event_id: int) -> EventLiveResponse:
        """Fetch live player data for the supplied gameweek event id."""
        payload = await self._fetch_json(path=f"/event/{event_id}/live/")
        return validate_fpl_model(
            model=EventLiveResponse,
            payload=payload,
            endpoint="/event/{event_id}/live/",
        )

    async def _fetch_json(self, *, path: str) -> JsonValue:
        """Fetch a JSON document from an upstream path."""
        response = await self._client.get(path)
        return response_json(response=response, path=path)

    async def _fetch_json_with_params(
        self,
        *,
        path: str,
        params: dict[str, int],
    ) -> JsonValue:
        """Fetch a JSON document from an upstream path with query params."""
        response = await self._client.get(path, params=params)
        return response_json(response=response, path=path)


def response_json(*, response: httpx.Response, path: str) -> JsonValue:
    """Decode a successful HTTP response as JSON or raise a fetch error."""
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
    """Compatibility wrapper for validating model lists."""
    return validate_fpl_model_list(
        model=model,
        payload=payload,
        endpoint=model.__name__,
    )


def model_to_payload(*, model: BaseModel | Sequence[BaseModel]) -> JsonValue:
    """Convert validated Pydantic models back to JSON-compatible payloads."""
    if isinstance(model, BaseModel):
        return cast("JsonValue", model.model_dump(mode="json"))
    return [cast("JsonValue", item.model_dump(mode="json")) for item in model]
