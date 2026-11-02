-- ============================================================================
-- 0150_model_charge_percent -- Seccl's modelCharge is a fraction; we store 0-100.
--
-- Seccl publishes `modelCharge` as `0.002` meaning 0.2% — the same convention as fee rates
-- (`0.0035` = 0.35%) and allocation weights. `platform.models.model_charge` is labelled (%)
-- in the catalogue, sits beside `target_return` (6.5 = 6.5%), and is sent to CTC as
-- `management_charge` ("% management charge"). Storing the fraction made the form show 0.0020
-- under that label and an illustration run net of a hundredth of the charge.
--
-- Scaled at the conformed layer going forward (`services/seccl/models/platform/models.sql`).
-- THIS BACKFILL is the existing book: merge_linked_entity sets a model's typed columns ONCE
-- (0065 — they are Graphene's after insert, `links.data` is what the nightly run refreshes),
-- so a pipeline rebuild would not rewrite them.
--
-- The cutoff is 0.1. A synced fraction is 0.001–0.03 (0.1%–3%); a catalogue edit already in
-- percentage points is 0.2–2. Values at or above 0.1 are left alone rather than turned into
-- 20%. Zero stays zero (a recorded nil charge, not a missing one).
-- ============================================================================

-- FORCE RLS: a backfill as the migrator sees no rows and reports success. service_role is
-- BYPASSRLS. See 0059 / 0127 / 0128.
SET LOCAL ROLE service_role;

UPDATE platform.models
   SET model_charge = model_charge * 100
 WHERE model_charge IS NOT NULL
   AND model_charge > 0
   AND model_charge < 0.1;

RESET ROLE;
