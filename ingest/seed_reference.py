"""Seed the engine's reference tables (schema v003).

Kept separate from seed_phase1.py because this is not school data: it describes
the student's side of the affordability equation and the federal/state
environment. All three sources revise annually.

    ./.venv/bin/python ingest/seed_reference.py

Idempotent: every write is an UPSERT on the natural key.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import connect  # noqa: E402

# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------
ASPE = "https://aspe.hhs.gov/topics/poverty-economic-mobility/poverty-guidelines"
CA_DIR = "https://www.dir.ca.gov/dlse/faq_minimumwage.htm"
MA_WAGE = "https://www.mass.gov/info-details/massachusetts-law-about-minimum-wage"
TX_WAGE = "https://www.twc.texas.gov/programs/wage-and-hour/texas-minimum-wage-law"
DOL_WAGE = "https://www.dol.gov/agencies/whd/minimum-wage/state"
FSA_LIMITS = (
    "https://fsapartners.ed.gov/knowledge-center/fsa-handbook/2025-2026/vol8/"
    "ch4-annual-and-aggregate-loan-limits"
)

# ---------------------------------------------------------------------------
# 2026 HHS poverty guidelines
# ---------------------------------------------------------------------------
# Read from the ASPE detailed guidelines. Stored at 100%; the engine applies the
# 2x multiplier. Trailing value in each tuple is the published
# "add $X for each additional person" for that region.
POVERTY_2026 = {
    "contiguous": ([15960, 21640, 27320, 33000, 38680, 44360, 50040, 55720], 5680),
    "alaska":     ([19950, 27050, 34150, 41250, 48350, 55450, 62550, 69650], 7100),
    "hawaii":     ([18360, 24890, 31420, 37950, 44480, 51010, 57540, 64070], 6530),
}

# ---------------------------------------------------------------------------
# Minimum wages
# ---------------------------------------------------------------------------
# ===========================================================================
# OPEN VERIFICATION GAP -- minimum wages (recorded so it is not forgotten)
# ===========================================================================
# Verification status of each row below is NOT uniform:
#
#   CA  PRIMARY-VERIFIED. Fetched from the issuing authority, dir.ca.gov:
#       "Effective January 1, 2026, the minimum wage is $16.90 per hour for all
#       employers, not otherwise covered by a higher minimum wage specific to an
#       industry or a locality."
#
#   MA  SECONDARY-CORROBORATED, PENDING PRIMARY CHECK.
#   TX  SECONDARY-CORROBORATED, PENDING PRIMARY CHECK.
#   US  SECONDARY-CORROBORATED, PENDING PRIMARY CHECK.
#
# mass.gov, twc.texas.gov, dol.gov and ecfr.gov all returned HTTP 403 to
# automated fetches, so those three figures are corroborated across multiple
# independent secondary sources that agree with each other, rather than read at
# the source. That is weaker than every other number in this project and is why
# it is written down here rather than in a commit message.
#
# Risk is low (all three are long-stable round figures, and the sources did not
# disagree -- unlike the out-of-state net price case, where they did and we
# therefore stored nothing). But "low risk" is not "verified".
#
# source_url below always points at the authoritative page a human should open
# to close this out. TO CLEAR: open each by hand, confirm the figure, and
# promote the status above to PRIMARY-VERIFIED.
# source_url -> (source_tier, short verbatim quote or None), per spec S3.
SOURCE_TIERS = {
    # Fetched and read directly from the issuing authority.
    ASPE: ("primary_verified", "48 Contiguous States and District of Columbia"),
    CA_DIR: ("primary_verified",
             "Effective January 1, 2026, the minimum wage is $16.90 per hour"),
    # Blocked or not read at the issuing authority -- see the gap block above.
    MA_WAGE: ("secondary_corroborated", None),
    TX_WAGE: ("secondary_corroborated", None),
    DOL_WAGE: ("secondary_corroborated", None),
    # The stored URL is the ED handbook, but the figure was confirmed at TICAS,
    # not at ED itself. Tiering by what was actually read, not by what is linked.
    FSA_LIMITS: ("secondary_corroborated", None),
}


def provenance(url):
    return SOURCE_TIERS.get(url, ("secondary_corroborated", None))


MINIMUM_WAGES = [
    # (state, hourly_wage, source_url)
    ("CA", "16.90", CA_DIR),      # PRIMARY-VERIFIED
    ("MA", "15.00", MA_WAGE),     # secondary-corroborated, pending primary check
    # Texas sets no rate of its own; the Texas Minimum Wage Act adopts the
    # federal rate. Recorded explicitly because that is a fact, not a gap.
    ("TX", "7.25", TX_WAGE),      # secondary-corroborated, pending primary check
    # Federal fallback for any state without a row above.
    ("US", "7.25", DOL_WAGE),     # secondary-corroborated, pending primary check
]

# ---------------------------------------------------------------------------
# Federal loan limits
# ---------------------------------------------------------------------------
# Dependent first-year undergraduate: up to $5,500 total, of which no more than
# $3,500 may be subsidized.
LOAN_LIMITS = [
    # (award_year, dependency_status, year_level, subsidized, combined, source)
    ("2026-2027", "dependent", 1, 3500, 5500, FSA_LIMITS),
]


def main() -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            for region, (amounts, increment) in POVERTY_2026.items():
                for size, amount in enumerate(amounts, start=1):
                    cur.execute(
                        """
                        INSERT INTO poverty_guidelines
                            (year, region, family_size, amount,
                             additional_person_amount, source_url,
                             source_tier, source_quote)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (year, region, family_size) DO UPDATE SET
                            amount                   = EXCLUDED.amount,
                            additional_person_amount = EXCLUDED.additional_person_amount,
                            source_url               = EXCLUDED.source_url,
                            source_tier              = EXCLUDED.source_tier,
                            source_quote             = EXCLUDED.source_quote,
                            date_ingested            = CURRENT_DATE
                        """,
                        (2026, region, size, amount, increment, ASPE,
                         *provenance(ASPE)),
                    )

            for state, wage, src in MINIMUM_WAGES:
                cur.execute(
                    """
                    INSERT INTO state_minimum_wage
                        (state, hourly_wage, source_url, source_tier, source_quote)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (state) DO UPDATE SET
                        hourly_wage   = EXCLUDED.hourly_wage,
                        source_url    = EXCLUDED.source_url,
                        source_tier   = EXCLUDED.source_tier,
                        source_quote  = EXCLUDED.source_quote,
                        date_ingested = CURRENT_DATE
                    """,
                    (state, wage, src, *provenance(src)),
                )

            for row in LOAN_LIMITS:
                cur.execute(
                    """
                    INSERT INTO federal_loan_limits
                        (award_year, dependency_status, year_level,
                         subsidized_annual_limit, combined_annual_limit, source_url,
                         source_tier, source_quote)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (award_year, dependency_status, year_level) DO UPDATE SET
                        subsidized_annual_limit = EXCLUDED.subsidized_annual_limit,
                        combined_annual_limit   = EXCLUDED.combined_annual_limit,
                        source_url              = EXCLUDED.source_url,
                        source_tier             = EXCLUDED.source_tier,
                        source_quote            = EXCLUDED.source_quote,
                        date_ingested           = CURRENT_DATE
                    """,
                    (*row, *provenance(row[-1])),
                )

        conn.commit()

    print("Reference seed complete.")


if __name__ == "__main__":
    main()
