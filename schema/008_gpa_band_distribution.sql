-- Pontis — schema v008: published GPA band distributions
--
--   psql -h localhost -U enzoarantes -d pontis -f schema/008_gpa_band_distribution.sql
--
-- The mission unlock. The engine has never honestly produced LIKELY except from
-- a published p25/p75 pair, and almost nobody publishes one -- so the label that
-- fights undermatching has effectively never fired. What schools DO publish, in
-- CDS section C11, is a BANDED distribution: "18.04% of enrolled first-years had
-- a 4.0; 22.13% had 3.75-3.99; ..." (Georgia State, CDS 2025-26).
--
-- A banded distribution supports a statement no point average can: summing the
-- shares of the bands entirely below a student's GPA proves "at least X% of the
-- class had a lower GPA than yours". That is a cumulative-share LOWER BOUND read
-- straight off published numbers -- no interpolation inside a band, no invented
-- percentiles, no midpoint fabrication. When the provable bound clears the same
-- bar the percentile path uses (75%), LIKELY is finally honest.
--
-- One row per published band. Same provenance discipline as every other table.
--
-- Re-runnable.

BEGIN;

-- Needed so the no-overlap constraint below can mix equality columns with a
-- range operator in one GiST index. Standard contrib extension.
CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TABLE IF NOT EXISTS gpa_band_distribution (
    id            INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    college_id    INTEGER NOT NULL REFERENCES colleges(id) ON DELETE CASCADE,

    -- Entering fall cohort year, same convention as admission_stats.year.
    year          SMALLINT NOT NULL CHECK (year BETWEEN 1900 AND 2100),

    -- The scale the bands are on. Same discipline as everywhere else: the value
    -- travels with its measurement, and the engine refuses cross-scale reads.
    gpa_type      gpa_type NOT NULL,

    -- Who the distribution describes. CDS C11 counts ENROLLED first-years; UC's
    -- admit-data pages count ADMITTED students. Different populations support
    -- different sentences, so the row must say which it is.
    population    TEXT NOT NULL CHECK (population IN ('enrolled', 'admitted')),

    -- The band exactly as published: "3.75-3.99" -> floor 3.75, ceiling 3.99.
    -- A point band like "GPA of 4.0" is floor = ceiling = 4.0.
    band_floor    NUMERIC(4,3) NOT NULL CHECK (band_floor >= 0),
    band_ceiling  NUMERIC(4,3) NOT NULL,

    -- Share of the (reporting) population in this band: 0.2213 = 22.13%.
    -- Zero is a real published value ("Percent who had GPA below 1.0: 0"),
    -- not a gap, so it is storable.
    share         NUMERIC(7,6) NOT NULL CHECK (share >= 0 AND share <= 1),

    -- Share of students the school collected/received a GPA for at all (GSU:
    -- 0.9991). Same honesty job as class_rank_reporting_share in v004: without
    -- it the distribution reads as a fact about the whole class when it is a
    -- fact about those who reported. Nullable -- not every school states it.
    reporting_share NUMERIC(5,4)
        CHECK (reporting_share IS NULL OR (reporting_share >= 0 AND reporting_share <= 1)),

    source_url    TEXT NOT NULL,
    source_tier   source_tier NOT NULL,
    source_quote  TEXT,
    date_ingested DATE NOT NULL DEFAULT CURRENT_DATE,

    CONSTRAINT gpa_band_floor_lte_ceiling CHECK (band_floor <= band_ceiling),

    -- The enum-vs-CHECK lesson from v007, applied on day one this time: the
    -- gpa_type enum contains labels ('not_published', 'class_rank_proxy') that
    -- are meaningful elsewhere but nonsensical as a scale for numeric bands.
    -- The enum alone would admit them; this CHECK is what actually forbids it.
    CONSTRAINT gpa_band_scale_is_numeric CHECK (
        gpa_type IN ('unweighted', 'uc_weighted_capped', 'weighted')
    ),

    CONSTRAINT gpa_band_uniq UNIQUE (college_id, year, gpa_type, population, band_floor),

    -- Spec S3 quote length, same rule as v006 applies to every sourced table.
    CONSTRAINT gpa_band_distribution_quote_len CHECK (
        source_quote IS NULL
     OR array_length(regexp_split_to_array(btrim(source_quote), '\s+'), 1) <= 15
    )
);

-- Two bands in one distribution must not overlap: a student falling in two
-- bands at once would be counted twice and every cumulative bound would be
-- wrong. UNIQUE on band_floor cannot see this ([3.0-3.5] and [3.2-3.7] have
-- different floors); a range-overlap exclusion can. btree_gist indexes enums
-- natively (since Postgres 11), so gpa_type participates directly.
ALTER TABLE gpa_band_distribution
    DROP CONSTRAINT IF EXISTS gpa_band_no_overlap;
ALTER TABLE gpa_band_distribution
    ADD CONSTRAINT gpa_band_no_overlap EXCLUDE USING gist (
        college_id WITH =,
        year WITH =,
        gpa_type WITH =,
        population WITH =,
        numrange(band_floor, band_ceiling, '[]') WITH &&
    );

CREATE INDEX IF NOT EXISTS gpa_band_lookup_idx
    ON gpa_band_distribution (college_id, year DESC);

COMMENT ON TABLE gpa_band_distribution IS
    'Published GPA band distributions (CDS C11 and cousins). Each row is one '
    'band exactly as published. The engine places a same-scale student by '
    'CUMULATIVE SHARE: summing whole bands below/above the student''s GPA '
    'proves "at least X% of the class sat below/above you" with no '
    'interpolation. This is the only honest route to a LIKELY label at schools '
    'that publish no p25/p75.';

COMMENT ON COLUMN gpa_band_distribution.share IS
    'Fraction of the reporting population inside this band, exactly as '
    'published. Shares of one distribution should sum to ~1; ingestion and '
    'tests enforce the sum because a row CHECK cannot see across rows.';

COMMIT;
