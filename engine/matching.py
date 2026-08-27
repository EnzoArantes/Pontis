"""Pontis matching engine (Phase 1) — pure functions, no I/O.

Nothing in this module touches the database, the network, or the clock. Callers
load reference data and school data, hand them in as plain values, and get back
verdicts. That is what makes the tricky cases unit-testable without a fixture
database.

Two rules shape almost every decision below:

  1. The two verdicts NEVER blend. A school gets an admissions category and,
     independently, an affordability verdict. There is deliberately no combined
     "match score", because averaging "you'd probably get in" with "you can't pay
     for it" produces a number that is wrong in both directions and hides the one
     fact the student most needs.

  2. A GPA is never converted between scales. If the student's scale and the
     school's scale differ, the honest output is "unable to assess", not a
     plausible-looking translation. 3.9 unweighted and 4.2 UC-weighted-capped are
     not interconvertible without the student's actual coursework.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, Decimal
from enum import Enum
from typing import Mapping, Optional, Sequence

# ---------------------------------------------------------------------------
# Policy constants (engine-side, not ingested facts)
# ---------------------------------------------------------------------------

# Lumina's affordability framing: a family is expected to contribute from income
# above 200% of the federal poverty guideline, not from the first dollar earned.
POVERTY_MULTIPLIER = Decimal("2.0")

# 10% of discretionary income saved for 10 years.
SAVINGS_RATE = Decimal("0.10")
SAVINGS_YEARS = Decimal("10")

# A student work expectation of 500 hours a year -- roughly 10 hours a week
# during term plus some summer, i.e. work that does not displace coursework.
ANNUAL_WORK_HOURS = Decimal("500")

DEFAULT_YEARS_OF_COLLEGE = 4

# IPEDS/Scorecard income bands. Upper bound of each band, in order; the final
# band is open-ended.
INCOME_BANDS: Sequence[tuple[Optional[int], str]] = (
    (30_000, "0-30k"),
    (48_000, "30-48k"),
    (75_000, "48-75k"),
    (110_000, "75-110k"),
    (None, "110k+"),
)

FEDERAL_WAGE_KEY = "US"

# GPA scales that describe an actual numeric scale a student could also be on.
COMPARABLE_SCALES = frozenset({"unweighted", "uc_weighted_capped", "weighted"})

# The bar a cumulative-share bound must clear before a band placement may claim
# LIKELY or REACH. Deliberately the SAME quartiles the percentile path uses
# (above p75 -> likely, below p25 -> reach), so the two paths assert the same
# standard: LIKELY always means "provably above the 75th percentile", whether
# the proof is a published p75 or a sum of whole published bands.
BAND_PLACEMENT_QUARTILE = Decimal("0.75")

# Published band shares are rounded figures and may not sum to exactly 1 (GSU's
# print as 1.00002). Within this tolerance the distribution is treated as
# complete; outside it, bands are refused as a placement basis entirely -- a
# distribution missing a visible chunk of the class cannot prove any bound.
BAND_SHARE_SUM_TOLERANCE = Decimal("0.02")


# ---------------------------------------------------------------------------
# Verdict vocabularies
# ---------------------------------------------------------------------------


class AdmissionsCategory(Enum):
    # The strongest label in the system, and the only one that is not a
    # prediction. It is reserved for a published, in-advance GUARANTEE the school
    # offers (UT Austin's automatic admission), never for a strong-looking
    # probability. Nothing inferred is ever allowed to reach this value.
    GUARANTEED = "guaranteed"

    LIKELY = "likely"
    TARGET = "target"
    REACH = "reach"

    # The applicant falls outside a published guarantee and into the school's
    # holistic pool. Deliberately NOT a placement: we have no admit-rate-by-rank
    # data for that pool, so the engine describes what the pool weighs instead of
    # asserting odds. Describing beats guessing.
    HOLISTIC_REVIEW = "holistic_review"

    # The student's scale and the school's scale cannot be compared, or the
    # student supplied no GPA. Distinct from REACH: this is missing information
    # about the student, not a weak position.
    UNABLE_TO_ASSESS = "unable_to_assess"

    # The SCHOOL does not publish GPA (Boston College) or publishes a signal we
    # cannot evaluate. GPA is simply not the operative lever there; Phase 2
    # admission_factors is what will speak to these schools.
    UNABLE_TO_ASSESS_ON_GPA = "unable_to_assess_on_gpa"

    # The school publishes a usable class-rank signal, but the student has not
    # supplied their rank, so they cannot be placed against it. The published
    # context IS returned so the student can see the bar and go look their rank
    # up -- this is "here is the bar, you are not on it yet", NOT a blank and NOT
    # a guessed placement.
    CONTEXT_NOT_PLACED = "context_not_placed"


class AffordabilityVerdict(Enum):
    AFFORDABLE = "affordable"
    UNAFFORDABLE = "unaffordable"

    # No published net price for THIS student's residency. Never inferred from
    # the in-state figure. See CLAUDE.md: falling back would understate the
    # out-of-state money trap by tens of thousands of dollars.
    UNKNOWN = "unknown"


class Residency(Enum):
    IN_STATE = "in_state"
    OUT_OF_STATE = "out_of_state"
    NOT_APPLICABLE = "not_applicable"


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PovertyGuideline:
    amount: Decimal
    additional_person_amount: Decimal


@dataclass(frozen=True)
class ReferenceData:
    """Everything the engine needs that is not about a specific school."""

    # (region, family_size) -> guideline. Sizes 1..8 as published by HHS.
    poverty_guidelines: Mapping[tuple[str, int], PovertyGuideline]

    # state code (or 'US') -> hourly wage
    minimum_wages: Mapping[str, Decimal]

    subsidized_loan_limit: Decimal


@dataclass(frozen=True)
class GpaBand:
    """One published band of a GPA distribution, exactly as published.

    "3.75-3.99: 22.13%" -> floor 3.75, ceiling 3.99, share 0.2213. A point band
    like "GPA of 4.0: 18.04%" is floor == ceiling. Shares are fractions of the
    REPORTING population (see SchoolGpaData.band_reporting_share).
    """

    floor: Decimal
    ceiling: Decimal
    share: Decimal


@dataclass(frozen=True)
class SchoolGpaData:
    """A school's published academic signal, exactly as published."""

    gpa_type: str                              # matches the gpa_type enum
    gpa_value: Optional[Decimal] = None        # point average, if published
    gpa_p25: Optional[Decimal] = None          # 25th percentile, if published
    gpa_p75: Optional[Decimal] = None          # 75th percentile, if published

    # Published banded distribution (schema v008, CDS section C11), on the SAME
    # scale as gpa_type. Empty tuple means no distribution is published --
    # a real absence, not a gap to be interpolated over.
    bands: tuple[GpaBand, ...] = ()
    band_population: Optional[str] = None            # 'enrolled' | 'admitted'
    band_reporting_share: Optional[Decimal] = None   # 0.9991 => GPA known for 99.91%

    # Published class-rank DISTRIBUTION (schema v004), e.g. BC's "90% of
    # first-year students were in the top 10% of their class". Distinct from
    # School.auto_admit: that is a guarantee the school offers in advance, this
    # is a description of who happened to get in.
    class_rank_top_pct: Optional[Decimal] = None          # 10 => "top 10%"
    class_rank_share: Optional[Decimal] = None            # 0.90 => 90% of students
    class_rank_reporting_share: Optional[Decimal] = None  # 0.266 => rank known for 26.6%

    def has_rank_distribution(self) -> bool:
        return self.class_rank_top_pct is not None and self.class_rank_share is not None


@dataclass(frozen=True)
class AutoAdmitRule:
    """A published automatic-admission guarantee (schema v005).

    A promise the school makes in advance, not a pattern observed after the fact.
    Scoped to one admission cycle and one resident state, because it is both
    time-limited and a residency benefit.
    """

    effective_cycle: str
    resident_state: str
    threshold_top_pct: Decimal
    guarantees_university: bool
    guarantees_major: bool


@dataclass(frozen=True)
class School:
    name: str
    state: str
    is_public: bool
    meets_full_need: bool
    gpa: SchoolGpaData

    # (income_band, residency value) -> avg net price. A MISSING key means no
    # published figure, which is materially different from a zero.
    net_prices: Mapping[tuple[str, str], Decimal] = field(default_factory=dict)

    # The guarantee governing the cycle this student is applying in, if any.
    # None means no threshold on file for this school.
    auto_admit: Optional[AutoAdmitRule] = None


@dataclass(frozen=True)
class Student:
    state: str
    region: str                    # 'contiguous' | 'alaska' | 'hawaii'
    family_income: Decimal
    family_size: int

    gpa_value: Optional[Decimal] = None
    gpa_scale: Optional[str] = None
    class_rank_percentile: Optional[Decimal] = None   # "top N percent"

    # The cycle this student is applying in, e.g. 'fall-2026'. Matched for EXACT
    # equality against a guarantee's effective_cycle; None never matches.
    applicant_cycle: Optional[str] = None

    years_of_college: int = DEFAULT_YEARS_OF_COLLEGE

    def __post_init__(self) -> None:
        """Reject impossible inputs at construction, before any verdict exists.

        Every rule here enforces something DEFINITIONAL, not a judgement call:
        a "top -5%" class rank would sail under every published threshold and
        fire an automatic-admission guarantee off nonsense, and a 4.3 on the
        unweighted scale (which maxes at 4.0 by definition) would prove a
        LIKELY bound no real student could hold. Rejecting these loudly at the
        door beats any downstream code quietly turning them into confident
        verdicts. Scales without a definitional cap (weighted, and the UC
        scheme, whose cap value we have not verified at the source) get no
        invented upper bound.
        """
        if not self.family_income.is_finite():
            raise ValueError("family_income must be a finite number")
        if self.family_size < 1:
            raise ValueError("family_size must be at least 1")
        if self.years_of_college < 1:
            raise ValueError("years_of_college must be positive")
        if self.gpa_value is not None:
            if not self.gpa_value.is_finite() or self.gpa_value < 0:
                raise ValueError("gpa_value must be a non-negative number")
            if self.gpa_scale == "unweighted" and self.gpa_value > Decimal("4.0"):
                raise ValueError(
                    "an unweighted GPA cannot exceed 4.0 -- if the GPA is on a "
                    "weighted scale, say so, because the scales do not compare"
                )
        if self.class_rank_percentile is not None:
            if (
                not self.class_rank_percentile.is_finite()
                or self.class_rank_percentile <= 0
                or self.class_rank_percentile > 100
            ):
                raise ValueError(
                    "class_rank_percentile means 'top N percent' and must be "
                    "greater than 0 and at most 100"
                )


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CeilingBreakdown:
    """Every term kept separately so the engine can show its work."""

    poverty_guideline: Decimal
    poverty_threshold: Decimal      # guideline x multiplier
    discretionary_income: Decimal
    family_term: Decimal
    work_term: Decimal
    loan_term: Decimal
    ceiling: Decimal

    # Which wage fed work_term. When the student's state has no wage row the
    # engine uses the documented federal rate -- correct for a federal-floor
    # state like Georgia, silently wrong for a higher-wage state that is merely
    # missing its row. Recording the fallback makes it a visible, deliberate
    # event a caller (or a test) can check, instead of an accident.
    wage_state_used: str = FEDERAL_WAGE_KEY
    wage_is_federal_fallback: bool = True

    def explain(self) -> str:
        return (
            f"Family contribution ${self.family_term:,.0f} "
            f"+ student work ${self.work_term:,.0f} "
            f"+ subsidized loan ${self.loan_term:,.0f} "
            f"= ${self.ceiling:,.0f} per year"
        )


@dataclass(frozen=True)
class AdmissionsAssessment:
    category: AdmissionsCategory
    reason: str
    basis: Optional[str] = None     # which published signal was used

    # What a guarantee actually covers. Both stay False for every non-guarantee
    # verdict, so a caller can never read a prediction as a promise.
    university_admission_guaranteed: bool = False
    major_admission_guaranteed: bool = False

    # The major caveat lives HERE rather than buried at the end of `reason`, so
    # a consumer renders it as a labelled next step under the headline rather
    # than as a qualifier that swallows the good news.
    next_step: Optional[str] = None

    # A guarantee must never reach a student without the cost read beside it --
    # "you're admitted" alone is half the answer, and the wrong half to deliver
    # on its own. Populated for GUARANTEED verdicts; the constructor path that
    # builds them refuses to run without it.
    affordability: Optional["AffordabilityAssessment"] = None


@dataclass(frozen=True)
class AffordabilityAssessment:
    verdict: AffordabilityVerdict
    reason: str
    net_price: Optional[Decimal] = None
    residency_used: Optional[Residency] = None
    gap: Optional[Decimal] = None        # net_price - ceiling, when unaffordable


@dataclass(frozen=True)
class SchoolAssessment:
    school_name: str
    admissions: AdmissionsAssessment
    affordability: AffordabilityAssessment


@dataclass(frozen=True)
class MatchResult:
    """Pre-split so the caller never has to re-derive the affordability gate.

    on_your_list  -> affordable; show these grouped by admissions category.
    not_on_your_list -> unaffordable or unknown; show separately WITH the reason,
                        which is why the reason string is mandatory on every
                        affordability assessment rather than optional.
    """

    ceiling: CeilingBreakdown
    on_your_list: Sequence[SchoolAssessment]
    not_on_your_list: Sequence[SchoolAssessment]


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def income_band_for(income: Decimal) -> str:
    """Map a family income to its IPEDS band."""
    for upper, label in INCOME_BANDS:
        if upper is None or income <= upper:
            return label
    raise AssertionError("INCOME_BANDS must end with an open-ended band")


def residency_for(student: Student, school: School) -> Residency:
    """Which price applies to this student at this school."""
    if not school.is_public:
        # Privates charge the same regardless of home state.
        return Residency.NOT_APPLICABLE
    if student.state == school.state:
        return Residency.IN_STATE
    return Residency.OUT_OF_STATE


def poverty_guideline_for(
    reference: ReferenceData, region: str, family_size: int
) -> Decimal:
    """HHS guideline for a household, extending past size 8 by the published increment."""
    if family_size < 1:
        raise ValueError("family_size must be at least 1")

    key = (region, min(family_size, 8))
    row = reference.poverty_guidelines.get(key)
    if row is None:
        raise KeyError(f"no poverty guideline for region={region!r} size={family_size}")

    if family_size <= 8:
        return row.amount
    extra = Decimal(family_size - 8) * row.additional_person_amount
    return row.amount + extra


def minimum_wage_for(reference: ReferenceData, state: str) -> Decimal:
    """State minimum wage, falling back to the documented federal rate."""
    if state in reference.minimum_wages:
        return reference.minimum_wages[state]
    return reference.minimum_wages[FEDERAL_WAGE_KEY]


# ---------------------------------------------------------------------------
# Affordability
# ---------------------------------------------------------------------------


def affordability_ceiling(student: Student, reference: ReferenceData) -> CeilingBreakdown:
    """What this student can plausibly pay per year, before looking at any school.

    family_term is an IDEALISED SAVINGS CAPACITY, not cash on hand: it models a
    family setting aside 10% of income above 200% of poverty for ten years, then
    spreading that pot across the years of college. A real family that did not
    save for ten years does not have this money. It is a policy benchmark for
    "what should college cost", which is exactly the question Pontis asks -- but
    it must never be presented to a student as their bank balance.
    """
    guideline = poverty_guideline_for(reference, student.region, student.family_size)
    threshold = guideline * POVERTY_MULTIPLIER

    # A family at or below 2x poverty is expected to contribute nothing. The
    # max() is the whole reason a low-income student is not quoted a negative
    # contribution that would silently inflate their ceiling.
    discretionary = max(Decimal("0"), student.family_income - threshold)

    if student.years_of_college <= 0:
        raise ValueError("years_of_college must be positive")

    # Written out as specified rather than pre-simplified, so it stays legible
    # against the source policy. (It does reduce to discretionary / years.)
    family_term = (SAVINGS_RATE * discretionary * SAVINGS_YEARS) / Decimal(
        student.years_of_college
    )

    wage_is_fallback = student.state not in reference.minimum_wages
    work_term = ANNUAL_WORK_HOURS * minimum_wage_for(reference, student.state)
    loan_term = reference.subsidized_loan_limit

    return CeilingBreakdown(
        poverty_guideline=guideline,
        poverty_threshold=threshold,
        discretionary_income=discretionary,
        family_term=family_term,
        work_term=work_term,
        loan_term=loan_term,
        ceiling=family_term + work_term + loan_term,
        wage_state_used=FEDERAL_WAGE_KEY if wage_is_fallback else student.state,
        wage_is_federal_fallback=wage_is_fallback,
    )


def assess_affordability(
    student: Student, school: School, ceiling: CeilingBreakdown
) -> AffordabilityAssessment:
    """Compare this school's price FOR THIS STUDENT against their ceiling."""
    residency = residency_for(student, school)
    band = income_band_for(student.family_income)

    # Exactly one lookup, with this student's residency. There is deliberately no
    # second attempt at another residency: an out-of-state student is never
    # quoted the in-state price.
    net_price = school.net_prices.get((band, residency.value))

    if net_price is None:
        return AffordabilityAssessment(
            verdict=AffordabilityVerdict.UNKNOWN,
            reason=(
                f"No published net price for the {band} income band at "
                f"{residency.value.replace('_', '-')} rates. Federal data reports "
                f"net price for public universities using in-state students only, "
                f"so this cost is genuinely unknown rather than high or low."
            ),
            residency_used=residency,
        )

    if net_price <= ceiling.ceiling:
        return AffordabilityAssessment(
            verdict=AffordabilityVerdict.AFFORDABLE,
            reason=(
                f"Net price ${net_price:,.0f} is within your estimated "
                f"${ceiling.ceiling:,.0f} per year."
            ),
            net_price=net_price,
            residency_used=residency,
        )

    gap = net_price - ceiling.ceiling
    return AffordabilityAssessment(
        verdict=AffordabilityVerdict.UNAFFORDABLE,
        reason=(
            f"Net price ${net_price:,.0f} exceeds your estimated "
            f"${ceiling.ceiling:,.0f} per year by ${gap:,.0f}."
        ),
        net_price=net_price,
        residency_used=residency,
        gap=gap,
    )


# ---------------------------------------------------------------------------
# Admissions
# ---------------------------------------------------------------------------


def _assess_class_rank_proxy(
    student: Student,
    school: School,
    affordability: Optional["AffordabilityAssessment"],
) -> AdmissionsAssessment:
    """Rank-driven schools (UT Austin) against a published automatic-admission rule.

    Three fences must ALL hold before the guarantee fires: the student is a
    resident of the state the guarantee covers, a class rank was actually
    submitted, and the student's application cycle matches the rule's cycle
    exactly. Any one unmet means no guarantee -- not a weaker guarantee, none.

    The cycle fence is the important one. UT ran top 6% for Fall 2025 and top 5%
    from Fall 2026, so applying a stored threshold to the wrong cycle promises a
    student something nobody offered them.
    """
    rule = school.auto_admit

    if rule is None:
        return AdmissionsAssessment(
            category=AdmissionsCategory.UNABLE_TO_ASSESS_ON_GPA,
            reason=(
                "This school admits on class rank rather than GPA, and no "
                "published automatic-admission threshold is on file for it."
            ),
            basis="class_rank_proxy",
        )

    bar = (
        f"{school.name} guarantees admission to {rule.resident_state} residents in "
        f"the top {_fmt_pct(rule.threshold_top_pct)}% of their high school class "
        f"for the {rule.effective_cycle} cycle"
    )

    # Fence 1 -- a rank was actually submitted. Same discipline as the BC branch:
    # show the bar, invite the rank, place nobody.
    if student.class_rank_percentile is None:
        return AdmissionsAssessment(
            category=AdmissionsCategory.CONTEXT_NOT_PLACED,
            reason=(
                f"{bar}. You have not given your class rank, so you have not been "
                f"placed against that bar -- add your rank to see whether you "
                f"clear it."
            ),
            basis="class_rank_proxy",
        )

    # Fence 2 -- the cycle matches exactly. No nearest-year fallback: a stale
    # promise is worse than no promise.
    if student.applicant_cycle != rule.effective_cycle:
        applying = student.applicant_cycle or "an unspecified cycle"
        return AdmissionsAssessment(
            category=AdmissionsCategory.CONTEXT_NOT_PLACED,
            reason=(
                f"{bar}. You are applying in {applying}, and these thresholds move "
                f"between cycles, so the rule on file cannot be applied to your "
                f"application. Check the threshold published for your own cycle."
            ),
            basis="class_rank_proxy",
        )

    # Fence 3 -- residency. The guarantee is a state-law benefit.
    if student.state != rule.resident_state:
        return AdmissionsAssessment(
            category=AdmissionsCategory.HOLISTIC_REVIEW,
            reason=(
                f"{bar}, which does not extend to applicants from outside "
                f"{rule.resident_state}. Your application goes to the holistic pool, "
                f"which is read on the whole record -- coursework and rigour, essays, "
                f"context, and the competitiveness of the major you name. There is no "
                f"published admit rate by class rank for that pool, so no placement "
                f"is claimed here."
            ),
            basis="class_rank_proxy",
        )

    # All three fences hold.
    if student.class_rank_percentile <= rule.threshold_top_pct:
        if affordability is None:
            # Structural, not stylistic. A guarantee handed over without its price
            # tag is the half of the answer that gets a student to apply and then
            # strands them, so the object cannot be built without the cost read.
            raise ValueError(
                "the automatic-admission guarantee cannot be assessed without an "
                "affordability read -- call assess_school() or match(), which pair "
                "them, rather than assess_admissions() alone"
            )

        # Headline first, plainly: the door is open.
        headline = (
            f"You are automatically admitted to {school.name}. Your class rank in "
            f"the top {_fmt_pct(student.class_rank_percentile)}% clears the published "
            f"top {_fmt_pct(rule.threshold_top_pct)}% guarantee for "
            f"{rule.resident_state} residents in the {rule.effective_cycle} cycle. "
            f"This is a published guarantee, not an estimate."
        )

        # Caveat second, and as a route forward rather than a retraction.
        next_step = None
        if rule.guarantees_university and not rule.guarantees_major:
            next_step = (
                "How to go further, into a competitive major: your place at the "
                "university is secured, and majors are admitted separately. Name the "
                "major you want on your application and build the strongest case for "
                "it -- relevant coursework, and anything showing sustained work in "
                "the field. If that major does not take you, you are still admitted "
                "to the university and can pursue it from inside."
            )

        return AdmissionsAssessment(
            category=AdmissionsCategory.GUARANTEED,
            reason=headline,
            basis="class_rank_auto_admit",
            university_admission_guaranteed=rule.guarantees_university,
            major_admission_guaranteed=rule.guarantees_major,
            next_step=next_step,
            affordability=affordability,
        )

    # Just outside the line. Describe the pool; assert nothing about odds.
    return AdmissionsAssessment(
        category=AdmissionsCategory.HOLISTIC_REVIEW,
        reason=(
            f"Your class rank in the top {_fmt_pct(student.class_rank_percentile)}% is "
            f"just outside the published top {_fmt_pct(rule.threshold_top_pct)}% "
            f"automatic-admission threshold, so your application is read in the "
            f"holistic pool. That review weighs the whole record -- coursework and "
            f"rigour, essays, context, and the competitiveness of the major you name. "
            f"No admit rate by class rank is published for that pool, so this is a "
            f"description of how you will be read, not a prediction of the outcome."
        ),
        basis="class_rank_proxy",
    )


def _fmt_pct(value: Decimal) -> str:
    """Render a Decimal percentage for humans: 90.0000 -> '90', 26.6000 -> '26.6'.

    Decimal's 'g' format does NOT strip trailing zeros the way float's does, and
    Decimal.normalize() turns 100 into '1E+2', so both obvious one-liners are
    wrong. Hence the explicit integral check.
    """
    integral = value.to_integral_value()
    if value == integral:
        return str(int(integral))
    return str(value.normalize())


def _rank_context_sentence(gpa: SchoolGpaData) -> str:
    """The published distribution, stated with its own coverage caveat."""
    share_pct = _fmt_pct(gpa.class_rank_share * Decimal(100))
    sentence = (
        f"{share_pct}% of this school's first-year students were in the top "
        f"{_fmt_pct(gpa.class_rank_top_pct)}% of their high school class"
    )
    if gpa.class_rank_reporting_share is not None:
        coverage_pct = _fmt_pct(gpa.class_rank_reporting_share * Decimal(100))
        # Without this the figure reads as a fact about the whole class when it
        # is a fact about the minority who reported a rank.
        sentence += (
            f", among the {coverage_pct}% of students the school collected a "
            f"class rank for"
        )
    return sentence


def _assess_on_rank_distribution(
    student: Student, school: School
) -> AdmissionsAssessment:
    """Place a student against a published class-rank distribution (BC's case).

    Deliberately caps out at TARGET and never returns LIKELY. The published
    statistic is "of those who got in, 90% were top 10%" -- that is
    P(top decile | admitted), NOT P(admitted | top decile). Reading it the second
    way at a school admitting 16% of applicants would tell a top-decile student
    they are safe when they are not. Being inside the band means you look like the
    students who got in; it does not mean you will.
    """
    gpa = school.gpa
    context = _rank_context_sentence(gpa)

    if student.class_rank_percentile is None:
        return AdmissionsAssessment(
            category=AdmissionsCategory.CONTEXT_NOT_PLACED,
            reason=(
                f"This school publishes no GPA, but it does publish class rank: "
                f"{context}. You have not given your class rank, so you have not "
                f"been placed against that bar -- add your rank to see where you "
                f"fall."
            ),
            basis="class_rank_distribution",
        )

    if student.class_rank_percentile <= gpa.class_rank_top_pct:
        return AdmissionsAssessment(
            category=AdmissionsCategory.TARGET,
            reason=(
                f"Your class rank in the top {_fmt_pct(student.class_rank_percentile)}% is "
                f"inside the band where most admitted students sat: {context}. That "
                f"matches the profile of students who got in, though admission "
                f"remains competitive."
            ),
            basis="class_rank_distribution",
        )

    return AdmissionsAssessment(
        category=AdmissionsCategory.REACH,
        reason=(
            f"Your class rank in the top {_fmt_pct(student.class_rank_percentile)}% is "
            f"outside the band where most admitted students sat: {context}."
        ),
        basis="class_rank_distribution",
    )


def _fmt_floor_pct(fraction: Decimal) -> str:
    """Render a fraction as a percentage FLOORED to one decimal: 0.59832 -> '59.8'.

    Flooring is load-bearing: every band sentence says "at least X%", so X must
    round DOWN or the sentence overstates what the published bands prove.
    """
    pct = (fraction * Decimal(100)).quantize(Decimal("0.1"), rounding=ROUND_FLOOR)
    return _fmt_pct(pct)


def _band_coverage_caveat(gpa: SchoolGpaData) -> str:
    """', among the N% of students the school collected a GPA for' -- or ''.

    Same job as the class-rank coverage caveat: without it a bounded claim about
    the reporting subset reads as a claim about the whole class.
    """
    if gpa.band_reporting_share is None:
        return ""
    return (
        f", among the {_fmt_pct(gpa.band_reporting_share * Decimal(100))}% of "
        f"students the school collected a GPA for"
    )


def _assess_on_band_distribution(
    student: Student, school: School
) -> Optional[AdmissionsAssessment]:
    """Place a student by cumulative share of a published GPA band distribution.

    The honest mechanics: only WHOLE bands strictly below (or strictly above)
    the student's GPA are summed. Students inside the student's own band are
    never split or interpolated -- their position relative to the student is
    unknown, so they count toward neither bound. Each sum is therefore a hard
    LOWER BOUND: "at least X% of the class had a lower GPA than yours" is true
    by arithmetic on published figures alone.

    A bound that clears the same 75% bar the percentile path uses supports the
    same label the percentile path would give. This is what finally lets LIKELY
    fire at schools that publish bands but no p25/p75 -- the anti-undermatching
    half of the tool.

    Returns None when the published shares do not sum to ~1: an incomplete or
    suppressed distribution proves nothing, and the caller falls through to the
    next-weaker published signal rather than a partial sum being passed off as
    a bound.
    """
    gpa_data = school.gpa
    g = student.gpa_value
    assert g is not None  # caller guarantees; the scale/value guards run first

    total = sum((band.share for band in gpa_data.bands), Decimal(0))
    if abs(total - Decimal(1)) > BAND_SHARE_SUM_TOLERANCE:
        return None

    below = sum(
        (band.share for band in gpa_data.bands if band.ceiling < g), Decimal(0)
    )
    above = sum(
        (band.share for band in gpa_data.bands if band.floor > g), Decimal(0)
    )
    # Published rounding can push a sum a hair over 1 (GSU's total is 1.00002);
    # a bound above 100% is not a thing.
    below = min(below, Decimal(1))
    above = min(above, Decimal(1))

    population = gpa_data.band_population or "enrolled"
    caveat = _band_coverage_caveat(gpa_data)

    if below >= BAND_PLACEMENT_QUARTILE:
        return AdmissionsAssessment(
            category=AdmissionsCategory.LIKELY,
            reason=(
                f"Your {g} ({gpa_data.gpa_type} scale) is above the 75th "
                f"percentile of this school's {population} first-year students: "
                f"at least {_fmt_floor_pct(below)}% had a lower GPA than yours, "
                f"summed from the school's published GPA bands with no "
                f"interpolation{caveat}."
            ),
            basis="band_distribution",
        )

    if above >= BAND_PLACEMENT_QUARTILE:
        return AdmissionsAssessment(
            category=AdmissionsCategory.REACH,
            reason=(
                f"At least {_fmt_floor_pct(above)}% of this school's "
                f"{population} first-year students had a higher GPA than your "
                f"{g} ({gpa_data.gpa_type} scale), summed from the school's "
                f"published GPA bands{caveat}."
            ),
            basis="band_distribution",
        )

    return AdmissionsAssessment(
        category=AdmissionsCategory.TARGET,
        reason=(
            f"Your {g} ({gpa_data.gpa_type} scale) sits with the middle of this "
            f"school's {population} first-year students: at least "
            f"{_fmt_floor_pct(below)}% had a lower GPA than yours and at least "
            f"{_fmt_floor_pct(above)}% a higher one, summed from the school's "
            f"published GPA bands{caveat}."
        ),
        basis="band_distribution",
    )


def assess_admissions(
    student: Student,
    school: School,
    affordability: Optional["AffordabilityAssessment"] = None,
) -> AdmissionsAssessment:
    """Position-based admissions read. Never converts between GPA scales.

    `affordability` is required only on the path that can produce a GUARANTEED
    verdict, which refuses to be built without it. Every other path ignores it,
    so the ordinary two-argument call still works.
    """
    school_gpa = school.gpa

    if school_gpa.gpa_type == "not_published":
        # No GPA, but a published class-rank distribution is still a real signal.
        if school_gpa.has_rank_distribution():
            return _assess_on_rank_distribution(student, school)
        return AdmissionsAssessment(
            category=AdmissionsCategory.UNABLE_TO_ASSESS_ON_GPA,
            reason=(
                "This school does not publish GPA data for admitted or enrolled "
                "students, so GPA is not the operative signal here. Its published "
                "admission factors will speak to this school once available."
            ),
            basis="not_published",
        )

    if school_gpa.gpa_type == "class_rank_proxy":
        return _assess_class_rank_proxy(student, school, affordability)

    if school_gpa.gpa_type not in COMPARABLE_SCALES:
        return AdmissionsAssessment(
            category=AdmissionsCategory.UNABLE_TO_ASSESS,
            reason=f"Unrecognised published GPA scale {school_gpa.gpa_type!r}.",
        )

    if student.gpa_value is None or student.gpa_scale is None:
        return AdmissionsAssessment(
            category=AdmissionsCategory.UNABLE_TO_ASSESS,
            reason="No GPA (with its scale) was provided for the student.",
        )

    # The load-bearing refusal. Comparing 3.9 unweighted against a UC
    # weighted-capped range would produce a confident, wrong answer.
    if student.gpa_scale != school_gpa.gpa_type:
        return AdmissionsAssessment(
            category=AdmissionsCategory.UNABLE_TO_ASSESS,
            reason=(
                f"Your GPA is on the {student.gpa_scale} scale but this school "
                f"publishes {school_gpa.gpa_type}. These scales are not "
                f"interconvertible, so no honest comparison is possible."
            ),
        )

    gpa = student.gpa_value

    # Preferred: a published percentile range gives real dispersion.
    if school_gpa.gpa_p25 is not None and school_gpa.gpa_p75 is not None:
        if gpa > school_gpa.gpa_p75:
            category, phrase = AdmissionsCategory.LIKELY, "above the 75th percentile of"
        elif gpa >= school_gpa.gpa_p25:
            category, phrase = AdmissionsCategory.TARGET, "within the middle 50% of"
        else:
            category, phrase = AdmissionsCategory.REACH, "below the 25th percentile of"
        return AdmissionsAssessment(
            category=category,
            reason=(
                f"Your {gpa} is {phrase} admitted students "
                f"({school_gpa.gpa_p25}-{school_gpa.gpa_p75}, "
                f"{school_gpa.gpa_type} scale)."
            ),
            basis="percentile_range",
        )

    # Next-best: a published band distribution supports provable cumulative
    # bounds. Ranked below a published p25/p75 (which needs no derivation at
    # all) but above a bare point average (which carries no dispersion).
    if school_gpa.bands:
        band_assessment = _assess_on_band_distribution(student, school)
        if band_assessment is not None:
            return band_assessment

    if school_gpa.gpa_value is not None:
        if gpa >= school_gpa.gpa_value:
            # Deliberately TARGET and not LIKELY. A point average carries no
            # dispersion, so "at or above the average" cannot honestly be
            # upgraded to "likely" -- that would require knowing the spread.
            return AdmissionsAssessment(
                category=AdmissionsCategory.TARGET,
                reason=(
                    f"Your {gpa} is at or above the published average of "
                    f"{school_gpa.gpa_value} ({school_gpa.gpa_type} scale). Only an "
                    f"average is published, with no spread, so this is a target "
                    f"rather than a safe bet."
                ),
                basis="point_average",
            )
        return AdmissionsAssessment(
            category=AdmissionsCategory.REACH,
            reason=(
                f"Your {gpa} is below the published average of "
                f"{school_gpa.gpa_value} ({school_gpa.gpa_type} scale)."
            ),
            basis="point_average",
        )

    return AdmissionsAssessment(
        category=AdmissionsCategory.UNABLE_TO_ASSESS_ON_GPA,
        reason=(
            "This school publishes a GPA scale but no usable figure on it -- "
            "no average, no percentile range, and no complete band distribution."
        ),
    )


# ---------------------------------------------------------------------------
# Top level
# ---------------------------------------------------------------------------


def assess_school(
    student: Student, school: School, reference: ReferenceData
) -> SchoolAssessment:
    """Both verdicts for one school, computed independently of each other."""
    ceiling = affordability_ceiling(student, reference)
    affordability = assess_affordability(student, school, ceiling)
    return SchoolAssessment(
        school_name=school.name,
        admissions=assess_admissions(student, school, affordability=affordability),
        affordability=affordability,
    )


def match(
    student: Student, schools: Sequence[School], reference: ReferenceData
) -> MatchResult:
    """Assess every school and split on the affordability gate only.

    The split is by affordability alone; the admissions category rides along
    untouched inside each bucket. A school the student would walk into but cannot
    pay for appears in not_on_your_list WITH its 'likely' category intact and a
    reason attached, rather than being silently dropped or quietly downgraded.
    """
    ceiling = affordability_ceiling(student, reference)

    on_list: list[SchoolAssessment] = []
    off_list: list[SchoolAssessment] = []

    for school in schools:
        affordability = assess_affordability(student, school, ceiling)
        assessment = SchoolAssessment(
            school_name=school.name,
            admissions=assess_admissions(student, school, affordability=affordability),
            affordability=affordability,
        )
        if assessment.affordability.verdict is AffordabilityVerdict.AFFORDABLE:
            on_list.append(assessment)
        else:
            off_list.append(assessment)

    return MatchResult(ceiling=ceiling, on_your_list=on_list, not_on_your_list=off_list)
