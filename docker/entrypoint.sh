#!/bin/sh
# Pontis container entrypoint.
#
#   serve  (default)  wait for Postgres, migrate, seed, serve the API
#   test              wait for Postgres, migrate, seed, run the full suite
#   migrate           wait for Postgres, migrate + seed only, then exit
#
# Migrations and seeds are idempotent by design, so running them on every
# start is safe and keeps the container path identical to the documented
# local path: same files, same order.

set -e

wait_for_db() {
    echo "waiting for postgres at ${PGHOST:-localhost}:${PGPORT:-5432}..."
    i=0
    until pg_isready -q; do
        i=$((i + 1))
        [ "$i" -gt 60 ] && echo "postgres never became ready" && exit 1
        sleep 1
    done
}

migrate_and_seed() {
    for f in schema/*.sql; do
        echo "== $f"
        psql -v ON_ERROR_STOP=1 -q -f "$f"
    done
    python ingest/seed_reference.py
    python ingest/seed_phase1.py
}

case "${1:-serve}" in
    serve)
        wait_for_db
        migrate_and_seed
        exec python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
        ;;
    test)
        wait_for_db
        migrate_and_seed
        exec python -m pytest -q
        ;;
    migrate)
        wait_for_db
        migrate_and_seed
        ;;
    *)
        exec "$@"
        ;;
esac
