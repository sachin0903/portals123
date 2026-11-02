-- ============================================================================
-- platform.accounts.uid NOT NULL -- an account with no uid has no address.
--
-- The uid is how every portal surface addresses an account. `0065_merge_linked_entity` already
-- says so in as many words, and mints one for every row the provider pipeline creates:
--
--     -- `uid` is how every portal surface addresses a contact or an account, it is NOT NULL on
--     -- crm.contacts with no default, and no provider publishes one -- so the merge mints it
--
-- `crm.contacts.uid` was declared `NOT NULL UNIQUE` in 0002 for exactly that reason.
-- `platform.accounts.uid` was declared merely `UNIQUE`, and the asymmetry has never been
-- deliberate: every writer sets it (the merge, `routers/applications.py`, the ingest and demo
-- scripts), so the column has never actually held a NULL. What the laxity buys is a way for a
-- future writer to create an account that no URL can reach -- and the failure would surface as a
-- 404 on a row that plainly exists, which reads as a permissions or RLS problem rather than as a
-- missing identifier.
--
-- Now that the portals address an account by its uid rather than by its serial id, that stops
-- being hypothetical, so the constraint is stated. Backfill first (a no-op on every environment
-- we have, and the honest thing to do for one we have not seen) in the platform-wide shape --
-- `nanoid(12)`, the function 0110 installs and `app/ids.py::new_id` already mints -- then
-- constrain. The merge procedures still mint hex here; `0113_uid_defaults` is what makes every
-- column agree, and this backfill is written to the shape it lands on rather than to the one it
-- is replacing.
--
-- No expand/contract dance: tightening a constraint a writer already satisfies is
-- backwards-compatible for readers, and there is no window in which a writer is allowed to
-- violate it -- none does today.
-- ============================================================================
UPDATE platform.accounts
   SET uid = public.nanoid(12)
 WHERE uid IS NULL;

-- lock_timeout, per the repository's migration rule: SET NOT NULL takes an ACCESS EXCLUSIVE lock
-- while it validates. The table is small (hundreds to low thousands of rows per firm) so the scan
-- is fast, but the lock wait behind a long-running read is what would actually stall a deploy.
SET LOCAL lock_timeout = '5s';

ALTER TABLE platform.accounts
  ALTER COLUMN uid SET NOT NULL;

COMMENT ON COLUMN platform.accounts.uid IS
  'The account''s opaque public identifier -- what appears in a portal URL. NOT NULL because an '
  'account without one cannot be addressed; minted by the writer (see 0065 merge_linked_entity).';
