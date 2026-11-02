-- ============================================================================
-- platform.merge_runs -- what the per-tenant merge DID, per entity, per run.
--
-- The sibling of platform.merge_rejects (0062). That one records the rows REFUSED; this one
-- records the work: created / updated / rejected counts, the pooled `reasons` (which include the
-- counted OBSERVATIONS that are deliberately not rejects -- "unmodelled model wrapper: SSAS" --
-- and therefore appear nowhere else), and the per-entity error when one entity fails and the walk
-- continues.
--
-- WHY IT NEEDS AN OLTP HOME. `book_sink.merge.merge_tenant` already builds exactly this ledger and
-- returns it, and `book_{uid}.merge_ledger` already publishes it as a warehouse relation. But that
-- model declares no strategy, so it holds ONE run -- and it is reachable only from the admin SQL
-- console, by an operator who already knows to look for a relation named after a tenant uid. So
-- the durable record that docs/provider-pipeline.md section 5 argues for ("where the old path
-- returned them in an HTTP response and stored them nowhere, so they survived exactly one call")
-- had, in practice, no reader at all.
--
-- This is the difference between "this firm has no data with this custodian" and "the merge ran
-- and refused everything", which are indistinguishable from the book alone -- the failure mode
-- the whole pipeline keeps producing.
-- ============================================================================
CREATE TABLE platform.merge_runs (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  -- One id per merge_tenant() call, so a run reads as a run rather than as N unrelated rows.
  -- Minted by the pipeline, not defaulted here: every entity of one walk must share it, and a
  -- column default would give each row its own.
  run_id      uuid NOT NULL,
  tenant_id   int  NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
  provider    text NOT NULL,
  entity      text NOT NULL,          -- the conformed relation: orgs | accounts | transactions | …
  created     bigint NOT NULL DEFAULT 0,
  updated     bigint NOT NULL DEFAULT 0,
  rejected    bigint NOT NULL DEFAULT 0,
  -- Pooled reason -> count. Free text from the provider's own vocabulary, so jsonb rather than a
  -- lookup: the point is to read what happened, not to constrain it in advance.
  reasons     jsonb NOT NULL DEFAULT '{}'::jsonb,
  -- Set when THIS entity failed and the walk carried on. Its presence is the distinction between
  -- "merged nothing because there was nothing" and "merged nothing because it could not".
  note        text,
  run_at      timestamptz NOT NULL DEFAULT now()
);

-- The two questions asked of it: "how is this firm's book doing" and "what did this run do".
CREATE INDEX merge_runs_tenant_idx
  ON platform.merge_runs (tenant_id, provider, run_at DESC);
CREATE INDEX merge_runs_run_idx
  ON platform.merge_runs (run_id);

-- Append-only by withheld privilege, exactly as merge_rejects is: not even service_role may UPDATE
-- or DELETE. A ledger that can be edited answers nothing.
ALTER TABLE platform.merge_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.merge_runs FORCE ROW LEVEL SECURITY;
CREATE POLICY merge_runs_read ON platform.merge_runs
  FOR SELECT TO authenticated USING (tenant_id = ANY (auth.tenant_lineage()));
GRANT SELECT ON platform.merge_runs TO authenticated;
GRANT SELECT, INSERT ON platform.merge_runs TO service_role, book_writer;

COMMENT ON TABLE platform.merge_runs IS
  'Append-only ledger of what the per-tenant provider merge did, per entity per run. '
  'Written by pkgs/book-sink as book_writer; see docs/provider-pipeline.md.';
