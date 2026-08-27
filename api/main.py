"""Pontis REST API.

Thin by design: validation at the door, the pure engine in the middle, and a
response that preserves the engine's contract --

  * two axes per school, never a combined score;
  * `unknown` and "not ingested" as first-class rendered states;
  * the ceiling arithmetic returned term by term, so the UI can show the
    math instead of a verdict from nowhere;
  * the "not on your list" section carrying each school's reason and its
    intact admissions category.

    POST /api/match      student profile -> full two-axis result
    GET  /api/schools    the roster and what data each school carries
    GET  /api/health     liveness + database reachability
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from api import loader
from engine.matching import (
    CeilingBreakdown,
    SchoolAssessment,
    Student,
    match,
)

app = FastAPI(
    title="Pontis",
    description=(
        "Which colleges can a low-income student both get into and afford? "
        "Two independent verdicts per school -- deliberately never blended "
        "into a single score."
    ),
)

# The React dev server; same-origin in the containerized deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class StudentProfile(BaseModel):
    state: str = Field(min_length=2, max_length=2,
                       description="Two-letter home state, e.g. 'GA'")
    region: str = Field(default="contiguous",
                        pattern="^(contiguous|alaska|hawaii)$")
    family_income: Decimal
    family_size: int
    gpa_value: Optional[Decimal] = None
    gpa_scale: Optional[str] = Field(
        default=None,
        description="unweighted | weighted | uc_weighted_capped",
    )
    class_rank_percentile: Optional[Decimal] = Field(
        default=None, description="'top N percent' as N")
    applicant_cycle: Optional[str] = Field(
        default=None, description="e.g. 'fall-2026'")


def _money(value: Optional[Decimal]) -> Optional[float]:
    """Decimal -> JSON number. Pydantic would emit Decimals as strings;
    these are dollar amounts well inside float exactness."""
    return None if value is None else float(value)


def _ceiling_payload(ceiling: CeilingBreakdown) -> dict:
    return {
        "poverty_guideline": _money(ceiling.poverty_guideline),
        "poverty_threshold": _money(ceiling.poverty_threshold),
        "discretionary_income": _money(ceiling.discretionary_income),
        "family_term": _money(ceiling.family_term),
        "work_term": _money(ceiling.work_term),
        "loan_term": _money(ceiling.loan_term),
        "ceiling": _money(ceiling.ceiling),
        "wage_state_used": ceiling.wage_state_used,
        "wage_is_federal_fallback": ceiling.wage_is_federal_fallback,
        "explain": ceiling.explain(),
    }


def _assessment_payload(assessment: SchoolAssessment) -> dict:
    admissions = assessment.admissions
    affordability = assessment.affordability
    return {
        "school_name": assessment.school_name,
        "admissions": {
            "category": admissions.category.value,
            "reason": admissions.reason,
            "basis": admissions.basis,
            "university_admission_guaranteed":
                admissions.university_admission_guaranteed,
            "major_admission_guaranteed": admissions.major_admission_guaranteed,
            "next_step": admissions.next_step,
        },
        "affordability": {
            "verdict": affordability.verdict.value,
            "reason": affordability.reason,
            "net_price": _money(affordability.net_price),
            "residency_used": (
                affordability.residency_used.value
                if affordability.residency_used else None
            ),
            "gap": _money(affordability.gap),
        },
    }


@app.post("/api/match")
def match_endpoint(profile: StudentProfile) -> dict:
    try:
        student = Student(
            state=profile.state.upper(),
            region=profile.region,
            family_income=profile.family_income,
            family_size=profile.family_size,
            gpa_value=profile.gpa_value,
            gpa_scale=profile.gpa_scale,
            class_rank_percentile=profile.class_rank_percentile,
            applicant_cycle=profile.applicant_cycle,
        )
    except ValueError as exc:
        # The engine's definitional rejections (negative rank, unweighted
        # above 4.0, ...) surface with their own explanations.
        raise HTTPException(status_code=422, detail=str(exc))

    reference, raw_schools = loader.load()
    schools = [
        loader.build_school(raw, student.applicant_cycle, student.state)
        for raw in raw_schools
    ]
    result = match(student, schools, reference)

    return {
        "ceiling": _ceiling_payload(result.ceiling),
        "on_your_list": [_assessment_payload(a) for a in result.on_your_list],
        "not_on_your_list": [
            _assessment_payload(a) for a in result.not_on_your_list
        ],
    }


@app.get("/api/schools")
def schools_endpoint() -> dict:
    _, raw_schools = loader.load()
    return {
        "schools": [
            {
                "name": raw["name"],
                "state": raw["state"],
                "ipeds_unitid": raw["unitid"],
                "is_public": raw["is_public"],
                "meets_full_need": raw["meets_full_need"],
                "has_admissions_data": "stats" in raw,
                "has_band_distribution": "bands" in raw,
                "net_price_rows": len(raw.get("net_prices", {})),
            }
            for raw in raw_schools
        ]
    }


@app.get("/api/health")
def health() -> dict:
    try:
        loader.load()
        return {"status": "ok", "database": "reachable"}
    except Exception as exc:  # pragma: no cover - only fires without a DB
        raise HTTPException(status_code=503, detail=f"database unreachable: {exc}")


# The built React app, when present (local `npm run build`, or baked into the
# container image). Mounted last so /api/* keeps priority; absent in pure-API
# development, where the Vite dev server proxies instead.
_WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
if _WEB_DIST.is_dir():  # pragma: no branch
    app.mount("/", StaticFiles(directory=_WEB_DIST, html=True), name="web")
