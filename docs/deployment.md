# AWS Serverless Deployment

Production is deployed to `eu-west-2` as two independent top-level
CloudFormation stacks:

1. `template-data.yaml` defines durable, protected data infrastructure.
2. `template-app.yaml` defines disposable application infrastructure.

The data stack owns the VPC, isolated database subnets, Aurora PostgreSQL 17.7
Serverless v2 cluster and writer, managed database secret, and monthly budget.
The application stack owns API Gateway, both Lambdas, SQS and its dead-letter
queue, EventBridge Scheduler resources, operational SNS and alarms, the private
frontend bucket, and CloudFront.

The application imports database outputs from the data stack. CloudFormation
therefore prevents deletion of the data stack while the application stack still
uses those exports. The stacks are deliberately not nested: application
creation, update, rollback, and deletion cannot modify the data stack.

There is no NAT Gateway, RDS Proxy, Aurora reader, WAF, custom domain, or
always-running application compute. Aurora is configured for 0–2 ACUs and
pauses after 300 idle seconds.

## Prerequisites and explicit deployment values

Install `uv`, Node.js, Docker, the AWS CLI, and AWS SAM CLI. Configure AWS
credentials with permission to create every resource in both templates.

Set every deployment value explicitly:

```fish
set -x AWS_REGION eu-west-2
set -x AWS_DEFAULT_REGION eu-west-2
set -x DATA_STACK_NAME fpl-relay-data
set -x APP_STACK_NAME fpl-relay-app
set -x ALERT_EMAIL you@example.com
set -x FPL_API_BASE_URL https://fantasy.premierleague.com/api
set -x FPL_CLIENT_USER_AGENT fpl-data-relay/production

uv run aws sts get-caller-identity
uv run aws configure get region
```

The reported account must be the intended production account and the configured
region must be `eu-west-2`.

## AWS compatibility preflight

The templates were checked against the AWS service catalog and documentation on
25 July 2026. Before deployment, verify the external engine version and managed
CloudFront policy identifier that static SAM lint cannot resolve:

```fish
uv run aws rds describe-orderable-db-instance-options \
  --region $AWS_REGION \
  --engine aurora-postgresql \
  --db-instance-class db.serverless \
  --query "OrderableDBInstanceOptions[?EngineVersion=='17.7'].EngineVersion"

uv run aws cloudfront get-cache-policy \
  --id 4135ea2d-6df8-44a3-9df3-4b5a84be39ad
```

The first command must include `17.7`. The second must return
`Managed-CachingDisabled`. `template-app.yaml` records the names of every
AWS-managed CloudFront policy beside its UUID.

## Local and repository verification

Local development remains Uvicorn, `asyncpg`, PostgreSQL 17.7, and the
in-process ingestion scheduler:

```fish
uv run docker compose up --build
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
uv run sam validate --lint \
  --template-file template-data.yaml \
  --region $AWS_REGION
uv run sam validate --lint \
  --template-file template-app.yaml \
  --region $AWS_REGION
```

## 1. Deploy the durable data stack

The initial data deployment preserves successfully created resources if
provisioning fails. This is intentional: automatic rollback must not partially
tear down a newly created cluster or writer.

```fish
uv run sam deploy \
  --template-file template-data.yaml \
  --stack-name $DATA_STACK_NAME \
  --region $AWS_REGION \
  --resolve-s3 \
  --disable-rollback \
  --parameter-overrides \
    AlertEmail=$ALERT_EMAIL
```

Do not deploy the application until the data stack reaches `CREATE_COMPLETE`.
Then enable stack-level termination protection:

```fish
uv run aws cloudformation update-termination-protection \
  --region $AWS_REGION \
  --stack-name $DATA_STACK_NAME \
  --enable-termination-protection
```

Resolve and verify the durable outputs:

```fish
set -x DATABASE_CLUSTER_IDENTIFIER (uv run aws cloudformation describe-stacks \
  --region $AWS_REGION \
  --stack-name $DATA_STACK_NAME \
  --query "Stacks[0].Outputs[?OutputKey=='DatabaseClusterIdentifier'].OutputValue | [0]" \
  --output text)

set -x DATABASE_RESOURCE_ARN (uv run aws cloudformation describe-stacks \
  --region $AWS_REGION \
  --stack-name $DATA_STACK_NAME \
  --query "Stacks[0].Outputs[?OutputKey=='DatabaseClusterArn'].OutputValue | [0]" \
  --output text)

set -x DATABASE_SECRET_ARN (uv run aws cloudformation describe-stacks \
  --region $AWS_REGION \
  --stack-name $DATA_STACK_NAME \
  --query "Stacks[0].Outputs[?OutputKey=='DatabaseSecretArn'].OutputValue | [0]" \
  --output text)

set -x DATABASE_NAME (uv run aws cloudformation describe-stacks \
  --region $AWS_REGION \
  --stack-name $DATA_STACK_NAME \
  --query "Stacks[0].Outputs[?OutputKey=='DatabaseName'].OutputValue | [0]" \
  --output text)

uv run aws rds describe-db-clusters \
  --region $AWS_REGION \
  --db-cluster-identifier $DATABASE_CLUSTER_IDENTIFIER \
  --query 'DBClusters[0].DeletionProtection'

uv run aws cloudformation describe-stacks \
  --region $AWS_REGION \
  --stack-name $DATA_STACK_NAME \
  --query 'Stacks[0].EnableTerminationProtection'
```

Both verification commands must return `true`.

If initial provisioning fails, the stack remains in `CREATE_FAILED` and
successfully created resources remain present. Inspect stack events, correct the
template or permissions, and deploy the same data stack again with
`--disable-rollback`. Do not delete or roll back a data stack merely to retry a
correctable provisioning failure.

## 2. Apply production migrations

Apply migrations after the data stack is healthy and before creating scheduled
ingestion infrastructure. Production administration uses RDS Data API and never
falls back to `asyncpg`:

```fish
set -x DATABASE_EXECUTOR rds_data

uv run fpl-relay config-check
uv run fpl-relay db status
uv run fpl-relay db apply
uv run fpl-relay db status
```

`db status` is read-only and verifies migration names and SHA-256 checksums.
`db apply` executes pending versions strictly in order. The destructive
`db drop-and-create` command rejects the `rds_data` executor.

## 3. Build and deploy the disposable application stack

The custom Makefile exports the locked `uv.lock`, installs only the production
dependency set for CPython 3.14/x86_64, and copies only the Python package into
each Lambda artifact:

```fish
uv run sam build \
  --template-file template-app.yaml \
  --build-dir .aws-sam/app-build

uv run sam deploy \
  --template-file .aws-sam/app-build/template.yaml \
  --stack-name $APP_STACK_NAME \
  --region $AWS_REGION \
  --resolve-s3 \
  --capabilities CAPABILITY_IAM \
  --no-disable-rollback \
  --parameter-overrides \
    DataStackName=$DATA_STACK_NAME \
    AlertEmail=$ALERT_EMAIL \
    FplApiBaseUrl=$FPL_API_BASE_URL \
    FplClientUserAgent=$FPL_CLIENT_USER_AGENT
```

The `DataStackName` parameter is required and has no default. Missing exports,
an incorrect stack name, or an attempt to deploy in another region fails during
CloudFormation evaluation. There is no database fallback.

Confirm the operational SNS email subscription after each fresh application
stack creation. The monthly budget notification belongs to the durable data
stack and remains active when the application stack is absent.

Resolve the disposable application outputs:

```fish
set -x FRONTEND_BUCKET_NAME (uv run aws cloudformation describe-stacks \
  --region $AWS_REGION \
  --stack-name $APP_STACK_NAME \
  --query "Stacks[0].Outputs[?OutputKey=='FrontendBucketName'].OutputValue | [0]" \
  --output text)

set -x CLOUDFRONT_DISTRIBUTION_ID (uv run aws cloudformation describe-stacks \
  --region $AWS_REGION \
  --stack-name $APP_STACK_NAME \
  --query "Stacks[0].Outputs[?OutputKey=='CloudFrontDistributionId'].OutputValue | [0]" \
  --output text)

set -x CLOUDFRONT_URL (uv run aws cloudformation describe-stacks \
  --region $AWS_REGION \
  --stack-name $APP_STACK_NAME \
  --query "Stacks[0].Outputs[?OutputKey=='CloudFrontUrl'].OutputValue | [0]" \
  --output text)

set -x INGESTION_QUEUE_URL (uv run aws cloudformation describe-stacks \
  --region $AWS_REGION \
  --stack-name $APP_STACK_NAME \
  --query "Stacks[0].Outputs[?OutputKey=='IngestionQueueUrl'].OutputValue | [0]" \
  --output text)
```

If initial application creation fails, normal rollback removes the disposable
resources. A resulting `ROLLBACK_COMPLETE` application stack can be deleted and
created again without touching the data stack.

## 4. Seed reference data

Send the strict versioned reference job to the disposable queue:

```fish
uv run aws sqs send-message \
  --region $AWS_REGION \
  --queue-url $INGESTION_QUEUE_URL \
  --message-body '{"version":1,"kind":"reference"}'
```

Verify the ingestion Lambda logs and future match-window schedules in the
`$APP_STACK_NAME-live` EventBridge Scheduler group. The daily reference schedule
runs at 04:00 in the `Europe/London` time zone.

## 5. Build and upload the frontend

The React build uses relative `/api` requests. CloudFront strips that prefix
before forwarding to API Gateway:

```fish
uv run npm --prefix client ci
uv run npm --prefix client run build

uv run aws s3 sync client/dist/ "s3://$FRONTEND_BUCKET_NAME/" \
  --delete \
  --exclude index.html \
  --cache-control 'public,max-age=31536000,immutable'

uv run aws s3 cp client/dist/index.html \
  "s3://$FRONTEND_BUCKET_NAME/index.html" \
  --cache-control 'no-cache' \
  --content-type text/html

uv run aws cloudfront create-invalidation \
  --distribution-id $CLOUDFRONT_DISTRIBUTION_ID \
  --paths /index.html
```

The S3 origin remains private. SPA rewriting runs only on the S3 behavior,
while `/api/*` uses the uncached API behavior.

## 6. Smoke test and pause/resume acceptance

```fish
uv run curl --fail "$CLOUDFRONT_URL/api/healthz"
uv run curl --fail "$CLOUDFRONT_URL/api/readyz"
uv run curl --fail \
  "$CLOUDFRONT_URL/api/v1/change-events?after_id=0&limit=100"
```

Verify API throttling, the reference-data screens, match schedules, CloudWatch
alarms, and budget notifications. Leave the system without database traffic for
more than five minutes and verify Aurora reaches 0 ACUs. Opening a
database-backed screen during resume must produce HTTP 503,
`Retry-After: 5`, and:

```json
{
  "code": "database_waking",
  "detail": "The database is waking from idle. Retry shortly.",
  "retry_after_seconds": 5
}
```

The client displays “Service waking up,” retries eight times at five-second
intervals, and exposes manual retry if the database is still unavailable.

## Delete and recreate only the application stack

The frontend bucket contains reproducible build artifacts, but CloudFormation
can delete only an empty S3 bucket. Empty it before deliberate stack deletion:

```fish
uv run aws s3 rm "s3://$FRONTEND_BUCKET_NAME/" --recursive

uv run aws cloudformation delete-stack \
  --region $AWS_REGION \
  --stack-name $APP_STACK_NAME

uv run aws cloudformation wait stack-delete-complete \
  --region $AWS_REGION \
  --stack-name $APP_STACK_NAME
```

This deletes API, compute, queues and messages, schedules, operational
notifications, logs, alarms, frontend hosting, and CloudFront. It does not
modify the data stack.

## Deliberately delete the data stack

Deleting production data is exceptional and destructive. First delete the
application stack so no imports remain. Then disable both protection layers:

```fish
uv run aws cloudformation update-termination-protection \
  --region $AWS_REGION \
  --stack-name $DATA_STACK_NAME \
  --no-enable-termination-protection

uv run aws rds modify-db-cluster \
  --region $AWS_REGION \
  --db-cluster-identifier $DATABASE_CLUSTER_IDENTIFIER \
  --no-deletion-protection \
  --apply-immediately

uv run aws rds wait db-cluster-available \
  --region $AWS_REGION \
  --db-cluster-identifier $DATABASE_CLUSTER_IDENTIFIER

uv run aws cloudformation delete-stack \
  --region $AWS_REGION \
  --stack-name $DATA_STACK_NAME

uv run aws cloudformation wait stack-delete-complete \
  --region $AWS_REGION \
  --stack-name $DATA_STACK_NAME
```

The Aurora cluster has `DeletionPolicy: Snapshot`, so CloudFormation requests a
final snapshot when this deliberate deletion is allowed to proceed.

Production starts with an empty database populated from the current FPL API.
Local PostgreSQL contents are not copied.
