-- Pontis — schema v003: engine reference data
--
--   psql -h localhost -U enzoarantes -d pontis -f schema/003_reference_data.sql
--
-- Three small lookup tables the matching engine needs to compute an affordability
-- ceiling. They are NOT school data -- they describe the student's side of the
-- equation (what a family can plausibly contribute) and the federal/state
-- environment. Same discipline as everything else: source_url + date_ingested on
-- every row, because all three drift annually and a stale figure silently
-- mis-gates every school.
--
-- Re-runnable; guarded throughout.

BEGIN;

-- ---------------------------------------------------------------------------
-- poverty_guidelines
-- ---------------------------------------------------------------------------
-- HHS publishes three separate tables rather than one table plus a multiplier,
-- so region is an enum of the three published tables, not a cost-of-living knob.
DO $$
BEGIN
    CREATE TYPE poverty_region AS ENUM ('contiguous', 'alaska', 'hawaii');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

CREATE TABLE IF NOT EXISTS poverty_guidelines (
    id            INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    year          SMALLINT NOT NULL,
    region        poverty_region NOT NULL,
    family_size   SMALLINT NOT NULL CHECK (family_size BETWEEN 1 AND 8),

    -- The raw 100% guideline exactly as published. The 2x multiplier is the
    -- ENGINE's policy choice and is applied there, so that changing the policy
    -- never requires re-ingesting HHS data.
    amount        NUMERIC(10,2) NOT NULL CHECK (amount > 0),

    -- HHS publishes sizes 1-8 plus "add $X for each additional person". That
    -- increment is a property of (year, region), repeated on each row of the
    -- group so a single lookup returns everything needed to handle family_size
    -- greater than 8 without a second query.
    additional_person_amount NUMERIC(10,2) NOT NULL CHECK (additional_person_amount > 0),

    source_url    TEXT NOT NULL,
    date_ingested DATE NOT NULL DEFAULT CURRENT_DATE,

    CONSTRAINT poverty_guidelines_uniq UNIQUE (year, region, family_size)
);

-- ---------------------------------------------------------------------------
-- state_minimum_wage
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS state_minimum_wage (
    id            INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- Two-letter state code, plus the sentinel 'US' holding the federal rate.
    -- The engine falls back to 'US' for any state with no row of its own, which
    -- is the documented behaviour for states that set no rate above the federal
    -- floor. Texas is stored explicitly rather than left to the fallback,
    -- because "Texas adopts the federal rate" is itself a sourceable fact worth
    -- recording rather than an absence.
    state         CHAR(2) NOT NULL,
    hourly_wage   NUMERIC(6,2) NOT NULL CHECK (hourly_wage > 0),

    source_url    TEXT NOT NULL,
    date_ingested DATE NOT NULL DEFAULT CURRENT_DATE,

    CONSTRAINT state_minimum_wage_uniq UNIQUE (state)
);

-- ---------------------------------------------------------------------------
-- federal_loan_limits
-- ---------------------------------------------------------------------------
-- A dated reference value rather than a constant in code: annual limits are set
-- per award year and have changed by statute before.
CREATE TABLE IF NOT EXISTS federal_loan_limits (
    id                       INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    award_year               TEXT NOT NULL,          -- e.g. '2026-2027'
    dependency_status        TEXT NOT NULL CHECK (dependency_status IN ('dependent', 'independent')),
    year_level               SMALLINT NOT NULL CHECK (year_level BETWEEN 1 AND 5),

    -- The engine uses the SUBSIDIZED figure only. The combined figure is stored
    -- because it is published in the same breath and omitting it would discard
    -- real sourced information, but treating unsubsidized borrowing as
    -- "affordability" is a policy call this engine does not make.
    subsidized_annual_limit  INTEGER NOT NULL CHECK (subsidized_annual_limit >= 0),
    combined_annual_limit    INTEGER NOT NULL CHECK (combined_annual_limit >= 0),

    source_url               TEXT NOT NULL,
    date_ingested            DATE NOT NULL DEFAULT CURRENT_DATE,

    CONSTRAINT federal_loan_limits_uniq UNIQUE (award_year, dependency_status, year_level),
    CONSTRAINT federal_loan_limits_sub_lte_combined
        CHECK (subsidized_annual_limit <= combined_annual_limit)
);

COMMIT;
