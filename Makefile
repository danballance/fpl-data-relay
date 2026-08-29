SHELL := /usr/bin/env
.SHELLFLAGS := bash -eu -o pipefail -c
.ONESHELL:
.NOTPARALLEL:
.DEFAULT_GOAL := help
.DELETE_ON_ERROR:

PYTHON_ARTIFACTS := $(ARTIFACTS_DIR)
COMPOSE := uv run docker compose --env-file .env
CLIENT_DEV := uv run npm --prefix client run dev
ADMIN := uv run --group admin fpl-admin --config .admin.env

export REASON CONFIRM SHA DLQ STATE_FILE

define INSTALL_DEPENDENCIES
uv python install 3.14
uv sync --frozen --group dev
uv run npm --prefix client ci
endef

.PHONY: \
	help doctor install setup \
	local-dev local-up local-client local-logs local-ps local-down \
	local-db-status local-db-migrate \
	aws-profile-bootstrap aws-profile-setup aws-profile-onboard \
	aws-profile-login aws-profile-status aws-profile-logout \
	aws-doctor aws-status aws-app-revision aws-db-status aws-db-migrate \
	aws-queues-status aws-queues-drain aws-dlqs-status aws-dlq-peek \
	aws-send-reference aws-send-live aws-send-community \
	aws-schedules-status aws-maintenance-status \
	aws-schedules-bootstrap-pause aws-schedules-bootstrap-restore \
	aws-schedules-pause aws-schedules-restore aws-rebaseline-current \
	nas-doctor nas-status nas-start nas-stop nas-logs nas-update nas-rollback \
	prod-doctor prod-status prod-maintenance-begin prod-maintenance-end \
	prod-rebaseline-current \
	lint lint-python lint-client test test-python test-client \
	check check-python check-client infra images ci \
	require-local-env require-client-env require-admin-env prepare-admin-env \
	require-production-confirm require-reason require-sha require-dlq \
	require-state-file \
	prepare-local-database \
	build-ApiFunction build-IngestionFunction build-CommunityFunction build-python

help: ## Show this help and the required developer tools.
	@printf 'FPL Data Relay\n\n'
	printf 'Execution boundaries:\n'
	printf '  local-*    Local Docker and development services only.\n'
	printf '  aws-*      Run locally and control production AWS resources.\n'
	printf '  nas-*      Run locally and control the NAS collector over SSH.\n'
	printf '  prod-*     Run locally and coordinate AWS plus the NAS.\n'
	printf '  unprefixed Repository setup, quality, and build tasks only.\n'
	printf '  deployment Run and inspect production deployment in GitHub Actions.\n\n'
	printf 'Required tools: uv, Node.js 24, npm, Docker Compose, AWS CLI >= 2.32, AWS SAM CLI\n\n'
	printf 'Targets:\n'
	awk 'BEGIN { FS = ":.*## " } /^[a-zA-Z0-9_-]+:.*## / { printf "  %-22s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

doctor: ## Verify the complete local development and CI toolchain.
	@command -v uv >/dev/null || { printf 'uv is required: https://docs.astral.sh/uv/\n' >&2; exit 1; }
	uv --version
	uv run node --eval 'const major = Number(process.versions.node.split(".")[0]); if (major !== 24) { console.error(`Node.js 24 is required; found $${process.version}`); process.exit(1); } console.log(process.version);'
	uv run npm --version
	uv run docker --version
	uv run docker compose version
	uv run aws --version
	uv run sam --version
	printf 'toolchain ok\n'

install: ## Install the locked Python and client dependencies.
	$(INSTALL_DEPENDENCIES)

setup: ## Install dependencies and create new local environment files.
	@test ! -e .env || { printf '.env already exists; use make install to refresh dependencies.\n' >&2; exit 1; }
	test ! -e client/.env.local || { printf 'client/.env.local already exists; use make install to refresh dependencies.\n' >&2; exit 1; }
	$(INSTALL_DEPENDENCIES)
	cp .env.example .env
	cp client/.env.example client/.env.local
	printf 'local environment files created\n'

local-dev: local-up require-client-env ## Start the local backend and Vite client.
	@printf 'Compose services remain running after the client stops; use make local-down.\n'
	$(CLIENT_DEV)

local-up: local-db-migrate ## Build and start the local database and API.
	@$(COMPOSE) up --detach --wait app

local-client: require-client-env ## Run the local Vite development client.
	@$(CLIENT_DEV)

local-logs: require-local-env ## Follow local API and PostgreSQL logs.
	@$(COMPOSE) logs --follow app postgres

local-ps: require-local-env ## Show local Compose service status.
	@$(COMPOSE) ps

local-down: require-local-env ## Stop local services while preserving database data.
	@$(COMPOSE) down

local-db-status: prepare-local-database ## Show applied and pending local migrations.
	@$(COMPOSE) run --rm app fpl-relay db status

local-db-migrate: prepare-local-database ## Apply all pending local migrations.
	@$(COMPOSE) run --rm app fpl-relay db apply

prepare-admin-env:
	@if test -e .admin.env; then \
		printf 'using existing .admin.env\n'; \
	else \
		cp .admin.env.example .admin.env; \
		printf 'created .admin.env from .admin.env.example\n'; \
	fi

aws-profile-bootstrap: prepare-admin-env ## Generate and attach AWS login and relay policies.
	@$(ADMIN) aws profile-bootstrap

aws-profile-setup: prepare-admin-env ## Configure and authenticate the console-login profile.
	$(ADMIN) aws profile-setup

aws-profile-onboard: aws-profile-bootstrap aws-profile-setup aws-doctor ## Bootstrap IAM, log in, and verify AWS administration.
	@printf 'AWS administrator onboarding complete\n'

aws-profile-login: require-admin-env ## Renew the admin profile's console login.
	@$(ADMIN) aws profile-login

aws-profile-status: require-admin-env ## Verify the admin profile and AWS identity.
	@$(ADMIN) aws profile-status

aws-profile-logout: require-admin-env ## Log out only the configured admin profile.
	@$(ADMIN) aws profile-logout

aws-doctor: require-admin-env ## Validate the local AWS administration connection.
	@$(ADMIN) aws doctor

aws-status: require-admin-env ## Show production AWS, schema, queue, and schedule status.
	@$(ADMIN) aws status

aws-app-revision: require-admin-env ## Show the Git revision deployed in the application stack.
	@$(ADMIN) aws app-revision

aws-db-status: require-admin-env ## Show production database migration status.
	@$(ADMIN) aws db-status

aws-db-migrate: require-admin-env require-production-confirm ## Apply production database migrations.
	@$(ADMIN) aws db-migrate --confirm "$${CONFIRM}"

aws-queues-status: require-admin-env ## Show all production working-queue depths.
	@$(ADMIN) aws queues-status

aws-queues-drain: require-admin-env ## Wait for all three working queues to drain.
	@$(ADMIN) aws queues-drain

aws-dlqs-status: require-admin-env ## Show all production dead-letter queue depths.
	@$(ADMIN) aws dlqs-status

aws-dlq-peek: require-admin-env require-dlq ## Inspect up to ten DLQ messages without deleting them.
	@$(ADMIN) aws dlq-peek --queue "$${DLQ}" --max-messages 10

aws-send-reference: require-admin-env ## Send one strict production reference job.
	@$(ADMIN) aws send-reference

aws-send-live: require-admin-env ## Send one strict current-event production live job.
	@$(ADMIN) aws send-live

aws-send-community: require-admin-env ## Send one strict production community dispatch job.
	@$(ADMIN) aws send-community

aws-schedules-status: require-admin-env ## Show fixed and dynamic production schedules.
	@$(ADMIN) aws schedules-status

aws-schedules-bootstrap-pause: require-admin-env require-production-confirm require-state-file ## Snapshot and pause schedules for the migration 0005 bootstrap.
	@$(ADMIN) aws schedules-bootstrap-pause --state-file "$${STATE_FILE}" --confirm "$${CONFIRM}"

aws-schedules-bootstrap-restore: require-admin-env require-production-confirm require-state-file ## Restore the migration 0005 bootstrap schedule snapshot.
	@$(ADMIN) aws schedules-bootstrap-restore --state-file "$${STATE_FILE}" --confirm "$${CONFIRM}"

aws-maintenance-status: require-admin-env ## Show the open or latest maintenance audit.
	@$(ADMIN) aws maintenance-status

aws-schedules-pause: require-admin-env require-production-confirm require-reason ## Open maintenance and pause AWS schedules only.
	@$(ADMIN) aws schedules-pause --reason "$${REASON}" --confirm "$${CONFIRM}"

aws-schedules-restore: require-admin-env require-production-confirm ## Restore audited AWS schedule states only.
	@$(ADMIN) aws schedules-restore --confirm "$${CONFIRM}"

aws-rebaseline-current: require-admin-env require-production-confirm require-reason ## Rebaseline the current season during active maintenance.
	@$(ADMIN) aws rebaseline-current --reason "$${REASON}" --confirm "$${CONFIRM}"

nas-doctor: require-admin-env ## Validate SSH, Compose, and Docker on the NAS.
	@$(ADMIN) nas doctor

nas-status: require-admin-env ## Show the NAS collector state, health, and image.
	@$(ADMIN) nas status

nas-start: require-admin-env require-production-confirm ## Start the NAS collector and wait for health.
	@$(ADMIN) nas start --confirm "$${CONFIRM}"

nas-stop: require-admin-env require-production-confirm ## Stop the NAS collector without removing it.
	@$(ADMIN) nas stop --confirm "$${CONFIRM}"

nas-logs: require-admin-env ## Show the configured bounded NAS collector log tail.
	@$(ADMIN) nas logs

nas-update: require-admin-env require-production-confirm require-sha ## Activate an existing immutable collector image.
	@$(ADMIN) nas update --sha "$${SHA}" --confirm "$${CONFIRM}"

nas-rollback: require-admin-env require-production-confirm require-sha ## Return the NAS collector to an explicit prior image.
	@$(ADMIN) nas rollback --sha "$${SHA}" --confirm "$${CONFIRM}"

prod-doctor: require-admin-env ## Validate both production AWS and NAS control planes.
	@$(ADMIN) prod doctor

prod-status: require-admin-env ## Show one combined AWS, database, and NAS status.
	@$(ADMIN) prod status

prod-maintenance-begin: require-admin-env require-production-confirm require-reason ## Quiesce writers, drain queues, and stop the collector.
	@$(ADMIN) prod maintenance-begin --reason "$${REASON}" --confirm "$${CONFIRM}"

prod-maintenance-end: require-admin-env require-production-confirm ## Restore the audited collector and schedule states.
	@$(ADMIN) prod maintenance-end --confirm "$${CONFIRM}"

prod-rebaseline-current: require-admin-env require-production-confirm require-reason ## Refresh normalized data and rebaseline under maintenance.
	@$(ADMIN) prod rebaseline-current --reason "$${REASON}" --confirm "$${CONFIRM}"

lint: lint-python lint-client ## Run all backend and client static checks.

lint-python:
	uv run --group dev ruff check
	uv run --group dev ty check
	uv run --group dev lint-imports

lint-client:
	uv run npm --prefix client run typecheck
	uv run npm --prefix client run lint

test: test-python test-client ## Run backend and client tests with coverage gates.

test-python:
	uv run --group dev python -m pytest --cov ./src/fpl_data_relay tests

test-client:
	uv run npm --prefix client run test:coverage

check: check-python check-client ## Run the normal backend and client quality gates.

check-python: lint-python test-python

check-client:
	uv run npm --prefix client run check

infra: ## Validate SAM and Compose infrastructure definitions.
	uv run sam validate --lint --template-file template-data.yaml --region eu-west-2
	uv run sam validate --lint --template-file template-app.yaml --region eu-west-2
	uv run docker compose --env-file .env.example config --quiet
	temporary_directory="$$(mktemp -d)"
	trap 'rm -rf -- "$$temporary_directory"' EXIT
	cp deploy/nas/compose.yaml "$$temporary_directory/compose.yaml"
	cp deploy/nas/.env.example "$$temporary_directory/.env"
	uv run docker compose \
		--file "$$temporary_directory/compose.yaml" \
		--env-file "$$temporary_directory/.env" \
		config --quiet

images: ## Build and verify the application and collector container images.
	uv run docker build --file Dockerfile --tag fpl-relay:test .
	uv run docker run --rm fpl-relay:test fpl-relay --help
	uv run docker build --file Dockerfile.collector --tag fpl-collector:test .
	uv run docker run --rm fpl-collector:test fpl-collector --help
	uv run docker run --rm fpl-collector:test python -c 'import importlib.util; assert importlib.util.find_spec("fastapi") is None'

ci: check infra images ## Run the complete local equivalent of the CI quality job.

require-local-env:
	@test -f .env || { printf '.env is required; run make setup or copy .env.example explicitly.\n' >&2; exit 1; }

require-client-env:
	@test -f client/.env.local || { printf 'client/.env.local is required; run make setup or copy client/.env.example explicitly.\n' >&2; exit 1; }

require-admin-env:
	@test -f .admin.env || { printf '.admin.env is required; copy .admin.env.example and set explicit administration values.\n' >&2; exit 1; }

require-production-confirm:
	@test "$${CONFIRM:-}" = production || { printf 'Set CONFIRM=production for this operation.\n' >&2; exit 1; }

require-reason:
	@test -n "$${REASON:-}" || { printf 'Set a nonempty REASON for this operation.\n' >&2; exit 1; }

require-sha:
	@printf '%s' "$${SHA:-}" | grep -Eq '^[0-9a-f]{40}$$' || { printf 'Set SHA to a full 40-character lowercase Git revision.\n' >&2; exit 1; }

require-dlq:
	@case "$${DLQ:-}" in fetch|result|schedule|community) ;; *) printf 'Set DLQ to fetch, result, schedule, or community.\n' >&2; exit 1 ;; esac

require-state-file:
	@test -n "$${STATE_FILE:-}" || { printf 'Set STATE_FILE to an explicit schedule snapshot path.\n' >&2; exit 1; }

prepare-local-database: require-local-env
	@$(COMPOSE) build app
	$(COMPOSE) up --detach --wait postgres

build-ApiFunction: build-python

build-IngestionFunction: build-python

build-CommunityFunction:
	test -n "$(ARTIFACTS_DIR)"
	uv export --frozen --no-dev --group aws --group community --no-emit-project \
		--format requirements-txt | \
		uv pip install --python-version 3.14 --python-platform x86_64-manylinux_2_28 \
		--target "$(PYTHON_ARTIFACTS)" --no-deps --requirements -
	cp -R src/fpl_data_relay "$(PYTHON_ARTIFACTS)/fpl_data_relay"

build-python:
	test -n "$(ARTIFACTS_DIR)"
	uv export --frozen --no-dev --group aws --no-emit-project \
		--format requirements-txt | \
		uv pip install --python-version 3.14 --python-platform x86_64-manylinux_2_28 \
		--target "$(PYTHON_ARTIFACTS)" --no-deps --requirements -
	cp -R src/fpl_data_relay "$(PYTHON_ARTIFACTS)/fpl_data_relay"
