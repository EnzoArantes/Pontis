"""Guards that each seeded school is the institution we think it is.

Schools were identified by NAME until schema v007, which is not an identity. The
Georgia pull made the cost of that concrete -- the College Scorecard file holds
three rows a name match cannot separate:

    139940  Georgia State University                    Atlanta, GA
    139861  Georgia College & State University          Milledgeville, GA
    244437  Georgia State University-Perimeter College  Atlanta, GA

Georgia College & State University is a different university in a different city.
Perimeter College is the sharper trap: it shares the name prefix AND the city with
the target, so any substring match on "Georgia State University" catches both --
and its numbers are materially different (0-30k band $10,380 vs $13,787, admit
rate 91% vs 55%). Seeding either one would put a student's whole affordability
and admissions read against the wrong school while looking entirely plausible.

UNITID is the federal primary key and the only thing that actually separates them.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest.seed_phase1 import COLLEGES  # noqa: E402

# The institution each seeded school MUST resolve to, read from the College
# Scorecard file during ingestion.
EXPECTED_UNITID = {
    "Boston College": 164924,
    "University of California-Berkeley": 110635,
    "The University of Texas at Austin": 228778,
    "University of Massachusetts-Amherst": 166629,
    "Georgia State University": 139940,
}

# Institutions that must NEVER appear in the seed, with why they are confusable.
REJECTED_UNITID = {
    139861: "Georgia College & State University (Milledgeville) -- a different university",
    244437: "Georgia State University-Perimeter College -- same name prefix and city",
    139621: "East Georgia State College -- different institution",
    482158: "Middle Georgia State University -- different institution",
}


def _seeded_unitids() -> dict[str, int]:
    return {name: unitid for unitid, name, *_ in COLLEGES}


def test_every_school_carries_a_unitid():
    """A school with no federal identifier cannot be verified as itself."""
    for unitid, name, *_ in COLLEGES:
        assert isinstance(unitid, int) and unitid > 0, (
            f"{name} has no usable IPEDS UNITID (got {unitid!r})"
        )


def test_unitids_match_the_expected_institutions():
    assert _seeded_unitids() == EXPECTED_UNITID, (
        "seeded UNITIDs do not match the expected institutions -- a school was "
        "added, renamed, or bound to the wrong federal identifier"
    )


def test_no_rejected_institution_was_seeded():
    """The explicit rejection: confusable Georgia institutions must be absent."""
    seeded = set(_seeded_unitids().values())
    for bad, why in REJECTED_UNITID.items():
        assert bad not in seeded, f"UNITID {bad} was seeded, but it is {why}"


def test_georgia_state_is_the_atlanta_campus():
    """Named directly, because this is the substitution the brief called out."""
    seeded = _seeded_unitids()
    assert seeded.get("Georgia State University") == 139940, (
        "Georgia State University must bind to UNITID 139940 (Atlanta), not "
        "139861 (Georgia College & State University, Milledgeville) and not "
        "244437 (Georgia State University-Perimeter College)"
    )


def test_unitids_are_unique():
    """Two rows claiming one institution would double-count it in every result."""
    unitids = [unitid for unitid, *_ in COLLEGES]
    assert len(set(unitids)) == len(unitids), f"duplicate UNITID in seed: {unitids}"
