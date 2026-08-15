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
the AWS SAM CLI, GNU Make, and the GitHub CLI. Check it before starting:

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

Start PostgreSQL and the API, apply pending migrations, and run the Vite client:

```fish
make dev
```

The API is available at <http://127.0.0.1:8000> and the client at
<http://127.0.0.1:5173>. Stopping Vite leaves the Compose services running so
they can be reused; stop them without deleting PostgreSQL data with:

```fish
make down
```

## Common commands

Run `make` or `make help` for the complete command list. The main workflows are:

| Command | Purpose |
| --- | --- |
| `make up` | Build and start the database and API, including migrations. |
| `make client` | Run only the Vite development client. |
| `make logs` | Follow API and PostgreSQL logs. |
| `make db-status` | Show applied and pending local migrations. |
| `make lint` | Run all Python and client static checks. |
| `make test` | Run Python and client coverage suites. |
| `make check` | Run the normal backend and client quality gate. |
| `make ci` | Reproduce the complete CI quality job, including infrastructure and images. |

## Production deployment

Production deployment remains a guarded GitHub Actions operation. It deploys
the remote `main` commit only after that commit has passed CI and has an
immutable collector image:

```fish
make deploy
make deploy-status
```

AWS credentials remain in the protected GitHub `production` environment; the
Make target does not perform a direct local AWS deployment.

## Documentation

- [Architecture](docs/architecture.md)
- [HTTP API](docs/api.md)
- [React client](docs/client.md)
- [AWS deployment and recovery](docs/deployment.md)
- [NAS collector](docs/collector.md)
