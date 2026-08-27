"""Load engine inputs from Postgres.

The engine is pure; this module is the one place database rows become engine
values. The mapping rules mirror the honesty constraints:

  * A school with no admission_stats row gets gpa=None ("nothing ingested"),
    which the engine renders as OUR gap, never as a claim about the school.
  * Bands attach only when their scale matches the school's headline GPA
    scale for the same cohort year -- the engine refuses mixed scales, so the
    loader never builds them.
  * Auto-admit rules are chosen per request: the rule matching the student's
    exact cycle and state if one exists, otherwise the newest rule on file so
    the engine can show the bar while honestly refusing to apply it.

Rows are cached for a short TTL: the data changes at ingestion cadence
(days), not request cadence, and a stale-by-a-minute roster is harmless.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Optional

from engine.matching import (
    AutoAdmitRule,
    GpaBand,
    PovertyGuideline,
    ReferenceData,
    School,
    SchoolGpaData,
)
from ingest.db import connect

CACHE_TTL_SECONDS = 60

_cache: dict = {"at": 0.0, "reference": None, "raw_schools": None}


def _load_reference(cur) -> ReferenceData:
    cur.execute(
        """
        SELECT region, family_size, amount, additional_person_amount
          FROM poverty_guidelines
         WHERE year = (SELECT max(year) FROM poverty_guidelines)
        """
    )
    guidelines = {
        (region, size): PovertyGuideline(
            amount=amount, additional_person_amount=extra
        )
        for region, size, amount, extra in cur.fetchall()
    }

    cur.execute("SELECT state, hourly_wage FROM state_minimum_wage")
    wages = {state: wage for state, wage in cur.fetchall()}

    # The engine's policy: dependent first-year subsidized limit only.
    cur.execute(
        """
        SELECT subsidized_annual_limit
          FROM federal_loan_limits
         WHERE dependency_status = 'dependent' AND year_level = 1
         ORDER BY award_year DESC
         LIMIT 1
        """
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("federal_loan_limits is empty; run the reference seed")

    return ReferenceData(
        poverty_guidelines=guidelines,
        minimum_wages=wages,
        subsidized_loan_limit=Decimal(row[0]),
    )


def _load_raw_schools(cur) -> list[dict]:
    cur.execute(
        """
        SELECT id, ipeds_unitid, name, state, is_public, meets_full_need
          FROM colleges ORDER BY name
        """
    )
    schools = [
        dict(zip(("id", "unitid", "name", "state", "is_public",
                  "meets_full_need"), row))
        for row in cur.fetchall()
    ]
    by_id = {s["id"]: s for s in schools}

    # Latest admissions row per school.
    cur.execute(
        """
        SELECT DISTINCT ON (college_id)
               college_id, year, gpa_type, gpa_value, gpa_p25, gpa_p75,
               class_rank_top_pct, class_rank_share, class_rank_reporting_share
          FROM admission_stats
         ORDER BY college_id, year DESC
        """
    )
    for (cid, year, gpa_type, value, p25, p75,
         rank_top, rank_share, rank_cover) in cur.fetchall():
        by_id[cid]["stats"] = dict(
            year=year, gpa_type=gpa_type, gpa_value=value, gpa_p25=p25,
            gpa_p75=p75, class_rank_top_pct=rank_top,
            class_rank_share=rank_share,
            class_rank_reporting_share=rank_cover,
        )

    cur.execute(
        """
        SELECT college_id, year, gpa_type, population, band_floor,
               band_ceiling, share, reporting_share
          FROM gpa_band_distribution
         ORDER BY college_id, year DESC, band_floor
        """
    )
    for (cid, year, gpa_type, population, floor, ceiling,
         share, reporting) in cur.fetchall():
        bands = by_id[cid].setdefault("bands", dict(
            year=year, gpa_type=gpa_type, population=population,
            reporting_share=reporting, rows=[],
        ))
        if bands["year"] == year and bands["gpa_type"] == gpa_type:
            bands["rows"].append((floor, ceiling, share))

    cur.execute(
        """
        SELECT college_id, income_band, residency, net_price_within_band
          FROM net_price_by_income
        """
    )
    for cid, band, residency, price in cur.fetchall():
        by_id[cid].setdefault("net_prices", {})[(band, residency)] = Decimal(price)

    cur.execute(
        """
        SELECT college_id, effective_cycle, resident_state, threshold_top_pct,
               guarantees_university, guarantees_major
          FROM class_rank_auto_admit
         ORDER BY college_id, effective_cycle
        """
    )
    for cid, cycle, res_state, threshold, g_uni, g_major in cur.fetchall():
        by_id[cid].setdefault("auto_admit", []).append(dict(
            effective_cycle=cycle, resident_state=res_state,
            threshold_top_pct=threshold, guarantees_university=g_uni,
            guarantees_major=g_major,
        ))

    return schools


def load(refresh: bool = False) -> tuple[ReferenceData, list[dict]]:
    now = time.monotonic()
    if refresh or _cache["reference"] is None or now - _cache["at"] > CACHE_TTL_SECONDS:
        with connect() as conn:
            with conn.cursor() as cur:
                _cache["reference"] = _load_reference(cur)
                _cache["raw_schools"] = _load_raw_schools(cur)
                _cache["at"] = now
    return _cache["reference"], _cache["raw_schools"]


def _pick_auto_admit(rules: Optional[list[dict]],
                     applicant_cycle: Optional[str],
                     student_state: Optional[str]) -> Optional[AutoAdmitRule]:
    if not rules:
        return None
    chosen = None
    for rule in rules:
        if (rule["effective_cycle"] == applicant_cycle
                and rule["resident_state"] == student_state):
            chosen = rule
            break
    if chosen is None:
        # Newest rule on file: the engine will show this bar and honestly
        # refuse to apply it across a cycle or state mismatch.
        chosen = max(rules, key=lambda r: r["effective_cycle"])
    return AutoAdmitRule(**chosen)


def build_school(raw: dict, applicant_cycle: Optional[str],
                 student_state: Optional[str]) -> School:
    gpa: Optional[SchoolGpaData] = None
    stats = raw.get("stats")
    bands = raw.get("bands")

    if stats is not None:
        band_kwargs: dict = {}
        # Bands attach only on a matching scale and cohort year; anything
        # else would hand the engine a mixed-scale signal it must refuse.
        if (bands is not None
                and bands["gpa_type"] == stats["gpa_type"]
                and bands["year"] == stats["year"]):
            band_kwargs = dict(
                bands=tuple(
                    GpaBand(floor=f, ceiling=c, share=s)
                    for f, c, s in bands["rows"]
                ),
                band_population=bands["population"],
                band_reporting_share=bands["reporting_share"],
            )
        gpa = SchoolGpaData(
            gpa_type=stats["gpa_type"],
            gpa_value=stats["gpa_value"],
            gpa_p25=stats["gpa_p25"],
            gpa_p75=stats["gpa_p75"],
            class_rank_top_pct=stats["class_rank_top_pct"],
            class_rank_share=stats["class_rank_share"],
            class_rank_reporting_share=stats["class_rank_reporting_share"],
            **band_kwargs,
        )

    return School(
        name=raw["name"],
        state=raw["state"],
        is_public=raw["is_public"],
        meets_full_need=raw["meets_full_need"],
        gpa=gpa,
        net_prices=raw.get("net_prices", {}),
        auto_admit=_pick_auto_admit(
            raw.get("auto_admit"), applicant_cycle, student_state
        ),
    )
