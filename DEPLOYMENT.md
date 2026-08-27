# Deployment

Pontis deploys as two containers: Postgres 18 and the application image (a
multi-stage build: Node builds the React app, the Python stage serves it with
FastAPI alongside the API). Migrations and seeds run at container start — they
are idempotent by design, so a restart converges instead of duplicating.

## The Linux path

```sh
docker compose up --build          # db + app; UI and API on :8000
docker compose run --rm app test   # the full test suite, inside the container,
                                   # against the containerized database
```

If something already holds port 8000 on the host:

```sh
PONTIS_PORT=8200 docker compose up --build
```

The app container waits for Postgres (`pg_isready`), applies `schema/*.sql` in
filename order — the same files, same order, as local development and CI; there
is no second migration mechanism to drift — runs both seeds, and serves. The
entrypoint's other modes (`test`, `migrate`) reuse the identical sequence.

## Configuration

Everything is standard libpq environment variables (`PGHOST`, `PGPORT`,
`PGUSER`, `PGPASSWORD`, `PGDATABASE`). The compose file's default password is
a dev-only value scoped to the compose network; real deployments supply
`POSTGRES_PASSWORD` from the environment. No credentials exist in the image or
the repository.

Batch ingestion (`ingest/pipeline.py`) is not part of container start — it is
an operator action, run against a downloaded College Scorecard file (see
README), and its per-school PASS/FLAG/FAIL report is meant to be read.

## What this setup is, honestly

A containerized Python/Postgres application, built and run end-to-end on Linux
(developed on macOS via a colima Linux VM; the same compose file is what a
Linux host runs natively, and CI exercises the identical migrate→seed→test
sequence on an ubuntu runner with a Postgres service on every push). It is a
single-host deployment: no orchestration, no replicas, no claims beyond what
is here.
