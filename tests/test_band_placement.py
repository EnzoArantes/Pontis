"""Phase 0 — cumulative-share placement from published GPA band distributions.

The mission unlock: LIKELY has effectively never fired, because almost no school
publishes a p25/p75 pair, and every other published signal was capped at TARGET.
What schools DO publish (CDS C11) is a banded distribution, and summing WHOLE
published bands proves hard lower bounds -- "at least X% of the class had a
lower GPA than yours" -- with no interpolation and no invented percentiles.

Fixtures use Georgia State's real CDS 2025-26 figures (see conftest.GSU_BANDS).

The bar for LIKELY/REACH is deliberately the SAME 75%/25% quartile standard the
percentile path uses, proven from band sums instead of read from published
percentiles, so the two paths never assert different standards.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.matching import (  # noqa: E402
    AdmissionsCategory,
    AffordabilityVerdict,
    GpaBand,
    School,
    SchoolGpaData,
    Student,
    affordability_ceiling,
    assess_admissions,
    match,
)

D = Decimal

GA_BASE = dict(state="GA", region="contiguous", family_income=D(28_000), family_size=4)


def _ga_student(gpa: str) -> Student:
    return Student(**GA_BASE, gpa_value=D(gpa), gpa_scale="unweighted")


# ---------------------------------------------------------------------------
# The full label span, from one published distribution
# ---------------------------------------------------------------------------


def test_top_of_distribution_is_likely(georgia_state):
    """A 4.0 student: every band except the 4.0 band lies wholly below them,
    so at least ~82% of the class provably had a lower GPA -> above the 75th
    percentile -> LIKELY. The label that has never fired before."""
    result = assess_admissions(_ga_student("4.000"), georgia_state)

    assert result.category is AdmissionsCategory.LIKELY
    assert result.basis == "band_distribution"
    # The provable bound is stated, floored so "at least" stays literally true.
    assert "at least 81.9%" in result.reason
    assert "no interpolation" in result.reason


def test_middle_of_distribution_is_target(georgia_state):
    """A 3.6 student (GSU's own published average) sits mid-distribution:
    neither bound reaches 75%, so TARGET -- with both bounds stated."""
    result = assess_admissions(_ga_student("3.600"), georgia_state)

    assert result.category is AdmissionsCategory.TARGET
    assert result.basis == "band_distribution"
    assert "at least 34.5%" in result.reason      # bands wholly below 3.6
    assert "at least 40.1%" in result.reason      # bands wholly above 3.6


def test_bottom_of_distribution_is_reach(georgia_state):
    """A 2.4 student: at least 99.5% of the class provably outscored them."""
    result = assess_admissions(_ga_student("2.400"), georgia_state)

    assert result.category is AdmissionsCategory.REACH
    assert result.basis == "band_distribution"
    assert "at least 99.5%" in result.reason.lower()


def test_full_span_is_reachable_from_one_school(georgia_state):
    """The Phase 0 definition of done: one published distribution spans
    reach -> target -> likely for same-scale students."""
    seen = {
        assess_admissions(_ga_student(g), georgia_state).category
        for g in ("2.400", "3.600", "4.000")
    }
    assert seen == {
        AdmissionsCategory.REACH,
        AdmissionsCategory.TARGET,
        AdmissionsCategory.LIKELY,
    }


# ---------------------------------------------------------------------------
# No interpolation, no invented precision
# ---------------------------------------------------------------------------


def test_position_inside_a_band_never_changes_the_answer(georgia_state):
    """3.51 and 3.74 sit in the same published band. Any difference between
    their placements could only come from interpolating INSIDE the band, which
    is precision the source did not publish."""
    import re

    low_edge = assess_admissions(_ga_student("3.510"), georgia_state)
    high_edge = assess_admissions(_ga_student("3.740"), georgia_state)

    assert low_edge.category is high_edge.category
    # The provable bounds must be identical; only the student's own GPA may
    # differ between the two sentences.
    bounds = lambda r: re.findall(r"at least [\d.]+%", r.reason, flags=re.I)  # noqa: E731
    assert bounds(low_edge) == bounds(high_edge) != []


def test_own_band_counts_toward_neither_bound(georgia_state):
    """A 3.8 student: the 22.13% sharing their band are at unknown positions
    relative to them, so they must not inflate either bound. Bands wholly below
    sum to 59.8% -- if the reason ever claims more, the student's own band
    leaked into the bound."""
    result = assess_admissions(_ga_student("3.800"), georgia_state)

    assert result.category is AdmissionsCategory.TARGET
    assert "at least 59.8%" in result.reason
    assert "at least 18%" in result.reason        # only the 4.0 band is wholly above


@pytest.mark.parametrize(
    "gpa,expected",
    [
        ("3.750", AdmissionsCategory.TARGET),   # exact band floor: own band, not below
        ("3.990", AdmissionsCategory.TARGET),   # exact band ceiling
        ("3.745", AdmissionsCategory.TARGET),   # in the rounding gap between bands
        ("4.000", AdmissionsCategory.LIKELY),   # exact top point-band
        ("1.000", AdmissionsCategory.REACH),    # exact bottom of a real band
    ],
)
def test_band_edges_and_gaps_are_placed_honestly(georgia_state, gpa, expected):
    """A GPA exactly on a band edge (or in the 3.74->3.75 rounding gap) must
    resolve by strict comparison, never by double-counting a boundary band."""
    assert assess_admissions(_ga_student(gpa), georgia_state).category is expected


def test_incomplete_distribution_proves_nothing(georgia_state):
    """Bands summing to 60% of the class cannot bound anything. The engine must
    fall through to the point average (capped at TARGET), never sum a partial
    distribution as if it were complete."""
    partial = School(
        name="Suppressed Bands U",
        state="GA",
        is_public=True,
        meets_full_need=False,
        gpa=SchoolGpaData(
            gpa_type="unweighted",
            gpa_value=D("3.600"),
            bands=(
                GpaBand(floor=D("4.00"), ceiling=D("4.00"), share=D("0.18")),
                GpaBand(floor=D("3.75"), ceiling=D("3.99"), share=D("0.22")),
                GpaBand(floor=D("3.50"), ceiling=D("3.74"), share=D("0.20")),
                # remaining bands suppressed by the source
            ),
            band_population="enrolled",
        ),
    )
    result = assess_admissions(_ga_student("4.000"), partial)

    # A 4.0 would be LIKELY if the partial 40% below were treated as a bound.
    assert result.category is AdmissionsCategory.TARGET
    assert result.basis == "point_average"


def test_published_percentiles_outrank_bands():
    """A directly published p25/p75 needs no derivation at all, so it wins over
    the band path when both exist."""
    both = School(
        name="Both Signals U",
        state="GA",
        is_public=True,
        meets_full_need=False,
        gpa=SchoolGpaData(
            gpa_type="unweighted",
            gpa_p25=D("3.400"),
            gpa_p75=D("3.900"),
            bands=(GpaBand(floor=D("0.00"), ceiling=D("4.00"), share=D("1.0")),),
            band_population="enrolled",
        ),
    )
    result = assess_admissions(_ga_student("3.950"), both)
    assert result.basis == "percentile_range"
    assert result.category is AdmissionsCategory.LIKELY


# ---------------------------------------------------------------------------
# Invariants hold inside the new path
# ---------------------------------------------------------------------------


def test_scale_mismatch_still_refused_with_bands(georgia_state):
    """Bands do not soften the no-conversion rule: a UC-scale student cannot be
    placed against unweighted bands, however strong the number looks."""
    student = Student(**GA_BASE, gpa_value=D("4.200"), gpa_scale="uc_weighted_capped")
    result = assess_admissions(student, georgia_state)
    assert result.category is AdmissionsCategory.UNABLE_TO_ASSESS


def test_reason_carries_the_reporting_coverage_caveat(georgia_state):
    """The distribution covers the 99.91% who submitted a GPA, and the output
    must say so -- same discipline as the BC class-rank caveat."""
    reason = assess_admissions(_ga_student("3.600"), georgia_state).reason
    assert "99.91%" in reason
    assert "collected a GPA for" in reason


def test_reason_names_the_population(georgia_state):
    """C11 counts ENROLLED students, not admitted -- the sentence must not let
    an enrolled-class fact read as an admit-pool fact."""
    reason = assess_admissions(_ga_student("3.600"), georgia_state).reason
    assert "enrolled" in reason


# ---------------------------------------------------------------------------
# Seed-constant guards — same defence style as test_net_price_source
# ---------------------------------------------------------------------------


def test_seeded_band_constants_are_a_complete_distribution():
    """Runs against the seed constants with no database, because the seed file
    is where a typo'd share or an overlapping band would be introduced."""
    from ingest.seed_phase1 import GPA_BAND_DISTRIBUTIONS

    assert GPA_BAND_DISTRIBUTIONS, "no band distributions are seeded at all"
    for name, year, gpa_type, population, reporting, bands, _src in (
        GPA_BAND_DISTRIBUTIONS
    ):
        assert gpa_type in {"unweighted", "uc_weighted_capped", "weighted"}
        assert population in {"enrolled", "admitted"}
        assert D("0") < D(reporting) <= D("1")

        total = sum(D(share) for _, _, share in bands)
        assert abs(total - 1) <= D("0.02"), (
            f"{name} {year}: shares sum to {total}, not ~1"
        )

        spans = sorted((D(f), D(c)) for f, c, _ in bands)
        for (f1, c1), (f2, c2) in zip(spans, spans[1:]):
            assert f1 <= c1 and f2 <= c2, f"{name} {year}: inverted band"
            assert c1 < f2, (
                f"{name} {year}: bands [{f1},{c1}] and [{f2},{c2}] overlap -- "
                f"a student falling in both would be double-counted"
            )


# ---------------------------------------------------------------------------
# The Georgia regression locks (brief section 2 and 8)
# ---------------------------------------------------------------------------


def test_georgia_mid_profile_regression(
    reference, georgia_state, boston_college, berkeley, ut_austin, umass_amherst
):
    """The student this phase exists for: mid-profile Georgia student, 3.6
    unweighted, low income. Previously zero LIKELY fired anywhere on the
    roster; now they get a real placement at their own state's public, with the
    affordability verdict paired beside it -- not blended into it."""
    student = Student(**GA_BASE, gpa_value=D("3.600"), gpa_scale="unweighted")
    result = match(
        student,
        [georgia_state, boston_college, berkeley, ut_austin, umass_amherst],
        reference,
    )

    everything = list(result.on_your_list) + list(result.not_on_your_list)
    placed = {
        a.school_name: a
        for a in everything
        if a.admissions.category
        in {AdmissionsCategory.LIKELY, AdmissionsCategory.TARGET}
    }
    assert placed, "the mid-profile Georgia student got no likely/target anywhere"

    gsu = placed["Georgia State University"]
    assert gsu.admissions.category is AdmissionsCategory.TARGET
    # Paired, not blended: the admissions read arrives WITH a cost verdict.
    assert gsu.affordability.verdict in set(AffordabilityVerdict)
    assert gsu.affordability.reason


def test_georgia_high_profile_unlocks_likely(reference, georgia_state):
    """A 4.0 Georgia student finally sees LIKELY -- and sees the cost truth
    beside it in the same result."""
    student = Student(**GA_BASE, gpa_value=D("4.000"), gpa_scale="unweighted")
    result = match(student, [georgia_state], reference)

    (assessment,) = list(result.on_your_list) + list(result.not_on_your_list)
    assert assessment.admissions.category is AdmissionsCategory.LIKELY
    assert assessment.affordability.verdict is AffordabilityVerdict.UNAFFORDABLE


def test_gsu_unaffordable_for_its_own_low_income_in_state_student(
    reference, georgia_state
):
    """The finding, not a bug (brief section 8): GSU's published $0-30k in-state
    net price ($13,787) exceeds its own low-income resident's ceiling ($7,125 =
    $0 family + 500h x $7.25 + $3,500 loan). Pontis exists to say this out loud."""
    student = Student(**GA_BASE)
    ceiling = affordability_ceiling(student, reference)
    assert ceiling.ceiling == D(7125)

    result = match(student, [georgia_state], reference)
    assert result.on_your_list == []
    (excluded,) = result.not_on_your_list
    assert excluded.affordability.verdict is AffordabilityVerdict.UNAFFORDABLE
    assert excluded.affordability.gap == D(6662)


def test_georgia_wage_fallback_is_recorded_as_a_fallback(reference):
    """Known item (brief section 8): the $7.25 used for Georgia is the federal
    fallback -- the correct operative floor there, but it must be VISIBLE as a
    fallback so a higher-wage state can never silently inherit it."""
    ga = Student(**GA_BASE)
    ceiling = affordability_ceiling(ga, reference)
    assert ceiling.wage_is_federal_fallback is True
    assert ceiling.wage_state_used == "US"
    assert ceiling.work_term == D("3625.00")

    ca = Student(state="CA", region="contiguous", family_income=D(28_000), family_size=4)
    ceiling = affordability_ceiling(ca, reference)
    assert ceiling.wage_is_federal_fallback is False
    assert ceiling.wage_state_used == "CA"
