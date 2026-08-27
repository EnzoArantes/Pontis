-- Pontis — schema v007: institutional identity + the weighted GPA scale
--
--   psql -h localhost -U enzoarantes -d pontis -f schema/007_unitid_and_weighted_gpa.sql
--
-- Two changes, both forced by the Georgia State ingestion.
--
-- 1. colleges.ipeds_unitid
--    Schools were identified by NAME, which is not an identity. The Georgia pull
--    surfaced three rows a name match cannot separate:
--        139940  Georgia State University                     Atlanta, GA
--        139861  Georgia College & State University           Milledgeville, GA
--        244437  Georgia State University-Perimeter College   Atlanta, GA
--    Perimeter is the dangerous one: same name prefix, same city, and materially
--    different numbers (0-30k band $10,380 vs $13,787; admit rate 91% vs 55%).
--    A substring match on "Georgia State University" hits both. UNITID is the
--    federal primary key and the only thing that actually distinguishes them.
--
-- 2. gpa_type gains 'weighted'
--    Signed off by the spec owner. A plain weighted GPA (weighted, uncapped, not
--    the UC scheme) previously had no representation, so a school reporting one
--    could only be recorded via a false statement -- 'unweighted' (wrong scale)
--    or 'not_published' (wrong fact). The engine already refuses to compare
--    across scales, so naming this scale correctly makes it comparable to
--    students on the same scale and honestly incomparable to everyone else.
--
-- Re-runnable.

-- ALTER TYPE ... ADD VALUE runs outside the transaction below: the new label
-- cannot be USED in the same transaction that adds it.
ALTER TYPE gpa_type ADD VALUE IF NOT EXISTS 'weighted';

BEGIN;

ALTER TABLE colleges
    ADD COLUMN IF NOT EXISTS ipeds_unitid INTEGER;

-- Backfill the four schools already seeded, each read from the same College
-- Scorecard file the net prices came from.
UPDATE colleges SET ipeds_unitid = 164924 WHERE name = 'Boston College'                      AND ipeds_unitid IS NULL;
UPDATE colleges SET ipeds_unitid = 110635 WHERE name = 'University of California-Berkeley'   AND ipeds_unitid IS NULL;
UPDATE colleges SET ipeds_unitid = 228778 WHERE name = 'The University of Texas at Austin'   AND ipeds_unitid IS NULL;
UPDATE colleges SET ipeds_unitid = 166629 WHERE name = 'University of Massachusetts-Amherst' AND ipeds_unitid IS NULL;

-- Unique, so two rows can never claim the same institution, and NOT NULL so a
-- school cannot be seeded without an identity again.
ALTER TABLE colleges DROP CONSTRAINT IF EXISTS colleges_unitid_uniq;
ALTER TABLE colleges ADD CONSTRAINT colleges_unitid_uniq UNIQUE (ipeds_unitid);

ALTER TABLE colleges
    ALTER COLUMN ipeds_unitid SET NOT NULL;

COMMENT ON COLUMN colleges.ipeds_unitid IS
    'IPEDS/College Scorecard UNITID -- the federal identifier for this institution. '
    'The join key of record: school NAMES are not unique enough to identify a '
    'campus (see Georgia State University vs Georgia State University-Perimeter '
    'College, both in Atlanta).';

-- Adding the enum label is not enough on its own: the honesty CHECKs written in
-- v001/v002 enumerate the scales that may carry a real number, and 'weighted' was
-- not among them -- so a weighted row would have been rejected outright. A new
-- scale is only usable once these know about it.
ALTER TABLE admission_stats
    DROP CONSTRAINT IF EXISTS admission_stats_gpa_honesty;
ALTER TABLE admission_stats
    ADD CONSTRAINT admission_stats_gpa_honesty CHECK (
        (gpa_type = 'not_published'
             AND gpa_value IS NULL AND gpa_p25 IS NULL AND gpa_p75 IS NULL)
     OR (gpa_type = 'class_rank_proxy')
     OR (gpa_type IN ('unweighted', 'uc_weighted_capped', 'weighted')
             AND (gpa_value IS NOT NULL
                  OR (gpa_p25 IS NOT NULL AND gpa_p75 IS NOT NULL)))
    );

ALTER TABLE majors
    DROP CONSTRAINT IF EXISTS majors_gpa_honesty;
ALTER TABLE majors
    ADD CONSTRAINT majors_gpa_honesty CHECK (
        (major_gpa_value IS NULL AND major_gpa_type IS NULL)
     OR (major_gpa_value IS NULL AND major_gpa_type IN ('not_published', 'class_rank_proxy'))
     OR (major_gpa_value IS NOT NULL
         AND major_gpa_type IN ('unweighted', 'uc_weighted_capped', 'weighted', 'class_rank_proxy'))
    );

COMMIT;
