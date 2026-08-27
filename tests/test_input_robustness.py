"""Phase A — the engine must reject or degrade honestly on bad input.

Two acceptable outcomes for a hostile or broken input: a loud ValueError at
construction (for values that are impossible by definition), or an honest
unknown/unable verdict (for values that are merely missing). Never a crash, and
never a confident verdict fabricated from nonsense.

The sharpest case locked here: before validation, class_rank_percentile=-5
would have sailed under UT Austin's top-5% threshold and fired an
automatic-admission GUARANTEE -- the system's strongest label -- off an
impossible input.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.matching import (  # noqa: E402
    AffordabilityVerdict,
    Residency,
    Student,
    affordability_ceiling,
    assess_affordability,
    match,
)

D = Decimal

BASE = dict(state="GA", region="contiguous", family_income=D(28_000), family_size=4)


# ---------------------------------------------------------------------------
# Impossible values are rejected at the door
# ---------------------------------------------------------------------------


def test_negative_class_rank_cannot_fire_a_guarantee():
    """The hole this file exists for: 'top -5%' is under every threshold."""
    with pytest.raises(ValueError, match="class_rank_percentile"):
        Student(**BASE, class_rank_percentile=D(-5))


@pytest.mark.parametrize("rank", ["0", "101", "250"])
def test_out_of_range_rank_is_rejected(rank):
    with pytest.raises(ValueError, match="class_rank_percentile"):
        Student(**BASE, class_rank_percentile=D(rank))


@pytest.mark.parametrize("rank", ["0.5", "5", "100"])
def test_valid_rank_is_accepted(rank):
    assert Student(**BASE, class_rank_percentile=D(rank)).class_rank_percentile == D(rank)


def test_unweighted_gpa_above_scale_max_is_rejected():
    """4.3 unweighted is definitionally impossible; accepting it would let a
    student prove a LIKELY bound no real student could hold."""
    with pytest.raises(ValueError, match="unweighted"):
        Student(**BASE, gpa_value=D("4.300"), gpa_scale="unweighted")


def test_scales_without_a_definitional_cap_get_no_invented_one():
    """weighted and uc_weighted_capped run above 4.0 by design; no upper bound
    is enforced because no verified cap exists to enforce."""
    Student(**BASE, gpa_value=D("4.500"), gpa_scale="weighted")
    Student(**BASE, gpa_value=D("4.300"), gpa_scale="uc_weighted_capped")


def test_negative_gpa_is_rejected():
    with pytest.raises(ValueError, match="gpa_value"):
        Student(**BASE, gpa_value=D("-1.0"), gpa_scale="unweighted")


def test_non_finite_inputs_are_rejected():
    with pytest.raises(ValueError):
        Student(state="GA", region="contiguous", family_income=D("NaN"), family_size=4)
    with pytest.raises(ValueError):
        Student(**BASE, gpa_value=D("Infinity"), gpa_scale="weighted")


def test_impossible_household_shapes_are_rejected():
    with pytest.raises(ValueError, match="family_size"):
        Student(state="GA", region="contiguous", family_income=D(28_000), family_size=0)
    with pytest.raises(ValueError, match="years_of_college"):
        Student(**BASE, years_of_college=0)


# ---------------------------------------------------------------------------
# Odd-but-possible values degrade honestly
# ---------------------------------------------------------------------------


def test_negative_income_floors_at_zero_contribution(reference):
    """A negative AGI is a real thing (business loss). It must clamp to a zero
    family contribution, not go below it or crash."""
    student = Student(
        state="MA", region="contiguous", family_income=D(-12_000), family_size=4
    )
    ceiling = affordability_ceiling(student, reference)
    assert ceiling.family_term == D(0)
    assert ceiling.ceiling == ceiling.work_term + ceiling.loan_term


def test_unknown_state_degrades_to_fallback_wage_and_unknown_prices(
    reference, berkeley
):
    """A state with no wage row and no residency match: federal wage fallback
    (recorded as such) and UNKNOWN cost -- never a borrowed in-state price."""
    student = Student(
        state="ZZ", region="contiguous", family_income=D(28_000), family_size=4
    )
    ceiling = affordability_ceiling(student, reference)
    assert ceiling.wage_is_federal_fallback is True

    result = assess_affordability(student, berkeley, ceiling)
    assert result.verdict is AffordabilityVerdict.UNKNOWN
    assert result.residency_used is Residency.OUT_OF_STATE
    assert result.net_price is None


def test_empty_roster_returns_empty_lists_not_an_error(reference):
    student = Student(**BASE)
    result = match(student, [], reference)
    assert list(result.on_your_list) == []
    assert list(result.not_on_your_list) == []
    assert result.ceiling.ceiling > 0


def test_unknown_poverty_region_fails_loudly(reference):
    """A typo'd region must not silently borrow another region's guideline."""
    student = Student(
        state="GA", region="mars", family_income=D(28_000), family_size=4
    )
    with pytest.raises(KeyError, match="mars"):
        affordability_ceiling(student, reference)
