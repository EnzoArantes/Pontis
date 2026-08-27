# Pontis

## What this is
Pontis (Latin, genitive of *pons*: "of the bridge") is a college-matching tool for
low-income students. Mission: **Pontis opens low-income students' eyes to the
colleges they can both get into and afford, no matter their financial situation.**

Every feature is judged against that mission sentence. If a feature does not serve a
low-income student trying to find schools they can both get into AND afford, it is
scope creep and gets cut.

## The core insight this tool exists to deliver
For a low-income student, expensive private schools that meet full financial need are
often CHEAPER in reality than "affordable-looking" out-of-state publics that give
little aid. Sticker price lies; net price after aid is the only number Pontis trusts.
Affordability is not a property of a school. It is a property of a school FOR A
SPECIFIC STUDENT (their state, their family income band, how that school gives aid).
Affordability is a HARD GATE: a school the student cannot afford is removed, no matter
how likely admission is.

## Non-negotiable design principles
1. **Honesty over false precision.** The tool never invents a number to fill a gap.
   "Not published" is a real, displayed value, not a blank and not a guess. A tool
   that bluffs is worse than useless for students making a life decision.
2. **Track the measurement, not just the value.** GPA figures live on different
   scales across schools (standard unweighted maxes at 4.0; the UC system uses a
   weighted-capped scale that runs above 4.0; Texas leans on class rank). Never
   compare GPAs across scales without normalizing. Every GPA is stored WITH its
   measurement type.
3. **Show its work.** Every recommendation should be explainable in plain language,
   traceable to published data. (This becomes fully live in Phase 2 via
   admission_factors.)
4. **Verify at the source.** Aid thresholds, partner counts, and eligibility rules
   drift year to year. Treat them as data to verify at ingestion, never as facts
   hardcoded from memory. Every ingested row carries source_url and date_ingested.

## v1 scope
- Anchor states: California and Massachusetts.
- 30-40 schools, chosen so every archetype appears: affordable in-state public; the
  out-of-state public "money trap"; the meets-full-need private that looks scary but
  is affordable for high-need students.
- Backend is the star. Minimal or no frontend in v1.
- The three seed/test schools that stress every hard case: Boston College (holistic,
  major mostly does not gate, does NOT publish GPA), UC Berkeley (public, weighted-
  capped GPA scale, some major gating), UT Austin (major-first admissions, rank-
  driven, CS brutally selective and admitted separately). These three are three
  different admissions ARCHITECTURES. If the schema holds all three cleanly, it holds
  almost anything.

## Phasing (decided)
- **Phase 1 (now):** Populate five tables — colleges, admission_stats, majors,
  net_price_by_income, scholarships. CREATE admission_factors in the schema but leave
  it EMPTY. The matching engine must work without it (GPA-fit + affordability gate)
  and be structured so that adding factor data later ENHANCES results with no rewrite.
- **Phase 2 (later):** Populate admission_factors (read the factors section from each
  school's Common Data Set) and teach the engine to use it. This is what turns Pontis
  from an honest calculator into a counselor that shows its work.

### Deferred phases
Two areas are parked in an honest resting state: both are high-value but hard to
source reliably, so they get focused attention later. The engine must work correctly
while they are unpopulated, and populating them must ENHANCE results with no rewrite.

1. **admission_factors — empty.** CDS section C7 factor ratings are per-school and
   inconsistently formatted; parsing them properly is its own job.
2. **majors.competitiveness — `unknown_not_published`.** No seed school publishes a
   major-level admission statistic, so earlier `very_competitive` / `standard` labels
   were withdrawn as inference rather than data. Major names and source_urls are kept.
   The bar for a real value is a published statistic, not an argument.
   *Narrow exception:* `not_a_separate_admit` states the unit of admission rather than
   a competitiveness judgement, so it is allowed where a citation supports it.

## Data sources
- **Common Data Set (CDS):** per-school published PDF/page. Source of GPA distribution
  (where published), acceptance rate, and (Phase 2) the factors-considered ratings.
  Inconsistent format school to school — this parsing is real data engineering.
- **IPEDS:** federal data, source of net price by income band for nearly every school.
- **Scholarships:** curated, small. Anchor on QuestBridge and cousins (Gates, Jack
  Kent Cooke). Do NOT try to scrape the whole scholarship universe — Fastweb/Bold.org
  own that and a sloppy clone is worse than nothing.

## Schema (v1 — six tables)
Different tables have different GRAIN; that is why they are separate and joined on
college_id, not crammed into one wide table.

- **colleges** — one row per school. Stable facts + affordability flags.
  Columns: id, name, state, is_public (bool), meets_full_need (bool),
  css_profile_required (bool), source_url, date_ingested.

- **admission_stats** — one row per school PER YEAR (3-year history). No single `gpa`
  column. Columns: id, college_id (FK), year, acceptance_rate, gpa_value (nullable),
  gpa_type (enum: unweighted | uc_weighted_capped | class_rank_proxy | not_published),
  gpa_p25 (nullable), gpa_p75 (nullable), source_url, date_ingested.
  *(v002)* gpa_p25/gpa_p75 hold selectivity published as a 25th–75th RANGE rather than
  an average (UC publishes Berkeley's admitted-student GPA as 4.16–4.28 and no mean).
  A CHECK requires a declared scale to carry either a point value or a complete range.

- **admission_factors** — CREATED IN PHASE 1, POPULATED IN PHASE 2. The CDS rulebook.
  Columns: id, college_id (FK), factor_name, importance (enum: very_important |
  important | considered | not_considered), source_url, date_ingested.

- **majors** — one row per school per major. Columns: id, college_id (FK),
  major_name, competitiveness (enum: very_competitive | standard |
  not_a_separate_admit | unknown_not_published), major_gpa_value (nullable),
  major_gpa_type (same enum as admission_stats.gpa_type, nullable),
  source_url, date_ingested.

- **net_price_by_income** — one row per school per IPEDS income band PER RESIDENCY.
  Columns: id, college_id (FK), income_band (e.g. '0-30k', '30-48k', '48-75k',
  '75-110k', '110k+'), residency (enum: in_state | out_of_state | not_applicable),
  avg_net_price, source_url, date_ingested.
  *(v002)* residency exists because IPEDS/Scorecard NPT4 for publics counts in-state
  payers ONLY; out-of-state net price is not a reported federal metric at any income
  band. `not_applicable` is a real state for privates, which charge the same
  regardless of home state — distinct from NULL/unknown.

  **Data gap:** no out_of_state rows are populated; no trustworthy source publishes
  them. The engine MUST treat a missing out_of_state row as unknown and must NOT fall
  back to the in-state figure — that would understate the out-of-state money trap by
  tens of thousands of dollars. Net Price Calculators are the likely future source.

- **scholarships** — curated, small. Columns: id, name, provider,
  eligibility_description, income_cap (nullable), state_restriction (nullable),
  first_gen_only (bool), source_url, date_ingested.

## Engine behavior (v1)
Input: a student profile (GPA + its scale, and/or class rank; state; family income and
size). Output: TWO independent verdicts per school — an admissions category and an
affordability verdict — never blended into a single match score.

**Admittability is position-based and same-scale-only.** A student's GPA is compared
against a school's published figure ONLY when both are on the same scale: above p75 →
likely, within p25–p75 → target, below p25 → reach. A lone point average yields target
at best, because an average carries no dispersion. GPAs are NEVER converted between
scales — a scale mismatch returns `unable_to_assess`, since a plausible-looking
conversion is a confident wrong answer. Where a school publishes no GPA but does
publish class rank, the student is placed on rank instead; if no rank is supplied, the
published context is shown with `context_not_placed` rather than a guessed placement.
A low GPA is never a hard cut — reach is the floor. Factor-aware explanations layer on
in Phase 2.

**Affordability is a hard gate** against a per-student annual ceiling: family
contribution from income above 2x the poverty guideline, plus student work, plus the
federal subsidized loan limit. No published net price for the student's residency means
`unknown` — never the in-state figure.

**Design fork — DECIDED: label, do not hide.** Unaffordable and unknown-cost schools are
returned in a separate "not on your list, and why" section, each keeping its reason and
its admissions category, rather than being dropped.

## Tech stack
- **Python** — ingestion (parse CDS documents, pull IPEDS files) + matching engine
  (pure, testable) + FastAPI service exposing endpoints.
- **PostgreSQL** — the relational store. Postgres 18 installed locally on macOS.
- **psycopg** — Postgres connector for Python. Use a virtual environment for isolation.
- **Systems layer (honest, defensible — NOT buzzword):** concurrent fan-out on
  ingestion, scheduled jobs, idempotent upserts so re-running a year does not
  duplicate rows, caching of expensive computations. Do not claim distributed-systems
  features that are not really built.

## Working conventions
- This is a resume project. The person building it is a sophomore CS student, strong
  in iOS/Swift, deliberately building backend + SQL depth. Explain backend/SQL/systems
  choices clearly enough to defend in an interview.
- Prefer clarity and correctness over cleverness. Comment the non-obvious "why."
- Keep secrets/config out of the repo.
