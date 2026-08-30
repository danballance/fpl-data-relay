# FPL Data Relay

## Live Explorer and API

- **[Open the FPL Data Relay Explorer](https://d3nlodyhp5cnr8.cloudfront.net/)**
  to browse the latest players, teams, fixtures, gameweeks, and ingestion data.
- **[Explore the interactive Swagger API documentation](https://d3nlodyhp5cnr8.cloudfront.net/api/docs)**
  to inspect the read-only HTTP API and try requests against the live data.

FPL Data Relay is a self-hosted relay for public Fantasy Premier League data.
It stores normalized reference and live data in PostgreSQL, exposes a read-only
HTTP API and React client, and can run locally or as an AWS serverless
deployment with collection performed by a NAS worker.

## Getting started

The supported developer toolchain is uv, Node.js 24 with npm, Docker Compose,
the AWS SAM CLI, and GNU Make. Check it before starting:

```fish
make doctor
```

For a new checkout, install the locked dependencies and create local
environment files from the checked-in examples:

```fish
make setup
```

`setup` never overwrites `.env` or `client/.env.local`. If those files already
exist, refresh only the dependencies with `make install`.

Launch the interactive developer and production console with:

```fish
make tui
```

The TUI exposes every operational command shown by `make help` (`tui` itself is
the launcher exemption), while Make and the Typer CLIs remain the supported
scriptable interfaces. Production data is refreshed only on request. TUI
mutations run directly after collecting their required inputs; Make and Typer
retain their explicit `production` confirmation for production writes.

Production administration uses the exact AWS profile named in `.admin.env`.
Copy `.admin.env.example` to `.admin.env` and edit it if needed; credentials and
IAM permissions are managed with standard AWS tooling outside this project.

Start PostgreSQL and the API, apply pending migrations, and run the Vite client:

```fish
make local-dev
```

The API is available at <http://127.0.0.1:8000> and the client at
<http://127.0.0.1:5173>. Stopping Vite leaves the Compose services running so
they can be reused; stop them without deleting PostgreSQL data with:

```fish
make local-down
```

## Common commands

Run `make` or `make help` for the complete command list. The main workflows are:

| Command | Purpose |
| --- | --- |
| `make tui` | Open the interactive local and production operations console. |
| `make local-up` | Build and start the local database and API, including migrations. |
| `make local-client` | Run only the local Vite development client. |
| `make local-logs` | Follow local API and PostgreSQL logs. |
| `make local-db-status` | Show applied and pending local migrations. |
| `make aws-doctor` | Verify the configured AWS profile and production resources. |
| `make lint` | Run all Python and client static checks. |
| `make test` | Run Python and client coverage suites. |
| `make check` | Run the normal backend and client quality gate. |
| `make ci` | Reproduce the complete CI quality job, including infrastructure and images. |

## Production deployment

Production deployment remains a guarded GitHub Actions operation. Run and
inspect **Deploy production** from GitHub after the remote `main` commit has
passed CI and published its immutable collector image. The Makefile deliberately
contains no deployment target.

## Documentation

- [Architecture](docs/architecture.md)
- [HTTP API](docs/api.md)
- [React client](docs/client.md)
- [AWS deployment and recovery](docs/deployment.md)
- [NAS collector](docs/collector.md)
- [Production administration](docs/administration.md)
