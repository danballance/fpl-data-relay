# Production Administration

The root Makefile is the supported local control surface for production
operations. Its prefixes describe where the effect occurs:

- `aws-*` runs locally using the profile configured in `.admin.env` and changes
  or inspects AWS production resources.
- `nas-*` runs locally and controls the collector on the NAS over SSH.
- `prod-*` runs locally and coordinates both AWS and the NAS.
- Deployment remains exclusively in the **Deploy production** GitHub Actions
  workflow. There are no Make deployment targets.

## Interactive console

Run the developer and production terminal interface from the repository root:

```fish
make tui
```

The console covers every operational target displayed by `make help`; `tui`
itself is the launcher exemption. It presents
typed AWS, database, queue, schedule, maintenance, and collector results as
tables and detail views; local development and quality targets retain their
native output in a managed task log. The main workspace uses a 60/40 split,
with navigation and command pages on the left and live task status, progress,
and console output on the right. Below 110 columns the task pane stacks beneath
the controls, and below 140 columns the sidebar collapses into a selector. The
minimum supported terminal remains 80 by 24. `Ctrl+P` searches by the exact
Make target name, `r` refreshes the active remote page, `?` opens help, and `q`
exits when no write or managed process is active. Remote state is never polled
automatically.

Long-running `local-dev`, `local-client`, and `local-logs` processes remain
active while navigating. **Stop** sends `SIGINT`; if the process remains alive
for five seconds, a second explicit confirmation can send `SIGTERM`. The
console never sends `SIGKILL` automatically.

TUI mutations run directly after any required reason, revision, queue, or state
file input has been collected. Only one write or local task can run at a time,
and the application cannot exit while one is active. Make and Typer retain the
literal `production` confirmation for production writes. A failed refresh
preserves the last successful snapshot, labels it stale, and displays the exact
failure without retrying.

Raw console history is retained in rotating JSONL files under
`.admin-state/tui/`. The directory and files are owner-only, but the content is
still sensitive: it includes complete subprocess output, NAS log text, and DLQ
message bodies. Five files of at most 10 MiB each are retained. Environment
file contents, credentials not printed by an underlying command, keystrokes,
and form input keystrokes are not recorded.

Make and `fpl-admin` remain available for automation and recovery. The TUI does
not expose deployment, hidden Make helpers, arbitrary shell commands, or the
unwrapped server and collector entry points.

## Initial setup

Copy the non-secret administration settings and select the AWS profile already
configured on this machine:

```fish
cp .admin.env.example .admin.env
```

`FPL_ADMIN_AWS_PROFILE` is explicit and defaults to `default` in the example.
The AWS SDK resolves that profile through the normal shared AWS configuration
and credentials files. Static credentials, IAM Identity Center, role-based
profiles, and other supported AWS SDK mechanisms are all configured and
authenticated externally.

The administration CLI and TUI never create, edit, log in to, log out of, or
delete AWS profiles. They never write credentials or create, attach, or update
IAM policies. The selected identity and its required IAM permissions are
assumed to have been provisioned already. `.admin.env` contains only non-secret
application settings and is required for production operations.

Verify the live identity and access to the configured relay resources:

```fish
make aws-doctor
```

If validation fails, correct or authenticate the selected profile with your
usual AWS tooling, then run the doctor again. The doctor reports the identity
that AWS actually returns; the TUI shows the same live account and principal
when production state is refreshed. NAS authentication remains in OpenSSH
configuration.

The NAS SSH target should be an alias in `~/.ssh/config`. The remote account
must be able to run the configured Docker and Docker Compose executables without
an interactive prompt.

Validate each boundary:

```fish
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
