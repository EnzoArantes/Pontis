-- Pontis — schema v005: published automatic-admission class rank thresholds
--
--   psql -h localhost -U enzoarantes -d pontis -f schema/005_class_rank_auto_admit.sql
--
-- Distinct from the class-rank DISTRIBUTION added in v004, and the distinction is
-- the whole point:
--
--   v004 class_rank_top_pct/share -> DESCRIPTIVE. "90% of our admits were top 10%."
--        A fact about who got in. Says nothing certain about any applicant.
--   v005 this table              -> A GUARANTEE the school offers in advance.
--        "Top 5% of a Texas high school class is admitted." A promise, not a
--        pattern. It supports a categorically stronger statement to a student.
--
-- Conflating the two would let a descriptive statistic masquerade as a promise,
-- so they are separate tables with separate semantics.
--
-- Every row is scoped to an ADMISSION CYCLE, because these thresholds move: UT
-- Austin ran top 6% for Fall 2025 and top 5% from Fall 2026. A threshold applied
-- to the wrong cycle is a stale promise, which is worse than no promise -- hence
-- the engine refuses to fire unless the applicant's cycle matches exactly.
--
-- Re-runnable.

BEGIN;

CREATE TABLE IF NOT EXISTS class_rank_auto_admit (
    id                    INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    college_id            INTEGER NOT NULL REFERENCES colleges(id) ON DELETE CASCADE,

    -- The cycle this threshold governs, e.g. 'fall-2026'. Compared for EXACT
    -- equality against the applicant's cycle; no "nearest" or "latest" fallback,
    -- because silently applying last year's promise is the failure mode here.
    effective_cycle       TEXT NOT NULL,

    -- The guarantee is a state-law residency benefit, so it is scoped to a
    -- state rather than offered to everyone.
    resident_state        CHAR(2) NOT NULL,

    -- 5.00 means "top 5% of the high school graduating class".
    threshold_top_pct     NUMERIC(5,2) NOT NULL
                          CHECK (threshold_top_pct > 0 AND threshold_top_pct <= 100),

    -- What the guarantee actually covers. For UT Austin: admission to the
    -- university, NOT to a chosen major. Stored as data rather than assumed,
    -- because a student reading "you're automatically admitted" and expecting
    -- their major is the specific misunderstanding this project should prevent.
    guarantees_university BOOLEAN NOT NULL,
    guarantees_major      BOOLEAN NOT NULL,

    source_url            TEXT NOT NULL,
    date_ingested         DATE NOT NULL DEFAULT CURRENT_DATE,

    CONSTRAINT class_rank_auto_admit_uniq
        UNIQUE (college_id, effective_cycle, resident_state)
);

CREATE INDEX IF NOT EXISTS class_rank_auto_admit_lookup_idx
    ON class_rank_auto_admit (college_id, effective_cycle, resident_state);

COMMIT;
