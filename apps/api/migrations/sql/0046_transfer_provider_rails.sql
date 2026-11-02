-- ============================================================================
-- 0046_transfer_provider_rails -- a transfer provider is reachable on rails, and is keyed by
-- the provider's own id rather than its name.
--
-- WHAT SECCL ACTUALLY PUBLISHES (verified against the live register, 661 rows). A master party
-- carries a LIST of rails -- Origo, ATG -- each with its own reference and its own set of
-- supported products:
--   [{transferProviderName: 'Origo', providerReference: '3526', PENSION: {...}},
--    {transferProviderName: 'ATG',   providerReference: 'LAWHGB21XXX', PENSION, GIA, ISA}]
-- 0042 modelled this as two scalar columns (masterparty_id, origo_id), which cannot hold a
-- second rail at all, and nothing recorded which products a provider can actually transfer --
-- the one fact an adviser needs before starting one.
--
-- THE KEY CHANGES, and this is a correctness fix rather than tidying. `transfer_providers_name_
-- unique (tenant_id, name)` assumed a provider's name identifies it. It does not: P1 Investment
-- Management Limited exists TWICE in the register, once reachable on Origo and once on ATG, as
-- two master parties. Keyed on name, the second upsert overwrote the first and the firm lost a
-- rail -- silently, because both rows look equally valid. The provider's own id is the identity.
-- ============================================================================

ALTER TABLE platform.transfer_providers
  ADD COLUMN status text,
  ADD COLUMN atg_reference text,
  -- The rails, verbatim as conformed: [{rail, reference, products[]}]. jsonb rather than a child
  -- table because nothing joins to a rail -- it is read as a whole, per provider, to render what
  -- the provider supports and to address an instruction.
  ADD COLUMN rails jsonb NOT NULL DEFAULT '[]'::jsonb,
  -- The union of products across rails, denormalised for filtering. A firm filters the register
  -- by "who can take an ISA transfer", and asking that of a jsonb array on every row is a scan.
  ADD COLUMN products text[] NOT NULL DEFAULT '{}';

-- Filtering by product is the primary query on this table, so give it an index that supports the
-- containment test rather than making 661 rows a sequential scan per keystroke.
CREATE INDEX transfer_providers_products_idx
    ON platform.transfer_providers USING gin (products);

-- Re-key on the provider's own id. Both steps run as service_role: the migrator owns these
-- tables but is subject to FORCE ROW LEVEL SECURITY and is not bypassrls, so a DELETE that has
-- to SEE rows to choose between them silently matches nothing (precedent: 0023, 0041, 0044).
SET LOCAL ROLE service_role;

-- Rows minted before this migration were keyed by name, so a name that occurred twice upstream
-- kept only whichever landed last. Those rows carry no rails and cannot be repaired here -- the
-- next sync re-creates them correctly against the new key -- but a duplicate masterparty_id
-- would block the constraint, so collapse to one row per id first.
DELETE FROM platform.transfer_providers p
 WHERE p.masterparty_id IS NOT NULL
   AND p.id <> (SELECT min(q.id) FROM platform.transfer_providers q
                 WHERE q.tenant_id = p.tenant_id AND q.masterparty_id = p.masterparty_id);

RESET ROLE;

ALTER TABLE platform.transfer_providers
  DROP CONSTRAINT IF EXISTS transfer_providers_name_unique;

-- Partial: a provider added BY HAND has no masterparty_id, and two hand-added providers must not
-- collide with each other on NULL.
CREATE UNIQUE INDEX transfer_providers_masterparty_key
    ON platform.transfer_providers (tenant_id, masterparty_id)
 WHERE masterparty_id IS NOT NULL;

COMMENT ON COLUMN platform.transfer_providers.rails IS
  'Rails the provider is reachable on: [{rail, reference, products[]}]. A provider may hold '
  'several -- Origo and ATG carry different products with different references.';
