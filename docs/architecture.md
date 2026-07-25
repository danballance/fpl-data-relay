# Architecture

The relay is a single hexagonal modular monolith. It has one FPL relay domain
and one deployable process, with explicit dependency direction:

```text
bootstrap → adapters → application → domain
```

- `domain` owns shared Pydantic entities, change-event values, and pure rules.
- `application` owns ingestion and query use cases plus the ports they require.
- `adapters/inbound` contains FastAPI, Typer, and polling-scheduler drivers.
- `adapters/outbound` contains the public FPL HTTP client and PostgreSQL
  persistence/administration implementations.
- `bootstrap.py` is the only production composition root and owns shared client
  and connection-pool lifecycles.

Persistence is exposed to application services through narrow ports:
`IngestionRepository`, `ReferenceRepository`, `LiveRepository`,
`ChangeEventRepository`, `SchemaManager`, and `DatabaseRecreator`. The
PostgreSQL adapters share a single pool, but inbound adapters cannot access that
pool or the persistence engine.

The architecture boundary is executable:

```bash
uv run lint-imports
```

Run all quality gates with:

```bash
uv run ruff check
uv run ty check
uv run lint-imports
uv run python -m pytest --cov ./src/fpl_data_relay tests
```
