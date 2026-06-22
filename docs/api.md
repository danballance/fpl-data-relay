# API

All REST endpoints serve the latest normalised data already stored in Postgres.
The relay does not proxy upstream requests on demand.

## Health

- `GET /healthz`

Returns service liveness and the schema version expected by the running app.

## Reference Entities

Reference entities are populated by:

```fish
uv run fpl-relay ingest reference
```

Endpoints:

- `GET /v1/events`
- `GET /v1/events/current`
- `GET /v1/events/{event_id}`
- `GET /v1/phases`
- `GET /v1/teams`
- `GET /v1/teams/{team_id}`
- `GET /v1/element-types`
- `GET /v1/elements`
- `GET /v1/elements/{element_id}`
- `GET /v1/fixtures`
- `GET /v1/events/{event_id}/fixtures`

## Live Entities

Live entities are populated by:

```fish
uv run fpl-relay ingest live
```

By default, live ingestion uses the current event from stored reference data.
You can target a specific event/gameweek, or resolve the event through a stored
fixture:

```fish
uv run fpl-relay ingest live --target-id 5
uv run fpl-relay ingest live --fixture-id 123
```

Endpoints:

- `GET /v1/event-status`
- `GET /v1/events/{event_id}/live-elements`
- `GET /v1/events/{event_id}/live-elements/{element_id}`

## Change Events

- `GET /v1/change-events?after_id=0&limit=100`
- `GET /v1/stream`

Change events describe the normalised entity families that changed during an
ingestion cycle. Event payloads include fields such as:

- `id`
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
