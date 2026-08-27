"""Guards which Scorecard field feeds the affordability read.

College Scorecard publishes net price two ways, and they are easy to confuse:

    NPT41..NPT45_{PUB,PRIV}   average net price WITHIN each income band  <- correct
    NPT4_{PUB,PRIV}           average net price across ALL students      <- wrong here

Only the per-band figures may be seeded. The column they land in was renamed to
`net_price_within_band` in schema v006 precisely so it cannot be misread as "the
average net price" -- but a name only reduces the chance of the substitution. It
still produces data that is wrong while remaining perfectly well-formed: five
identical numbers per school, every CHECK constraint satisfied, nothing failing.
These tests are the defence that actually catches it.

The damage is not subtle. Boston College's overall average is $41,704; its $0-30k
band is $4,284. A high-need student quoted the former sees a school that is
nearly ten times more expensive than it is for them -- and BC is exactly the
meets-full-need private this project exists to surface. That single substitution
would invert the tool's core finding.

These tests run against the SEED CONSTANTS rather than the database, with no
connection required, because that is where the substitution would be made.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest.seed_phase1 import COLLEGES, NET_PRICE  # noqa: E402

# The five IPEDS/Scorecard bands, in published order.
EXPECTED_BANDS = ["0-30k", "30-48k", "48-75k", "75-110k", "110k+"]

# The overall-average figures (NPT4_PUB / NPT4_PRIV) read from the same Scorecard
# release as the seeded band values, recorded here ONLY so the tests can prove
# they were not used. These must never appear in NET_PRICE.
#   Source: College Scorecard "Most Recent Institution-Level Data", rel. 2026-06-10
#   https://collegescorecard.ed.gov/data/
SCORECARD_OVERALL_AVERAGE = {
    "Boston College": 41704,                        # NPT4_PRIV
    "University of California-Berkeley": 13481,     # NPT4_PUB
    "The University of Texas at Austin": 19857,     # NPT4_PUB
    "University of Massachusetts-Amherst": 22383,   # NPT4_PUB
    "Georgia State University": 15931,              # NPT4_PUB (UNITID 139940)
}


def _seeded_by_school() -> dict[tuple[str, str], dict[str, int]]:
    """{(school, residency): {band: price}} straight from the seed constants."""
    grouped: dict[tuple[str, str], dict[str, int]] = defaultdict(dict)
    for name, residency, band, price in NET_PRICE:
        grouped[(name, residency)][band] = price
    return grouped


def test_every_school_has_all_five_bands():
    """A missing band silently becomes 'unknown cost' for whoever falls in it."""
    grouped = _seeded_by_school()
    assert grouped, "no net price rows are seeded at all"

    for (name, residency), bands in grouped.items():
        assert sorted(bands) == sorted(EXPECTED_BANDS), (
            f"{name} ({residency}) does not carry all five Scorecard bands: "
            f"got {sorted(bands)}"
        )


def test_band_values_are_distinct_within_each_school():
    """Five identical values is the exact signature of the overall average.

    A genuine per-band series varies, because the whole point of the series is
    that price changes with income. If these ever collapse to one repeated
    number, NPT4 has been seeded in place of NPT41..NPT45.
    """
    for (name, residency), bands in _seeded_by_school().items():
        values = [bands[b] for b in EXPECTED_BANDS]
        assert len(set(values)) == len(values), (
            f"{name} ({residency}) has repeated band values {values} -- this is "
            f"what seeding the NPT4 overall average instead of NPT41..NPT45 "
            f"looks like"
        )


def test_no_band_equals_the_schools_overall_average():
    """The direct assertion: the wrong field's value appears nowhere."""
    grouped = _seeded_by_school()
    checked = 0

    for (name, residency), bands in grouped.items():
        overall = SCORECARD_OVERALL_AVERAGE.get(name)
        if overall is None:
            continue
        checked += 1
        for band in EXPECTED_BANDS:
            assert bands[band] != overall, (
                f"{name} ({residency}) band {band} is {bands[band]}, which is the "
                f"school's Scorecard OVERALL average (NPT4), not its per-band "
                f"figure (NPT41..NPT45)"
            )

    assert checked == len(SCORECARD_OVERALL_AVERAGE), (
        "a school with a known overall average was not covered -- if schools were "
        "added or renamed, SCORECARD_OVERALL_AVERAGE needs the same update"
    )


def test_every_seeded_school_is_covered():
    """Fails loudly the moment a school is seeded without its overall average.

    Spec S7: adding a school without recording its Scorecard average is a
    deliberate test failure, cleared by recording the average -- never by
    loosening this check.
    """
    names = {name for name, _ in _seeded_by_school()}
    assert names == set(SCORECARD_OVERALL_AVERAGE), (
        f"seeded schools {sorted(names)} do not match the schools with a recorded "
        f"overall average {sorted(SCORECARD_OVERALL_AVERAGE)}"
    )


def test_residency_labels_cohere_with_school_type():
    """A private priced by residency, or a public priced as not_applicable,
    is a category error the residency enum alone cannot see (Phase A: every
    enum cross-checked against the rule that gives it meaning).

    Privates charge every state the same -> not_applicable ONLY. Public rows
    must carry a real residency -- and out_of_state rows do not currently
    exist because no trustworthy source publishes them; if one ever appears
    here, it needs a source that actually does (see CLAUDE.md data gap)."""
    is_public = {name: pub for _, name, _, pub, *_ in COLLEGES}

    for name, residency, band, _price in NET_PRICE:
        assert name in is_public, f"{name} has prices but is not in COLLEGES"
        if is_public[name]:
            assert residency in ("in_state", "out_of_state"), (
                f"{name} is public; residency {residency!r} is a category error"
            )
        else:
            assert residency == "not_applicable", (
                f"{name} is private and charges every state the same; "
                f"residency {residency!r} is a category error"
            )


def test_lowest_band_is_below_the_overall_average():
    """A weaker cross-check that survives future reseeds.

    Distinctness catches a wholesale swap; this catches a subtler one. At every
    school here the poorest band pays well below the all-student average, which
    is the entire premise of net price. If the $0-30k figure ever lands at or
    above the overall average, something has been mixed up even if the five
    values still differ.
    """
    for (name, residency), bands in _seeded_by_school().items():
        overall = SCORECARD_OVERALL_AVERAGE.get(name)
        if overall is None:
            continue
        assert bands["0-30k"] < overall, (
            f"{name} ({residency}) quotes {bands['0-30k']} for the $0-30k band, "
            f"which is not below the {overall} all-student average"
        )
