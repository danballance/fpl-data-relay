# AWS Serverless Deployment

Production is defined by `template.yaml` and is deployed to `eu-west-2`. The
stack creates a public HTTP API, API and ingestion Lambdas, Aurora PostgreSQL
17.7 Serverless v2, SQS and EventBridge schedules, a private S3 frontend behind
CloudFront, alarms, SNS notifications, and a monthly budget.

The stack is intentionally small: there is no NAT Gateway, RDS Proxy, reader
instance, WAF, custom domain, or always-running application compute. Aurora is
configured for 0–2 ACUs and pauses after 300 idle seconds.

## Prerequisites

Install `uv`, Node.js, Docker, the AWS CLI, and AWS SAM CLI. Configure AWS
credentials with permission to create the resources in `template.yaml`. Have a
real email address ready for budget and alarm notifications.

Verify account and region before creating chargeable resources:

```fish
aws sts get-caller-identity
aws configure get region
set -x AWS_REGION eu-west-2
set -x AWS_DEFAULT_REGION eu-west-2
```

## Local verification

The local application is deliberately a different composition: Uvicorn,
`asyncpg`, PostgreSQL 17.7, and the in-process scheduler. Supply every Compose
variable explicitly and start it with the existing workflow:

```fish
docker compose up --build
```

The Compose volume is named `postgres-17-data`, leaving the former PostgreSQL
18 volume untouched. Delete the old volume manually only after the PostgreSQL
17.7 setup and required local data have been verified.

Run all quality and infrastructure gates:

```fish
uv run --group dev ruff check
uv run --group dev ty check
uv run --group dev lint-imports
uv run --group dev python -m pytest --cov ./src/fpl_data_relay tests
uv run npm --prefix client run check
uv run sam validate --lint --template-file template.yaml --region eu-west-2
```

## Build and deploy the stack

The SAM Makefile build exports from the locked `uv.lock`, installs only the
production dependency set for CPython 3.14/x86_64, and copies only the Python
package and Lambda entry point into each artifact:

```fish
uv run sam build --template-file template.yaml
uv run sam deploy --guided \
  --region eu-west-2 \
  --parameter-overrides \
    AlertEmail=you@example.com \
    FplApiBaseUrl=https://fantasy.premierleague.com/api \
    FplClientUserAgent=fpl-data-relay/production
```

Choose a stable stack name such as `fpl-data-relay`, allow SAM to create the
deployment role capabilities, and save the arguments to `samconfig.toml` if
desired. Confirm both SNS subscription emails after deployment. CloudFormation
retains the database cluster and frontend bucket when the stack is deleted; a
deliberate database removal therefore requires removing deletion protection and
requesting a final snapshot.

Capture the deployed identifiers:

```fish
set -x FPL_STACK_NAME fpl-data-relay
aws cloudformation describe-stacks \
  --region eu-west-2 \
  --stack-name $FPL_STACK_NAME \
  --query 'Stacks[0].Outputs'
```

The relevant output keys are `CloudFrontUrl`, `ApiEndpoint`,
`FrontendBucketName`, `CloudFrontDistributionId`, `DatabaseClusterArn`,
`DatabaseSecretArn`, and `IngestionQueueUrl`.

## Apply production migrations

Production administration uses the same ordered, checksum-validated migrations
as local development, but executes them through RDS Data API. It never falls
back to `asyncpg`.

```fish
set -x DATABASE_EXECUTOR rds_data
set -x DATABASE_RESOURCE_ARN <DatabaseClusterArn>
set -x DATABASE_SECRET_ARN <DatabaseSecretArn>
set -x DATABASE_NAME fplrelay

uv run fpl-relay config-check
uv run fpl-relay db status
uv run fpl-relay db apply
uv run fpl-relay db status
```

`db status` is read-only and verifies migration names and SHA-256 checksums.
`db apply` executes pending versions strictly in order. The destructive
`db drop-and-create` command rejects the `rds_data` executor.

## Seed reference data

Send the strict versioned reference job to SQS:

```fish
aws sqs send-message \
  --region eu-west-2 \
  --queue-url <IngestionQueueUrl> \
  --message-body '{"version":1,"kind":"reference"}'
```

Inspect the ingestion Lambda log group and verify that future match-window
schedules appear in the `fpl-data-relay-live` EventBridge Scheduler group. The
daily schedule runs at 04:00 in the `Europe/London` time zone.

## Build and upload the frontend

The React production build uses relative `/api` requests; CloudFront strips that
prefix before forwarding to API Gateway.

```fish
uv run npm --prefix client ci
uv run npm --prefix client run build

aws s3 sync client/dist/ s3://<FrontendBucketName>/ \
  --delete \
  --exclude index.html \
  --cache-control 'public,max-age=31536000,immutable'

aws s3 cp client/dist/index.html \
  s3://<FrontendBucketName>/index.html \
  --cache-control 'no-cache' \
  --content-type text/html

aws cloudfront create-invalidation \
  --distribution-id <CloudFrontDistributionId> \
  --paths /index.html
```

The S3 origin remains private. The SPA rewrite runs only on the S3 behavior,
while `/api/*` uses the uncached API behavior.

## Smoke test and pause/resume acceptance

Use the CloudFront URL for browser tests:

```fish
curl --fail https://<distribution-domain>/api/healthz
curl --fail https://<distribution-domain>/api/readyz
curl --fail \
  'https://<distribution-domain>/api/v1/change-events?after_id=0&limit=100'
```

Verify API throttling, the reference data screens, match schedules, CloudWatch
alarms, and the confirmed budget notifications. Leave the system without
database traffic for more than five minutes and verify Aurora reaches 0 ACUs.
Then open a database-backed screen. During resume, the API must return:

```json
{
  "code": "database_waking",
  "detail": "The database is waking from idle. Retry shortly.",
  "retry_after_seconds": 5
}
```

with HTTP 503 and `Retry-After: 5`. The client displays “Service waking up,”
retries eight times at five-second intervals, and exposes manual retry if the
database is still unavailable. General database and schema failures have
separate error states and must not be treated as resume events.

Production starts with an empty database populated from the current FPL API.
Local PostgreSQL contents are not copied.
