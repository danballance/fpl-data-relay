# Dokploy Deployment

The intended production shape is one app container and one Postgres container.
Set all Compose/Dokploy variables explicitly, then map the app service to port
`8000` with Dokploy Domains.

The app container receives unprefixed runtime variables from
`docker-compose.yml`. The values are supplied by the following Compose variables:

- `FPL_RELAY_DATABASE_URL`
- `FPL_RELAY_API_BASE_URL`
- `FPL_RELAY_CLIENT_USER_AGENT`
- `FPL_RELAY_HTTP_TIMEOUT_SECONDS`
- `FPL_RELAY_REFERENCE_POLL_SECONDS`
- `FPL_RELAY_LIVE_POLL_SECONDS`
- `FPL_RELAY_IDLE_POLL_SECONDS`
- `FPL_RELAY_SSE_HEARTBEAT_SECONDS`
- `FPL_RELAY_POSTGRES_DB`
- `FPL_RELAY_POSTGRES_USER`
- `FPL_RELAY_POSTGRES_PASSWORD`

Optional, only required when running `db drop-and-create` from the app container:

- `FPL_RELAY_POSTGRES_MAINTENANCE_DATABASE_URL`

Example database URLs:

```env
FPL_RELAY_DATABASE_URL=postgresql://relay:relay@postgres:5432/relay
FPL_RELAY_POSTGRES_MAINTENANCE_DATABASE_URL=postgresql://relay:relay@postgres:5432/postgres
```

## Running CLI Commands In Dokploy

In Dokploy/Compose, run `fpl-relay` from the `app` container so it has the
same image, network, and environment variables as the web service. Do not run
production CLI commands on the Dokploy host unless you have installed the app and
exported the same environment there.

Use a one-off app container for setup and manual ingestion:

```fish
docker compose run --rm app uv run --no-dev fpl-relay config-check
docker compose run --rm app uv run --no-dev fpl-relay db apply
docker compose run --rm app uv run --no-dev fpl-relay ingest reference
docker compose run --rm app uv run --no-dev fpl-relay ingest live
```

If the app container is already running, use `exec` instead:

```fish
docker compose exec app uv run --no-dev fpl-relay config-check
docker compose exec app uv run --no-dev fpl-relay db apply
docker compose exec app uv run --no-dev fpl-relay ingest reference
docker compose exec app uv run --no-dev fpl-relay ingest live
```

For a first deployment, apply the schema and perform an initial reference ingest
before depending on the HTTP service. Run live ingestion afterwards to populate
current event status, current fixtures, and live element state:

```fish
docker compose run --rm app uv run --no-dev fpl-relay db apply
docker compose run --rm app uv run --no-dev fpl-relay ingest reference
docker compose run --rm app uv run --no-dev fpl-relay ingest live
```

## Targeted Live Ingestion

Live data from the upstream FPL API is event/gameweek scoped. The live ingestion
command therefore accepts either an event id directly or a fixture id that is
already stored in the database and can be resolved to its event id:

```fish
docker compose run --rm app uv run --no-dev fpl-relay ingest live --target-id 5
docker compose run --rm app uv run --no-dev fpl-relay ingest live --fixture-id 123
```

If neither option is supplied, the command uses the current event from stored
bootstrap/reference data. `--target-id` and `--fixture-id` are mutually
exclusive.

## Dropping and Recreating the Database

There is intentionally no truncate command. To get a fresh start, drop and
recreate the app database, then apply the schema again.

`db drop-and-create` is destructive and requires `--yes`. It also requires
`POSTGRES_MAINTENANCE_DATABASE_URL` inside the app container, supplied through
`FPL_RELAY_POSTGRES_MAINTENANCE_DATABASE_URL` in Compose/Dokploy.

```fish
docker compose run --rm app uv run --no-dev fpl-relay db drop-and-create --yes
docker compose run --rm app uv run --no-dev fpl-relay db apply
docker compose run --rm app uv run --no-dev fpl-relay ingest reference
docker compose run --rm app uv run --no-dev fpl-relay ingest live
```

The `app` service starts the server automatically with `fpl-relay serve` via
the Dockerfile `CMD`; do not run a second long-lived `serve` process manually.

Run one Uvicorn worker and one Dokploy replica for v1 so the background
scheduler has a single owner.
