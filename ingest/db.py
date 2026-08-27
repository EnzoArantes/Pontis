"""Database connection helper.

Config comes from standard libpq environment variables so that NO credentials
ever live in the repo:

    PGHOST      default localhost
    PGPORT      default 5432
    PGUSER      default the OS user
    PGDATABASE  default pontis

The password is deliberately not read here at all. libpq picks it up from
~/.pgpass (chmod 600), which is the same mechanism psql uses. That keeps the
secret in one place, outside the project tree, and out of git history.
"""

from __future__ import annotations

import os

import psycopg

DEFAULTS = {
    "host": os.environ.get("PGHOST", "localhost"),
    "port": os.environ.get("PGPORT", "5432"),
    "user": os.environ.get("PGUSER", os.environ.get("USER", "")),
    "dbname": os.environ.get("PGDATABASE", "pontis"),
}


def connect(**overrides) -> psycopg.Connection:
    """Open a connection to the Pontis database."""
    params = {**DEFAULTS, **overrides}
    return psycopg.connect(**params)
