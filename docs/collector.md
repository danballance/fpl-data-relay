# NAS Collector

The production collector is a continuously running SQS long-polling worker and
the only production component that calls the upstream FPL API:

```text
Scheduler -> fetch SQS -> NAS -> private S3 -> result SQS -> Lambda -> Aurora
```

It exposes no port. It processes one job at a time, uploads the exact JSON
bundle, publishes its S3 pointer, and deletes the fetch job only after both
operations succeed.

## AWS identity

The collector uses the existing dedicated IAM user
`fpl-relay-nas-source`. `template-app.yaml` attaches one inline policy directly
to that user with only:

- receive, inspect, change visibility, and delete on the fetch queue;
- send on the result queue;
- put objects under the configured payload prefix.

There is no collector role or `sts:AssumeRole` path. Do not attach another
inline policy, managed policy, or group membership to this identity.

Store the existing access key only on the NAS:

```text
/volume1/docker/appdata/fpl-data-relay-collector/aws/config
/volume1/docker/appdata/fpl-data-relay-collector/aws/credentials
```

Copy `deploy/nas/aws-config.example` to `config`. The credentials file is:

```ini
[fpl-collector-source]
aws_access_key_id = REPLACE_ME
aws_secret_access_key = REPLACE_ME
```

Set the AWS directory to mode `700` and both files to mode `600`.

## Install on Synology

Copy `deploy/nas/compose.yaml` to:

```text
/volume1/docker/stacks/fpl-data-relay-collector/compose.yaml
```

Copy `deploy/nas/.env.example` beside it as `.env`. Set:

- `COLLECTOR_IMAGE_TAG` to `sha-<full-git-commit>`;
- `AWS_PROFILE` to `fpl-collector-source`;
- the queue, bucket, and prefix values from the application-stack outputs.

Authenticate the NAS once to the private GHCR package using a token with
`read:packages`, then start the stack:

```sh
printf '%s' "$GHCR_TOKEN" |
  /usr/local/bin/docker login ghcr.io \
    --username danballance \
    --password-stdin

cd /volume1/docker/stacks/fpl-data-relay-collector
/usr/local/bin/docker-compose config
/usr/local/bin/docker-compose pull collector
/usr/local/bin/docker-compose up -d collector
/usr/local/bin/docker-compose ps
/usr/local/bin/docker-compose logs --tail 100 collector
```

The container remains read-only, drops all capabilities, exposes no ports,
uses bounded temporary storage, stops gracefully, and rotates local logs.

## Updates and rollback

Every deployed tag is immutable. Change `COLLECTOR_IMAGE_TAG` to the desired
`sha-<full-git-commit>`, then:

```sh
/usr/local/bin/docker-compose pull collector
/usr/local/bin/docker-compose up -d collector
```

Rollback uses the same commands with a previously verified SHA tag.

## Operations

From an administrator checkout, prefer the root `nas-status`, `nas-start`,
`nas-stop`, `nas-logs`, `nas-update`, and `nas-rollback` targets documented in
[administration.md](administration.md). These commands execute locally and
control this Compose service over the configured SSH alias.

The health check requires a current SQS polling heartbeat. A failed job is not
acknowledged: it becomes visible after 240 seconds and reaches the fetch DLQ
after three receives. AWS alarms report queue messages older than five minutes,
queue and Scheduler DLQ contents, missed 15-minute reference invocations,
Scheduler delivery errors or dropped invocations, and repeated ingestion
Lambda failures. Reference jobs collect bootstrap, fixtures, and event status
every 15 minutes. Live schedules span ten minutes before kickoff through four
hours after, polling every 15 seconds while active and 60 seconds while idle.
Each live window keeps a stable retained Scheduler identity while active, so
reference reconciliation cannot restart an already-fired polling chain.

Collected S3 bundles and their result messages use strict payload contract v2
and are stored under the `v2` prefix. Fetch jobs remain the separately versioned
v1 job contract.

Use the `FetchDeadLetterQueueUrl`, `ResultDeadLetterQueueUrl`, and
`ScheduleDeadLetterQueueUrl` outputs when inspecting failures:

```fish
uv run aws sqs get-queue-attributes \
  --region eu-west-2 \
  --queue-url $FETCH_QUEUE_URL \
  --attribute-names \
    ApproximateNumberOfMessages \
    ApproximateNumberOfMessagesNotVisible

uv run aws sqs receive-message \
  --region eu-west-2 \
  --queue-url $FETCH_DEAD_LETTER_QUEUE_URL \
  --max-number-of-messages 10 \
  --visibility-timeout 0 \
  --wait-time-seconds 0

uv run aws sqs receive-message \
  --region eu-west-2 \
  --queue-url $RESULT_DEAD_LETTER_QUEUE_URL \
  --max-number-of-messages 10 \
  --visibility-timeout 0 \
  --wait-time-seconds 0
```

Force a reference refresh with:

```fish
uv run aws sqs send-message \
  --region eu-west-2 \
  --queue-url $FETCH_QUEUE_URL \
  --message-body '{"version":1,"kind":"reference"}'
```

Payloads expire after seven days. Rotate credentials by installing a new key,
recreating the container, confirming health, and only then deleting the former
key.

## Teardown

Stop and remove the NAS service before deleting the application stack:

```sh
cd /volume1/docker/stacks/fpl-data-relay-collector
/usr/local/bin/docker-compose down
```

Remove the NAS stack and profile files only when the collector is being
retired. Delete the IAM access key before deleting the user. Routine collector
or application teardown must not delete the durable data stack.
