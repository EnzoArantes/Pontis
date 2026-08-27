"""Phase 1 seed: three schools, five live tables, admission_factors left empty.

Every value below was read from a primary source during ingestion and carries the
URL it came from. Where a school does not publish a figure, the row stores the
honest absence (NULL + a gpa_type that says why) rather than a guess.

Re-running this script is safe: every write is an idempotent UPSERT keyed on the
natural key declared in schema/001_initial_schema.sql, so re-ingesting a year
updates that year's row instead of duplicating it.

    ./.venv/bin/python ingest/seed_phase1.py
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import connect  # noqa: E402

# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------
BC_CDS_2024 = (
    "https://www.bc.edu/content/dam/bc1/offices/irp/ir/cds/"
    "Boston_College_CDS_2024-2025_Final.pdf"
)
BC_AID = (
    "https://www.bc.edu/bc-web/offices/student-services/financial-aid/"
    "undergraduate/how-aid-works.html"
)
# BC's own admission site, verbatim: "When you apply to Boston College, you submit
# your application for admission to one of the four undergraduate divisions at the
# University." The same page lists Computer Science and Economics under the
# Morrissey College of Arts & Sciences division.
BC_DIVISIONS = "https://www.bc.edu/bc-web/admission/majors-minors.html"
CAL_CDS_2024 = (
    "https://opa.berkeley.edu/campus-data/common-data-set"
    "#cds-2024-2025"
)
CAL_CDS_2023 = (
    "https://opa.berkeley.edu/campus-data/common-data-set"
    "#cds-2023-2024"
)
CAL_CDS_2022 = (
    "https://opa.berkeley.edu/campus-data/common-data-set"
    "#cds-2022-2023"
)
CAL_AID = "https://financialaid.berkeley.edu/apply-now/apply-for-aid/"
CAL_MAJORS = (
    "https://admission.universityofcalifornia.edu/campuses-majors/berkeley/"
    "first-year-admit-data.html"
)
# UC's systemwide admit-data page. Note this reports ADMITTED students, whereas
# the CDS rows below report ENROLLED students -- different populations, which is
# part of why the two GPA figures are not comparable even before the scale
# difference. UC defines its published "HS GPA" as the weighted, capped GPA.
CAL_UC_ADMIT = (
    "https://admission.universityofcalifornia.edu/campuses-majors/berkeley/"
    "first-year-admit-data.html"
)
UT_REVIEW = "https://admissions.utexas.edu/apply/review-decision-process/"
UT_AID = (
    "https://admissions.utexas.edu/cost-aid/financial-aid/"
    "texas-advance-commitment/"
)

# College Scorecard "Most Recent Institution-Level Data", released 2026-06-10.
# This is the IPEDS-derived federal file; NPT41..NPT45 are the five income bands.
SCORECARD = "https://collegescorecard.ed.gov/data/"

QUESTBRIDGE = "https://www.questbridge.org/apply-to-college/programs/national-college-match"
GATES = "https://www.thegatesscholarship.org/scholarship/"
JKCF = "https://www.jkcf.org/our-scholarships/college-scholarship-program/"

# --------------------------------------------------------------------------
# UMass Amherst (school #4)
# --------------------------------------------------------------------------
# umass.edu returns HTTP 403 to every automated request (WebFetch and curl with a
# browser user-agent, on the CDS download, the IR index, and the admissions
# statistics page). So UMass's own publications were NOT readable during this
# ingestion. Cost still ships PRIMARY because College Scorecard is a federal
# source independent of the university; everything that only UMass publishes is
# either tiered secondary or, where the schema cannot state it honestly, withheld.
UMASS_SCORECARD = "https://collegescorecard.ed.gov/data/"
UMASS_AID = "https://www.umass.edu/financialaid/"
UMASS_CDS = "https://www.umass.edu/uair/data/common-data-set"

# --------------------------------------------------------------------------
# Georgia State University, Atlanta (school #5)
# --------------------------------------------------------------------------
# Bound to IPEDS UNITID 139940. Three rows in the Scorecard file are confusable
# by name and a substring match hits at least two of them:
#     139940  Georgia State University                    Atlanta, GA
#     139861  Georgia College & State University          Milledgeville, GA
#     244437  Georgia State University-Perimeter College  Atlanta, GA
# Perimeter shares both the name prefix AND the city, and its numbers differ
# materially (0-30k band $10,380 vs $13,787; admit rate 91% vs 55%).
GSU_CDS_2025 = (
    "https://www.dropbox.com/scl/fi/wzzswsxb26ml907zmuvip/"
    "CDS-2025-26-for-Georgia-State-University.xlsx"
)
GSU_ADMISSIONS = "https://admissions.gsu.edu/bachelors-degree/apply/high-school/"
GSU_AID = "https://financialaid.gsu.edu/"

# --------------------------------------------------------------------------
# Provenance registry (spec S3)
# --------------------------------------------------------------------------
# source_url -> (tier, short verbatim quote or None)
#
# Kept as one registry rather than two extra fields on every data tuple: the tier
# is a property of the SOURCE, not of each row drawn from it, so recording it per
# row would invite the same source being tiered two different ways.
#
# primary_verified means the document was actually retrieved and read during
# ingestion. Anything reached only through search summaries is secondary, however
# reputable it looked -- that distinction is the point of the tier.
SOURCE_TIERS = {
    # --- read directly ----------------------------------------------------
    BC_CDS_2024: ("primary_verified",
                  "Percent of first-time, first-year students who submitted high school GPA: 0.0%"),
    BC_DIVISIONS: ("primary_verified",
                   "you submit your application for admission to one of the four undergraduate divisions"),
    CAL_CDS_2024: ("primary_verified", "UC Berkeley reports unweighted GPA"),
    CAL_CDS_2023: ("primary_verified", "UC Berkeley reports unweighted GPA, Updated May 2024"),
    CAL_CDS_2022: ("primary_verified", "UC Berkeley reports unweighted GPA"),
    CAL_UC_ADMIT: ("primary_verified", "High School GPA (middle 25%-75%): 4.16-4.28"),
    UT_REVIEW: ("primary_verified", "Summer/Fall 2026 and Spring 2027 applicants: Top 5%"),
    SCORECARD: ("primary_verified", "NPT4_PUB, 2023-24 award year cohort"),
    UMASS_SCORECARD: ("primary_verified", "NPT4_PUB, 2023-24 award year cohort"),
    GSU_CDS_2025: ("primary_verified",
                   "Average high school GPA of all degree-seeking, first-time, first-year students: 3.6"),
    GSU_ADMISSIONS: ("primary_verified",
                     "We calculate your high school grade point average based on your core academic classes only"),

    # --- reached only via search summaries; primary page failed or blocked --
    # TO CLEAR: open each by hand, confirm, and promote to primary_verified.
    BC_AID: ("secondary_corroborated", None),        # bc.edu fetch socket-hung
    CAL_AID: ("secondary_corroborated", None),       # reached via search only
    UT_AID: ("secondary_corroborated", None),        # reached via search only
    UMASS_AID: ("secondary_corroborated", None),     # umass.edu 403s all requests
    UMASS_CDS: ("secondary_corroborated", None),     # umass.edu 403s all requests
    GSU_AID: ("secondary_corroborated", None),       # not read at the source
    QUESTBRIDGE: ("secondary_corroborated", None),
    GATES: ("secondary_corroborated", None),
    JKCF: ("secondary_corroborated", None),
}

# CAL_MAJORS is the same URL as CAL_UC_ADMIT; keep the tiering consistent.
SOURCE_TIERS.setdefault(CAL_MAJORS, SOURCE_TIERS[CAL_UC_ADMIT])


def provenance(url):
    """(source_tier, source_quote) for a source URL.

    Unregistered sources default to the WEAKER tier on purpose: a source nobody
    tiered is a source nobody checked.
    """
    return SOURCE_TIERS.get(url, ("secondary_corroborated", None))


# College Scorecard "Most Recent Institution-Level Data" (rel. 2026-06-10).
# The glossary states NPT4_PUB / NPT4_PRIV are the "2023-24 award year cohort",
# so every net-price row from this file carries 2023 as its award-year start.
SCORECARD_DATA_YEAR = 2023

# --------------------------------------------------------------------------
# colleges
# --------------------------------------------------------------------------
# ipeds_unitid leads each row deliberately: it, not the name, is the identity.
COLLEGES = [
    # (ipeds_unitid, name, state, is_public, meets_full_need,
    #  css_profile_required, source_url)
    #
    # BC: "committed to meeting your full demonstrated institutional financial
    # need"; institutional aid is awarded off the CSS Profile (code 3083), not
    # the FAFSA SAI. This is the archetype the whole tool exists to surface.
    (164924, "Boston College", "MA", False, True, True, BC_AID),
    #
    # Berkeley: FAFSA/CADAA only, no CSS Profile. meets_full_need is False on
    # purpose -- the Blue and Gold Opportunity Plan covers systemwide TUITION for
    # California residents under ~$80k, which is not the same promise as meeting
    # full cost-of-attendance need, and it does nothing for non-residents.
    (110635, "University of California-Berkeley", "CA", True, False, False, CAL_AID),
    #
    # UT Austin: FAFSA/TASFA only. Texas Advance Commitment covers tuition for
    # Texas residents up to $100k AGI (partial to $125k) -- again tuition, not
    # full need, and residents only.
    (228778, "The University of Texas at Austin", "TX", True, False, False, UT_AID),
    #
    # UMass Amherst (school #4): the affordable IN-STATE public archetype for an
    # MA student, which the seed previously lacked.
    # meets_full_need=False and css_profile_required=False are SECONDARY: umass.edu
    # 403s every request, so neither was read at the source. Both are set in the
    # conservative direction -- claiming a school meets full need when it does not
    # would overpromise on the hard affordability gate, so False is the safe error.
    # TO CLEAR: open umass.edu/financialaid by hand and confirm both.
    (166629, "University of Massachusetts-Amherst", "MA", True, False, False, UMASS_AID),
    #
    # Georgia State University, ATLANTA -- UNITID 139940. NOT 139861 (Georgia
    # College & State University, Milledgeville) and NOT 244437 (Georgia State
    # University-Perimeter College, which shares both the name prefix and the
    # city). Aid flags are SECONDARY: financialaid.gsu.edu was not read at the
    # source, and both are set conservatively, since claiming a public meets full
    # need would overpromise on the hard affordability gate.
    # TO CLEAR: open financialaid.gsu.edu by hand and confirm both.
    (139940, "Georgia State University", "GA", True, False, False, GSU_AID),
]

# --------------------------------------------------------------------------
# admission_stats
# --------------------------------------------------------------------------
# Acceptance rates are computed from the CDS C1 counts rather than copied from a
# marketing page, so the arithmetic is reproducible from the cited document.
ADMISSION_STATS = [
    # (college_name, year, acceptance_rate, gpa_value, gpa_type,
    #  gpa_p25, gpa_p75,
    #  class_rank_top_pct, class_rank_share, class_rank_reporting_share,
    #  source_url)
    #
    # Boston College, Fall 2024: 5,632 admitted / 34,779 applied.
    # CDS field C12 (average HS GPA) prints "0.00" and C11 reports that 0.0% of
    # enrolled students submitted a GPA -- i.e. BC does not collect or publish it.
    # Storing that literal 0.00 would be the worst lie this tool could tell.
    #
    # But the SAME CDS section reports a class-rank distribution: 90.0% of
    # first-year students were in the top tenth of their high school class. That
    # is BC's only published academic-position signal, and it is real.
    # The 26.6% is the share of students BC collected a rank for at all, so the
    # 90% describes that subset rather than the whole class -- carried alongside
    # so the engine can say so out loud.
    ("Boston College", 2024, "0.1619", None, "not_published", None, None,
     "10.00", "0.9000", "0.2660", BC_CDS_2024),
    #
    # UC Berkeley, from its own CDS. The workbook carries an explicit annotation:
    # "*UC Berkeley reports unweighted GPA" -- so these are unweighted, NOT the
    # UC weighted-capped scale, despite Berkeley being a UC. These describe
    # ENROLLED students. No percentile range is published alongside them (the CDS
    # gives a banded distribution, not p25/p75), so those stay NULL.
    ("University of California-Berkeley", 2022, "0.1140", "3.904", "unweighted", None, None,
     None, None, None, CAL_CDS_2022),
    ("University of California-Berkeley", 2023, "0.1173", "3.890", "unweighted", None, None,
     None, None, None, CAL_CDS_2023),
    ("University of California-Berkeley", 2024, "0.1104", "3.900", "unweighted", None, None,
     None, None, None, CAL_CDS_2024),
    #
    # UC Berkeley, Fall 2026, from UC's systemwide admit-data page: 13,967
    # admitted / 133,154 applied. This is the weighted-capped case -- UC defines
    # its published HS GPA as "weighted, capped", and the figure exceeds 4.0,
    # which is only possible on that scale.
    #
    # gpa_value stays NULL on purpose: UC publishes ONLY the 25th-75th range
    # (4.16-4.28) and no average. Recording 4.22 as if it were a published mean
    # would be exactly the invented number this schema exists to prevent.
    #
    # Kept as a separate row rather than merged into a CDS year because it counts
    # ADMITTED students while the CDS rows count ENROLLED students.
    ("University of California-Berkeley", 2026, "0.1049", None, "uc_weighted_capped",
     "4.160", "4.280", None, None, None, CAL_UC_ADMIT),
    #
    # UT Austin, Fall 2024. Rate from the federal College Scorecard (0.2664),
    # which reconciles exactly with UT's reported 19,417 admits / 72,885
    # applicants. GPA is NULL with gpa_type class_rank_proxy: UT's architecture
    # is rank-driven (automatic admission by class rank), and no average GPA was
    # verified from a primary UT document.
    ("The University of Texas at Austin", 2024, "0.2664", None, "class_rank_proxy", None, None,
     None, None, None, SCORECARD),
    #
    # Georgia State University, Fall 2025, from GSU's own CDS 2025-26 (downloaded
    # and parsed): 24,217 admitted / 42,323 applied = 57.22%. Average HS GPA 3.6,
    # submitted by 99.91% of students.
    #
    # gpa_type INFERRED, not stated. GSU's CDS carries no weighting annotation --
    # unlike Berkeley, which explicitly prints "UC Berkeley reports unweighted
    # GPA". The inference rests on two primary sources: GSU's admissions page says
    # the GPA is recalculated "based on your core academic classes only" with no
    # mention of extra points for honors or advanced courses, and the CDS's own
    # definitions sheet says weighting is precisely "additional points for grades
    # in advanced or honors courses". Equal weight per course = unweighted.
    # Flagged rather than silent: if GSU is in fact reporting something weighted,
    # this label is the thing to change.
    #
    # No class-rank fields: GSU collected class rank for 0% of students.
    # No p25/p75: GSU publishes a point average and a BANDED distribution, and
    # interpolating percentiles out of bands would invent precision.
    ("Georgia State University", 2025, "0.5722", "3.600", "unweighted", None, None,
     None, None, None, GSU_CDS_2025),
    #
    # UMass Amherst, Fall 2025, CDS 2025-26 field C12: average high school GPA
    # 4.05, submitted by 99.3% of students.
    #
    # gpa_type 'weighted' (schema v007) is not an inference here: 4.05 EXCEEDS 4.0,
    # which is impossible on an unweighted scale, so the scale is settled by the
    # value itself. This is the case that could not be recorded at all before --
    # the previous vocabulary forced either 'unweighted' (wrong scale) or
    # 'not_published' (wrong fact).
    #
    # SECONDARY: umass.edu returns 403 to every automated request, so the figure
    # comes from a search summary of UMass's own CDS rather than from the document.
    # TO CLEAR: open umass.edu/uair/data/common-data-set by hand, confirm C12, and
    # promote to primary_verified.
    #
    # acceptance_rate deliberately NULL: the only rate on hand is College
    # Scorecard's 0.5973, which describes the Fall 2024 cohort, not this row's
    # Fall 2025 one. Borrowing it across cohorts to fill the column would be the
    # same class of error as borrowing an in-state price for an out-of-state student.
    ("University of Massachusetts-Amherst", 2025, None, "4.050", "weighted", None, None,
     None, None, None, UMASS_CDS),
]

# --------------------------------------------------------------------------
# gpa_band_distribution (schema v008)
# --------------------------------------------------------------------------
# Georgia State's CDS 2025-26 section C11, read from the downloaded workbook
# (sheet CDS-C, rows 232-241): the full banded GPA distribution of enrolled
# first-time, first-year students on the 4.0 scale, exactly as filed. Shares
# total 1.00002 -- the workbook's own rounding, preserved rather than smoothed.
#
# OPEN ITEM -- which sub-column GSU filled. The CDS form splits C11 into three
# columns: "students who submitted scores", "students who did not", and "all
# enrolled students". GSU put its distribution in the FIRST column and left the
# other two zero. Read literally, that would mean the distribution covers only
# test-score submitters (~40% of the class, per C9). Two facts say otherwise:
#   1. The implied mean of these bands (midpoint-weighted) is 3.602, which
#      reproduces C12's published all-student average of 3.6 exactly. A
#      test-submitter-only subset matching the all-student mean to the decimal
#      would be a coincidence.
#   2. The form's own instruction is to report ALL students in a single column
#      when a split is unavailable; filing that single distribution in the
#      first column rather than the third is a common filing quirk.
# Seeded as population='enrolled' with reporting_share 0.9991 (C12: percent who
# submitted GPA) on that basis, with the ambiguity recorded here rather than
# silently resolved. TO CLEAR: confirm with GSU IR or the next CDS edition.
GSU_GPA_BANDS = [
    # (band_floor, band_ceiling, share) -- "GPA of 4.0" is a point band.
    ("4.00", "4.00", "0.1804"),
    ("3.75", "3.99", "0.2213"),
    ("3.50", "3.74", "0.2529"),
    ("3.25", "3.49", "0.1704"),
    ("3.00", "3.24", "0.1493"),
    ("2.50", "2.99", "0.0207"),
    ("2.00", "2.49", "0.0050"),
    ("1.00", "1.99", "0.00002"),
    ("0.00", "0.99", "0"),      # published zero -- a real value, not a gap
]

GPA_BAND_DISTRIBUTIONS = [
    # (college_name, year, gpa_type, population, reporting_share, bands, source_url)
    ("Georgia State University", 2025, "unweighted", "enrolled", "0.9991",
     GSU_GPA_BANDS, GSU_CDS_2025),
]

# --------------------------------------------------------------------------
# majors
# --------------------------------------------------------------------------
# DEFERRED PHASE -- parked deliberately, same posture as admission_factors.
#
# Every competitiveness value here is unknown_not_published, because none of
# these three schools publishes a major-level admission statistic. The earlier
# very_competitive / standard / not_a_separate_admit labels were inferences from
# program structure and reasoning, not figures anyone published, so they have
# been withdrawn rather than left sitting in the database looking like data.
#
# The major NAMES and their source_urls are kept: which majors exist, and that
# UT and Berkeley admit at the major level at all, is genuinely documented. What
# is not documented is how hard any individual major is to get into.
#
# unknown_not_published is doing exactly the job it was defined for: the school
# gates by major but will not say how hard. Restoring a real value later requires
# a published statistic, not an argument.
#
# NARROW EXCEPTION -- sourced architectural facts.
# not_a_separate_admit is not a competitiveness judgement; it is a statement about
# the unit of admission, and BC documents it directly: "When you apply to Boston
# College, you submit your application for admission to one of the four
# undergraduate divisions at the University." Computer Science and Economics are
# both listed on that page under the Morrissey College of Arts & Sciences
# division, so neither is a per-major competitive admit -- there is no major-level
# gate to be more or less selective. That is citable, and different in kind from
# an invented admit rate. The exception covers ONLY architectural facts carrying a
# real citation; it does not reopen competitiveness.
MAJORS = [
    # (college_name, major_name, competitiveness, gpa_value, gpa_type, source_url)
    ("Boston College", "Computer Science", "not_a_separate_admit", None, None, BC_DIVISIONS),
    ("Boston College", "Economics", "not_a_separate_admit", None, None, BC_DIVISIONS),

    ("University of California-Berkeley", "Electrical Engineering and Computer Sciences",
     "unknown_not_published", None, None, CAL_MAJORS),
    ("University of California-Berkeley", "Computer Science",
     "unknown_not_published", None, None, CAL_MAJORS),
    ("University of California-Berkeley", "Undeclared (Letters and Science)",
     "unknown_not_published", None, None, CAL_MAJORS),

    ("The University of Texas at Austin", "Computer Science",
     "unknown_not_published", None, None, UT_REVIEW),
    ("The University of Texas at Austin", "Business Administration",
     "unknown_not_published", None, None, UT_REVIEW),
    ("The University of Texas at Austin", "Liberal Arts (undeclared)",
     "unknown_not_published", None, None, UT_REVIEW),
]

# --------------------------------------------------------------------------
# net_price_by_income
# --------------------------------------------------------------------------
# Now carries residency (schema v002).
#
# NO OUT-OF-STATE ROWS EXIST BELOW, and that is a deliberate, load-bearing
# absence. IPEDS calculates NPT4 for public institutions using only students who
# paid the in-state rate; out-of-state net price is not a reported federal
# metric at any income band. Nothing published fills that gap: the aggregator
# sites that appear to have it disagree with each other (two of them give
# Berkeley's $0-30k band as $5,895 and $8,392), so none of them is usable.
#
# The consequence matters more than the gap: for a Massachusetts student,
# Pontis currently has NO net price for Berkeley or UT Austin. The engine must
# read a missing out_of_state row as UNKNOWN and refuse to fall back to the
# in-state number -- doing so would quietly understate the money trap by tens of
# thousands of dollars, which is the precise failure this project exists to stop.
NET_PRICE = [
    # (college_name, residency, income_band, avg_net_price)
    #
    # Boston College: private, so one price regardless of home state.
    # NPT41..NPT45_PRIV.
    ("Boston College", "not_applicable", "0-30k", 4284),
    ("Boston College", "not_applicable", "30-48k", 7304),
    ("Boston College", "not_applicable", "48-75k", 13112),
    ("Boston College", "not_applicable", "75-110k", 19999),
    ("Boston College", "not_applicable", "110k+", 60308),
    #
    # UC Berkeley -- NPT41..NPT45_PUB, in-state only.
    ("University of California-Berkeley", "in_state", "0-30k", 5311),
    ("University of California-Berkeley", "in_state", "30-48k", 6501),
    ("University of California-Berkeley", "in_state", "48-75k", 9693),
    ("University of California-Berkeley", "in_state", "75-110k", 15074),
    ("University of California-Berkeley", "in_state", "110k+", 34529),
    #
    # UT Austin -- NPT41..NPT45_PUB, in-state only.
    ("The University of Texas at Austin", "in_state", "0-30k", 12553),
    ("The University of Texas at Austin", "in_state", "30-48k", 14297),
    ("The University of Texas at Austin", "in_state", "48-75k", 17207),
    ("The University of Texas at Austin", "in_state", "75-110k", 24406),
    ("The University of Texas at Austin", "in_state", "110k+", 30082),
    #
    # UMass Amherst -- NPT41..NPT45_PUB, in-state only (school #4).
    # Overall average NPT4_PUB is 22383 and is deliberately NOT here; it lives in
    # the net-price test constants so the guards can prove it was not used.
    ("University of Massachusetts-Amherst", "in_state", "0-30k", 10164),
    ("University of Massachusetts-Amherst", "in_state", "30-48k", 10456),
    ("University of Massachusetts-Amherst", "in_state", "48-75k", 12932),
    ("University of Massachusetts-Amherst", "in_state", "75-110k", 18964),
    ("University of Massachusetts-Amherst", "in_state", "110k+", 30793),
    #
    # Georgia State University (UNITID 139940) -- NPT41..NPT45_PUB, in-state.
    # Overall average NPT4_PUB is 15931 and is deliberately NOT here; it lives in
    # the net-price test constants so the guards can prove it was not used.
    ("Georgia State University", "in_state", "0-30k", 13787),
    ("Georgia State University", "in_state", "30-48k", 14430),
    ("Georgia State University", "in_state", "48-75k", 16656),
    ("Georgia State University", "in_state", "75-110k", 19390),
    ("Georgia State University", "in_state", "110k+", 20305),
]

# --------------------------------------------------------------------------
# class_rank_auto_admit (schema v005)
# --------------------------------------------------------------------------
# From UT Austin's own review-and-decision page, which lists the threshold per
# cycle explicitly:
#     "Summer/Fall 2026 and Spring 2027 applicants: Top 5%"
#     "Summer/Fall 2027 and Spring 2028 applicants: Top 5%"
# The same page states admission decisions are made "in relation to the admission
# factors for the University and for the college, school and majors to which you
# apply" -- automatic admission is to the UNIVERSITY, not to a major.
#
# Fall 2025 (top 6%) is deliberately NOT seeded: that figure came only from news
# coverage, not from a UT page I could read. An applicant in an unseeded cycle
# gets "no threshold on file", which is the honest answer.
AUTO_ADMIT = [
    # (college_name, effective_cycle, resident_state, threshold_top_pct,
    #  guarantees_university, guarantees_major, source_url)
    ("The University of Texas at Austin", "fall-2026", "TX", "5.00", True, False, UT_REVIEW),
    ("The University of Texas at Austin", "fall-2027", "TX", "5.00", True, False, UT_REVIEW),
]

# --------------------------------------------------------------------------
# scholarships
# --------------------------------------------------------------------------
SCHOLARSHIPS = [
    # (name, provider, eligibility_description, income_cap, state_restriction,
    #  first_gen_only, source_url)
    #
    # income_cap is NULL here on purpose. QuestBridge states it has no absolute
    # cut-off for income; the widely-quoted "$65,000 for a family of four" is
    # guidance about who tends to be selected, not a rule. Encoding 65000 as a
    # cap would silently filter out students who are in fact eligible.
    (
        "QuestBridge National College Match",
        "QuestBridge",
        "No published income cut-off. QuestBridge states there are no absolute "
        "criteria or cut-offs for income, GPA or test scores; its guidance is "
        "that Finalists typically come from households earning less than "
        "$65,000 a year for a family of four with minimal assets. Applicants "
        "are U.S. high school seniors; citizenship status is not restricted.",
        None,
        None,
        False,
        QUESTBRIDGE,
    ),
    #
    # Also NULL: eligibility is defined by federal Pell eligibility, which is a
    # formula (SAI/cost of attendance), not a dollar threshold.
    (
        "The Gates Scholarship",
        "Bill & Melinda Gates Foundation",
        "Pell-eligible (income limit is set by federal Pell eligibility rather "
        "than a fixed dollar cap); minimum 3.3 cumulative GPA; U.S. citizen, "
        "national or permanent resident; must be a high school senior from one "
        "of the ethnic groups the program serves; full-time enrollment at an "
        "accredited not-for-profit four-year institution.",
        None,
        None,
        False,
        GATES,
    ),
    #
    # A genuine hard cap, so this one gets a number.
    (
        "Jack Kent Cooke Foundation College Scholarship Program",
        "Jack Kent Cooke Foundation",
        "Family adjusted gross income up to $95,000 is considered, followed by "
        "a full financial review of student and parent income and assets; "
        "cumulative unweighted GPA of 3.75 or better; current U.S. high school "
        "senior planning full-time enrollment at an accredited four-year "
        "institution.",
        95000,
        None,
        False,
        JKCF,
    ),
]


def main() -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            # ---- colleges -------------------------------------------------
            ids: dict[str, int] = {}
            for unitid, name, state, pub, full_need, css, src in COLLEGES:
                cur.execute(
                    """
                    INSERT INTO colleges
                        (ipeds_unitid, name, state, is_public, meets_full_need,
                         css_profile_required, source_url, source_tier, source_quote)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (ipeds_unitid) DO UPDATE SET
                        name                 = EXCLUDED.name,
                        state                = EXCLUDED.state,
                        is_public            = EXCLUDED.is_public,
                        meets_full_need      = EXCLUDED.meets_full_need,
                        css_profile_required = EXCLUDED.css_profile_required,
                        source_url           = EXCLUDED.source_url,
                        source_tier          = EXCLUDED.source_tier,
                        source_quote         = EXCLUDED.source_quote,
                        date_ingested        = CURRENT_DATE
                    RETURNING id
                    """,
                    (unitid, name, state, pub, full_need, css, src, *provenance(src)),
                )
                ids[name] = cur.fetchone()[0]

            # ---- admission_stats -----------------------------------------
            for (name, year, rate, gpa, gpa_type, p25, p75,
                 rank_top, rank_share, rank_cover, src) in ADMISSION_STATS:
                cur.execute(
                    """
                    INSERT INTO admission_stats
                        (college_id, year, acceptance_rate, gpa_value,
                         gpa_type, gpa_p25, gpa_p75,
                         class_rank_top_pct, class_rank_share,
                         class_rank_reporting_share, source_url,
                         source_tier, source_quote)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (college_id, year) DO UPDATE SET
                        acceptance_rate = EXCLUDED.acceptance_rate,
                        gpa_value       = EXCLUDED.gpa_value,
                        gpa_type        = EXCLUDED.gpa_type,
                        gpa_p25         = EXCLUDED.gpa_p25,
                        gpa_p75         = EXCLUDED.gpa_p75,
                        class_rank_top_pct         = EXCLUDED.class_rank_top_pct,
                        class_rank_share           = EXCLUDED.class_rank_share,
                        class_rank_reporting_share = EXCLUDED.class_rank_reporting_share,
                        source_url      = EXCLUDED.source_url,
                        source_tier     = EXCLUDED.source_tier,
                        source_quote    = EXCLUDED.source_quote,
                        date_ingested   = CURRENT_DATE
                    """,
                    (ids[name], year, rate, gpa, gpa_type, p25, p75,
                     rank_top, rank_share, rank_cover, src, *provenance(src)),
                )

            # ---- gpa_band_distribution ------------------------------------
            for (name, year, gpa_type, population, reporting,
                 bands, src) in GPA_BAND_DISTRIBUTIONS:
                # Refuse to seed a distribution that does not describe a whole
                # class: a partial set of bands proves nothing, and seeding it
                # would let the engine mistake it for a complete one.
                total = sum(Decimal(share) for _, _, share in bands)
                if abs(total - 1) > Decimal("0.02"):
                    raise ValueError(
                        f"{name} {year}: band shares sum to {total}, not ~1 -- "
                        f"incomplete distribution refused"
                    )
                for floor, ceiling, share in bands:
                    cur.execute(
                        """
                        INSERT INTO gpa_band_distribution
                            (college_id, year, gpa_type, population,
                             band_floor, band_ceiling, share, reporting_share,
                             source_url, source_tier, source_quote)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (college_id, year, gpa_type, population, band_floor)
                        DO UPDATE SET
                            band_ceiling    = EXCLUDED.band_ceiling,
                            share           = EXCLUDED.share,
                            reporting_share = EXCLUDED.reporting_share,
                            source_url      = EXCLUDED.source_url,
                            source_tier     = EXCLUDED.source_tier,
                            source_quote    = EXCLUDED.source_quote,
                            date_ingested   = CURRENT_DATE
                        """,
                        (ids[name], year, gpa_type, population, floor, ceiling,
                         share, reporting, src, *provenance(src)),
                    )

            # ---- majors ---------------------------------------------------
            for name, major, comp, mgpa, mtype, src in MAJORS:
                cur.execute(
                    """
                    INSERT INTO majors
                        (college_id, major_name, competitiveness,
                         major_gpa_value, major_gpa_type, source_url,
                         source_tier, source_quote)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (college_id, major_name) DO UPDATE SET
                        competitiveness = EXCLUDED.competitiveness,
                        major_gpa_value = EXCLUDED.major_gpa_value,
                        major_gpa_type  = EXCLUDED.major_gpa_type,
                        source_url      = EXCLUDED.source_url,
                        source_tier     = EXCLUDED.source_tier,
                        source_quote    = EXCLUDED.source_quote,
                        date_ingested   = CURRENT_DATE
                    """,
                    (ids[name], major, comp, mgpa, mtype, src, *provenance(src)),
                )

            # ---- net_price_by_income --------------------------------------
            for name, resid, band, price in NET_PRICE:
                cur.execute(
                    """
                    INSERT INTO net_price_by_income
                        (college_id, income_band, residency, net_price_within_band,
                         source_url, source_tier, source_quote, data_year)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (college_id, income_band, residency) DO UPDATE SET
                        net_price_within_band = EXCLUDED.net_price_within_band,
                        source_url    = EXCLUDED.source_url,
                        source_tier   = EXCLUDED.source_tier,
                        source_quote  = EXCLUDED.source_quote,
                        data_year     = EXCLUDED.data_year,
                        date_ingested = CURRENT_DATE
                    """,
                    (ids[name], band, resid, price, SCORECARD,
                     *provenance(SCORECARD), SCORECARD_DATA_YEAR),
                )

            # ---- class_rank_auto_admit ------------------------------------
            for name, cycle, resident, threshold, uni, major, src in AUTO_ADMIT:
                cur.execute(
                    """
                    INSERT INTO class_rank_auto_admit
                        (college_id, effective_cycle, resident_state,
                         threshold_top_pct, guarantees_university,
                         guarantees_major, source_url, source_tier, source_quote)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (college_id, effective_cycle, resident_state)
                    DO UPDATE SET
                        threshold_top_pct     = EXCLUDED.threshold_top_pct,
                        guarantees_university = EXCLUDED.guarantees_university,
                        guarantees_major      = EXCLUDED.guarantees_major,
                        source_url            = EXCLUDED.source_url,
                        source_tier           = EXCLUDED.source_tier,
                        source_quote          = EXCLUDED.source_quote,
                        date_ingested         = CURRENT_DATE
                    """,
                    (ids[name], cycle, resident, threshold, uni, major, src,
                     *provenance(src)),
                )

            # ---- scholarships ---------------------------------------------
            for row in SCHOLARSHIPS:
                cur.execute(
                    """
                    INSERT INTO scholarships
                        (name, provider, eligibility_description, income_cap,
                         state_restriction, first_gen_only, source_url,
                         source_tier, source_quote)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (name) DO UPDATE SET
                        provider                = EXCLUDED.provider,
                        eligibility_description = EXCLUDED.eligibility_description,
                        income_cap              = EXCLUDED.income_cap,
                        state_restriction       = EXCLUDED.state_restriction,
                        first_gen_only          = EXCLUDED.first_gen_only,
                        source_url              = EXCLUDED.source_url,
                        source_tier             = EXCLUDED.source_tier,
                        source_quote            = EXCLUDED.source_quote,
                        date_ingested           = CURRENT_DATE
                    """,
                    (*row, *provenance(row[-1])),
                )

        conn.commit()

    print("Seed complete.")


if __name__ == "__main__":
    main()
