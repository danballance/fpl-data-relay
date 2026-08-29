# Production Administration

The root Makefile is the supported local control surface for production
operations. Its prefixes describe where the effect occurs:

- `aws-*` runs locally using the dedicated AWS profile and changes or inspects
  AWS production resources.
- `nas-*` runs locally and controls the collector on the NAS over SSH.
- `prod-*` runs locally and coordinates both AWS and the NAS.
- Deployment remains exclusively in the **Deploy production** GitHub Actions
  workflow. There are no Make deployment targets.

## Initial setup

The toolkit uses AWS CLI console login, which provides temporary credentials
through the browser without storing access keys. AWS CLI 2.32 or newer is
required. Run the composed onboarding workflow:

```fish
make aws-profile-onboard
```

If `.admin.env` does not exist, this copies `.admin.env.example` once; an
existing file is never overwritten. The command prints the one root-of-trust
step it cannot perform, then asks for:

1. The name of a separately authenticated bootstrap AWS CLI profile.
2. Whether the target console identity is an IAM `user`, `group`, or `role`.
3. The target IAM principal name.
4. The literal confirmation `production`.

The bootstrap profile must already have permission to inspect the two relay
stacks, create and version a customer-managed policy, and attach managed
policies to the selected principal. An unauthenticated program cannot grant
itself this initial authority. If no such profile exists, ask an AWS account
administrator to grant those temporary bootstrap permissions through the
approved console or infrastructure process, authenticate that identity under a
separate named AWS CLI profile, and verify it with:

```fish
aws sts get-caller-identity --profile PROFILE_NAME
```

The CLI prints the exact required IAM actions before prompting and repeats the
instructions when bootstrap authentication or authorization fails. It verifies
that the bootstrap caller is in account `757771412865`; no profile is selected
implicitly. Entering `default` uses that profile only because the operator
explicitly named it, and the toolkit never changes its configuration.

The bootstrap operation attaches AWS's managed
`SignInLocalDevelopmentAccess` policy. It also reads the deployed
CloudFormation queue and database outputs, generates an account-specific
`FplRelayAdministrator` policy, and attaches it idempotently. Fixed schedule
identities and the bounded `fpl-live-*` schedule prefix are derived from the
configured application stack name, matching the names declared by the relay
template. The toolkit therefore does not depend on administrator-specific
outputs having already been deployed. The generated policy is versioned only
when its content changes. There are no account-ID placeholders to edit.

After successful onboarding, the CLI reminds the operator to ask the account
administrator to remove any IAM-management access that was granted to the
bootstrap identity only for this operation. The toolkit cannot safely remove an
unknown external grant itself.

`deploy/admin-policy.example.json` is an illustrative review artifact only;
the CLI-generated policy is the operational source of truth. The command does
not read credentials, put credentials in `.admin.env`, or modify IAM Identity
Center.

After bootstrap, onboarding configures the dedicated `fpl-relay-admin` profile
for `eu-west-2`, opens AWS console login in the local browser, verifies account
`757771412865`, and runs the AWS doctor check. Review `.admin.env` after its
first creation; it contains no credentials. NAS authentication remains in
OpenSSH configuration.

The individual onboarding primitives remain available:

```fish
make aws-profile-bootstrap
make aws-profile-setup
make aws-doctor
```

Manage the temporary session with:

```fish
make aws-profile-status
make aws-profile-login
make aws-profile-logout
```

`aws-profile-setup` resumes a partial previous setup and refreshes an existing
console-login profile. It refuses to overwrite access-key, IAM Identity Center,
assume-role, web-identity, or credential-process profiles. If AWS login returns
HTTP 400, run `make aws-profile-bootstrap`, confirm the selected console
identity received AWS's `SignInLocalDevelopmentAccess` managed policy, then
retry. Do not use `aws sso login` for this console-login profile.

The NAS SSH target should be an alias in `~/.ssh/config`. The remote account
must be able to run the configured Docker and Docker Compose executables without
an interactive prompt.

Validate each boundary:

```fish
make aws-profile-status
make aws-doctor
make nas-doctor
make prod-doctor
make prod-status
```

All mutating maintenance, database, and collector lifecycle commands require
the literal `CONFIRM=production`. Commands which create an audit record also
require a nonempty operational reason.

## Routine commands

Inspect AWS work and failures:

```fish
make aws-status
make aws-db-status
make aws-queues-status
make aws-dlqs-status
make aws-dlq-peek DLQ=fetch
```

Valid DLQ selectors are `fetch`, `result`, `schedule`, and `community`.
Peeking receives up to ten messages with a zero visibility timeout and never
deletes or redrives them.

Submit strict typed jobs:

```fish
make aws-send-reference
make aws-send-live
make aws-send-community
```

The live command resolves the single normalized current season and event and
derives its bounded fixture window. Arbitrary JSON and result-queue messages
are deliberately unsupported. Queue sends are rejected during maintenance,
except for the controlled refresh inside `prod-rebaseline-current`.

Control the collector:

```fish
make nas-status
make nas-logs
make nas-stop CONFIRM=production
make nas-start CONFIRM=production
make nas-update SHA=FULL_40_CHARACTER_REVISION CONFIRM=production
make nas-rollback SHA=FULL_40_CHARACTER_REVISION CONFIRM=production
```

Update and rollback only activate an immutable image that has already been
published by GitHub. They do not build or publish code. The NAS `.env` change
is atomic and a timestamped backup is retained. A failed update is reported
without an automatic rollback.

## Maintenance workflow

Begin maintenance with:

```fish
make prod-maintenance-begin \
  REASON="database maintenance" \
  CONFIRM=production
```

The workflow records the AWS operator, original schedule definitions and
states, original collector state, and queue depths. It then disables reference,
community, and live schedules; lets fetch, result, and community queues drain;
stops the collector; confirms stable emptiness again; and marks the window
active.

SQS counts are approximate. Stable emptiness therefore includes visible,
in-flight, and delayed messages and must remain zero for the configured stable
period. Any nonempty DLQ stops the workflow for review.

Inspect the durable phase after an interruption:

```fish
make aws-maintenance-status
make prod-status
```

Rerunning `prod-maintenance-begin` with the same reason continues an `entering`
window. An `active` or `exiting` window must be completed rather than replaced.

End maintenance with:

```fish
make prod-maintenance-end CONFIRM=production
```

The collector is restored only if it was running originally. Reference and
community schedules regain their exact recorded states. Future live schedules
regain their state and trigger; an elapsed but still-active window receives a
one-minute catch-up trigger; expired windows remain disabled and are removed by
the next reference reconciliation. The audit is closed only after restoration
succeeds.

The lower-level AWS-only primitives are available when deliberately managing
the collector separately:

```fish
make aws-schedules-pause REASON="manual work" CONFIRM=production
make aws-queues-drain
make aws-schedules-restore CONFIRM=production
```

## Reusable current-season rebaseline

Rebaselining requires an active maintenance window:

```fish
make prod-rebaseline-current \
  REASON="correct normalized season state" \
  CONFIRM=production
```

The workflow temporarily starts the collector, runs and drains one reference
job, resolves and runs one current-event live job, drains again, stops the
collector, and calls the atomic current-season rebaseline. It leaves
maintenance active so the administrator can inspect the result before running
`prod-maintenance-end`.

`aws-rebaseline-current` skips the refresh but still requires active
maintenance. Repeated runs remain supported and create separate audit records.

## Manual migration 0005 bootstrap

Migration `0005` coordinates schema, payload, Lambda, UI, and collector changes
and must be introduced once under a manually supervised quiet window. The
resumable bootstrap script performs the local orchestration through Make while
leaving deployment exclusively in GitHub Actions.

Choose the exact full SHA that will be deployed and run:

```fish
scripts/bootstrap-migration-0005.sh prepare \
  --sha FULL_40_CHARACTER_REVISION \
  --reason "season-start relay correction" \
  --confirm production
```

`prepare` verifies migration `0005` is the only pending migration, requires all
four DLQs to be empty, records the collector and complete fixed/live schedule
states, disables those schedules, drains all three working queues, stops the
collector if necessary, and confirms stable emptiness again. The immutable
recovery state is stored with owner-only permissions under
`.admin-state/migration-0005/` and is never replaced by a retry.

The script then exits successfully with production quiesced. Push the recorded
SHA to `main`, run **Deploy production** for that revision in GitHub Actions,
and wait for the workflow to succeed. The workflow applies migration `0005`
and deploys the coordinated application; the bootstrap script does not trigger
GitHub Actions or apply migrations locally.

Continue with:

```fish
scripts/bootstrap-migration-0005.sh complete --confirm production
```

`complete` verifies the deployed CloudFormation revision and schema, opens the
new database-backed maintenance audit, installs the matching collector image,
runs the reference/live refresh and audited current-season rebaseline, closes
maintenance, and restores the original collector and schedule states. If the
collector was originally running, it sends and drains one final reference job
to reconcile current live schedules. The selected bootstrap intentionally has
no inspection pause between refresh and rebaseline.

Inspect progress at any time with:

```fish
scripts/bootstrap-migration-0005.sh status
```

Every operational phase is persisted before its mutation. On failure the
script leaves writers in their current safe state, prints the saved phase and
the exact resume command, and never restores production automatically. Rerun
that command after resolving the reported problem. Do not delete
`.admin-state/migration-0005/` until the completed audit and restored production
state have been reviewed.

The bootstrap-only Make primitives used by the script are available for
recovery, but should not replace the script in a normal run:

```fish
make aws-app-revision
make aws-schedules-bootstrap-pause \
  STATE_FILE=/absolute/path/to/schedules.json \
  CONFIRM=production
make aws-schedules-bootstrap-restore \
  STATE_FILE=/absolute/path/to/schedules.json \
  CONFIRM=production
```

After this one-off bootstrap, use the durable `prod-maintenance-*` workflow for
future maintenance and rebaseline operations.
