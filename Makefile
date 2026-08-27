# Pontis — the documented command path. Every target here is what CI and the
# README mean when they say "migrate", "seed", "test".
#
# Configuration comes from the standard libpq environment variables (PGHOST,
# PGPORT, PGUSER, PGDATABASE, PGPASSWORD); defaults match ingest/db.py.

PYTHON ?= ./.venv/bin/python
PGDATABASE ?= pontis
PSQL = psql -v ON_ERROR_STOP=1 -q

.PHONY: venv db migrate seed pipeline test api clean-db all

venv:                ## create the virtualenv and install dependencies
	python3 -m venv .venv
	./.venv/bin/pip install -r requirements.txt

db:                  ## create the database (no-op if it exists)
	createdb $(PGDATABASE) 2>/dev/null || true

migrate: db          ## apply every migration, in filename order, re-runnably
	for f in schema/*.sql; do echo "== $$f"; $(PSQL) -d $(PGDATABASE) -f $$f; done

seed: migrate        ## reference data + curated schools (idempotent upserts)
	$(PYTHON) ingest/seed_reference.py
	$(PYTHON) ingest/seed_phase1.py

pipeline: seed       ## batch-ingest the roster from the College Scorecard file
	$(PYTHON) ingest/pipeline.py --csv data/Most-Recent-Cohorts-Institution.csv

test:                ## full suite (DB-backed constraint tests skip without Postgres)
	$(PYTHON) -m pytest -q

api:                 ## serve the REST API on :8000
	$(PYTHON) -m uvicorn api.main:app --host 0.0.0.0 --port 8000

clean-db:            ## drop the database (asks nothing; data is rebuildable)
	dropdb --if-exists $(PGDATABASE)

all: seed test       ## clean-room path: migrate, seed, test
