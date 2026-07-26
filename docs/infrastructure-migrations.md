# Infrastructure Migrations

The normal production deployment is idempotent CloudFormation reconciliation.
Numbered infrastructure migrations are reserved for exceptional transitions
that need state-aware work around a normal stack update, such as disabling an
old event-source mapping before changing queue ownership.

## Durable history

Migration implementations live in:

```text
src/fpl_data_relay/infrastructure_migrations/versions/
```

Applied records are non-secret SSM String parameters below:

```text
/fpl-data-relay/production/infrastructure-migrations/
```

Each record contains the version, immutable name and source SHA-256, UTC
application time, Git commit, AWS account, region, and affected stack. The
runner rejects:

- unknown or non-contiguous versions;
- renamed migrations;
- a source checksum changed after application;
- records belonging to another account, region, or stack.

Parameters are created with overwrite disabled. Applied migration files must
never be edited, renamed, reordered, or deleted. Add a new migration to correct
or extend an applied transition.

Inspect the ledger without changing it:

```fish
uv run aws ssm get-parameters-by-path \
  --region eu-west-2 \
  --path /fpl-data-relay/production/infrastructure-migrations/ \
  --no-recursive \
  --no-with-decryption
```

## Workflow lifecycle

At the start of a workflow, `fpl-infrastructure-migrate begin` validates SSM and
creates an ephemeral JSON state file on the GitHub runner. Pending migrations
then wrap one of three explicit boundaries:

1. `data-stack`
2. `application-stack`
3. `post-deployment`

Each boundary calls `prepare` before the core operation and `finalize` after it
succeeds. No SSM records are written during these phases. After all boundaries
and smoke tests pass, `commit` verifies every postcondition and creates pending
records in numeric order.

If record creation is interrupted, SSM can contain only a valid prefix. The
next run verifies already-completed AWS state and resumes record creation.

Every migration also implements `secure_failure`. Once the application failure
guard is armed, the workflow runs these guards after any later failure.

## Migration 0001: collector/ingestion split

`0001_split_collector_ingestion` handles the first upgrade from Lambda fetching
FPL documents directly to the NAS collector pipeline.

Before the application change set executes it:

- resolves the legacy `IngestionQueueUrl`, upgraded `FetchQueueUrl`, or the
  physical `IngestionQueue` resource;
- records the physical queue URL and ARN;
- disables every Lambda mapping consuming that queue and waits for `Disabled`.

After deployment it requires:

- the upgraded `FetchQueueUrl` to be the same physical queue;
- no active Lambda mapping on the fetch queue;
- exactly one enabled ingestion mapping on the result queue;
- the strict result queue, payload bucket, prefix, and collector-role outputs;
- a seven-day S3 lifecycle and complete public-access block;
- exact collector-role trust and the NAS user's assume-role-only inline policy.

The SSM marker is written only after every check succeeds. Its failure guard
rediscovers the fetch queue from CloudFormation and disables its Lambda
mappings without relying on the ephemeral workflow file.

## Adding a migration

1. Add the next contiguous `vNNNN_descriptive_name.py` module and register its
   singleton in `runner.py`.
2. Use strict Pydantic models for preparation context and return canonical JSON
   from `prepare`.
3. Make `prepare`, `finalize`, `verify`, and `secure_failure` safe to run again
   after partial completion.
4. Select the one core boundary the transition wraps.
5. Add fake-AWS tests for legacy, partial, completed-but-unmarked, applied, and
   failure states.
6. Run the full quality gates and deploy through the manual production
   workflow.

Do not add arbitrary shell scripts or infer completion only from resource
names. Every migration needs both an explicit AWS postcondition and its durable
SSM record.
