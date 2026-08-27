"""Unit tests for the Phase 1 matching engine.

Fixtures mirror the REAL seeded values (2026 HHS guidelines, real minimum wages,
the three seed schools' actual published figures) so that a test failing here
means the engine is wrong, not that the fixture drifted from reality.
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
    Residency,
    School,
    SchoolGpaData,
    Student,
    affordability_ceiling,
    assess_admissions,
    assess_affordability,
    income_band_for,
    match,
    minimum_wage_for,
    poverty_guideline_for,
)

D = Decimal


# ---------------------------------------------------------------------------
# Fixtures — shared school/reference fixtures live in conftest.py
# ---------------------------------------------------------------------------


@pytest.fixture
def no_gpa_no_rank_school() -> School:
    """Publishes neither GPA nor class rank -- nothing to assess on at all."""
    return School(
        name="Silent College",
        state="MA",
        is_public=False,
        meets_full_need=True,
        gpa=SchoolGpaData(gpa_type="not_published"),
        net_prices={("0-30k", "not_applicable"): D(4000)},
    )


# ---------------------------------------------------------------------------
# Required case 1 — family below 2x poverty contributes nothing
# ---------------------------------------------------------------------------


def test_below_2x_poverty_family_term_is_zero(reference):
    """A $28k family of four is under 2x poverty ($66k), so family_term is 0 -- not negative."""
    student = Student(
        state="MA", region="contiguous", family_income=D(28_000), family_size=4
    )
    ceiling = affordability_ceiling(student, reference)

    assert ceiling.poverty_guideline == D(33_000)
    assert ceiling.poverty_threshold == D(66_000)
    assert ceiling.discretionary_income == D(0)
    assert ceiling.family_term == D(0)

    # The remaining terms still stand on their own.
    assert ceiling.work_term == D(7_500)          # 500 hours x $15.00 MA
    assert ceiling.loan_term == D(3_500)
    assert ceiling.ceiling == D(11_000)


def test_negative_discretionary_never_reduces_the_ceiling(reference):
    """A far-below-poverty family must not get a SMALLER ceiling than a merely poor one."""
    very_low = Student(
        state="MA", region="contiguous", family_income=D(5_000), family_size=4
    )
    near_threshold = Student(
        state="MA", region="contiguous", family_income=D(65_000), family_size=4
    )
    assert (
        affordability_ceiling(very_low, reference).ceiling
        == affordability_ceiling(near_threshold, reference).ceiling
    )


def test_above_threshold_family_term_is_proportional(reference):
    """$86k family of four: $20k discretionary -> 10% x 10 years / 4 years = $5,000/yr."""
    student = Student(
        state="MA", region="contiguous", family_income=D(86_000), family_size=4
    )
    ceiling = affordability_ceiling(student, reference)
    assert ceiling.discretionary_income == D(20_000)
    assert ceiling.family_term == D(5_000)
    assert ceiling.ceiling == D(16_000)           # 5000 + 7500 + 3500


# ---------------------------------------------------------------------------
# Required case 2 — out-of-state public: UNKNOWN, never the in-state figure
# ---------------------------------------------------------------------------


def test_out_of_state_public_is_unknown_not_in_state_price(reference, berkeley):
    """The money-trap guard: an MA student at Berkeley gets UNKNOWN, never $5,311."""
    student = Student(
        state="MA", region="contiguous", family_income=D(28_000), family_size=4
    )
    ceiling = affordability_ceiling(student, reference)
    result = assess_affordability(student, berkeley, ceiling)

    assert result.verdict is AffordabilityVerdict.UNKNOWN
    assert result.residency_used is Residency.OUT_OF_STATE

    # The specific regression this guards: the in-state number must not leak out.
    assert result.net_price is None
    assert result.net_price != D(5311)
    assert "unknown" in result.reason.lower()


def test_in_state_public_does_use_the_in_state_price(reference, berkeley):
    """Same school, CA student: the in-state row is correct here and must be used."""
    student = Student(
        state="CA", region="contiguous", family_income=D(28_000), family_size=4
    )
    ceiling = affordability_ceiling(student, reference)
    result = assess_affordability(student, berkeley, ceiling)

    assert result.residency_used is Residency.IN_STATE
    assert result.net_price == D(5311)
    assert result.verdict is AffordabilityVerdict.AFFORDABLE


def test_private_school_ignores_residency(reference, boston_college):
    """BC charges an out-of-state student the same, so residency is not_applicable."""
    for state in ("MA", "CA", "TX"):
        student = Student(
            state=state, region="contiguous", family_income=D(28_000), family_size=4
        )
        ceiling = affordability_ceiling(student, reference)
        result = assess_affordability(student, boston_college, ceiling)
        assert result.residency_used is Residency.NOT_APPLICABLE
        assert result.net_price == D(4284)


# ---------------------------------------------------------------------------
# Required case 3 — scale mismatch is refused, never converted
# ---------------------------------------------------------------------------


def test_scale_mismatch_is_unable_to_assess(reference, berkeley):
    """3.9 unweighted vs a UC weighted-capped range must NOT be compared."""
    student = Student(
        state="CA",
        region="contiguous",
        family_income=D(28_000),
        family_size=4,
        gpa_value=D("3.900"),
        gpa_scale="unweighted",
    )
    result = assess_admissions(student, berkeley)

    assert result.category is AdmissionsCategory.UNABLE_TO_ASSESS
    assert "not" in result.reason.lower()
    # Must not have silently landed in a real category.
    assert result.category not in {
        AdmissionsCategory.LIKELY,
        AdmissionsCategory.TARGET,
        AdmissionsCategory.REACH,
    }


def test_scale_mismatch_is_not_rescued_by_a_high_gpa(reference, berkeley):
    """Even the strongest possible GPA stays unassessable -- the refusal is
    about scales, not size. (A perfect 4.0 unweighted, and a 4.5 on the plain
    weighted scale, against Berkeley's UC weighted-capped range.)"""
    for value, scale in (("4.000", "unweighted"), ("4.500", "weighted")):
        student = Student(
            state="CA",
            region="contiguous",
            family_income=D(28_000),
            family_size=4,
            gpa_value=D(value),
            gpa_scale=scale,
        )
        assert (
            assess_admissions(student, berkeley).category
            is AdmissionsCategory.UNABLE_TO_ASSESS
        )


# ---------------------------------------------------------------------------
# Required case 4 — Boston College publishes no GPA
# ---------------------------------------------------------------------------


def test_not_published_with_no_rank_is_unable_to_assess_on_gpa(
    reference, no_gpa_no_rank_school
):
    """A blank C12 and no rank data must surface as 'GPA is not the signal here'."""
    student = Student(
        state="MA",
        region="contiguous",
        family_income=D(28_000),
        family_size=4,
        gpa_value=D("3.900"),
        gpa_scale="unweighted",
    )
    result = assess_admissions(student, no_gpa_no_rank_school)

    assert result.category is AdmissionsCategory.UNABLE_TO_ASSESS_ON_GPA
    assert result.basis == "not_published"
    assert "does not publish" in result.reason
    # A strong student must not be penalised into REACH by the school's silence.
    assert result.category is not AdmissionsCategory.REACH


# ---------------------------------------------------------------------------
# Class-rank distribution (schema v004) — both branches
# ---------------------------------------------------------------------------


def test_bc_places_student_who_provides_class_rank(reference, boston_college):
    """Branch 1: rank supplied -> a real category, placed against the published band."""
    student = Student(
        state="MA",
        region="contiguous",
        family_income=D(28_000),
        family_size=4,
        class_rank_percentile=D(5),        # top 5%, inside BC's top-10% band
    )
    result = assess_admissions(student, boston_college)

    assert result.category is AdmissionsCategory.TARGET
    assert result.basis == "class_rank_distribution"
    assert "top 5%" in result.reason
    assert "90% of this school's first-year students were in the top 10%" in result.reason


def test_bc_rank_outside_the_band_is_reach(reference, boston_college):
    student = Student(
        state="MA",
        region="contiguous",
        family_income=D(28_000),
        family_size=4,
        class_rank_percentile=D(30),
    )
    result = assess_admissions(student, boston_college)
    assert result.category is AdmissionsCategory.REACH
    assert result.basis == "class_rank_distribution"


def test_bc_without_rank_shows_context_but_does_not_place(reference, boston_college):
    """Branch 2: no rank -> context shown, explicitly NOT placed and NOT fabricated."""
    student = Student(
        state="MA",
        region="contiguous",
        family_income=D(28_000),
        family_size=4,
        gpa_value=D("3.900"),              # a GPA must not be silently used as rank
        gpa_scale="unweighted",
    )
    result = assess_admissions(student, boston_college)

    assert result.category is AdmissionsCategory.CONTEXT_NOT_PLACED
    assert result.basis == "class_rank_distribution"

    # The published bar IS communicated -- this is not a blank.
    assert "90% of this school's first-year students were in the top 10%" in result.reason
    assert result.reason.strip() != ""

    # ...but no placement was invented.
    assert result.category not in {
        AdmissionsCategory.LIKELY,
        AdmissionsCategory.TARGET,
        AdmissionsCategory.REACH,
    }


def test_rank_context_carries_its_coverage_caveat(reference, boston_college):
    """90% is of the 26.6% who reported a rank, and the output must say so."""
    student = Student(
        state="MA", region="contiguous", family_income=D(28_000), family_size=4
    )
    reason = assess_admissions(student, boston_college).reason
    assert "26.6%" in reason
    assert "collected a class rank for" in reason


def test_rank_distribution_never_returns_likely(reference, boston_college):
    """Base-rate guard: 'top 10%' describes admits, it does not predict admission.

    Even a rank of top 1% must not become LIKELY at a school admitting 16%.
    """
    for rank in (D(1), D("0.5"), D(2), D(5), D(10)):
        student = Student(
            state="MA",
            region="contiguous",
            family_income=D(28_000),
            family_size=4,
            class_rank_percentile=rank,
        )
        assert assess_admissions(student, boston_college).category is not (
            AdmissionsCategory.LIKELY
        )


def test_school_with_nothing_ingested_is_our_gap_not_theirs(reference):
    """gpa=None means Pontis has not ingested admissions data. That must
    surface as OUR gap -- never as 'this school does not publish', which is a
    different (and here unverified) claim about the school."""
    school = School(
        name="Batch-Only U", state="MA", is_public=False, meets_full_need=False,
        gpa=None, net_prices={("0-30k", "not_applicable"): D(4000)},
    )
    student = Student(
        state="MA", region="contiguous", family_income=D(28_000), family_size=4,
        gpa_value=D("3.900"), gpa_scale="unweighted",
    )
    result = assess_admissions(student, school)

    assert result.category is AdmissionsCategory.UNABLE_TO_ASSESS_ON_GPA
    assert result.basis == "no_data_ingested"
    assert "gap in Pontis's data" in result.reason
    assert "does not publish" not in result.reason


# ---------------------------------------------------------------------------
# Required case 5 — a normal, in-range, affordable school
# ---------------------------------------------------------------------------


def test_normal_in_range_affordable_case(reference, berkeley):
    """CA student, matching scale, inside the published range, price under ceiling."""
    student = Student(
        state="CA",
        region="contiguous",
        family_income=D(28_000),
        family_size=4,
        gpa_value=D("4.200"),
        gpa_scale="uc_weighted_capped",
    )
    ceiling = affordability_ceiling(student, reference)
    assert ceiling.ceiling == D("11950.00")       # 0 + (500 x 16.90) + 3500

    admissions = assess_admissions(student, berkeley)
    assert admissions.category is AdmissionsCategory.TARGET
    assert admissions.basis == "percentile_range"

    affordability = assess_affordability(student, berkeley, ceiling)
    assert affordability.verdict is AffordabilityVerdict.AFFORDABLE
    assert affordability.net_price == D(5311)


# ---------------------------------------------------------------------------
# Percentile positioning
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "gpa,expected",
    [
        (D("4.300"), AdmissionsCategory.LIKELY),   # above p75
        (D("4.280"), AdmissionsCategory.TARGET),   # exactly p75 -> still within
        (D("4.200"), AdmissionsCategory.TARGET),
        (D("4.160"), AdmissionsCategory.TARGET),   # exactly p25 -> still within
        (D("4.000"), AdmissionsCategory.REACH),    # below p25
        (D("2.000"), AdmissionsCategory.REACH),    # never harder than REACH
    ],
)
def test_percentile_positioning(reference, berkeley, gpa, expected):
    student = Student(
        state="CA",
        region="contiguous",
        family_income=D(28_000),
        family_size=4,
        gpa_value=gpa,
        gpa_scale="uc_weighted_capped",
    )
    assert assess_admissions(student, berkeley).category is expected


def test_low_gpa_is_never_hard_cut(reference, berkeley):
    """Spec: keep as reach; do not disqualify on GPA alone."""
    student = Student(
        state="CA",
        region="contiguous",
        family_income=D(28_000),
        family_size=4,
        gpa_value=D("1.000"),
        gpa_scale="uc_weighted_capped",
    )
    assert assess_admissions(student, berkeley).category is AdmissionsCategory.REACH


def test_point_average_at_or_above_is_target_not_likely(reference):
    """A point average has no dispersion, so it cannot support a LIKELY call."""
    school = School(
        name="Point Average U",
        state="MA",
        is_public=False,
        meets_full_need=False,
        gpa=SchoolGpaData(gpa_type="unweighted", gpa_value=D("3.900")),
    )
    strong = Student(
        state="MA", region="contiguous", family_income=D(28_000), family_size=4,
        gpa_value=D("4.000"), gpa_scale="unweighted",
    )
    weak = Student(
        state="MA", region="contiguous", family_income=D(28_000), family_size=4,
        gpa_value=D("3.500"), gpa_scale="unweighted",
    )
    assert assess_admissions(strong, school).category is AdmissionsCategory.TARGET
    assert assess_admissions(weak, school).category is AdmissionsCategory.REACH


# ---------------------------------------------------------------------------
# Rank-driven schools
# ---------------------------------------------------------------------------


TX_BASE = dict(state="TX", region="contiguous", family_income=D(28_000), family_size=4)


def _afford(student, school, reference):
    return assess_affordability(student, school, affordability_ceiling(student, reference))


def test_top5_tx_matching_cycle_returns_guarantee(reference, ut_austin):
    """Headline case: all three fences hold -> strongest label, university yes, major no."""
    student = Student(**TX_BASE, class_rank_percentile=D(4), applicant_cycle="fall-2026")
    result = assess_admissions(
        student, ut_austin, affordability=_afford(student, ut_austin, reference)
    )

    assert result.category is AdmissionsCategory.GUARANTEED
    assert result.university_admission_guaranteed is True
    assert result.major_admission_guaranteed is False
    assert result.basis == "class_rank_auto_admit"

    # Copy leads with the open door, before any caveat.
    assert result.reason.startswith("You are automatically admitted")
    # The major caveat is a separate, forward-looking next step -- not a wall.
    assert result.next_step is not None
    assert "How to go further" in result.next_step
    assert "still admitted to the university" in result.next_step


def test_wrong_cycle_does_not_return_guarantee(reference, ut_austin):
    """Stale-threshold guard: thresholds move between cycles, so the fence is exact."""
    student = Student(**TX_BASE, class_rank_percentile=D(4), applicant_cycle="fall-2025")
    result = assess_admissions(
        student, ut_austin, affordability=_afford(student, ut_austin, reference)
    )

    assert result.category is not AdmissionsCategory.GUARANTEED
    assert result.category is AdmissionsCategory.CONTEXT_NOT_PLACED
    assert result.university_admission_guaranteed is False
    assert "fall-2025" in result.reason


def test_missing_cycle_does_not_return_guarantee(reference, ut_austin):
    """A student who never stated a cycle must not inherit the stored one."""
    student = Student(**TX_BASE, class_rank_percentile=D(4))
    result = assess_admissions(
        student, ut_austin, affordability=_afford(student, ut_austin, reference)
    )
    assert result.category is not AdmissionsCategory.GUARANTEED


def test_out_of_state_top5_does_not_return_guarantee(reference, ut_austin):
    """The guarantee is a Texas residency benefit; a top-5% MA student does not get it."""
    student = Student(
        state="MA", region="contiguous", family_income=D(28_000), family_size=4,
        class_rank_percentile=D(4), applicant_cycle="fall-2026",
    )
    result = assess_admissions(
        student, ut_austin, affordability=_afford(student, ut_austin, reference)
    )

    assert result.category is not AdmissionsCategory.GUARANTEED
    assert result.category is AdmissionsCategory.HOLISTIC_REVIEW
    assert result.university_admission_guaranteed is False


def test_no_rank_submitted_returns_context_not_placed(reference, ut_austin):
    """No quiet guarantee: show the bar, invite the rank."""
    student = Student(**TX_BASE, applicant_cycle="fall-2026")
    result = assess_admissions(
        student, ut_austin, affordability=_afford(student, ut_austin, reference)
    )

    assert result.category is AdmissionsCategory.CONTEXT_NOT_PLACED
    assert result.category is not AdmissionsCategory.GUARANTEED
    assert result.university_admission_guaranteed is False
    # The bar is still communicated.
    assert "top 5%" in result.reason
    assert "add your rank" in result.reason


def test_just_outside_never_asserts_a_placement(reference, ut_austin):
    """Top 7%: describe the holistic pool. Never 'target', never a probability.

    Locked the same way the distribution path locks "never returns likely": we
    have no admit-rate-by-rank data for the holistic pool, so any placement would
    be invented.
    """
    for rank in (D("5.01"), D(6), D(7), D(10), D(25)):
        student = Student(**TX_BASE, class_rank_percentile=rank, applicant_cycle="fall-2026")
        result = assess_admissions(
            student, ut_austin, affordability=_afford(student, ut_austin, reference)
        )
        assert result.category is AdmissionsCategory.HOLISTIC_REVIEW
        assert result.category not in {
            AdmissionsCategory.GUARANTEED,
            AdmissionsCategory.LIKELY,
            AdmissionsCategory.TARGET,
            AdmissionsCategory.REACH,
        }

    # And it describes what the pool weighs rather than quoting odds.
    student = Student(**TX_BASE, class_rank_percentile=D(7), applicant_cycle="fall-2026")
    reason = assess_admissions(
        student, ut_austin, affordability=_afford(student, ut_austin, reference)
    ).reason
    assert "holistic" in reason.lower()
    assert "not a prediction" in reason.lower()


def test_guarantee_always_carries_affordability(reference, ut_austin):
    """Admit and cost surface together, or the result is only half delivered."""
    student = Student(**TX_BASE, class_rank_percentile=D(4), applicant_cycle="fall-2026")
    result = assess_admissions(
        student, ut_austin, affordability=_afford(student, ut_austin, reference)
    )

    assert result.category is AdmissionsCategory.GUARANTEED
    assert result.affordability is not None
    assert result.affordability.verdict in set(AffordabilityVerdict)


def test_guarantee_without_cost_read_is_refused(reference, ut_austin):
    """A guarantee built without the cost read must fail loudly, not render alone."""
    student = Student(**TX_BASE, class_rank_percentile=D(4), applicant_cycle="fall-2026")
    with pytest.raises(ValueError, match="affordability"):
        assess_admissions(student, ut_austin)          # no affordability passed


def test_guarantee_via_match_is_paired_end_to_end(reference, ut_austin):
    """The public entry point pairs them without the caller having to remember."""
    student = Student(**TX_BASE, class_rank_percentile=D(4), applicant_cycle="fall-2026")
    result = match(student, [ut_austin], reference)
    (assessment,) = list(result.on_your_list) + list(result.not_on_your_list)

    assert assessment.admissions.category is AdmissionsCategory.GUARANTEED
    assert assessment.admissions.affordability is not None
    assert assessment.affordability is not None


def test_rank_school_without_any_threshold_on_file(reference):
    """class_rank_proxy with no guarantee row: honest 'nothing on file'."""
    bare = School(
        name="Rank U", state="TX", is_public=True, meets_full_need=False,
        gpa=SchoolGpaData(gpa_type="class_rank_proxy"),
    )
    student = Student(**TX_BASE, class_rank_percentile=D(4), applicant_cycle="fall-2026")
    result = assess_admissions(student, bare)
    assert result.category is AdmissionsCategory.UNABLE_TO_ASSESS_ON_GPA


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "income,band",
    [
        (D(0), "0-30k"),
        (D(30_000), "0-30k"),
        (D(30_001), "30-48k"),
        (D(48_000), "30-48k"),
        (D(48_001), "48-75k"),
        (D(75_000), "48-75k"),
        (D(75_001), "75-110k"),
        (D(110_000), "75-110k"),
        (D(110_001), "110k+"),
        (D(500_000), "110k+"),
    ],
)
def test_income_band_boundaries(income, band):
    assert income_band_for(income) == band


def test_family_size_beyond_eight_uses_published_increment(reference):
    """HHS publishes 1-8 plus 'add $5,680 per additional person'."""
    assert poverty_guideline_for(reference, "contiguous", 8) == D(55_720)
    assert poverty_guideline_for(reference, "contiguous", 9) == D(55_720) + D(5_680)
    assert poverty_guideline_for(reference, "contiguous", 11) == D(55_720) + D(5_680) * 3


def test_minimum_wage_falls_back_to_federal(reference):
    assert minimum_wage_for(reference, "CA") == D("16.90")
    assert minimum_wage_for(reference, "TX") == D("7.25")
    # A state with no row of its own falls back to the documented federal rate.
    assert minimum_wage_for(reference, "WY") == D("7.25")


# ---------------------------------------------------------------------------
# Output contract — the two verdicts must stay separate
# ---------------------------------------------------------------------------


def test_match_splits_on_affordability_only(reference, boston_college, berkeley, ut_austin):
    """An MA student: BC affordable; both publics unknown (out-of-state)."""
    student = Student(
        state="MA",
        region="contiguous",
        family_income=D(28_000),
        family_size=4,
        gpa_value=D("3.900"),
        gpa_scale="unweighted",
    )
    result = match(student, [boston_college, berkeley, ut_austin], reference)

    assert [a.school_name for a in result.on_your_list] == ["Boston College"]
    assert len(result.not_on_your_list) == 2
    assert all(
        a.affordability.verdict is AffordabilityVerdict.UNKNOWN
        for a in result.not_on_your_list
    )
    # Every excluded school must carry a reason the caller can display.
    assert all(a.affordability.reason for a in result.not_on_your_list)


def test_admissions_category_survives_being_unaffordable(reference, berkeley):
    """A school you'd get into but can't pay for keeps its category AND is excluded.

    This is the anti-blending guarantee: affordability gates the list, it does
    not overwrite or dilute the admissions read.
    """
    rich_enough_to_be_priced_out = Student(
        state="CA",
        region="contiguous",
        family_income=D(120_000),
        family_size=4,
        gpa_value=D("4.300"),          # above p75 -> LIKELY
        gpa_scale="uc_weighted_capped",
    )
    result = match(rich_enough_to_be_priced_out, [berkeley], reference)

    assert result.on_your_list == []
    (excluded,) = result.not_on_your_list
    assert excluded.admissions.category is AdmissionsCategory.LIKELY
    assert excluded.affordability.verdict is AffordabilityVerdict.UNAFFORDABLE
    assert excluded.affordability.gap is not None and excluded.affordability.gap > 0


def test_no_combined_score_is_exposed(reference, boston_college):
    """Structural guard: there must be no single blended field to misuse."""
    student = Student(
        state="MA", region="contiguous", family_income=D(28_000), family_size=4
    )
    (assessment,) = match(student, [boston_college], reference).on_your_list
    fields = set(vars(assessment).keys())
    assert fields == {"school_name", "admissions", "affordability"}
    assert not any("score" in f.lower() for f in fields)
