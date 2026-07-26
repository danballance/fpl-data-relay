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
   stack.

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

The executable dependency rule is:

```fish
uv run --group dev lint-imports
```
