# CLI database and ingestion command plan

## Goals

- Make the CLI explicit around database lifecycle and ingestion mode.
- Remove the combined `ingest-once` command.
- Keep destructive database behaviour simple: support `drop`, then re-create with `apply`.
- Allow live ingestion to target either:
  - the current FPL event/gameweek by default,
  - an explicit target event id,
  - or an existing fixture id that resolves to its event id.

## Non-goals

- Do not implement `db truncate`.
- Do not keep backwards compatibility for old flat CLI command names.
- Do not add fallback behaviour for database drop. If required configuration is absent, fail fast.
- Do not attempt to fetch FPL live data by fixture directly. FPL live data is event/gameweek scoped.

## Proposed CLI structure

Use grouped Typer commands:

```bash
uv run fpl-data-relay config-check
uv run fpl-data-relay db apply
uv run fpl-data-relay db drop --yes
uv run fpl-data-relay ingest reference
uv run fpl-data-relay ingest live
uv run fpl-data-relay ingest live --target-id 5
uv run fpl-data-relay ingest live --fixture-id 123
uv run fpl-data-relay serve
```

## Database commands

### `db apply`

Applies the application schema to the configured application database and verifies `SCHEMA_VERSION`.

Expected behaviour:

1. Load required runtime settings.
2. Build the normal app `PostgresStore`.
3. Run `apply_schema()`.
4. Run `check_schema_version(expected_version=SCHEMA_VERSION)`.
5. Print a concise success message.
6. Close the store.

### `db drop --yes`

Drops the configured application database.

Because PostgreSQL cannot drop the database currently being used by the connection, this command must not use the normal app store pool. It should connect to a separate maintenance database.

Proposed new required setting for this command only:

```env
POSTGRES_MAINTENANCE_DATABASE_URL=postgresql://relay:relay@postgres:5432/postgres
```

Expected behaviour:

1. Require `--yes`; fail fast without it.
2. Load settings, including `POSTGRES_MAINTENANCE_DATABASE_URL`.
3. Parse the application database name from `DATABASE_URL`.
4. Connect to the maintenance database.
5. Terminate active connections to the target app database.
6. Drop the target app database.
7. Print a concise success message.
8. Close the maintenance connection.

Safety rules:

- `--yes` is mandatory.
- If the maintenance DSN is missing, fail fast.
- If the target database cannot be parsed from `DATABASE_URL`, fail fast.
- Do not silently switch to another database or derive a maintenance DSN.

## Ingestion commands

### `ingest reference`

Runs only reference ingestion.

Expected behaviour:

1. Load settings.
2. Build the app store and ingestion service.
3. Verify schema version.
4. Run `IngestionService.ingest_reference_once()`.
5. Print changed/unchanged counts and current event id.
6. Close service and store.

Reference ingestion should continue to fetch and store:

- bootstrap static data,
- full fixture list.

### `ingest live`

Runs only live ingestion.

Default behaviour:

- If no targeting option is provided, ingest live data for the current FPL event/gameweek resolved from stored bootstrap data.

Targeting options:

```bash
uv run fpl-data-relay ingest live --target-id 5
uv run fpl-data-relay ingest live --fixture-id 123
```

`--target-id`:

- Interpreted as an FPL event/gameweek id.
- Used directly when fetching event-scoped live data.

`--fixture-id`:

- Interpreted as an FPL fixture id already present in the database.
- Resolve the fixture via the store.
- Use the fixture's `event` value as the target event/gameweek id.
- If the fixture does not exist, fail fast with a clear message.
- If the fixture has no event id, fail fast with a clear message.

Mutual exclusivity:

- `--target-id` and `--fixture-id` must not be provided together.
- If both are provided, fail fast before any network or database writes.

Live ingestion should fetch and store:

- event status,
- fixtures for the target event,
- event-live data for the target event.

## Service changes

Extend live ingestion to accept an explicit target selection.

Possible service API:

```python
async def ingest_live_once(
    *,
    target_event_id: int | None,
    fixture_id: int | None,
) -> IngestionResult:
    ...
```

Resolution rules:

1. If both `target_event_id` and `fixture_id` are provided, raise `ValueError`.
2. If `target_event_id` is provided, use it.
3. If `fixture_id` is provided, resolve it through the store and use its `event` id.
4. If neither is provided, use the current event from stored bootstrap data.
5. If no event can be resolved, raise `RuntimeError` with a clear message.

## Store changes

Add a fixture lookup method to the normalised store protocol and Postgres implementation:

```python
async def get_fixture(self, *, fixture_id: int) -> Fixture | None:
    ...
```

This supports `--fixture-id` resolution without embedding SQL in the ingestion service or CLI.

Add a database-drop helper outside `PostgresStore`, because it connects to the maintenance database rather than the app database:

```python
async def drop_database(
    *,
    database_url: str,
    maintenance_database_url: str,
) -> None:
    ...
```

## Config changes

Keep the existing required app settings unchanged for normal commands.

Add a command-specific loader for the maintenance database URL used by `db drop`:

```python
POSTGRES_MAINTENANCE_DATABASE_URL
```

This should be required only for `db drop`, not for serving or ingestion.

## Testing plan

Add or update tests for:

- CLI exposes grouped `db` and `ingest` commands.
- `db apply` applies and verifies schema.
- `db drop` fails without `--yes`.
- `db drop` fails without maintenance DSN.
- `db drop` connects to maintenance DB and drops the parsed target DB.
- `ingest reference` runs only reference ingestion.
- `ingest live` defaults to current event.
- `ingest live --target-id N` uses the provided event id.
- `ingest live --fixture-id N` resolves the fixture event id through the store.
- `ingest live --target-id N --fixture-id M` fails before side effects.

## Implementation order

1. Add the markdown plan.
2. Add command-specific config loading for `POSTGRES_MAINTENANCE_DATABASE_URL`.
3. Add database drop helper.
4. Add `get_fixture()` to the store protocol and Postgres implementation.
5. Extend `IngestionService.ingest_live_once()` with target resolution.
6. Rework CLI into grouped `db` and `ingest` commands.
7. Add unit tests.
8. Run validation:

```bash
uv run ruff check
uv run ty check
uv run pytest --cov ./fpl_data_relay/ tests
```
