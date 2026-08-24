SHELL := /usr/bin/env
.SHELLFLAGS := bash -eu -o pipefail -c
.ONESHELL:
.NOTPARALLEL:
.DEFAULT_GOAL := help
.DELETE_ON_ERROR:

PYTHON_ARTIFACTS := $(ARTIFACTS_DIR)
COMPOSE := uv run docker compose --env-file .env
CLIENT_DEV := uv run npm --prefix client run dev

define INSTALL_DEPENDENCIES
uv python install 3.14
uv sync --frozen --group dev
uv run npm --prefix client ci
endef

.PHONY: \
	help doctor install setup \
	local-dev local-up local-client local-logs local-ps local-down \
	local-db-status local-db-migrate \
	lint lint-python lint-client test test-python test-client \
	check check-python check-client infra images ci \
	require-local-env require-client-env prepare-local-database \
	build-ApiFunction build-IngestionFunction build-CommunityFunction build-python

help: ## Show this help and the required developer tools.
	@printf 'FPL Data Relay\n\n'
	printf 'Execution boundaries:\n'
	printf '  local-*    Local Docker and development services only.\n'
	printf '  unprefixed Repository setup, quality, and build tasks only.\n'
	printf '  deployment Run and inspect production deployment in GitHub Actions.\n\n'
	printf 'Required tools: uv, Node.js 24, npm, Docker Compose, AWS SAM CLI\n\n'
	printf 'Targets:\n'
	awk 'BEGIN { FS = ":.*## " } /^[a-zA-Z0-9_-]+:.*## / { printf "  %-22s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

doctor: ## Verify the complete local development and CI toolchain.
	@command -v uv >/dev/null || { printf 'uv is required: https://docs.astral.sh/uv/\n' >&2; exit 1; }
	uv --version
	uv run node --eval 'const major = Number(process.versions.node.split(".")[0]); if (major !== 24) { console.error(`Node.js 24 is required; found $${process.version}`); process.exit(1); } console.log(process.version);'
	uv run npm --version
	uv run docker --version
	uv run docker compose version
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
