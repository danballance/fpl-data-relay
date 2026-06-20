# FPL Data Relay

Self-hosted Python relay for public Fantasy Premier League data. The service
fetches core FPL API documents, stores latest state in Postgres, exposes
source-shaped REST endpoints, and streams stored change events over SSE.

## Local Commands

For local development, run commands through uv:

```fish
uv run fpl-relay config-check
uv run fpl-relay db-apply
uv run fpl-relay ingest-once
uv run fpl-relay serve
```

For Dokploy/Compose deployments, run CLI commands inside the `app` service:

```fish
docker compose run --rm app uv run --no-dev fpl-relay db-apply
docker compose run --rm app uv run --no-dev fpl-relay ingest-once
```

See [docs/deployment.md](docs/deployment.md) for production command examples.

## Checks

```fish
uv run ruff check
uv run ty check
uv run pytest --cov ./fpl_data_relay/ tests
```
