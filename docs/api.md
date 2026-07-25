# API

The FastAPI service returns normalised data already stored in PostgreSQL. It
does not proxy FPL requests on demand. Production is anonymous and public
through CloudFront `/api/*`; API Gateway throttles the default route at five
requests per second with a burst of ten.

OpenAPI is available at `/openapi.json`, `/docs`, and `/redoc`.

## Liveness and readiness

- `GET /healthz` checks only that the application is running. It never accesses
  the database.
- `GET /readyz` checks database access and migration version and returns
  `{"status":"ready","schema_version":1}`.

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

For example:

```text
GET /v1/change-events?after_id=0&limit=100
```

Small bounded collections such as seasons, events, phases, teams, and element
types remain arrays. Individual resource routes remain available for seasons,
events, teams, elements, and live elements.

There is no SSE endpoint, PostgreSQL listener, or `/v1/stream` route. Clients
consume changes with cursor polling.

Entity collections return 503 when their source data has not yet been ingested.
Unknown individual entities return 404. Invalid pagination arguments return
FastAPI validation errors.
