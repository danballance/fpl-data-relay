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
	dev up client logs ps down db-status db-apply \
	lint lint-python lint-client test test-python test-client \
	check check-python check-client infra images ci deploy deploy-status \
	require-root-env require-client-env prepare-local-database \
	build-ApiFunction build-IngestionFunction build-python

help: ## Show this help and the required developer tools.
	@printf 'FPL Data Relay\n\n'
	printf 'Required tools: uv, Node.js 24, npm, Docker Compose, AWS SAM CLI, GitHub CLI\n\n'
	printf 'Targets:\n'
	awk 'BEGIN { FS = ":.*## " } /^[a-zA-Z0-9_-]+:.*## / { printf "  %-15s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

doctor: ## Verify the complete local development and deployment toolchain.
	@command -v uv >/dev/null || { printf 'uv is required: https://docs.astral.sh/uv/\n' >&2; exit 1; }
	uv --version
	uv run node --eval 'const major = Number(process.versions.node.split(".")[0]); if (major !== 24) { console.error(`Node.js 24 is required; found $${process.version}`); process.exit(1); } console.log(process.version);'
	uv run npm --version
	uv run docker --version
	uv run docker compose version
	uv run sam --version
	uv run gh --version
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

dev: up require-client-env ## Start the backend, then run the Vite client in the foreground.
	@printf 'Compose services will remain running after the client stops; use make down.\n'
	$(CLIENT_DEV)

up: db-apply ## Build and start the database and API, waiting until both are ready.
	@$(COMPOSE) up --detach --wait app

client: require-client-env ## Run the Vite development client.
	@$(CLIENT_DEV)

logs: require-root-env ## Follow local API and PostgreSQL logs.
	@$(COMPOSE) logs --follow app postgres

ps: require-root-env ## Show local Compose service status.
	@$(COMPOSE) ps

down: require-root-env ## Stop local services while preserving database data.
	@$(COMPOSE) down

db-status: prepare-local-database ## Show applied and pending local migrations.
	@$(COMPOSE) run --rm app fpl-relay db status

db-apply: prepare-local-database ## Apply all pending local migrations.
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

deploy: ## Dispatch the guarded production workflow for the main branch.
	uv run gh auth status
	uv run gh workflow run deploy-production.yaml --ref main
	printf 'production deployment dispatched; use make deploy-status to inspect it\n'

deploy-status: ## List recent production deployment workflow runs.
	uv run gh auth status
	uv run gh run list \
		--workflow deploy-production.yaml \
		--branch main \
		--event workflow_dispatch \
		--limit 10

require-root-env:
	@test -f .env || { printf '.env is required; run make setup or copy .env.example explicitly.\n' >&2; exit 1; }

require-client-env:
	@test -f client/.env.local || { printf 'client/.env.local is required; run make setup or copy client/.env.example explicitly.\n' >&2; exit 1; }

prepare-local-database: require-root-env
	@$(COMPOSE) build app
	$(COMPOSE) up --detach --wait postgres

build-ApiFunction: build-python

build-IngestionFunction: build-python

build-python:
	test -n "$(ARTIFACTS_DIR)"
	uv export --frozen --no-dev --group aws --no-emit-project \
		--format requirements-txt | \
		uv pip install --python-version 3.14 --python-platform x86_64-manylinux_2_28 \
		--target "$(PYTHON_ARTIFACTS)" --no-deps --requirements -
	cp -R src/fpl_data_relay "$(PYTHON_ARTIFACTS)/fpl_data_relay"
