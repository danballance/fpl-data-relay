# AWS Serverless Deployment

Production runs in `eu-west-2` as two independent CloudFormation stacks:

1. `fpl-relay-data` is durable. It owns the VPC, isolated subnets, Aurora
   PostgreSQL 17.7, the managed database secret, and the monthly budget.
2. `fpl-relay-app` is disposable. It owns API Gateway, Lambda, the fetch and
   result queues, short-lived payload storage, Scheduler, operational alarms,
   the frontend bucket, and CloudFront.

The application stack imports the database outputs. It cannot replace or delete
the Aurora cluster or writer: the data template enables RDS deletion protection
and snapshot policies, the deployment enables stack termination protection,
and `deploy/data-stack-policy.json` denies replacement or deletion of those two
resources.

There is no NAT Gateway, RDS Proxy, Aurora reader, WAF, custom domain, or
always-running AWS application compute.

## Normal deployment

Run **Deploy production** manually from `main` in GitHub Actions. Configure the
`production` environment with these environment secrets:

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`

Configure this environment variable separately under **Environment variables**:

- `COMMUNITY_CREDENTIAL_SECRET_ARN`

The ARN identifies the Secrets Manager resource but contains no credential
material, so it is deliberately stored as a variable rather than a GitHub
secret. All other non-secret production values are explicit in the workflow
environment.
The workflow verifies the target AWS account and the commit-specific collector
image, deploys and verifies the data stack, applies database migrations,
deploys the application stack, publishes the frontend, and smoke-tests the
public API.

From an authenticated GitHub CLI session, dispatch the guarded workflow and
inspect its status with:

```fish
make deploy
make deploy-status
```

These targets deploy the remote `main` commit; they do not use local AWS
credentials or deploy uncommitted changes.

The workflow passes these required application parameters:

- `DataStackName=fpl-relay-data`
- `AlertEmail=nixprivacy@pm.me`
- `CollectorUserName=fpl-relay-nas-source`
- `PayloadPrefix=payloads`
- `CommunityCredentialSecretArn` from the production environment variable
- `CommunityScheduleState=DISABLED`

The payload builder adds its own schema-version segment, so objects are stored
under `payloads/v1/...`.

## Clean collector cutover

This project does not preserve the former disposable application stack.
Perform this once before the first deployment of the simplified template:

1. Stop the NAS collector if it is running.
2. Empty the application stack's frontend bucket.
3. Delete `fpl-relay-app` and wait for `DELETE_COMPLETE`. Its queues and queued
   messages are intentionally discarded; `fpl-relay-data` is not modified.
4. Delete the legacy `AssumeFplCollectorRole` inline policy from
   `fpl-relay-nas-source`.
5. Run the production workflow from the intended `main` commit.
6. Populate the NAS `.env` from the new outputs, install the direct AWS profile,
   and start the SHA-tagged collector.
7. Send one reference job and verify queue, S3, Lambda, and database activity.

The existing IAM access key is retained. Credential rotation is a separate
operation.

## Application outputs

Resolve an output with:

```fish
set -x OUTPUT_KEY FetchQueueUrl
uv run aws cloudformation describe-stacks \
  --region eu-west-2 \
  --stack-name fpl-relay-app \
  --query "Stacks[0].Outputs[?OutputKey=='$OUTPUT_KEY'].OutputValue | [0]" \
  --output text
```

The collector uses:

- `FetchQueueUrl`
- `FetchDeadLetterQueueUrl`
- `ResultQueueUrl`
- `ResultDeadLetterQueueUrl`
- `ScheduleDeadLetterQueueUrl`
- `PayloadBucketName`
- `PayloadPrefix`

Community operations additionally expose `CommunityQueueUrl` and
`CommunityDeadLetterQueueUrl`; these are not collector credentials.

Frontend deployment uses `FrontendBucketName`, `CloudFrontDistributionId`, and
`CloudFrontUrl`. `ApiEndpoint` is available for direct diagnostics.

## Manual recovery

Use the same explicit values as the workflow. Deploy and protect the data stack:

```fish
uv run sam deploy \
  --template-file template-data.yaml \
  --stack-name fpl-relay-data \
  --region eu-west-2 \
  --resolve-s3 \
  --disable-rollback \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset \
  --parameter-overrides AlertEmail=nixprivacy@pm.me

uv run aws cloudformation set-stack-policy \
  --region eu-west-2 \
  --stack-name fpl-relay-data \
  --stack-policy-body file://deploy/data-stack-policy.json

uv run aws cloudformation update-termination-protection \
  --region eu-west-2 \
  --stack-name fpl-relay-data \
  --enable-termination-protection
```

Resolve `DatabaseClusterArn`, `DatabaseSecretArn`, and `DatabaseName` from the
data outputs, export them as `DATABASE_RESOURCE_ARN`, `DATABASE_SECRET_ARN`, and
`DATABASE_NAME`, then apply the database schema:

```fish
set -x DATABASE_EXECUTOR rds_data
uv run fpl-relay db wait-ready --attempts 12 --interval-seconds 5
uv run fpl-relay db status
uv run fpl-relay db apply
uv run fpl-relay db status
```

Build and deploy the disposable stack:

```fish
uv run sam build \
  --template-file template-app.yaml \
  --build-dir .aws-sam/app-build

uv run sam deploy \
  --template-file .aws-sam/app-build/template.yaml \
  --stack-name fpl-relay-app \
  --region eu-west-2 \
  --resolve-s3 \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-disable-rollback \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset \
  --parameter-overrides \
    DataStackName=fpl-relay-data \
    AlertEmail=nixprivacy@pm.me \
    CollectorUserName=fpl-relay-nas-source \
    PayloadPrefix=payloads \
    CommunityCredentialSecretArn=$COMMUNITY_CREDENTIAL_SECRET_ARN \
    CommunityScheduleState=DISABLED
```

If initial application creation rolls back, delete the resulting
`ROLLBACK_COMPLETE` stack and create it again. Do not modify or delete the data
stack to recover an application deployment.

## Verification and operations

The data stack must report `CREATE_COMPLETE` or `UPDATE_COMPLETE`, termination
protection `true`, a non-empty stack policy, and Aurora deletion protection
`true`.

After each application deployment:

1. Confirm the SNS operational-alert subscription after a fresh stack creation.
2. Check `/api/healthz`, `/api/readyz`, and
   `/api/v1/change-events/recent?limit=100` and
   `/api/v1/ingestion-status` through `CloudFrontUrl`.
3. Confirm the NAS collector is healthy.
4. Send a strict reference job:

```fish
uv run aws sqs send-message \
  --region eu-west-2 \
  --queue-url $FETCH_QUEUE_URL \
  --message-body '{"version":1,"kind":"reference"}'
```

The fetch queue should be consumed, an object should appear under
`payloads/v1/reference/`, the result queue should drain through the ingestion
Lambda, and the API should expose persisted reference data.

The reference Scheduler sends a collection job every 15 minutes. Scheduler
targets retry for up to 15 minutes with three attempts and route exhausted
deliveries to the Scheduler DLQ. Operational alarms cover Scheduler target
errors, dropped invocations, Scheduler DLQ messages, and two missed reference
periods, in addition to queue age, queue DLQs, and repeated Lambda failures.
Dynamic live schedules use the same target retry and DLQ contract.

The separate community queue has its own encrypted DLQ and invokes the generic
community Lambda with batch size one, an 840-second timeout, reserved concurrency
one, and 14-day logs. A daily 06:00 `Europe/London` schedule submits only the
dispatch job. It must remain `DISABLED` until the source catalog, provider
terms/privacy review, credentials, and manual report review in
[community.md](community.md) are complete. Community alarms cover worker errors,
queue age and DLQ depth, Scheduler errors/drops, and no daily attempt within 26
hours. The worker reuses structured extraction results through the existing
Aurora database; the cache adds no AWS resource or new secret.

Each strategy explicitly configures its Supadata request rate. The worker uses
one in-memory, evenly spaced pacer across every YouTube channel in the invocation;
reserved concurrency one makes distributed coordination unnecessary. Do not
increase community concurrency or reuse the same Supadata credential in another
worker without adding cross-worker rate coordination.

## Teardown

Stop the NAS collector first. Empty both disposable buckets, then delete
`fpl-relay-app`. CloudFormation removes the direct collector-user policy with
the stack.

Deleting `fpl-relay-data` is a separate, deliberate disaster-recovery action.
It requires removing termination protection and overriding the stack policy.
The Aurora deletion policy creates a final snapshot. Never include data-stack
deletion in routine application or collector teardown.

For local Compose, recreate services without `--volumes`. This selects the
versioned PostgreSQL 17 volume while leaving any older PostgreSQL 18 volume
recoverable.
