-- Pontis — schema v009: close the NULL leak in majors_gpa_honesty
--
--   psql -h localhost -U enzoarantes -d pontis -f schema/009_majors_honesty_null_leak.sql
--
-- Found by the Phase A audit's constraint-proving pass (tests/test_db_constraints.py):
-- inserting a major with major_gpa_value = 3.5 and major_gpa_type = NULL was
-- ACCEPTED, even though the v001 comment promises "a bare number with no scale
-- ... is rejected". The mechanism is SQL three-valued logic: the constraint's
-- third arm evaluates
--
--     major_gpa_value IS NOT NULL AND major_gpa_type IN (...)
--
-- and with a NULL type, `NULL IN (...)` is NULL, so the arm is NULL, the whole
-- OR-chain is NULL -- and a CHECK passes unless it is definitely FALSE. The
-- unlabelled-GPA row the schema exists to make unrepresentable was therefore
-- representable the whole time.
--
-- The fix is an explicit IS NOT NULL guard in that arm: FALSE AND NULL is
-- FALSE, so the chain collapses to FALSE and the row is rejected. The other
-- honesty constraints were audited for the same leak and are null-safe -- every
-- nullable column they touch sits behind an IS [NOT] NULL predicate before any
-- comparison. (admission_stats.gpa_type cannot leak this way because that
-- column is NOT NULL.)
--
-- Re-runnable.

BEGIN;

ALTER TABLE majors
    DROP CONSTRAINT IF EXISTS majors_gpa_honesty;
ALTER TABLE majors
    ADD CONSTRAINT majors_gpa_honesty CHECK (
        (major_gpa_value IS NULL AND major_gpa_type IS NULL)
     OR (major_gpa_value IS NULL
         AND major_gpa_type IN ('not_published', 'class_rank_proxy'))
     OR (major_gpa_value IS NOT NULL
         AND major_gpa_type IS NOT NULL
         AND major_gpa_type IN ('unweighted', 'uc_weighted_capped', 'weighted',
                                'class_rank_proxy'))
    );

COMMIT;
