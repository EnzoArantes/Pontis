"""Shared fixtures: the real seeded schools and reference data.

Fixtures mirror the REAL seeded values (2026 HHS guidelines, real minimum wages,
the seed schools' actual published figures) so that a test failing here means
the engine is wrong, not that the fixture drifted from reality.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.matching import (  # noqa: E402
    AutoAdmitRule,
    GpaBand,
    PovertyGuideline,
    ReferenceData,
    School,
    SchoolGpaData,
)

D = Decimal


@pytest.fixture
def reference() -> ReferenceData:
    """2026 HHS guidelines (contiguous), real minimum wages, real loan limit.

    Georgia deliberately has NO wage row: Georgia's own minimum is below the
    federal floor, so the federal rate is the operative one, and the engine
    reaches it through the documented 'US' fallback -- which the fallback
    tests then prove is recorded as a fallback, not passed off as state data.
    """
    contiguous = [15960, 21640, 27320, 33000, 38680, 44360, 50040, 55720]
    guidelines = {
        ("contiguous", size): PovertyGuideline(
            amount=D(amount), additional_person_amount=D(5680)
        )
        for size, amount in enumerate(contiguous, start=1)
    }
    return ReferenceData(
        poverty_guidelines=guidelines,
        minimum_wages={"CA": D("16.90"), "MA": D("15.00"), "TX": D("7.25"), "US": D("7.25")},
        subsidized_loan_limit=D(3500),
    )


@pytest.fixture
def boston_college() -> School:
    """Private, meets full need, publishes NO GPA (CDS C12 blank) but DOES publish
    a class-rank distribution: 90% of first-years were top 10%, among the 26.6%
    of students BC collected a rank for."""
    return School(
        name="Boston College",
        state="MA",
        is_public=False,
        meets_full_need=True,
        gpa=SchoolGpaData(
            gpa_type="not_published",
            class_rank_top_pct=D(10),
            class_rank_share=D("0.9000"),
            class_rank_reporting_share=D("0.2660"),
        ),
        net_prices={
            ("0-30k", "not_applicable"): D(4284),
            ("30-48k", "not_applicable"): D(7304),
            ("48-75k", "not_applicable"): D(13112),
            ("75-110k", "not_applicable"): D(19999),
            ("110k+", "not_applicable"): D(60308),
        },
    )


@pytest.fixture
def berkeley() -> School:
    """Public. Publishes a UC weighted-capped RANGE. Only in-state net price exists."""
    return School(
        name="University of California-Berkeley",
        state="CA",
        is_public=True,
        meets_full_need=False,
        gpa=SchoolGpaData(
            gpa_type="uc_weighted_capped", gpa_p25=D("4.160"), gpa_p75=D("4.280")
        ),
        net_prices={
            ("0-30k", "in_state"): D(5311),
            ("30-48k", "in_state"): D(6501),
            ("48-75k", "in_state"): D(9693),
            ("75-110k", "in_state"): D(15074),
            ("110k+", "in_state"): D(34529),
        },
    )


@pytest.fixture
def ut_austin() -> School:
    """Public, rank-driven. Publishes a top-5% automatic-admission guarantee for
    TX residents in the fall-2026 cycle, covering the university but NOT a major."""
    return School(
        name="The University of Texas at Austin",
        state="TX",
        is_public=True,
        meets_full_need=False,
        gpa=SchoolGpaData(gpa_type="class_rank_proxy"),
        net_prices={
            ("0-30k", "in_state"): D(12553),
            ("30-48k", "in_state"): D(14297),
        },
        auto_admit=AutoAdmitRule(
            effective_cycle="fall-2026",
            resident_state="TX",
            threshold_top_pct=D("5.00"),
            guarantees_university=True,
            guarantees_major=False,
        ),
    )


@pytest.fixture
def umass_amherst() -> School:
    """Public. Publishes a WEIGHTED point average (4.05 -- impossible unweighted).
    Only in-state net price exists."""
    return School(
        name="University of Massachusetts-Amherst",
        state="MA",
        is_public=True,
        meets_full_need=False,
        gpa=SchoolGpaData(gpa_type="weighted", gpa_value=D("4.050")),
        net_prices={
            ("0-30k", "in_state"): D(10164),
            ("30-48k", "in_state"): D(10456),
            ("48-75k", "in_state"): D(12932),
            ("75-110k", "in_state"): D(18964),
            ("110k+", "in_state"): D(30793),
        },
    )


# Georgia State's published band distribution, CDS 2025-26 section C11, exactly
# as filed (shares total 1.00002 -- the workbook's own rounding, kept as-is).
GSU_BANDS = (
    GpaBand(floor=D("4.00"), ceiling=D("4.00"), share=D("0.1804")),
    GpaBand(floor=D("3.75"), ceiling=D("3.99"), share=D("0.2213")),
    GpaBand(floor=D("3.50"), ceiling=D("3.74"), share=D("0.2529")),
    GpaBand(floor=D("3.25"), ceiling=D("3.49"), share=D("0.1704")),
    GpaBand(floor=D("3.00"), ceiling=D("3.24"), share=D("0.1493")),
    GpaBand(floor=D("2.50"), ceiling=D("2.99"), share=D("0.0207")),
    GpaBand(floor=D("2.00"), ceiling=D("2.49"), share=D("0.0050")),
    GpaBand(floor=D("1.00"), ceiling=D("1.99"), share=D("0.00002")),
    GpaBand(floor=D("0.00"), ceiling=D("0.99"), share=D("0")),
)


@pytest.fixture
def georgia_state() -> School:
    """Public, unweighted scale. Publishes BOTH a point average (3.6) and the
    full C11 band distribution -- the band path should win over the average."""
    return School(
        name="Georgia State University",
        state="GA",
        is_public=True,
        meets_full_need=False,
        gpa=SchoolGpaData(
            gpa_type="unweighted",
            gpa_value=D("3.600"),
            bands=GSU_BANDS,
            band_population="enrolled",
            band_reporting_share=D("0.9991"),
        ),
        net_prices={
            ("0-30k", "in_state"): D(13787),
            ("30-48k", "in_state"): D(14430),
            ("48-75k", "in_state"): D(16656),
            ("75-110k", "in_state"): D(19390),
            ("110k+", "in_state"): D(20305),
        },
    )
