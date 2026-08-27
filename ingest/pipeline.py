"""Batch ingestion: N schools through one governed path.

Reads the College Scorecard "Most Recent Institution-Level Data" file and
ingests income-banded net price for every school on the roster, with the same
honesty rules the hand-built seed obeys -- now enforced by code on every school
instead of by care on each one:

  * Identity is UNITID, never name. The roster carries the expected state as a
    tripwire: if the row a UNITID resolves to sits in a different state, the
    school FAILS ingestion rather than trusting that the right campus was bound.
    (The file contains "Northeastern University Oakland" in CA, "University of
    Massachusetts Global" in CA, and three Georgia institutions that a name
    match cannot separate -- the traps are real.)
  * Only the per-band fields NPT41..NPT45 are ingested. The overall average
    NPT4 is read too, but ONLY to prove it was not what got seeded: a school
    whose five bands collapse to one identical value fails hard, because that
    is the exact signature of the wrong-field substitution.
  * A suppressed or missing band is a FLAG and an absent row -- never zero,
    never an interpolation. The engine reads an absent row as unknown.
  * A NEGATIVE band value is real (grant aid exceeding cost of attendance; see
    schema/010) -- ingested as published, flagged for visibility.
  * Public prices are in-state only (the federal metric counts in-state payers
    only); privates are residency-independent. Out-of-state rows are never
    written because the source does not publish them.

What this pipeline deliberately does NOT ingest:

  * Admission stats. Writing an admission_stats row requires declaring a
    gpa_type, and "not_published" is a claim about what the school publishes --
    a claim reading a federal cost file cannot support. GPA signals stay
    per-school curated work against each school's own publications.
  * Aid flags for NEW schools. meets_full_need / css_profile_required are not
    in the federal file, so new schools enter conservatively as False/False
    (never overpromising the affordability gate) pending a primary check of
    each school's aid pages. Existing curated flags are NEVER overwritten:
    the college upsert updates only what the federal file is authoritative for.

Run:
    ./.venv/bin/python ingest/pipeline.py --csv data/Most-Recent-Cohorts-Institution.csv
    ./.venv/bin/python ingest/pipeline.py --csv ... --dry-run   # validate only

Idempotent: UPSERTs keyed on UNITID / (college, band, residency). Exit code is
non-zero if any school FAILS, so a scheduler or CI can see a bad run.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import connect  # noqa: E402

SCORECARD_URL = "https://collegescorecard.ed.gov/data/"
# The release glossary states NPT4* describe the "2023-24 award year cohort".
SCORECARD_DATA_YEAR = 2023
SCORECARD_QUOTE = "NPT4_PUB, 2023-24 award year cohort"

BANDS = ["0-30k", "30-48k", "48-75k", "75-110k", "110k+"]

# Scorecard prints these where a value exists but is withheld or absent.
MISSING_VALUES = {"", "NA", "NULL", "PrivacySuppressed"}

# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------
# (unitid, expected_state). Names are deliberately absent: the file's INSTNM is
# authoritative and is recorded from the file at ingestion. expected_state is
# the identity tripwire described in the module docstring.
ROSTER: list[tuple[int, str]] = [
    # The original curated five (re-ingested through the governed path; values
    # must land identical to the hand-checked seed, which the seed-constant
    # tests still lock).
    (164924, "MA"),   # Boston College
    (110635, "CA"),   # University of California-Berkeley
    (228778, "TX"),   # The University of Texas at Austin
    (166629, "MA"),   # University of Massachusetts-Amherst
    (139940, "GA"),   # Georgia State University (Atlanta -- NOT Perimeter)
    # Massachusetts anchors: in-state publics an MA student can actually price.
    (166638, "MA"),   # University of Massachusetts-Boston
    (166513, "MA"),   # University of Massachusetts-Lowell
    # Massachusetts privates: the meets-full-need archetype at every intensity,
    # including the negative-net-price cases the schema had to grow to hold.
    (166027, "MA"),   # Harvard University
    (166683, "MA"),   # Massachusetts Institute of Technology
    (167358, "MA"),   # Northeastern University (Boston -- NOT the Oakland, CA campus)
    (164988, "MA"),   # Boston University
    (168148, "MA"),   # Tufts University
    (168218, "MA"),   # Wellesley College
    (164465, "MA"),   # Amherst College (private -- NOT UMass-Amherst)
    (168342, "MA"),   # Williams College
    # California anchors.
    (110662, "CA"),   # University of California-Los Angeles
    (110644, "CA"),   # University of California-Davis
    (110583, "CA"),   # California State University-Long Beach
    (122755, "CA"),   # San Jose State University
    (110422, "CA"),   # Cal Poly San Luis Obispo
    (243744, "CA"),   # Stanford University
    (122931, "CA"),   # Santa Clara University
]


# ---------------------------------------------------------------------------
# Pure validation layer (unit-tested without a database)
# ---------------------------------------------------------------------------


@dataclass
class SchoolResult:
    unitid: int
    name: str = ""
    state: str = ""
    is_public: Optional[bool] = None
    band_prices: dict[str, int] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def report_line(self) -> str:
        if self.failures:
            return f"FAIL  {self.unitid}  {self.name or '?'}: " + "; ".join(self.failures)
        status = "FLAG" if self.flags else "PASS"
        line = (
            f"{status}  {self.unitid}  {self.name} ({self.state}, "
            f"{'public' if self.is_public else 'private'}) -- "
            f"{len(self.band_prices)}/5 bands"
        )
        if self.flags:
            line += ": " + "; ".join(self.flags)
        return line


def parse_money(raw: str) -> Optional[int]:
    """A Scorecard money cell: an int, or None where withheld/absent."""
    if raw is None or raw.strip() in MISSING_VALUES:
        return None
    return int(round(float(raw)))


def validate_school(unitid: int, expected_state: str,
                    row: Optional[dict]) -> SchoolResult:
    """Every honesty check for one school, on the raw CSV row.

    Returns a SchoolResult carrying either the validated values or the reasons
    nothing may be written for this school.
    """
    result = SchoolResult(unitid=unitid)

    if row is None:
        result.failures.append("UNITID not found in the Scorecard file")
        return result

    result.name = row["INSTNM"]
    result.state = row["STABBR"]

    # Identity tripwire: the wrong campus is the failure this catches.
    if result.state != expected_state:
        result.failures.append(
            f"resolved to {result.name} in {result.state}, expected "
            f"{expected_state} -- wrong institution bound to this UNITID?"
        )
        return result

    control = row["CONTROL"]
    if control == "1":
        result.is_public = True
    elif control in ("2", "3"):
        result.is_public = False
        if control == "3":
            result.failures.append(
                "for-profit institution -- outside Pontis's scope"
            )
            return result
    else:
        result.failures.append(f"unrecognised CONTROL value {control!r}")
        return result

    suffix = "PUB" if result.is_public else "PRIV"
    overall = parse_money(row.get(f"NPT4_{suffix}", ""))

    prices: dict[str, int] = {}
    for i, band in enumerate(BANDS, start=1):
        value = parse_money(row.get(f"NPT4{i}_{suffix}", ""))
        if value is None:
            result.flags.append(f"band {band} suppressed/missing -- left unknown")
            continue
        prices[band] = value
        if value < 0:
            result.flags.append(
                f"band {band} is negative ({value}): grant aid exceeds cost "
                f"of attendance -- real, ingested as published"
            )

    if not prices:
        result.failures.append("no per-band net price published at all")
        return result

    # The wrong-field signature: five identical values is what seeding the
    # overall average (NPT4) in place of the per-band series looks like.
    if len(prices) == 5 and len(set(prices.values())) == 1:
        result.failures.append(
            f"all five bands identical ({next(iter(prices.values()))}) -- "
            f"signature of the overall average being substituted for the "
            f"per-band series"
        )
        return result

    # Weaker cross-check, flag not fail: the poorest band at or above the
    # all-student average contradicts the premise of need-based pricing.
    if overall is not None and "0-30k" in prices and prices["0-30k"] >= overall:
        result.flags.append(
            f"$0-30k band ({prices['0-30k']}) is not below the overall "
            f"average ({overall}) -- worth a human look"
        )

    result.band_prices = prices
    return result


# ---------------------------------------------------------------------------
# I/O layer
# ---------------------------------------------------------------------------


def load_rows(csv_path: Path, unitids: set[int]) -> dict[int, dict]:
    """One streaming pass over the (large) file; keep only roster rows."""
    found: dict[int, dict] = {}
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            uid = int(row["UNITID"])
            if uid in unitids:
                found[uid] = row
    return found


def write_school(cur, result: SchoolResult) -> None:
    """Upsert one validated school. Never touches curated aid flags."""
    cur.execute(
        """
        INSERT INTO colleges
            (ipeds_unitid, name, state, is_public, meets_full_need,
             css_profile_required, source_url, source_tier, source_quote)
        VALUES (%s, %s, %s, %s, false, false, %s, 'primary_verified', %s)
        ON CONFLICT (ipeds_unitid) DO UPDATE SET
            -- Only what the federal file is authoritative for. Aid flags are
            -- curated per school and must survive a batch re-run untouched.
            name          = EXCLUDED.name,
            state         = EXCLUDED.state,
            is_public     = EXCLUDED.is_public,
            date_ingested = CURRENT_DATE
        RETURNING id
        """,
        (result.unitid, result.name, result.state, result.is_public,
         SCORECARD_URL, SCORECARD_QUOTE),
    )
    college_id = cur.fetchone()[0]

    residency = "in_state" if result.is_public else "not_applicable"
    for band, price in result.band_prices.items():
        cur.execute(
            """
            INSERT INTO net_price_by_income
                (college_id, income_band, residency, net_price_within_band,
                 source_url, source_tier, source_quote, data_year)
            VALUES (%s, %s, %s, %s, %s, 'primary_verified', %s, %s)
            ON CONFLICT (college_id, income_band, residency) DO UPDATE SET
                net_price_within_band = EXCLUDED.net_price_within_band,
                source_url    = EXCLUDED.source_url,
                source_tier   = EXCLUDED.source_tier,
                source_quote  = EXCLUDED.source_quote,
                data_year     = EXCLUDED.data_year,
                date_ingested = CURRENT_DATE
            """,
            (college_id, band, residency, price, SCORECARD_URL,
             SCORECARD_QUOTE, SCORECARD_DATA_YEAR),
        )


def run(csv_path: Path, dry_run: bool = False) -> int:
    rows = load_rows(csv_path, {uid for uid, _ in ROSTER})
    results = [
        validate_school(uid, expected_state, rows.get(uid))
        for uid, expected_state in ROSTER
    ]

    if not dry_run:
        with connect() as conn:
            with conn.cursor() as cur:
                for result in results:
                    if result.ok:
                        write_school(cur, result)
            conn.commit()

    passed = sum(1 for r in results if r.ok and not r.flags)
    flagged = sum(1 for r in results if r.ok and r.flags)
    failed = sum(1 for r in results if not r.ok)

    for result in results:
        print(result.report_line())
    print(
        f"\n{'DRY RUN -- nothing written. ' if dry_run else ''}"
        f"{passed} passed, {flagged} flagged, {failed} failed "
        f"of {len(results)} schools."
    )
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--csv", required=True, type=Path,
        help="College Scorecard Most-Recent-Cohorts-Institution.csv "
             f"(download at {SCORECARD_URL})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="validate and report only; write nothing",
    )
    args = parser.parse_args()
    if not args.csv.exists():
        print(f"no such file: {args.csv}", file=sys.stderr)
        return 2
    return run(args.csv, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
