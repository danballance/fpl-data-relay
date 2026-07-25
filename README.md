# FPL Data Relay

Self-hosted Python relay for public Fantasy Premier League data. The service
fetches core FPL API documents, validates them with explicit Pydantic models,
stores season-scoped state as normalised Postgres entities, exposes
entity-oriented REST endpoints, and streams stored change events over SSE.

## Local Commands

For local development, run commands through uv:

```fish
uv run fpl-relay config-check
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

For Dokploy/Compose deployments, run CLI commands inside the `app` service:

```fish
docker compose run --rm app uv run --no-dev fpl-relay db apply
docker compose run --rm app uv run --no-dev fpl-relay ingest reference
docker compose run --rm app uv run --no-dev fpl-relay ingest live
```

See [docs/deployment.md](docs/deployment.md) for production command examples.
See [docs/api.md](docs/api.md) for the exposed HTTP API.
See [docs/architecture.md](docs/architecture.md) for package boundaries.

## Checks

```fish
uv run ruff check
uv run ty check
uv run lint-imports
uv run python -m pytest --cov ./src/fpl_data_relay tests
```
