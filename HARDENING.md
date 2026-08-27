# Hardening notes

The Phase A audit standard: every honesty rule that claims to be structural must
be **proven** to fire (violation injected, rejection observed), the engine must
**reject or degrade honestly** on bad input (never crash, never fabricate), SQL
must be parameterized, secrets out of the repo, and a stranger must be able to
rebuild from zero. "Foolproof is a direction pursued to a sensible stop" — this
file names the stop: what was proven, what was found and fixed, and which
residual risks are accepted on purpose.

## Proven guards

**Database constraints** — every honesty CHECK, uniqueness, and exclusion rule
is exercised by `tests/test_db_constraints.py`, which attempts the exact
violation each rule exists to forbid and asserts the rejection (rolled back, so
the database is left untouched). Covered: the GPA-honesty constraints on
`admission_stats` and `majors`, the percentile-pair rules, class-rank
pair/range/coverage rules, band-distribution scale/inversion/share/overlap
rules (including the GiST range-exclusion that UNIQUE cannot replicate),
institution-identity uniqueness, income-band and net-price checks, auto-admit
threshold bounds, and the 15-word source-quote limit. These tests skip (never
silently pass) when no database is reachable.

**Engine guards** — mutation-tested: each guard was broken in source, the suite
was observed to fail, and the change reverted. Mutations run and caught:

| guard broken | tests that caught it |
|---|---|
| out-of-state price falls back to in-state | 3 failures |
| negative discretionary income allowed | 9 failures |
| cross-scale GPA comparison allowed | 3 failures |
| auto-admit cycle fence removed | 2 failures |
| auto-admit residency fence removed | 1 failure |
| guarantee built without affordability read | 1 failure |
| own band leaks into cumulative bound (`<` → `<=`) | 3 failures |
| incomplete band distribution accepted | 1 failure |
| bound display rounds up instead of flooring | 2 failures |

## Found and fixed by this audit

1. **`majors_gpa_honesty` NULL leak** (fixed in `schema/009`). SQL three-valued
   logic: `major_gpa_type IN (...)` with a NULL type evaluates to NULL, a CHECK
   passes unless definitely FALSE, so a bare GPA number with no scale — the row
   the constraint's own comment promises is unrepresentable — inserted cleanly.
   Every other honesty CHECK was audited for the same leak; they are null-safe
   (nullable columns sit behind `IS [NOT] NULL` predicates before comparison,
   and `admission_stats.gpa_type` is NOT NULL so its IN-tests cannot go NULL).
2. **Negative class rank could fire a guarantee.** `class_rank_percentile=-5`
   sails under any "top N%" threshold, so an impossible input produced the
   system's strongest label. `Student.__post_init__` now rejects rank outside
   (0, 100], negative or non-finite GPAs, an unweighted GPA above its
   definitional 4.0 maximum, non-finite income, and impossible household shapes.
3. **Colleges upsert keyed on name, not identity.** The seed's ON CONFLICT
   target was `(name, state)`; identity is `ipeds_unitid` (schema v007). A
   renamed school would have inserted a duplicate institution. Now keyed on
   UNITID, with name/state updated as attributes.
4. **Residency/school-type coherence untested.** A private priced by residency
   (or a public priced `not_applicable`) satisfied every constraint while being
   a category error. Now locked by `test_residency_labels_cohere_with_school_type`.
5. **`.gitignore` did not cover `.venv`, caches, or `.env`.**

## Residual risks, accepted deliberately

- **The engine trusts school-side data invariants to the database.** A
  hand-constructed `School` with p25 > p75 or an inverted band is not
  re-validated in the engine; those invariants are enforced where the data
  lives (schema CHECKs) and at ingestion. Re-validating in a third place buys
  little and invites drift between the copies.
- **No upper GPA bound for `weighted` / `uc_weighted_capped` students.** No
  verified cap exists to enforce; inventing one (5.0? 4.4?) would be exactly
  the unsourced precision this project refuses. Only the definitional
  unweighted 4.0 cap is enforced.
- **Band shares summing to ~1 is enforced at ingestion and in tests, not by
  the database.** A cross-row constraint needs a trigger or deferred aggregate
  check; at this scale the seed guard plus `test_seeded_band_constants_*` plus
  the engine's own refusal to use an incomplete distribution is triple
  coverage already.
- **One provenance quote per source URL.** The tier/quote registry is keyed by
  URL, so several facts drawn from one document share one quote. Fidelity per
  fact would require per-row quotes; the current design was chosen to keep one
  source from being tiered two ways, and that trade is kept.
- **Secondary-tier rows pending primary verification** (all flagged inline
  where they live): MA/TX/US minimum wages, the FSA loan-limit figure, UMass
  aid flags and GPA figure (umass.edu 403s automation), GSU and BC/Berkeley/UT
  aid-page flags, and the GSU C11 sub-column ambiguity (bands filed under
  "test-score submitters"; corroborated as the all-student distribution by the
  implied-mean checksum, see `ingest/seed_phase1.py`).
- **Engine reference data is trusted as loaded.** `ReferenceData` built by a
  caller with a missing 'US' wage row raises KeyError on lookup — loud, not
  silent, which is the acceptable failure mode.

## The stop

Audited for: correctness of every honesty rule, robustness to bad and boundary
input, parameterized SQL, secret-free repo, deterministic clean-room rebuild
(migrations 001→009 on an empty database, both seeds, full suite green —
exercised in CI on every push). Not attempted: formal verification, load/scale
tuning, fuzzing beyond the boundary cases named above.
