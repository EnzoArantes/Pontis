-- Pontis — schema v010: negative net prices are real published data
--
--   psql -h localhost -U enzoarantes -d pontis -f schema/010_negative_net_price.sql
--
-- Found by the batch pipeline against the College Scorecard release of
-- 2026-06-10: MIT's $0-30k band is -$2,533, Williams runs negative across its
-- three lowest bands, Stanford's lowest is -$2,536. These are not errors --
-- net price is cost of attendance minus grant aid, and at full-need-met
-- schools grant aid can EXCEED the cost of attendance (stipends for travel,
-- health insurance, personal expenses). A negative net price means "attending
-- costs less than staying home", which is the single strongest fact Pontis
-- could ever show a low-income student.
--
-- v001's CHECK (avg_net_price >= 0) would silently make that fact
-- unstorable, so the pipeline would have had to either drop the row (hiding
-- the best schools from the poorest students) or clamp it to zero (inventing
-- a number). The honest fix is to let the column say what the source says.
--
-- Wide sanity bounds are kept so a fat-fingered figure (an extra digit, a
-- sign flip on a large number) still cannot land: no plausible per-band net
-- price sits outside (-$60,000, $150,000).
--
-- Re-runnable.

BEGIN;

-- The v001 constraint was declared inline and carries the auto-generated name.
ALTER TABLE net_price_by_income
    DROP CONSTRAINT IF EXISTS net_price_by_income_avg_net_price_check;
ALTER TABLE net_price_by_income
    DROP CONSTRAINT IF EXISTS net_price_within_band_sane;
ALTER TABLE net_price_by_income
    ADD CONSTRAINT net_price_within_band_sane
    CHECK (net_price_within_band BETWEEN -60000 AND 150000);

COMMENT ON CONSTRAINT net_price_within_band_sane ON net_price_by_income IS
    'Negative is REAL: grant aid can exceed cost of attendance (MIT $0-30k '
    'band is -$2,533 in the 2026-06-10 Scorecard release). Bounds exist only '
    'to catch data-entry mistakes.';

COMMIT;
