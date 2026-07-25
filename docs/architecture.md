# Architecture

The relay remains a hexagonal modular monolith with dependency direction:

```text
composition roots → adapters → application → domain
```

It has three explicit runtime compositions:

1. Local API: Uvicorn/FastAPI, `asyncpg`, local PostgreSQL 17.7, the in-process
   ingestion scheduler, and Typer administration commands.
2. Production API: module-global FastAPI and Mangum handler, RDS Data API
   persistence, no lifespan handling, no FPL client, and no scheduler.
3. Production ingestion: one strict SQS job per Lambda invocation, FPL client,
   RDS Data API persistence, advisory locking, and EventBridge schedule
   reconciliation; it does not construct the web application.

`DATABASE_EXECUTOR` must be exactly `asyncpg` or `rds_data`. Each executor has
explicit required configuration and there is no fallback between them.

The `domain` package owns Pydantic entities and pure rules. `application` owns
ingestion/query use cases and narrow ports. Inbound adapters contain FastAPI,
Typer, and Lambda entry points. Outbound adapters contain the FPL client,
`asyncpg` engine, Data API connection implementation, migrations, and scheduler
integration.

Both persistence executors run the same ordered migrations and PostgreSQL SQL.
Data API writes batch in chunks of 100, multi-statement writes use
transactions, and bounded parent/child reads avoid N+1 queries. Transaction
advisory locks make concurrent ingestion exit cleanly; payload hashes and
database constraints make repeated SQS delivery idempotent.

The executable dependency rule is:

```fish
uv run --group dev lint-imports
```
