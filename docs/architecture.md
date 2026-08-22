# Architecture

The relay remains a hexagonal modular monolith with dependency direction:

```text
composition roots → adapters → application → domain
```

It has five explicit runtime compositions:

1. Local API: Uvicorn/FastAPI, `asyncpg`, local PostgreSQL 17.7, the in-process
   ingestion scheduler, and Typer administration commands.
2. Production API: module-global FastAPI and Mangum handler, RDS Data API
   persistence, no lifespan handling, no FPL client, and no scheduler.
3. Production collection: a NAS container long-polls strict SQS fetch jobs,
   calls FPL, and publishes raw bundles through private S3 and a result queue.
4. Production ingestion: one collected bundle per Lambda invocation, strict
   validation, RDS Data API persistence, advisory locking, and EventBridge
   schedule reconciliation; it has no upstream HTTP client.
5. Production community intelligence: a daily Scheduler delivery fans out
   versioned strategy jobs through a dedicated queue to one generic Lambda. The
   worker discovers official/feed-backed public sources, reuses unchanged
   structured topic extractions from PostgreSQL, materializes only misses,
   paces Supadata transcript traffic across all YouTube sources, performs daily
   synthesis and canonical entity linking, then inserts one immutable aggregate
   report row.

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

Schema version 5 stores canonical current snapshots for every normalized
family. A pure entity diff compares stable keys and top-level values, then an
atomic persistence operation writes normalized rows, source freshness,
snapshots, family summaries, and child entity changes together. Bootstrap and
full fixtures are authoritative for structural data; current-gameweek polling
owns dynamic fixture fields during the four-hour live window and cannot imply a
deletion. Older source timestamps are rejected, and equal timestamps must have
equal payload hashes. Explicit nulls overwrite stored values, and authoritative
missing entities are deleted in foreign-key-safe order.

Migration 5 replaces the obsolete event-status `leagues_updated` field with
FPL's required `points` state (`""`, `l`, `p`, or `r`) and adds the
`relay_change_feed_rebaselines` audit table. It never deletes change history.
The explicit `change-feed rebaseline-current` command rebuilds current-season
snapshots from normalized tables under the ingestion advisory lock, then records
the reason and affected counts while preserving source freshness watermarks.

Migration 3 adds insert-only `relay_community_reports`. Entity references and
their generation-time snapshots remain embedded in the report JSONB so each
report is one resource and remains historically meaningful when current FPL
records change. See [community.md](community.md) for the source, analysis,
ranking, and rollout contracts.

Migration 4 adds `relay_community_extraction_cache`, an insert-only,
expiry-deletable cache of strict per-document topic output and evidence
metadata. It never stores posts, transcripts, or article bodies. Exact strategy,
document revision, and extraction-contract hashes isolate reusable results;
daily synthesis is deliberately never cached.

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
