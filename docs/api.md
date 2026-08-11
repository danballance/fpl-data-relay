# API

The FastAPI service returns normalised data already stored in PostgreSQL. It
does not proxy FPL requests on demand. Production is anonymous and public
through CloudFront `/api/*`; API Gateway throttles the default route at five
requests per second with a burst of ten.

The OpenAPI schema is available directly from the API at `/openapi.json`, with
Swagger UI at `/docs` and ReDoc at `/redoc`. Through the Explorer and its
CloudFront/Vite API proxy, use `/api/openapi.json`, `/api/docs`, and
`/api/redoc`. The documentation uses relative schema and server URLs, so
Swagger's **Try it out** requests work through either access path.

## Liveness and readiness

- `GET /healthz` checks only that the application is running. It never accesses
  the database.
- `GET /readyz` checks database access and migration version and returns
  `{"status":"ready","schema_version":2}`.

An Aurora resume returns HTTP 503, `Retry-After: 5`, and:

```json
{
  "code": "database_waking",
  "detail": "The database is waking from idle. Retry shortly.",
  "retry_after_seconds": 5
}
```

Stable `database_unavailable` and `schema_unavailable` codes distinguish other
failures.

## Pagination

Potentially large collections require both `after_id` and `limit`. `limit` must
be from 1 through 200. Stable ascending IDs produce:

```json
{
  "items": [],
  "next_after_id": 123
}
```

Pass `next_after_id` as the next `after_id`; a null cursor means the caller is
caught up. The React client uses pages of 100.

Paginated routes are:

- `GET /v1/seasons/{season_id}/elements`
- `GET /v1/seasons/{season_id}/fixtures`
- `GET /v1/seasons/{season_id}/events/{event_id}/fixtures`
- `GET /v1/seasons/{season_id}/events/{event_id}/live-elements`
- `GET /v1/change-events`
- `GET /v1/change-events/{change_event_id}/entity-changes`

For example:

```text
GET /v1/change-events?after_id=0&limit=100
```

Small bounded collections such as seasons, events, phases, teams, and element
types remain arrays. Individual resource routes remain available for seasons,
events, teams, elements, and live elements.

There is no SSE endpoint, PostgreSQL listener, or `/v1/stream` route. Clients
consume changes with cursor polling.

## Change history and ingestion freshness

`GET /v1/change-events` is the forward cursor used for catch-up polling. Each
item is one accurate family summary with `created_count`, `updated_count`, and
`deleted_count`. Unchanged families do not emit events.

The explorer starts with `GET /v1/change-events/recent?limit=100`. This returns
the newest events first and a `next_before_id`. Older pages are loaded only on
request with:

```text
GET /v1/change-events/history?before_id=123&limit=100
```

Entity-level detail is separately bounded:

```text
GET /v1/change-events/123/entity-changes?after_id=0&limit=100
```

Each entity change contains its stable key, friendly label, operation, and
top-level field changes. Before and after values have an explicit `present`
flag, so an absent property is distinct from a present JSON `null`. Nested
values remain structured JSON values.

`GET /v1/ingestion-status` reports reference and live states, their expected
cadence and stale threshold, last successful check, last actual change, and
the current or next live window. An unchanged successful check advances
freshness without creating a change event.

Entity collections return 503 when their source data has not yet been ingested.
Unknown individual entities return 404. Invalid pagination arguments return
FastAPI validation errors.
