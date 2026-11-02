-- ============================================================================
-- 0062_pipeline_inbound -- the landing zone and reject ledger for the single pipeline.
--
-- Today a provider's data reaches the book through three hand-offs: the daemon syncs the core
-- warehouse, `app/fanout.py` projects a tenant slice into the tenant lake, and
-- `app/materialise.py` upserts OLTP. Every seam can report success while producing an empty
-- book, and each failure is indistinguishable from "this firm has no data".
--
-- The replacement is one interlace DAG per provider: it filters the tenant's slice in the lake
-- and then DELIVERS it into `inbound` here, on interlace 2.0.0's external-table plane
-- (`materialise: table` — a table it writes but never drops). `platform.merge_inbound()` then
-- performs the upsert.
--
-- WHY A LANDING ZONE, rather than letting the DAG merge platform.* directly. interlace's
-- `merge` (`merge_by_key` before interlace 2.0.1) emits DELETE-matching-keys + INSERT, which replaces whole rows. Against the
-- book that would:
--   1. rotate the surrogate `id`s that platform.transactions.account_id,
--      platform.valuations.account_id, platform.links.entity_id and the CRM/document references
--      all point at — the DELETE either cascades or fails on the FK;
--   2. wipe every staff-edited column (a firm's own product/model assignment, a renamed model),
--      because the provider does not supply them and DELETE+INSERT does not preserve them;
--   3. re-fire the audit.record triggers on platform.links/platform.orgs as DELETE+INSERT pairs.
-- An upsert that coalesces is a different statement from a replace, and only the merge function
-- can make it.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS inbound;
COMMENT ON SCHEMA inbound IS
  'Provider pipelines land a tenant''s conformed slice here; platform.merge_inbound() upserts it '
  'into the book. Interlace-managed tables — treat as transient, never read by the request path.';

-- No RLS: nothing here authorises a read. `authenticated` is granted nothing at all, so the
-- request path cannot reach it even by accident, which is a stronger statement than a policy.
GRANT USAGE, CREATE ON SCHEMA inbound TO book_writer;
GRANT USAGE ON SCHEMA inbound TO service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE book_writer IN SCHEMA inbound
  GRANT SELECT ON TABLES TO service_role;

-- ============================================================================
-- platform.merge_rejects -- what the merge refused, and why.
--
-- A table here, where `platform.reconciliation_breaks` was deliberately left unbuilt, and the
-- distinction is the point: a break is a statement about the CURRENT rows and goes stale the
-- moment either side is corrected, whereas a reject is a statement about ONE RUN — "this row was
-- refused, for this reason, at this time" — which is immutable by construction.
--
-- It exists because the ledger is load-bearing. On the dev Seccl slice, 117 transactions are
-- refused as `unmapped type: Expectation/FAD Gross Withdrawal` and 18 models as
-- `unmodelled model wrapper: SSAS`. Those are correct refusals (SEI publishes direction as a
-- separate code pair; SSAS is genuinely unmodelled) and they must stay VISIBLE, because an
-- unexplained shortfall in the book reads as a data problem rather than as a mapping gap.
-- Today they exist only as counts in an HTTP response body.
-- ============================================================================
CREATE TABLE platform.merge_rejects (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id   int  NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
  provider    text NOT NULL,
  entity      text NOT NULL,          -- the conformed relation: orgs | accounts | transactions | …
  reason      text NOT NULL,          -- the closed vocabulary materialise.py already uses
  -- The provider's own identifier for the row, so a reject can be chased back to source. NOT the
  -- row itself: a rejected transaction is still client data, and a ledger is not a quarantine.
  row_key     text,
  occurred_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX merge_rejects_tenant_run_idx
  ON platform.merge_rejects (tenant_id, provider, occurred_at DESC);

-- Append-only, and enforced by withheld privilege rather than by a trigger that could be
-- dropped: not even service_role may UPDATE or DELETE. Corrections are new rows.
ALTER TABLE platform.merge_rejects ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.merge_rejects FORCE ROW LEVEL SECURITY;
CREATE POLICY merge_rejects_read ON platform.merge_rejects
  FOR SELECT TO authenticated USING (tenant_id = ANY (auth.tenant_lineage()));
GRANT SELECT ON platform.merge_rejects TO authenticated;
GRANT SELECT, INSERT ON platform.merge_rejects TO service_role, book_writer;

-- ============================================================================
-- book_writer's grants -- the whole of the blast-radius reduction.
--
-- Deliberately enumerated rather than `ALL TABLES IN SCHEMA platform`: a future table should have
-- to be added here on purpose. A pipeline credential must not silently gain the next thing
-- somebody puts in the platform schema.
-- ============================================================================
GRANT USAGE ON SCHEMA platform, crm, auth TO book_writer;

GRANT SELECT, INSERT, UPDATE ON
  platform.orgs, platform.instruments, platform.accounts, platform.models,
  platform.model_versions, platform.model_allocations, platform.model_commentary,
  platform.valuations, platform.transactions, platform.products, platform.product_platforms,
  platform.fee_packages, platform.fee_package_platforms, platform.transfer_providers,
  platform.third_party_providers, platform.links, platform.platforms
  TO book_writer;
GRANT SELECT, INSERT, UPDATE ON crm.contacts TO book_writer;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA platform, crm TO book_writer;

-- Read-only on what it needs to know WHICH tenant a slice belongs to, and nothing more.
GRANT SELECT ON auth.tenants, platform.tenant_platforms TO book_writer;

-- The RLS principals stay out of reach: book_writer is not a member of service_role, so it
-- cannot SET ROLE its way into the tables above that it was not granted.
