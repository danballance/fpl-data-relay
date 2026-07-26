# NAS Collector

The production collector is a continuously running SQS long-polling worker. It
is the only production component that contacts the upstream FPL API:

```text
EventBridge -> fetch SQS -> NAS -> private S3 -> result SQS -> Lambda -> Aurora
```

The collector exposes no port. It receives one job at a time, uploads an exact
JSON bundle, sends its S3 pointer to the result queue, and deletes the fetch job
only after both operations succeed.

## AWS identity

Create a dedicated IAM user without resource permissions:

```fish
set -x COLLECTOR_USER_NAME fpl-relay-nas-source
uv run aws iam create-user --user-name $COLLECTOR_USER_NAME
```

Resolve its ARN and pass it as `CollectorPrincipalArn` when deploying the
application stack. After deployment, resolve `CollectorRoleArn`, attach an
inline policy allowing only `sts:AssumeRole` for that ARN, and create one access
key. Never place that key in the repository or Compose environment.

Copy `deploy/nas/assume-role-policy.json.example` to a temporary file outside
the repository, replace `REPLACE_WITH_COLLECTOR_ROLE_ARN`, and apply it:

```fish
uv run aws iam put-user-policy \
  --user-name $COLLECTOR_USER_NAME \
  --policy-name AssumeFplCollectorRole \
  --policy-document file:///absolute/path/to/assume-role-policy.json

uv run aws iam create-access-key \
  --user-name $COLLECTOR_USER_NAME
```

The second command displays the secret exactly once. Store it directly in the
NAS credentials file and do not retain it in shell history or project files.

On the NAS, create:

```text
/volume1/docker/appdata/fpl-data-relay-collector/aws/config
/volume1/docker/appdata/fpl-data-relay-collector/aws/credentials
```

Copy `deploy/nas/aws-config.example` to `config` and replace the role ARN. The
credentials file contains only the source identity:

```ini
[fpl-collector-source]
aws_access_key_id = REPLACE_ME
aws_secret_access_key = REPLACE_ME
```

Set the AWS directory to mode `700` and both files to mode `600`. Boto3 assumes
the collector role and refreshes its temporary credentials in memory.

## Install on Synology

Copy `deploy/nas/compose.yaml` to:

```text
/volume1/docker/stacks/fpl-data-relay-collector/compose.yaml
```

Copy `.env.example` beside it as `.env`, then replace every AWS output value.
Authenticate the NAS once to the private GHCR package with a classic GitHub PAT
that has only `read:packages`:

```sh
printf '%s' "$GHCR_TOKEN" |
  /usr/local/bin/docker login ghcr.io --username danballance --password-stdin
```

Start and inspect the stack:

```sh
cd /volume1/docker/stacks/fpl-data-relay-collector
/usr/local/bin/docker-compose config
/usr/local/bin/docker-compose pull
/usr/local/bin/docker-compose up -d
/usr/local/bin/docker-compose ps
/usr/local/bin/docker-compose logs --tail 100 collector
```

In GitHub, leave the resulting package visibility set to private. The workflow
does not make package visibility public.

## Updates and rollback

Successful `main` builds publish both `main` and
`sha-<full-git-commit>`. Update explicitly:

```sh
/usr/local/bin/docker-compose pull collector
/usr/local/bin/docker-compose up -d collector
```

For rollback, set `COLLECTOR_IMAGE_TAG` in `.env` to a known immutable SHA tag,
then repeat the pull and up commands.

## Operations

The worker is healthy while its SQS polling heartbeat is current. A failed job
is not acknowledged: it becomes visible after 240 seconds and reaches the fetch
DLQ after three receives. AWS alarms report fetch/result messages older than
five minutes and any DLQ contents.

Inspect queue depth, age, and DLQ contents without consuming production jobs:

```fish
uv run aws sqs get-queue-attributes \
  --region $AWS_REGION \
  --queue-url $FETCH_QUEUE_URL \
  --attribute-names \
    ApproximateNumberOfMessages \
    ApproximateNumberOfMessagesNotVisible

uv run aws sqs receive-message \
  --region $AWS_REGION \
  --queue-url $FETCH_DEAD_LETTER_QUEUE_URL \
  --max-number-of-messages 10 \
  --visibility-timeout 0 \
  --wait-time-seconds 0

uv run aws sqs receive-message \
  --region $AWS_REGION \
  --queue-url $COLLECTED_PAYLOAD_DEAD_LETTER_QUEUE_URL \
  --max-number-of-messages 10 \
  --visibility-timeout 0 \
  --wait-time-seconds 0
```

Resolve the two DLQ URLs from the `FetchDeadLetterQueueUrl` and
`CollectedPayloadDeadLetterQueueUrl` application-stack outputs. Use
`docker-compose logs --tail 200 collector` for worker failures and CloudWatch
Logs for result-ingestion failures.

Force a reference refresh by sending the strict versioned job:

```fish
uv run aws sqs send-message \
  --region $AWS_REGION \
  --queue-url $FETCH_QUEUE_URL \
  --message-body '{"version":1,"kind":"reference"}'
```

Raw S3 bundles expire after seven days. Rotate the source access key by creating
and installing a replacement in the NAS credentials file, recreating the
container, confirming it is healthy, and only then deleting the former access
key.

## Complete teardown

Stop and remove the NAS service first:

```sh
cd /volume1/docker/stacks/fpl-data-relay-collector
/usr/local/bin/docker-compose down
```

After the container is gone, remove the stack directory and the AWS profile
files from the NAS. Delete the source IAM user's access key and inline policy,
then delete the user. Empty the collected-payload and frontend buckets before
deleting the application stack, as described in
[deployment.md](deployment.md). The durable Aurora data stack is separate and
must not be deleted as part of collector teardown.
