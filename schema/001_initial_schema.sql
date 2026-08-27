-- Pontis — schema v001 (Phase 1)
--
-- Run with:  psql -h localhost -U enzoarantes -d pontis -f schema/001_initial_schema.sql
--
-- Six tables. They are separate rather than one wide table because they have
-- different GRAIN:
--   colleges            -> 1 row per school
--   admission_stats     -> 1 row per school PER YEAR
--   admission_factors   -> 1 row per school per CDS factor   (EMPTY until Phase 2)
--   majors              -> 1 row per school per major
--   net_price_by_income -> 1 row per school per IPEDS income band
--   scholarships        -> 1 row per scholarship (not school-scoped at all)
--
-- This file is re-runnable: dropping and recreating from scratch is the intended
-- way to rebuild a dev database. Idempotent UPSERTs live in the ingest layer and
-- rely on the UNIQUE constraints declared here.

BEGIN;

-- ---------------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------------

-- Why an enum and not free text: a GPA number is meaningless without its scale.
-- 4.18 is a strong UC weighted-capped GPA and an impossible unweighted one.
-- Storing the scale as a constrained type makes an unlabelled GPA unrepresentable.
--   unweighted         -> standard 4.0 scale, no honors bonus
--   uc_weighted_capped -> UC scale; honors points capped at 8 semesters, runs >4.0
--   class_rank_proxy   -> school leans on class rank; GPA may not be published at all
--   not_published      -> school does not publish this. A real, displayed value.
DROP TYPE IF EXISTS gpa_type CASCADE;
CREATE TYPE gpa_type AS ENUM (
    'unweighted',
    'uc_weighted_capped',
    'class_rank_proxy',
    'not_published'
);

-- CDS section C7 rates each factor on exactly this 4-level scale.
DROP TYPE IF EXISTS factor_importance CASCADE;
CREATE TYPE factor_importance AS ENUM (
    'very_important',
    'important',
    'considered',
    'not_considered'
);

-- not_a_separate_admit is the important one: at a holistic school like BC you are
-- admitted to the college, not the major, so "how competitive is this major" is a
-- category error rather than an unknown. That is different from
-- unknown_not_published, which means the school gates by major but won't say how hard.
DROP TYPE IF EXISTS major_competitiveness CASCADE;
CREATE TYPE major_competitiveness AS ENUM (
    'very_competitive',
    'standard',
    'not_a_separate_admit',
    'unknown_not_published'
);

-- ---------------------------------------------------------------------------
-- colleges
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS colleges CASCADE;
CREATE TABLE colleges (
    id                   INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name                 TEXT    NOT NULL,
    state                CHAR(2) NOT NULL,
    is_public            BOOLEAN NOT NULL,

    -- The two flags that drive the affordability branch in the engine.
    -- meets_full_need is what makes a $90k-sticker private cheaper than an
    -- out-of-state public for a high-need student.
    meets_full_need      BOOLEAN NOT NULL,
    css_profile_required BOOLEAN NOT NULL,

    source_url           TEXT    NOT NULL,
    date_ingested        DATE    NOT NULL DEFAULT CURRENT_DATE,

    -- Natural key for idempotent upserts. Same school name can legitimately exist
    -- in two states (e.g. multiple "Columbia College"), so the key is (name, state).
    CONSTRAINT colleges_name_state_uniq UNIQUE (name, state)
);

-- ---------------------------------------------------------------------------
-- admission_stats  — one row per school PER YEAR
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS admission_stats CASCADE;
CREATE TABLE admission_stats (
    id              INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    college_id      INTEGER NOT NULL REFERENCES colleges(id) ON DELETE CASCADE,

    -- Entering fall cohort year, e.g. 2024 = the class that enrolled Fall 2024,
    -- reported in the "2024-2025" Common Data Set.
    year            SMALLINT NOT NULL,

    acceptance_rate NUMERIC(5,4),          -- stored as a fraction: 0.1104 = 11.04%

    -- Deliberately NOT a single `gpa` column. Value and scale travel together.
    gpa_value       NUMERIC(4,3),
    gpa_type        gpa_type NOT NULL,

    source_url      TEXT NOT NULL,
    date_ingested   DATE NOT NULL DEFAULT CURRENT_DATE,

    CONSTRAINT admission_stats_college_year_uniq UNIQUE (college_id, year),
    CONSTRAINT admission_stats_rate_range
        CHECK (acceptance_rate IS NULL OR (acceptance_rate >= 0 AND acceptance_rate <= 1)),
    CONSTRAINT admission_stats_year_sane
        CHECK (year BETWEEN 1900 AND 2100),

    -- ***** The honesty constraint *****
    -- Principle 1 ("the tool never invents a number to fill a gap") enforced in the
    -- database rather than trusted to application code:
    --   * not_published MUST carry a NULL value. Boston College's CDS literally
    --     prints "0.00" in field C12; inserting that 0.00 as a GPA would be the
    --     single most damaging lie this tool could tell a student.
    --   * class_rank_proxy MAY be NULL — a rank-driven school (UT Austin) often
    --     publishes no GPA at all, and that absence is itself the fact.
    --   * a real scale MUST carry a real number, so a value can never be silently
    --     dropped while its label survives.
    CONSTRAINT admission_stats_gpa_honesty CHECK (
        (gpa_type = 'not_published'    AND gpa_value IS NULL)
     OR (gpa_type = 'class_rank_proxy')
     OR (gpa_type IN ('unweighted', 'uc_weighted_capped') AND gpa_value IS NOT NULL)
    )
);

CREATE INDEX admission_stats_college_idx ON admission_stats (college_id, year DESC);

-- ---------------------------------------------------------------------------
-- admission_factors — CREATED IN PHASE 1, POPULATED IN PHASE 2
-- ---------------------------------------------------------------------------
-- Intentionally left empty. The matching engine must produce correct results with
-- zero rows here; Phase 2 data ENHANCES explanations without an engine rewrite.
DROP TABLE IF EXISTS admission_factors CASCADE;
CREATE TABLE admission_factors (
    id            INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    college_id    INTEGER NOT NULL REFERENCES colleges(id) ON DELETE CASCADE,
    factor_name   TEXT NOT NULL,           -- e.g. 'Academic GPA', 'Rigor of record'
    importance    factor_importance NOT NULL,
    source_url    TEXT NOT NULL,
    date_ingested DATE NOT NULL DEFAULT CURRENT_DATE,

    CONSTRAINT admission_factors_college_factor_uniq UNIQUE (college_id, factor_name)
);

-- ---------------------------------------------------------------------------
-- majors — one row per school per major
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS majors CASCADE;
CREATE TABLE majors (
    id               INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    college_id       INTEGER NOT NULL REFERENCES colleges(id) ON DELETE CASCADE,
    major_name       TEXT NOT NULL,
    competitiveness  major_competitiveness NOT NULL,

    -- Nullable as a pair: most schools publish no major-level GPA at all.
    major_gpa_value  NUMERIC(4,3),
    major_gpa_type   gpa_type,

    source_url       TEXT NOT NULL,
    date_ingested    DATE NOT NULL DEFAULT CURRENT_DATE,

    CONSTRAINT majors_college_major_uniq UNIQUE (college_id, major_name),

    -- Same honesty rule, adapted: the pair is optional, but a bare number with no
    -- scale (or a not_published carrying a number) is rejected.
    CONSTRAINT majors_gpa_honesty CHECK (
        (major_gpa_value IS NULL AND major_gpa_type IS NULL)
     OR (major_gpa_value IS NULL AND major_gpa_type IN ('not_published', 'class_rank_proxy'))
     OR (major_gpa_value IS NOT NULL
         AND major_gpa_type IN ('unweighted', 'uc_weighted_capped', 'class_rank_proxy'))
    )
);

-- ---------------------------------------------------------------------------
-- net_price_by_income — one row per school per IPEDS income band
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS net_price_by_income CASCADE;
CREATE TABLE net_price_by_income (
    id            INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    college_id    INTEGER NOT NULL REFERENCES colleges(id) ON DELETE CASCADE,

    -- Constrained to the five federal IPEDS/Scorecard brackets (NPT41..NPT45).
    -- Kept as TEXT + CHECK rather than an enum so the band labels stay readable in
    -- query output, while still making a typo'd band impossible to insert.
    income_band   TEXT NOT NULL
        CHECK (income_band IN ('0-30k', '30-48k', '48-75k', '75-110k', '110k+')),

    -- Whole dollars. This is the ONLY price number Pontis trusts; sticker price is
    -- deliberately absent from the schema so nothing can accidentally rank on it.
    avg_net_price INTEGER NOT NULL CHECK (avg_net_price >= 0),

    source_url    TEXT NOT NULL,
    date_ingested DATE NOT NULL DEFAULT CURRENT_DATE,

    CONSTRAINT net_price_college_band_uniq UNIQUE (college_id, income_band)
);

-- ---------------------------------------------------------------------------
-- scholarships — curated, small, deliberately NOT school-scoped
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS scholarships CASCADE;
CREATE TABLE scholarships (
    id                      INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name                    TEXT NOT NULL,
    provider                TEXT NOT NULL,
    eligibility_description TEXT NOT NULL,

    -- NULL means "this program publishes no hard income cap", NOT "unknown" and
    -- NOT "no limit". QuestBridge explicitly states it has no cut-off; storing its
    -- ~$65k guidance figure as a cap would wrongly exclude eligible students.
    -- The nuance lives in eligibility_description where a human can read it.
    income_cap              INTEGER CHECK (income_cap IS NULL OR income_cap > 0),

    state_restriction       CHAR(2),        -- NULL = nationwide
    first_gen_only          BOOLEAN NOT NULL,
    source_url              TEXT NOT NULL,
    date_ingested           DATE NOT NULL DEFAULT CURRENT_DATE,

    CONSTRAINT scholarships_name_uniq UNIQUE (name)
);

COMMIT;
