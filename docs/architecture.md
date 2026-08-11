# Architecture

The relay remains a hexagonal modular monolith with dependency direction:

```text
composition roots → adapters → application → domain
```

It has four explicit runtime compositions:

1. Local API: Uvicorn/FastAPI, `asyncpg`, local PostgreSQL 17.7, the in-process
   ingestion scheduler, and Typer administration commands.
2. Production API: module-global FastAPI and Mangum handler, RDS Data API
   persistence, no lifespan handling, no FPL client, and no scheduler.
3. Production collection: a NAS container long-polls strict SQS fetch jobs,
   calls FPL, and publishes raw bundles through private S3 and a result queue.
4. Production ingestion: one collected bundle per Lambda invocation, strict
   validation, RDS Data API persistence, advisory locking, and EventBridge
   schedule reconciliation; it has no upstream HTTP client.

Production infrastructure has two independent top-level CloudFormation stacks:

1. The durable data stack owns the VPC, isolated subnets, Aurora cluster and
   writer, managed secret, and monthly cost budget. Aurora deletion protection,
   snapshot policies, stack termination protection, and exported outputs protect
   this boundary.
2. The disposable application stack owns API Gateway, Lambdas, IAM, SQS,
   EventBridge Scheduler, operational SNS and alarms, logs, S3, and CloudFront.
   It imports the database ARN, secret ARN, and database name from the data
   stack. Its single collector-user policy grants the NAS identity direct,
   resource-scoped access to the fetch queue, result queue, and payload prefix.

The stacks are not nested. Application rollback and deletion cannot modify the
data stack, and CloudFormation blocks data-stack deletion while its exports are
still imported.

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

Schema version 2 stores canonical current snapshots for every normalized
family. A pure entity diff compares stable keys and top-level values, then an
atomic persistence operation writes normalized rows, source freshness,
snapshots, family summaries, and child entity changes together. Bootstrap and
full fixtures are authoritative; event-live is authoritative within one
gameweek; current-gameweek fixture polling is partial and cannot imply a
deletion. Explicit nulls overwrite stored values, and authoritative missing
entities are deleted in foreign-key-safe order.

The first snapshot for a source is a silent baseline. Migration 2 discards the
incompatible coarse history and clears source hashes while preserving
normalized FPL data, so the next refresh rebuilds snapshots without a false
wave of created events.

The executable dependency rule is:

```fish
make lint
```

This runs the dependency rule together with the other backend and client
static checks. Use `uv run --group dev lint-imports` to diagnose that rule in
isolation.
