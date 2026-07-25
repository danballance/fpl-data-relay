# Local React Data Explorer

The data explorer is a local, read-only React application for inspecting the
normalised data stored by FPL Data Relay. Browser requests use only the relay's
REST and SSE endpoints. The client does not call the upstream FPL API and is not
included in the production image or Compose services.

## Install

Install the locked frontend dependencies from the repository root:

```fish
uv run npm --prefix client ci
```

## Run Locally

Start the relay in one terminal:

```fish
uv run fpl-relay serve
```

In a second terminal, set the proxy target explicitly and start Vite:

```fish
set -x RELAY_API_PROXY_TARGET http://127.0.0.1:8000
uv run npm --prefix client run dev
```

Vite prints the local explorer URL. Browser requests under `/api` are proxied
to the configured relay target, with `/api` removed before forwarding. This
same-origin arrangement covers ordinary JSON responses and the long-lived
change-event stream without enabling CORS in FastAPI.

Alternatively, create `client/.env.local` from `client/.env.example`. Vite
fails immediately with a clear message when no proxy target is configured.

## Explorer Views

The client provides:

- Relay health, schema version, current season, and current event selectors.
- Curated tables for seasons, events, phases, teams, element types, players,
  fixtures, event status, and live player rows.
- Domain-aware labels that resolve stored team, position, player, and event ids.
- URL-backed search, sorting, pagination, resource filters, and record
  selection.
- Structured record inspection and exact raw JSON response views.
- Stored change-event history followed by the SSE stream from the last seen
  event id. A disconnect is shown explicitly and requires a manual reconnect.

The selectors use only `is_current` markers returned by the relay. If no current
season or event has been ingested, choose one manually; the client does not
silently choose the first item.

## API Contract

The checked-in TypeScript contract at `client/src/api/generated.ts` is generated
from the running relay's `/openapi.json`. Regenerate it after changing the
FastAPI contract:

```fish
set -x RELAY_OPENAPI_URL http://127.0.0.1:8000/openapi.json
uv run npm --prefix client run api:generate
```

Generation fails when the URL is missing, the relay cannot be reached, or the
schema request fails. Normal builds and tests use the checked-in contract and
do not require a running relay.

## Frontend Checks

Run the complete frontend gate:

```fish
uv run npm --prefix client run check
```

It runs strict TypeScript checking, ESLint, Vitest with 90% minimum statement,
branch, function, and line coverage, and the production Vite build. Individual
commands are also available:

```fish
uv run npm --prefix client run typecheck
uv run npm --prefix client run lint
uv run npm --prefix client test
uv run npm --prefix client run test:coverage
uv run npm --prefix client run build
```
