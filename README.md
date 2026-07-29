# FPL Data Relay

Serverless Python relay for public Fantasy Premier League data. The service
fetches core FPL API documents, validates them with explicit Pydantic models,
stores season-scoped state as normalised Postgres entities, exposes
entity-oriented REST endpoints, and exposes stored changes through cursor
pagination.

## Local Commands

For local development, run commands through uv:

```fish
uv run fpl-relay config-check
uv run fpl-relay db status
uv run fpl-relay db apply
uv run fpl-relay ingest reference
uv run fpl-relay ingest live
uv run fpl-relay serve
```

Reference ingestion derives the current season id from FPL event deadlines, for
example `2025-26`. Live ingestion defaults to that current season and its
current FPL event/gameweek. You can target another event in the current season
directly, or resolve the event from a stored fixture:

```fish
uv run fpl-relay ingest live --target-id 5
uv run fpl-relay ingest live --fixture-id 123
```

To reset the app database, drop and recreate it, then apply the schema again:

```fish
uv run fpl-relay db drop-and-create --yes
uv run fpl-relay db apply
```

`db drop-and-create` requires `POSTGRES_MAINTENANCE_DATABASE_URL` so it can
connect to a separate maintenance database before recreating the configured app
database.

Production uses AWS SAM, Lambda, Aurora PostgreSQL Serverless v2 with Data API,
SQS/EventBridge Scheduler, a NAS-hosted upstream collector, and S3/CloudFront in
`eu-west-2`. Local Compose continues to use PostgreSQL 17.7 and `asyncpg`.

For a complete local container environment, copy `.env.example` to the ignored
`.env` file and start both services:

```fish
cp .env.example .env
uv run docker compose up --build
```

Stop the services with `uv run docker compose down`. Do not add `--volumes`
unless the PostgreSQL 17 data is intentionally being discarded.

See [docs/deployment.md](docs/deployment.md) for the complete AWS deployment
runbook.
See [docs/api.md](docs/api.md) for the exposed HTTP API.
See [docs/architecture.md](docs/architecture.md) for package boundaries.
See [docs/client.md](docs/client.md) for the local React data explorer.
See [docs/collector.md](docs/collector.md) for NAS collector deployment and
operations.

## Local Data Explorer

The repository includes a read-only React client under `client/`. It explores
the normalised data already held by this relay and never calls the upstream FPL
API. Run the relay and client in separate terminals:

```fish
uv run fpl-relay serve
```

```fish
set -x RELAY_API_PROXY_TARGET http://127.0.0.1:8000
uv run npm --prefix client run dev
```

## Checks

```fish
uv run ruff check
uv run ty check
uv run lint-imports
uv run python -m pytest --cov ./src/fpl_data_relay tests
uv run npm --prefix client run check
```
