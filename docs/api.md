# API

All REST endpoints serve normalised data already stored in Postgres. The relay
does not proxy upstream requests on demand. FPL upstream ids are season-local,
so entity routes include a `season_id` such as `2025-26`.

## OpenAPI and Interactive Documentation

The application generates its OpenAPI 3.1 contract directly from the FastAPI
routes and Pydantic response models:

- `GET /openapi.json` returns the machine-readable OpenAPI document.
- `GET /docs` opens the interactive Swagger UI.
- `GET /redoc` opens the ReDoc reference documentation.

Swagger UI can execute the finite REST requests against the running relay. It
also documents `GET /v1/stream`, including replay through `Last-Event-ID` and
the `text/event-stream` response, but its request runner is not suitable for
displaying a connection that remains open indefinitely.

## Health

- `GET /healthz`

Returns service liveness and the schema version expected by the running app.

## Reference Entities

Reference entities are populated by:

```fish
uv run fpl-relay ingest reference
```

Endpoints:

- `GET /v1/seasons`
- `GET /v1/seasons/current`
- `GET /v1/seasons/{season_id}`
- `GET /v1/seasons/{season_id}/events`
- `GET /v1/seasons/{season_id}/events/current`
- `GET /v1/seasons/{season_id}/events/{event_id}`
- `GET /v1/seasons/{season_id}/phases`
- `GET /v1/seasons/{season_id}/teams`
- `GET /v1/seasons/{season_id}/teams/{team_id}`
- `GET /v1/seasons/{season_id}/element-types`
- `GET /v1/seasons/{season_id}/elements`
- `GET /v1/seasons/{season_id}/elements/{element_id}`
- `GET /v1/seasons/{season_id}/fixtures`
- `GET /v1/seasons/{season_id}/events/{event_id}/fixtures`

## Live Entities

Live entities are populated by:

```fish
uv run fpl-relay ingest live
```

By default, live ingestion uses the current season and current event from stored
reference data. You can target a specific event/gameweek within the current
season, or resolve the event through a stored fixture:

```fish
uv run fpl-relay ingest live --target-id 5
uv run fpl-relay ingest live --fixture-id 123
```

Endpoints:

- `GET /v1/seasons/{season_id}/event-status`
- `GET /v1/seasons/{season_id}/events/{event_id}/live-elements`
- `GET /v1/seasons/{season_id}/events/{event_id}/live-elements/{element_id}`

## Change Events

- `GET /v1/change-events?after_id=0&limit=100`
- `GET /v1/stream`

Change events describe the normalised entity families that changed during an
ingestion cycle. Event payloads include fields such as:

- `id`
- `season_id`
- `entity_family`
- `event_name`
- `source_key`
- `event_id`
- `payload_hash`
- `fetched_at`
- `created_at`

The SSE stream emits the same change-event metadata. It accepts the standard
`Last-Event-ID` header to replay missed events and sends heartbeat comments
while idle.

## Not Yet Ingested Responses

Entity endpoints return `503` when the relevant data has not been ingested yet.
Missing individual rows, such as an unknown event id or player id, return `404`.
