# Normalised FPL Schema Plan

## Goal

Rework FPL Data Relay from an endpoint/blob cache into a latest-state,
entity-based relay built around the FPL schema described by
`.tasks/fpl-public-api.trimmed.openapi.yaml`.

The service should ingest the public FPL endpoints, validate them with Pydantic
models based on the OpenAPI component schemas, store the component entities in
normalised Postgres tables, and expose normalised/entity-oriented API endpoints.

## Decisions

- The target schema is `.tasks/fpl-public-api.trimmed.openapi.yaml`.
- `.tasks/fpl-public-api.openapi.yaml` is a larger reference schema and may be
  used to fill gaps in the trimmed schema.
- This service stores latest state only. No historical snapshots or time-series
  tables are required.
- Do not store upstream endpoint responses as opaque JSON blobs.
- Do not preserve unknown/additional properties generically.
- If upstream payloads contain unexpected fields, log them clearly and skip
  inserting those fields.
- No backwards compatibility is required; this is an alpha project.
- Prefer fail-fast behaviour for required fields and schema inconsistencies.
- Use explicit Pydantic models for FPL types.
- Keep ingestion metadata/change notifications separate from FPL entity storage.

## Current State Summary

Current persistence is built around `relay_resources`:

- `resource_key`
- `event_id`
- `payload jsonb`
- `payload_hash`
- timestamps

This stores large upstream responses such as `bootstrap-static` and
`event-live` as whole JSON documents. That prevents relational integrity,
entity-level updates, and normalised API access.

The intended replacement is a schema centred on FPL entities such as events,
teams, elements, fixtures, live element stats, and event status days.

## OpenAPI Preparation

The trimmed OpenAPI schema currently targets these paths:

- `/bootstrap-static/`
- `/fixtures/`
- `/event-status/`
- `/event/{event_id}/live/`

Before implementation, make the trimmed schema self-contained.

- [ ] Compare `.tasks/fpl-public-api.trimmed.openapi.yaml` against the larger
      `.tasks/fpl-public-api.openapi.yaml`.
- [ ] Add the missing `ElementType` component schema to the trimmed schema.
- [ ] Decide whether unused entry/element-summary schemas should remain in the
      trimmed file.
- [ ] Remove or complete references to schemas that are outside the trimmed
      endpoint target, especially `EntryEventHistory` if entry endpoints remain
      excluded.
- [ ] Confirm there are no unresolved `$ref` entries in the trimmed schema.
- [ ] Treat the trimmed OpenAPI file as the source of truth for relay models.

## Target Pydantic Model Structure

Create explicit Pydantic models around OpenAPI component schemas, not around
raw HTTP endpoint blobs.

Suggested module shape:

- `fpl_data_relay/fpl_models.py`
  - reusable FPL entity/component models
- `fpl_data_relay/fpl_responses.py`, if useful
  - endpoint response aggregate models composed from entity models

### Component Models

- [ ] Define `Event`.
- [ ] Define `Phase`.
- [ ] Define `Team`.
- [ ] Define `ElementType`.
- [ ] Define `ElementStatDefinition`.
- [ ] Define `Element`.
- [ ] Define `Fixture`.
- [ ] Define `FixtureStat`.
- [ ] Define `FixtureStatEntry`.
- [ ] Define `EventStatusDay`.
- [ ] Define `LiveElement`.
- [ ] Define `LiveElementStats`.
- [ ] Define `LiveElementExplain`.
- [ ] Define `LiveElementExplainStat`.

### Aggregate Response Models

These models are useful at the HTTP client boundary and for validating upstream
responses, but they should not define database table boundaries.

- [ ] Define `BootstrapStatic` as an aggregate of events, phases, teams,
      element types, element stat definitions, and elements.
- [ ] Define `EventStatusResponse` as an aggregate of event status rows and
      response-level status fields.
- [ ] Define `EventLiveResponse` as an aggregate of live element rows.
- [ ] Keep fixture responses as `list[Fixture]`.

### Unknown Field Handling

- [ ] Configure model validation so unexpected fields are detected.
- [ ] Log unexpected fields with enough context to identify the endpoint,
      parent model, entity id if available, and field names.
- [ ] Do not insert unexpected fields into generic JSONB columns.
- [ ] Continue processing valid known fields after logging unexpected optional
      fields.
- [ ] Fail fast if required model fields are missing or invalid.

Implementation detail to decide during coding: this may be easiest with a small
validation helper that compares raw payload keys with each model's declared
fields before calling `model_validate` with an `extra` policy that ignores those
fields after logging.

## Target Database Schema

Use schema version 2 and replace blob storage with normalised latest-state
tables. Table and column names can be refined during implementation, but this is
the intended shape.

### Bootstrap/Reference Tables

- [ ] `fpl_events`
  - primary key: `id`
  - stores gameweek/event metadata
- [ ] `fpl_phases`
  - primary key: `id`
- [ ] `fpl_teams`
  - primary key: `id`
- [ ] `fpl_element_types`
  - primary key: `id`
- [ ] `fpl_element_stat_definitions`
  - primary key: stat `name` or generated id, to be decided from schema
- [ ] `fpl_elements`
  - primary key: `id`
  - foreign keys to `fpl_teams(id)` and `fpl_element_types(id)`

### Fixture Tables

- [ ] `fpl_fixtures`
  - primary key: `id`
  - foreign keys to home/away teams and nullable event id
- [ ] `fpl_fixture_stat_entries`
  - stores fixture stat entries for home/away side
  - foreign key to `fpl_fixtures(id)`
  - includes stat identifier, side, element id when present, and value

The full fixtures endpoint and current-event fixtures endpoint should update the
same fixture tables. Do not store separate `fixtures` and `current_fixtures`
blobs.

### Event Status Tables

- [ ] `fpl_event_status_days`
  - primary key: event id or event/date pair, depending on confirmed payload
  - stores `bonus_added`, `date`, `leagues_updated`, and related fields
- [ ] `fpl_event_status`
  - stores latest response-level fields such as `leagues`, if needed

### Live Event Tables

- [ ] `fpl_event_live_elements`
  - primary key: `(event_id, element_id)`
  - stores live aggregate player stats for one gameweek
- [ ] `fpl_event_live_explain_stats`
  - foreign key to `(event_id, element_id)` live rows
  - includes fixture id, stat identifier, points, and value

### Relay Metadata Tables

Keep metadata separate from FPL entities.

- [ ] Keep or replace `relay_schema_version` for schema version tracking.
- [ ] Keep a store-level advisory lock for ingestion exclusivity.
- [ ] Replace `relay_resources` with ingestion-source metadata, e.g.
      `relay_ingestion_sources` or `relay_ingestion_runs_latest`.
- [ ] Store latest `payload_hash`, `fetched_at`, `checked_at`, source endpoint,
      and optional event id per ingestion source.
- [ ] Keep `relay_change_events`, but change events should describe logical
      entity/source changes rather than opaque resource payload replacement.
- [ ] Include enough metadata in change events for API clients to know which
      entity family changed.

## Change Detection

Latest-state only does not need historical row versions, but change detection is
still useful for SSE and polling clients.

- [ ] Compute canonical hashes for source payloads at ingestion-source level.
- [ ] Compute row-level hashes for entity rows where useful.
- [ ] Upsert changed rows only.
- [ ] Update `checked_at` metadata even when no data changed.
- [ ] Emit change events when an entity family changes.
- [ ] Avoid emitting a change event when only `checked_at` changed.

Suggested entity-family change events:

- `events.updated`
- `phases.updated`
- `teams.updated`
- `element_types.updated`
- `element_stats.updated`
- `elements.updated`
- `fixtures.updated`
- `event_status.updated`
- `event_live.updated`

## Repository/Store Interface Plan

Replace the generic `ResourceStore` interface with explicit repository methods.

- [ ] Define repository protocols around FPL entities rather than resources.
- [ ] Add bootstrap/reference upsert method:
      `upsert_bootstrap(bootstrap: BootstrapStatic, metadata: IngestionMetadata)`.
- [ ] Add fixture upsert method:
      `upsert_fixtures(fixtures: list[Fixture], metadata: IngestionMetadata)`.
- [ ] Add event status upsert method:
      `upsert_event_status(status: EventStatusResponse, metadata: IngestionMetadata)`.
- [ ] Add live event upsert method:
      `upsert_event_live(event_id: int, live: EventLiveResponse, metadata: IngestionMetadata)`.
- [ ] Add read methods for normalised API endpoints.
- [ ] Keep schema application, schema version checking, ingestion locking,
      change-event listing, change-event watching, and close methods.

## Ingestion Flow Plan

### Reference Ingestion

- [ ] Fetch `/bootstrap-static/`.
- [ ] Validate into `BootstrapStatic`.
- [ ] Log and ignore unexpected fields.
- [ ] Split into reference entity rows.
- [ ] Upsert events, phases, teams, element types, element stat definitions, and
      elements.
- [ ] Fetch `/fixtures/`.
- [ ] Validate into `list[Fixture]`.
- [ ] Log and ignore unexpected fields.
- [ ] Upsert fixtures and fixture stat entries.
- [ ] Determine current event from `fpl_events`.

### Live Ingestion

- [ ] Read the current event from `fpl_events`.
- [ ] Fail fast if reference data has not been ingested.
- [ ] Fetch `/event-status/`.
- [ ] Validate into `EventStatusResponse`.
- [ ] Upsert event status tables.
- [ ] Fetch `/fixtures/?event={current_event_id}`.
- [ ] Validate into `list[Fixture]`.
- [ ] Upsert into the same fixture tables used by full fixture ingestion.
- [ ] Fetch `/event/{current_event_id}/live/`.
- [ ] Validate into `EventLiveResponse`.
- [ ] Upsert live element and explain-stat tables keyed by current event id.
- [ ] Determine whether there is an active fixture from normalised fixture rows.

## Normalised API Plan

Expose entity-oriented endpoints instead of upstream-shaped blob endpoints.
Exact route names can be refined, but the API should be based around stored FPL
entities.

Suggested endpoints:

- [ ] `GET /v1/events`
- [ ] `GET /v1/events/current`
- [ ] `GET /v1/events/{event_id}`
- [ ] `GET /v1/phases`
- [ ] `GET /v1/teams`
- [ ] `GET /v1/teams/{team_id}`
- [ ] `GET /v1/element-types`
- [ ] `GET /v1/elements`
- [ ] `GET /v1/elements/{element_id}`
- [ ] `GET /v1/fixtures`
- [ ] `GET /v1/events/{event_id}/fixtures`
- [ ] `GET /v1/event-status`
- [ ] `GET /v1/events/{event_id}/live-elements`
- [ ] `GET /v1/events/{event_id}/live-elements/{element_id}`
- [ ] `GET /v1/change-events?after_id=0&limit=100`
- [ ] `GET /v1/stream`

Response models should be typed Pydantic models/schemas representing relay
entities, not arbitrary upstream JSON.

## Migration Strategy

Because this is alpha, prefer a clean schema replacement over compatibility
migrations.

- [ ] Create schema version 2 SQL.
- [ ] Drop or stop creating `relay_resources`.
- [ ] Replace tests that expect resource blobs.
- [ ] Keep schema version validation fail-fast.
- [ ] Document that existing v1 databases should be recreated or manually
      migrated before running schema version 2.

## Test Plan

- [ ] Add tests that the trimmed OpenAPI schema has no unresolved refs.
- [ ] Add Pydantic validation tests for representative bootstrap payloads.
- [ ] Add Pydantic validation tests for representative fixture payloads.
- [ ] Add Pydantic validation tests for event status payloads.
- [ ] Add Pydantic validation tests for event live payloads.
- [ ] Add tests that unknown fields are logged and ignored.
- [ ] Add tests that missing required fields fail fast.
- [ ] Add store tests for each normalised table family.
- [ ] Add tests that full fixtures and current fixtures update the same rows.
- [ ] Add tests that event live data is keyed by `(event_id, element_id)`.
- [ ] Add ingestion tests proving responses are split into component tables.
- [ ] Add API tests for the normalised endpoints.
- [ ] Keep `uv run ruff check` passing.
- [ ] Keep `uv run ty check` passing.
- [ ] Keep `uv run pytest --cov ./fpl_data_relay/ tests` above 90% coverage.

## Suggested Implementation Order

- [ ] Step 1: Make the trimmed OpenAPI schema self-contained and verify refs.
- [ ] Step 2: Add/replace Pydantic FPL component and response models.
- [ ] Step 3: Add unknown-field logging validation helpers.
- [ ] Step 4: Design and apply schema version 2 SQL for normalised tables.
- [ ] Step 5: Replace generic resource-store methods with entity repositories.
- [ ] Step 6: Rewrite reference ingestion to split bootstrap and fixtures.
- [ ] Step 7: Rewrite live ingestion to update event status, fixtures, and live
      element tables.
- [ ] Step 8: Replace blob REST endpoints with normalised entity endpoints.
- [ ] Step 9: Update SSE/change-event semantics for entity-family changes.
- [ ] Step 10: Update docs and command examples to describe the normalised API.
- [ ] Step 11: Run and fix all checks.

## Open Questions To Resolve Before Coding

- [ ] Should `element_stats` use `name` as the primary key, or should we keep a
      generated identity because the upstream object has no documented id?
- [ ] Should fixture stat entry values be stored as text plus type metadata, or
      as typed nullable numeric/string/boolean columns?
- [ ] What exact payload should change-event SSE data contain for entity-family
      changes?
- [ ] Should normalised list endpoints include pagination immediately, or is it
      acceptable to return complete latest-state tables for the alpha?
