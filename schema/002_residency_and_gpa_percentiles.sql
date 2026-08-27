-- Pontis — schema v002
--
-- Run AFTER 001:
--   psql -h localhost -U enzoarantes -d pontis -f schema/002_residency_and_gpa_percentiles.sql
--
-- Two schema changes, both driven by findings from the Phase 1 three-school seed:
--
--   1. net_price_by_income gains a RESIDENCY dimension. IPEDS/College Scorecard
--      NPT4 for public institutions is calculated for in-state students ONLY --
--      out-of-state net price is not reported by the federal survey at all. The
--      v001 table therefore stored Berkeley's $5,311 and UT Austin's $12,553
--      with no indication that they apply to residents only. For a Massachusetts
--      student -- one of the two v1 anchor states -- both of those schools are
--      out-of-state, so those numbers are the wrong ones and the "out-of-state
--      public money trap" archetype was silently understated.
--
--   2. admission_stats gains GPA_P25 / GPA_P75. Selectivity is very often
--      published as a 25th-75th percentile range rather than a single average
--      (UC publishes Berkeley's admitted-student GPA as 4.16-4.28; CDS reports
--      SAT the same way). v001 could only hold a point value, so a published
--      range had to be either discarded or reduced to an invented midpoint.
--
-- This file is re-runnable: every step is guarded so applying it twice is a
-- no-op. It changes SCHEMA only. Data (including parking major competitiveness)
-- is the seed script's job, so that migrations and facts stay separable.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Residency dimension on net_price_by_income
-- ---------------------------------------------------------------------------

-- not_applicable is a real third state, not a cop-out: a private university
-- charges a Massachusetts student and a Texas student the same price, so
-- "in-state vs out-of-state" is a category error for Boston College rather than
-- missing data. Keeping it distinct from NULL means the engine can tell
-- "residency does not affect this price" apart from "we do not know this price".
DO $$
BEGIN
    CREATE TYPE residency AS ENUM ('in_state', 'out_of_state', 'not_applicable');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

ALTER TABLE net_price_by_income
    ADD COLUMN IF NOT EXISTS residency residency;

-- Backfill v001 rows with what they always actually meant. Publics were IPEDS
-- in-state figures; the private was residency-independent all along.
UPDATE net_price_by_income n
   SET residency = CASE WHEN c.is_public THEN 'in_state'::residency
                        ELSE 'not_applicable'::residency
                   END
  FROM colleges c
 WHERE c.id = n.college_id
   AND n.residency IS NULL;

ALTER TABLE net_price_by_income
    ALTER COLUMN residency SET NOT NULL;

-- The grain is now (school, income band, residency). Drop the old two-part key
-- first, otherwise a school could never hold both an in-state and an
-- out-of-state price for the same band.
ALTER TABLE net_price_by_income
    DROP CONSTRAINT IF EXISTS net_price_college_band_uniq;
ALTER TABLE net_price_by_income
    DROP CONSTRAINT IF EXISTS net_price_college_band_residency_uniq;
ALTER TABLE net_price_by_income
    ADD CONSTRAINT net_price_college_band_residency_uniq
    UNIQUE (college_id, income_band, residency);

-- ---------------------------------------------------------------------------
-- 2. Percentile range on admission_stats
-- ---------------------------------------------------------------------------

ALTER TABLE admission_stats ADD COLUMN IF NOT EXISTS gpa_p25 NUMERIC(4,3);
ALTER TABLE admission_stats ADD COLUMN IF NOT EXISTS gpa_p75 NUMERIC(4,3);

-- A percentile pair is meaningless half-present, and p25 above p75 is a data
-- entry error rather than a fact about any school.
ALTER TABLE admission_stats
    DROP CONSTRAINT IF EXISTS admission_stats_percentile_pair;
ALTER TABLE admission_stats
    ADD CONSTRAINT admission_stats_percentile_pair CHECK (
        (gpa_p25 IS NULL AND gpa_p75 IS NULL)
     OR (gpa_p25 IS NOT NULL AND gpa_p75 IS NOT NULL AND gpa_p25 <= gpa_p75)
    );

-- Widen the honesty constraint from v001. The rule is unchanged in spirit --
-- a declared scale must be backed by a real published figure -- but a school
-- may now satisfy it with EITHER a point average OR a percentile range, which
-- is what makes Berkeley's published 4.16-4.28 storable without inventing a
-- midpoint. not_published still means all three number columns are empty.
--
-- Added ONLY IF ABSENT, not drop-and-re-add: v007 later widens this same
-- constraint again (the 'weighted' scale), so on an already-migrated database
-- a re-run of this file must not rewind it to the narrower version here --
-- that version would reject rows (UMass's weighted 4.05) that are legal under
-- the constraint's current owner. On a fresh database this adds the v002
-- version and v007 replaces it in turn. (Chain-convergence lesson; see
-- HARDENING.md.)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'admission_stats_gpa_honesty'
          AND conrelid = 'admission_stats'::regclass
    ) THEN
        ALTER TABLE admission_stats
            ADD CONSTRAINT admission_stats_gpa_honesty CHECK (
                (gpa_type = 'not_published'
                     AND gpa_value IS NULL AND gpa_p25 IS NULL AND gpa_p75 IS NULL)
             OR (gpa_type = 'class_rank_proxy')
             OR (gpa_type IN ('unweighted', 'uc_weighted_capped')
                     AND (gpa_value IS NOT NULL
                          OR (gpa_p25 IS NOT NULL AND gpa_p75 IS NOT NULL)))
            );
    END IF;
END
$$;

COMMIT;
