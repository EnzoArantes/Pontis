-- Pontis — schema v004: published class-rank admissions signal
--
--   psql -h localhost -U enzoarantes -d pontis -f schema/004_class_rank_signal.sql
--
-- Some schools publish no GPA at all but DO publish a class-rank distribution.
-- Boston College is the case in hand: its CDS reports no GPA (C12 is blank) but
-- section C11 reports that 90.0% of first-year students were in the top tenth of
-- their high school class. That is a real, sourceable admissions signal, and
-- without somewhere to put it the engine has nothing to say about BC at all.
--
-- Lives on admission_stats rather than a new table: same grain (school x year),
-- same document (the CDS), and it already carries source_url + date_ingested.
--
-- Re-runnable.

BEGIN;

ALTER TABLE admission_stats
    -- The band, as published: 10.00 means "top 10% of the class".
    ADD COLUMN IF NOT EXISTS class_rank_top_pct NUMERIC(5,2),

    -- The share of students falling in that band: 0.9000 means 90%.
    ADD COLUMN IF NOT EXISTS class_rank_share NUMERIC(5,4),

    -- The share of students the school actually COLLECTED rank for. BC's 90%
    -- figure is computed over the 26.6% of students who submitted a rank, not
    -- over the whole class. Publishing "90% were top 10%" without that
    -- denominator is exactly the false precision this project refuses: it reads
    -- as a fact about the class when it is a fact about a self-selected quarter
    -- of it. Nullable, because not every school reports it.
    ADD COLUMN IF NOT EXISTS class_rank_reporting_share NUMERIC(5,4);

ALTER TABLE admission_stats
    DROP CONSTRAINT IF EXISTS admission_stats_class_rank_ranges;
ALTER TABLE admission_stats
    ADD CONSTRAINT admission_stats_class_rank_ranges CHECK (
        (class_rank_top_pct IS NULL OR (class_rank_top_pct > 0 AND class_rank_top_pct <= 100))
    AND (class_rank_share IS NULL OR (class_rank_share >= 0 AND class_rank_share <= 1))
    AND (class_rank_reporting_share IS NULL
         OR (class_rank_reporting_share >= 0 AND class_rank_reporting_share <= 1))
    );

-- A band with no share, or a share with no band, is not a usable signal.
ALTER TABLE admission_stats
    DROP CONSTRAINT IF EXISTS admission_stats_class_rank_pair;
ALTER TABLE admission_stats
    ADD CONSTRAINT admission_stats_class_rank_pair CHECK (
        (class_rank_top_pct IS NULL AND class_rank_share IS NULL)
     OR (class_rank_top_pct IS NOT NULL AND class_rank_share IS NOT NULL)
    );

-- A coverage figure with nothing to qualify is meaningless on its own.
ALTER TABLE admission_stats
    DROP CONSTRAINT IF EXISTS admission_stats_class_rank_coverage;
ALTER TABLE admission_stats
    ADD CONSTRAINT admission_stats_class_rank_coverage CHECK (
        class_rank_reporting_share IS NULL OR class_rank_top_pct IS NOT NULL
    );

COMMIT;
