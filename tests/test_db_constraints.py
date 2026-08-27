"""Phase A — every honesty rule that claims to be structural must actually fire.

These tests attempt the exact violations the schema exists to forbid and assert
the database rejects them. A constraint nobody ever watched reject a row is a
convention, not a guarantee -- this file is where each one earns the word
"structural". Every attempt runs in a transaction that is rolled back, so the
database is left exactly as found.

Requires a reachable Pontis database (migrations 001-008 applied, seed run for
the one fixture school). Skips cleanly -- not passes -- when there is none, so
an environment without Postgres shows these as SKIPPED rather than silently
green.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

psycopg = pytest.importorskip("psycopg")

from ingest.db import connect  # noqa: E402


@pytest.fixture(scope="module")
def db():
    try:
        conn = connect()
    except psycopg.OperationalError as exc:
        pytest.skip(f"no reachable Pontis database: {exc}")
    yield conn
    conn.close()


@pytest.fixture()
def college_id(db):
    with db.cursor() as cur:
        cur.execute("SELECT id FROM colleges ORDER BY id LIMIT 1")
        row = cur.fetchone()
    if row is None:
        pytest.skip("colleges table is empty; run the seed first")
    return row[0]


def rejects(db, expected_fragment: str, sql: str, params: tuple):
    """Assert the INSERT violates a constraint whose name contains the fragment."""
    with db.transaction():
        with db.cursor() as cur:
            with pytest.raises(psycopg.errors.Error) as excinfo:
                cur.execute(sql, params)
            assert expected_fragment in str(excinfo.value), (
                f"rejected, but not by the expected guard "
                f"({expected_fragment!r}): {excinfo.value}"
            )
            # A raised error aborts the inner transaction; rolling back the
            # savepoint happens automatically when the context exits via raise.
            raise _Rollback()


class _Rollback(Exception):
    """Force the outer transaction block to roll back after the assertion."""


def check(db, fragment, sql, params):
    with pytest.raises(_Rollback):
        rejects(db, fragment, sql, params)


STATS_SQL = """
    INSERT INTO admission_stats
        (college_id, year, acceptance_rate, gpa_value, gpa_type, gpa_p25,
         gpa_p75, class_rank_top_pct, class_rank_share,
         class_rank_reporting_share, source_url, source_tier)
    VALUES (%s, 2099, %s, %s, %s, %s, %s, %s, %s, %s, 'x', 'secondary_corroborated')
"""


# ---------------------------------------------------------------------------
# admission_stats — the original honesty constraint and its v007 sibling
# ---------------------------------------------------------------------------


def test_not_published_cannot_smuggle_a_number(db, college_id):
    """BC's CDS literally prints 0.00 for GPA; storing it would be the worst
    lie in the system."""
    check(db, "gpa_honesty", STATS_SQL,
          (college_id, None, "0.00", "not_published", None, None, None, None, None))


def test_declared_scale_must_carry_a_figure(db, college_id):
    for scale in ("unweighted", "uc_weighted_capped", "weighted"):
        check(db, "gpa_honesty", STATS_SQL,
              (college_id, None, None, scale, None, None, None, None, None))


def test_half_a_percentile_pair_is_rejected(db, college_id):
    """gpa_value is supplied so gpa_honesty is satisfied -- only the pair rule
    can catch the dangling p25."""
    check(db, "percentile_pair", STATS_SQL,
          (college_id, None, "3.5", "unweighted", "3.2", None, None, None, None))


def test_inverted_percentile_pair_is_rejected(db, college_id):
    check(db, "percentile_pair", STATS_SQL,
          (college_id, None, None, "unweighted", "3.9", "3.5", None, None, None))


def test_acceptance_rate_beyond_one_is_rejected(db, college_id):
    check(db, "rate_range", STATS_SQL,
          (college_id, "1.5", "3.5", "unweighted", None, None, None, None, None))


def test_rank_band_without_share_is_rejected(db, college_id):
    check(db, "class_rank_pair", STATS_SQL,
          (college_id, None, None, "not_published", None, None, "10", None, None))


def test_rank_coverage_without_a_band_is_rejected(db, college_id):
    check(db, "class_rank_coverage", STATS_SQL,
          (college_id, None, None, "not_published", None, None, None, None, "0.5"))


# ---------------------------------------------------------------------------
# gpa_band_distribution (v008)
# ---------------------------------------------------------------------------

BAND_SQL = """
    INSERT INTO gpa_band_distribution
        (college_id, year, gpa_type, population, band_floor, band_ceiling,
         share, source_url, source_tier)
    VALUES (%s, 2099, %s, %s, %s, %s, %s, 'x', 'secondary_corroborated')
"""


def test_bands_on_a_non_numeric_scale_are_rejected(db, college_id):
    """The v007 lesson (enum admits what the rule forbids), guarded on day one."""
    for scale in ("not_published", "class_rank_proxy"):
        check(db, "scale_is_numeric", BAND_SQL,
              (college_id, scale, "enrolled", "3.0", "3.5", "0.5"))


def test_inverted_band_is_rejected(db, college_id):
    check(db, "floor_lte_ceiling", BAND_SQL,
          (college_id, "unweighted", "enrolled", "3.9", "3.5", "0.5"))


def test_share_beyond_one_is_rejected(db, college_id):
    check(db, "share", BAND_SQL,
          (college_id, "unweighted", "enrolled", "3.0", "3.5", "1.5"))


def test_overlapping_bands_are_rejected(db, college_id):
    """UNIQUE on band_floor cannot see [3.0-3.5] vs [3.2-3.7]; the range
    exclusion must."""
    with pytest.raises(_Rollback):
        with db.transaction():
            with db.cursor() as cur:
                cur.execute(BAND_SQL, (college_id, "unweighted", "enrolled",
                                       "3.0", "3.5", "0.5"))
                with pytest.raises(psycopg.errors.ExclusionViolation):
                    cur.execute(BAND_SQL, (college_id, "unweighted", "enrolled",
                                           "3.2", "3.7", "0.5"))
                raise _Rollback()


# ---------------------------------------------------------------------------
# colleges, net_price_by_income, majors, auto-admit, provenance
# ---------------------------------------------------------------------------


def test_two_rows_cannot_claim_one_institution(db):
    with pytest.raises(_Rollback):
        with db.transaction():
            with db.cursor() as cur:
                cur.execute("SELECT ipeds_unitid FROM colleges LIMIT 1")
                taken = cur.fetchone()[0]
                with pytest.raises(psycopg.errors.UniqueViolation):
                    cur.execute(
                        """INSERT INTO colleges (ipeds_unitid, name, state,
                               is_public, meets_full_need, css_profile_required,
                               source_url, source_tier)
                           VALUES (%s, 'Impostor U', 'ZZ', true, false, false,
                                   'x', 'secondary_corroborated')""",
                        (taken,),
                    )
                raise _Rollback()


def test_college_without_identity_is_rejected(db):
    check(db, "ipeds_unitid",
          """INSERT INTO colleges (ipeds_unitid, name, state, is_public,
                 meets_full_need, css_profile_required, source_url, source_tier)
             VALUES (NULL, 'Ghost U', 'ZZ', true, false, false, 'x',
                     'secondary_corroborated')""",
          ())


NET_SQL = """
    INSERT INTO net_price_by_income
        (college_id, income_band, residency, net_price_within_band,
         source_url, source_tier)
    VALUES (%s, %s, %s, %s, 'x', 'secondary_corroborated')
"""


def test_invented_income_band_is_rejected(db, college_id):
    check(db, "income_band", NET_SQL, (college_id, "0-25k", "in_state", 5000))


def test_net_price_sanity_bounds(db, college_id):
    """Since v010 a NEGATIVE net price is legal -- MIT's $0-30k band really is
    -$2,533 (grant aid exceeds cost of attendance) -- but implausible
    magnitudes still reject, so a typo cannot ride in on the widened rule."""
    check(db, "net_price_within_band_sane",
          NET_SQL, (college_id, "0-30k", "out_of_state", -70000))
    check(db, "net_price_within_band_sane",
          NET_SQL, (college_id, "0-30k", "out_of_state", 200000))
    # And the real MIT-shaped value goes in fine (rolled back).
    with pytest.raises(_Rollback):
        with db.transaction():
            with db.cursor() as cur:
                cur.execute(NET_SQL, (college_id, "0-30k", "out_of_state", -2533))
                raise _Rollback()


def test_bare_major_gpa_number_without_scale_is_rejected(db, college_id):
    check(db, "gpa_honesty",
          """INSERT INTO majors (college_id, major_name, competitiveness,
                 major_gpa_value, major_gpa_type, source_url, source_tier)
             VALUES (%s, 'Test Major', 'standard', 3.5, NULL, 'x',
                     'secondary_corroborated')""",
          (college_id,))


def test_auto_admit_threshold_must_be_a_real_percentage(db, college_id):
    for bad in ("0", "101"):
        check(db, "threshold",
              """INSERT INTO class_rank_auto_admit
                     (college_id, effective_cycle, resident_state,
                      threshold_top_pct, guarantees_university,
                      guarantees_major, source_url, source_tier)
                 VALUES (%s, 'fall-2099', 'TX', %s, true, false, 'x',
                         'secondary_corroborated')""",
              (college_id, bad))


def test_source_quote_word_limit_is_enforced(db, college_id):
    """Spec S3: a 'quote' longer than 15 words is a paraphrase wearing quotes."""
    long_quote = " ".join(["word"] * 16)
    check(db, "quote_len",
          NET_SQL.replace("source_tier)", "source_tier, source_quote)")
                 .replace("'secondary_corroborated')", "'secondary_corroborated', %s)"),
          (college_id, "0-30k", "in_state", 5000, long_quote))
