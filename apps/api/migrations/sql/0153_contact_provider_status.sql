-- 0153_contact_provider_status: the custodian's own status for a person, so the book can
-- show Suspended without a firm-side Deactivate.
--
-- Seccl publishes `status` on the client (Active / Pending / Suspended). Morgan Lloyd maps
-- Dataverse `statecode` 1 to inactive. SEI's Clients report has no person-level status, so
-- the conformed column is NULL there. The listing derives what it SHOWS (onboarding / active
-- / suspended / closed) from this plus `deleted_at` plus whether they hold an account —
-- `crm.contacts.enabled` is no longer that signal (Deactivate is gone; Closed replaced Archive).
--
-- Written only by the merge (`provider_status` is the one updatable column on contacts). A
-- firm Close is `deleted_at`, a different fact.

ALTER TABLE crm.contacts ADD COLUMN provider_status text;

COMMENT ON COLUMN crm.contacts.provider_status IS
  'The custodian''s own status for this person (Seccl suspended, Morgan Lloyd inactive). '
  'Never written by the product — Closed is deleted_at, and the listing derives the rest.';

-- Backfill from the link payload the merge already stored. FORCE-RLS on crm.contacts would
-- make this UPDATE a no-op that reports success (the migrator is not BYPASSRLS); service_role
-- is the same lift 0059 documents.
SET LOCAL ROLE service_role;
UPDATE crm.contacts AS c
   SET provider_status = lower(pl.data ->> 'status')
  FROM platform.links AS pl
 WHERE pl.entity_type = 'contact'
   AND pl.entity_id = c.id
   AND nullif(lower(pl.data ->> 'status'), '') IS NOT NULL;
RESET ROLE;
