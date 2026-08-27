# Pontis — architecture

Pontis opens low-income students' eyes to the colleges they can both get into
and afford. The design follows from one insight: **sticker price lies**. For a
high-need student, a meets-full-need private with a $90k sticker is routinely
cheaper than an "affordable-looking" out-of-state public — sometimes cheaper
than staying home (MIT's published net price for the $0–30k income band is
*negative*). The only number Pontis trusts is net price after aid, for a
specific student: their state, their income band, how that school gives aid.

Everything below serves two rules:

1. **Two verdicts, never one score.** Every school gets an admissions category
   and, independently, an affordability verdict. They are never blended,
   because averaging "you'd get in" with "you can't pay for it" produces a
   number that is wrong in both directions.
2. **Honesty over false precision.** Unknown is a real, displayed value. The
   system never invents a number to fill a gap — not a converted GPA, not an
   interpolated percentile, not an in-state price quoted to an out-of-state
   student.

## The two-axis engine (`engine/matching.py`)

Pure functions, no I/O — callers load data and hand in plain values, which is
what makes every tricky case unit-testable without a fixture database.

### Axis 1 — admissions, position-based and same-scale-only

A GPA is meaningless without its scale (3.9 unweighted and 4.2 UC
weighted-capped are not interconvertible without the student's coursework), so
scales travel with values everywhere and a mismatch returns
`unable_to_assess` — a refusal, not a guess. Placement uses the strongest
published signal available, in order:

1. **Published p25–p75 range** — real dispersion: above p75 → `likely`,
   inside → `target`, below p25 → `reach`.
2. **Published band distribution** (CDS section C11) — the anti-undermatching
   unlock. Summing WHOLE published bands strictly below a student's GPA proves
   a hard lower bound: "at least X% of the class had a lower GPA than yours."
   Students inside the student's own band count toward neither bound (their
   relative position is unknown), so there is no interpolation anywhere. A
   bound clearing the same 75% bar the percentile path uses supports the same
   label — this is the only honest route to `likely` at the many schools that
   publish bands but no percentiles.
3. **Published point average** — capped at `target`, because an average
   carries no dispersion and "at or above the average" cannot honestly become
   "likely".
4. **Class-rank signals** where GPA is not the operative lever:
   - a published *descriptive* distribution ("90% of admits were top 10%")
     places a student but caps at `target` — it states P(top-decile | admitted),
     not P(admitted | top-decile), and reading it backwards at a 16%-admit
     school would tell students they are safe when they are not;
   - a published *guarantee* (UT Austin's automatic admission) is the only
     source of `guaranteed`, the one non-prediction label — and it fires only
     when residency, submitted rank, and the exact admission cycle all match,
     because thresholds move between cycles and a stale promise is worse than
     none.

Missing student input yields `context_not_placed` (the published bar is shown;
no placement is invented). Missing school data yields `unable_to_assess_on_gpa`.
A low GPA is never a hard cut — `reach` is the floor.

### Axis 2 — affordability, a hard gate with shown work

The ceiling is computed per student, every term kept separately so the UI can
show the math: family contribution (10% of income above 2× the federal poverty
guideline, saved for 10 years, spread over the years of college — a policy
benchmark, never presented as a bank balance), plus 500 hours of student work
at the state minimum wage (federal fallback recorded *as* a fallback), plus
the federal subsidized loan limit.

Exactly one price lookup happens, at the student's own residency. A missing
out-of-state row is `unknown` — never the in-state figure, which would
understate the out-of-state money trap by tens of thousands of dollars.
Unaffordable and unknown schools are returned in a separate "not on your
list, and why" section with reasons attached, each keeping its admissions
category — labeled, not hidden.

## Schema (`schema/*.sql`, applied in filename order, all re-runnable)

Tables are separated by **grain** and joined on `college_id`; institutions are
identified by IPEDS `UNITID`, never by name (the Scorecard file contains three
Georgia institutions and two "Northeastern"/"UMass"-named California entities
that a name match cannot separate).

| table | grain |
|---|---|
| `colleges` | one row per institution (UNITID-unique) |
| `admission_stats` | school × year |
| `gpa_band_distribution` | school × year × scale × population × band |
| `admission_factors` | school × CDS factor — created, empty until Phase 2 |
| `majors` | school × major |
| `net_price_by_income` | school × income band × residency |
| `class_rank_auto_admit` | school × cycle × resident state |
| `scholarships`, `poverty_guidelines`, `state_minimum_wage`, `federal_loan_limits` | curated reference data |

The honesty rules are **database constraints, not conventions**, and every one
is proven to fire by `tests/test_db_constraints.py` (violation attempted,
rejection asserted, rolled back). Highlights:

- `admission_stats_gpa_honesty`: `not_published` must carry NULL (Boston
  College's CDS literally prints "0.00"; storing it would be the worst lie in
  the system); a declared scale must carry a real figure.
- `gpa_band_no_overlap`: a GiST range-exclusion — two bands of one
  distribution can never overlap, which plain UNIQUE cannot see.
- `net_price_within_band_sane`: since v010 negative net prices are legal
  (grant aid can exceed cost of attendance) inside wide typo-catching bounds.
- Every sourced row carries `source_url`, `source_tier`
  (`primary_verified` only when the document was actually read at the source),
  a ≤15-word verbatim `source_quote` where prose exists, `data_year` for cost
  figures, and `date_ingested`.

`schema/009` documents a bug this discipline caught in its own constraints: a
NULL leak in `majors_gpa_honesty` (SQL three-valued logic let an unlabelled
GPA through). See `HARDENING.md` for the audit that found it.

## Ingestion

- **Curated seeds** (`ingest/seed_reference.py`, `ingest/seed_phase1.py`):
  per-school facts read from primary documents (CDS workbooks, admissions
  pages), with the provenance registry tiering each source and open items
  flagged inline (`TO CLEAR:` notes) rather than silently resolved.
- **Batch pipeline** (`ingest/pipeline.py`): the roster through one governed
  path against the College Scorecard institution file. Per school: identity
  bound by UNITID with an expected-state tripwire, per-band prices only (five
  identical values — the signature of the overall average being substituted —
  fails hard), suppressed bands become honest absences, negative prices are
  ingested as published and flagged for visibility. Per-school PASS/FLAG/FAIL
  report; non-zero exit on any FAIL; idempotent upserts throughout, so
  re-running a release updates rather than duplicates. The pipeline never
  writes what its source cannot support: no admission stats (a federal cost
  file cannot say what a school publishes about GPA) and no aid-flag
  overwrites (curated flags survive every batch run).

## Serving (`api/`, `web/`)

A FastAPI layer exposes the engine over REST; a React single-page app consumes
it. Both render the same contract the engine guarantees: two axes side by
side, `unknown` as a first-class state, the ceiling arithmetic shown, and the
"not on your list, and why" section separating unaffordable (known cost, over
the ceiling) from unknown (no published cost for this student's residency).

## Testing posture

The suite covers the engine's honesty invariants case by case, the seed
constants (wrong-field and identity guards that run with no database), the
pipeline's validation layer, input robustness (impossible inputs rejected at
construction — a negative class rank once fired an admission guarantee), and
the database constraints live. Guards are periodically **mutation-tested**:
the guard is broken in source, the suite is watched to fail, the change is
reverted — a test never watched failing is not yet evidence. Current counts:
`make test` prints them; CI runs the identical clean-room path on Linux on
every push.

## Phasing

Phase 2 (deferred): populate `admission_factors` from each school's CDS C7 and
teach the engine to explain *what a holistic school weighs*; populate
`majors.competitiveness` only where a published statistic exists. Both are
parked in an honest resting state — the engine works correctly with them
empty, and filling them enhances results with no rewrite.
