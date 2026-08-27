# Pontis

*Pontis (Latin, genitive of pons: "of the bridge").* A college-matching tool
for low-income students: which schools can you both **get into** and
**afford** — two separate questions, answered separately, never blended into
a match score.

The core finding the tool exists to surface: for a high-need student, sticker
price lies. Boston College's $60k+ sticker resolves to **$4,284/year** for a
$0–30k family; MIT's resolves to **−$2,533** (grant aid exceeds the cost of
attendance). Meanwhile Georgia State — an "affordable" in-state public —
costs its own low-income resident **$13,787/year** against a realistic
ceiling of about $7,125. Pontis says these things out loud, with sources.

Design principles (see `ARCHITECTURE.md` for the full treatment):

- **Two axes, never one score.** Admissions category and affordability
  verdict travel together but never merge.
- **Honesty over false precision.** "Not published" and "unknown" are real,
  displayed values. No GPA is ever converted between scales; no percentile is
  ever interpolated; no out-of-state student is ever quoted an in-state price.
- **Every number has a source.** Rows carry `source_url`, a verification
  tier, a verbatim quote where prose exists, the data year, and the ingestion
  date. Verified at the source or flagged as pending — never assumed.

## Quickstart

Requires Python 3.12+ and PostgreSQL (developed against Postgres 18; the
`btree_gist` contrib extension must be available, as it ships by default).

```sh
make venv        # create ./.venv and install dependencies
make seed        # create DB, apply migrations in order, run both seeds
make test        # full suite; DB-backed tests skip if Postgres is absent
```

Database connection uses the standard libpq environment variables (`PGHOST`,
`PGPORT`, `PGUSER`, `PGDATABASE`, `PGPASSWORD`) — no credentials live in this
repository. Defaults: localhost, your OS user, database `pontis`.

### Batch ingestion

Cost data comes from the College Scorecard institution-level file
(https://collegescorecard.ed.gov/data/ → "Most Recent Institution-Level
Data"). Download and unzip it to `data/Most-Recent-Cohorts-Institution.csv`
(the `data/` directory is gitignored), then:

```sh
make pipeline    # or: ./.venv/bin/python ingest/pipeline.py --csv ... --dry-run
```

Every school on the roster is validated (identity by IPEDS UNITID with an
expected-state tripwire, per-band prices only, suppression handled as honest
absence) and reported PASS / FLAG / FAIL, one line per school. The run is
idempotent.

### API and frontend

```sh
make api                       # REST API on :8000 (interactive docs at /docs)
cd web && npm install && npm run dev   # React dev server, proxying to :8000
```

Containerized path (app + Postgres, per `DEPLOYMENT.md`):

```sh
docker compose up --build
```

## Repository map

```
schema/     numbered migrations, applied in order, all re-runnable
engine/     the matching engine -- pure functions, no I/O
ingest/     seeds (curated, per-school) + the batch pipeline (roster-wide)
api/        FastAPI service exposing the engine over REST
web/        React single-page app
tests/      engine invariants, seed guards, pipeline validation,
            input robustness, live DB constraint proofs
```

`ARCHITECTURE.md` explains the schema grain, the two-axis engine, and the
honesty invariants. `HARDENING.md` records what the audit proved, what it
found, and which residual risks are accepted deliberately. `DEPLOYMENT.md`
covers the container/Linux path. Current metrics (school count, migration
count, test count) are answered by the repo itself rather than hardcoded
here: `psql -d pontis -c 'select count(*) from colleges'`, `ls schema/`,
`make test`.
