-- Pontis — schema v006: provenance tiering, cost vintage, and the naming guard
--
--   psql -h localhost -U enzoarantes -d pontis -f schema/006_provenance_and_vintage.sql
--
-- Implements the Ingestion Spec's structural requirements, which the schema could
-- not previously satisfy:
--
--   S3  every seeded row carries source_url, source_tier, date_ingested
--   S3  PRIMARY-VERIFIED rows carry a short verbatim quote as provenance
--   S2  cost figures carry the DATA YEAR, not just the date we pulled them
--   S2  the band column must not read as "the overall average"
--
-- Re-runnable.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. source_tier
-- ---------------------------------------------------------------------------
-- Two tiers exactly, per spec S3. There is no "probably fine" middle: a row is
-- either read at the authoritative page or it is pending a primary check.
DO $$
BEGIN
    CREATE TYPE source_tier AS ENUM ('primary_verified', 'secondary_corroborated');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

-- Applied to every table that carries a source_url. Looping rather than repeating
-- ten near-identical blocks, so a future table cannot be half-migrated by copy-paste.
DO $$
DECLARE
    t TEXT;
    sourced_tables TEXT[] := ARRAY[
        'colleges', 'admission_stats', 'admission_factors', 'majors',
        'net_price_by_income', 'scholarships', 'poverty_guidelines',
        'state_minimum_wage', 'federal_loan_limits', 'class_rank_auto_admit'
    ];
BEGIN
    FOREACH t IN ARRAY sourced_tables LOOP
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS source_tier source_tier', t);

        -- Short verbatim quote from the authoritative page (spec S3, <15 words).
        -- Nullable: a CSV extract such as College Scorecard has no sentence to
        -- quote, so the requirement is enforced where prose exists rather than
        -- faked with a pseudo-quote everywhere.
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS source_quote TEXT', t);

        -- Backfill deliberately lands on the WEAKER tier. Claiming
        -- primary_verified for a pre-existing row nobody re-checked would be the
        -- exact over-claim the tiering exists to prevent; the seeds then promote
        -- the rows that genuinely were read at the source.
        EXECUTE format(
            'UPDATE %I SET source_tier = ''secondary_corroborated''::source_tier
              WHERE source_tier IS NULL', t);

        EXECUTE format('ALTER TABLE %I ALTER COLUMN source_tier SET NOT NULL', t);

        EXECUTE format(
            'ALTER TABLE %I DROP CONSTRAINT IF EXISTS %I', t, t || '_quote_len');
        EXECUTE format(
            'ALTER TABLE %I ADD CONSTRAINT %I CHECK (
                 source_quote IS NULL
              OR array_length(regexp_split_to_array(btrim(source_quote), ''\s+''), 1) <= 15
             )', t, t || '_quote_len');
    END LOOP;
END
$$;

-- ---------------------------------------------------------------------------
-- 2. Cost vintage (spec S2, S4)
-- ---------------------------------------------------------------------------
-- date_ingested records when WE pulled the row. It says nothing about how old
-- the underlying figure is, and a cost figure with no data year is, per spec S4,
-- "a stale figure waiting to happen".
ALTER TABLE net_price_by_income
    ADD COLUMN IF NOT EXISTS data_year SMALLINT;

ALTER TABLE net_price_by_income
    DROP CONSTRAINT IF EXISTS net_price_data_year_sane;
ALTER TABLE net_price_by_income
    ADD CONSTRAINT net_price_data_year_sane
    CHECK (data_year IS NULL OR data_year BETWEEN 1990 AND 2100);

-- ---------------------------------------------------------------------------
-- 3. The mislabeled-wire guard (spec S2)
-- ---------------------------------------------------------------------------
-- Defence one of two: the name. `avg_net_price` is accurate but reads as "the
-- average net price", one careless reseed away from someone writing the overall
-- NPT4 into it -- five identical numbers per school, every constraint green,
-- wrong data shipped. `net_price_within_band` cannot be misread that way.
--
-- Renamed in place rather than restructuring to per-band columns: this table's
-- grain is (college, income_band, residency), and per-band columns would take the
-- residency dimension with them, make partial band coverage inexpressible, and
-- still not stop someone writing NPT4 into all five. Defence two (the tests in
-- tests/test_net_price_source.py) is what actually catches the mistake.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'net_price_by_income' AND column_name = 'avg_net_price'
    ) THEN
        ALTER TABLE net_price_by_income RENAME COLUMN avg_net_price TO net_price_within_band;
    END IF;
END
$$;

COMMENT ON COLUMN net_price_by_income.net_price_within_band IS
    'Average net price WITHIN this income band (College Scorecard NPT41..NPT45). '
    'MUST NEVER receive the overall all-student average (NPT4): that figure '
    'includes full-pay families, describes nobody Pontis serves, and would '
    'overstate a high-need student''s real cost by thousands.';

COMMENT ON COLUMN net_price_by_income.data_year IS
    'Award year the underlying figure describes. Distinct from date_ingested, '
    'which is only when we pulled it.';

COMMENT ON COLUMN net_price_by_income.income_band IS
    'IPEDS/Scorecard bracket: 0-30k=NPT41, 30-48k=NPT42, 48-75k=NPT43, '
    '75-110k=NPT44, 110k+=NPT45.';

COMMIT;
