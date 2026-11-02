-- ============================================================================
-- 0227_complycube_client_risk.sql — ComplyCube's risk profile for each client.
--
-- ComplyCube rates every client it holds (GET /clients/{id}/riskProfile): an
-- overall level and a breakdown by country, political exposure, occupation and
-- watchlist. app/complycube/risk.py fetches it after a check of that client
-- changes, and keeps the latest here.
--
-- Its own table, not columns on compliance.clients: a firm can read every column
-- of compliance.clients, and a risk rating can say a client is a politically
-- exposed person or on a watchlist, which a firm must not learn from us (tipping
-- off, docs/aml.md §5). So: RLS forced, no policy, no grant to authenticated;
-- operations read it on service_role. Audited, because a change of a client's
-- risk is part of the record.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

CREATE TABLE compliance.client_risk (
  client_id int PRIMARY KEY REFERENCES compliance.clients(id) ON DELETE CASCADE,
  -- Null while the client is unmatched, like the client itself.
  tenant_id int REFERENCES auth.tenants(id) ON DELETE CASCADE,
  -- ComplyCube's `overall`: low, medium, high or not_set. Kept as given.
  overall text NOT NULL,
  profile jsonb NOT NULL DEFAULT '{}'::jsonb,
  -- ComplyCube's own `updatedAt` for the profile.
  provider_updated_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX compliance_client_risk_tenant_idx ON compliance.client_risk (tenant_id);

ALTER TABLE compliance.client_risk ENABLE ROW LEVEL SECURITY;
ALTER TABLE compliance.client_risk FORCE ROW LEVEL SECURITY;
REVOKE ALL ON compliance.client_risk FROM PUBLIC, authenticated;
GRANT ALL ON compliance.client_risk TO service_role;

CREATE TRIGGER set_updated_at BEFORE UPDATE ON compliance.client_risk
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

SELECT audit.attach(ARRAY['compliance.client_risk']);
