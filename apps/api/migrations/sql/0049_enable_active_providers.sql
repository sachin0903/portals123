-- ============================================================================
-- 0049_enable_active_providers -- an Active ceding scheme arrives available.
--
-- The register was discovered switched OFF, on the reasoning that Seccl's whole address book
-- appearing enabled would read as a firm having approved 661 counterparties. The call went the
-- other way: a firm that has chosen Seccl has, in effect, chosen the rails Seccl offers, and
-- making an adviser hunt for a provider before every transfer is friction with no decision in
-- it. So Active providers are available by default and the deliberate act is switching one OFF.
--
-- SUSPENDED STAYS OFF, and that is not a default -- it is the rule from 0046/catalogue.py: a
-- transfer addressed to a suspended scheme fails at the custodian, after a client has been told
-- it is under way.
--
-- WHY A BACKFILL IS SAFE HERE, which will not be true again. Every existing row was written
-- `false` by the sync itself; nothing has been enabled or disabled by a human yet, so there is no
-- operator decision for this to overwrite. A later re-run of this statement WOULD overwrite one,
-- which is why it lives in a numbered migration that runs once rather than in the sync, where
-- app/materialise.py deliberately excludes `enabled` from its DO UPDATE.
-- ============================================================================

-- The migrator owns the table but is subject to FORCE ROW LEVEL SECURITY and is not bypassrls,
-- so an UPDATE that has to match rows silently touches nothing without this (precedent: 0023,
-- 0041, 0044, 0046).
SET LOCAL ROLE service_role;

UPDATE platform.transfer_providers
   SET enabled = true, updated_at = now()
 WHERE lower(coalesce(status, '')) = 'active'
   AND enabled IS NOT TRUE;

RESET ROLE;
