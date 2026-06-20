# Dokploy Deployment

The intended production shape is one app container and one Postgres container.
Set all environment variables explicitly in Dokploy, then map the app service to
port `8000` with Dokploy Domains.

Required variables:

- `DATABASE_URL`
- `FPL_API_BASE_URL`
- `FPL_CLIENT_USER_AGENT`
- `HTTP_TIMEOUT_SECONDS`
- `REFERENCE_POLL_SECONDS`
- `LIVE_POLL_SECONDS`
- `IDLE_POLL_SECONDS`
- `SSE_HEARTBEAT_SECONDS`

## Running CLI Commands In Dokploy

In Dokploy/Compose, run `fpl-relay` from the `app` container so it has the same
image, network, and environment variables as the web service. Do not run
production CLI commands on the Dokploy host unless you have installed the app and
exported the same environment there.

Use a one-off app container for setup and manual ingestion:

```fish
docker compose run --rm app uv run --no-dev fpl-relay config-check
docker compose run --rm app uv run --no-dev fpl-relay db-apply
docker compose run --rm app uv run --no-dev fpl-relay ingest-once
```

If the app container is already running, use `exec` instead:

```fish
docker compose exec app uv run --no-dev fpl-relay config-check
docker compose exec app uv run --no-dev fpl-relay db-apply
docker compose exec app uv run --no-dev fpl-relay ingest-once
```

For a first deployment, apply the schema and perform an initial ingest before
depending on the HTTP service:

```fish
docker compose run --rm app uv run --no-dev fpl-relay db-apply
docker compose run --rm app uv run --no-dev fpl-relay ingest-once
```

The `app` service starts the server automatically with `fpl-relay serve` via the
Dockerfile `CMD`; do not run a second long-lived `serve` process manually.

Run one Uvicorn worker and one Dokploy replica for v1 so the background
scheduler has a single owner.
