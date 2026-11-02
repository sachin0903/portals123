-- ============================================================================
-- 0275_integrations_features -- the feature list is what the engine runs, and nothing else.
--
-- 0231 seeds the legacy sei_reports reference list: Analytics, Calendar, Clients, Deployments,
-- Email, Notifications, Pricing, Reporting (plus Valuations and Transactions, which the engine does
-- run). Nothing runs the eight that are not run: they only fill the integration's Features toggles
-- with switches that do nothing. Four features exist -- Valuations, Transactions, IncomeStatements,
-- IncomeStatementItems -- and 0231 itself adds the last two.
--
-- A NEW migration rather than an edit to 0231: 0231 is on main and already applied, so changing it
-- would only alter databases created from now on and let the rest drift.
--
-- A feature that a platform has enabled is kept. `platform_features.feature_code` is RESTRICT, so
-- deleting it would fail the whole migration; an operator disables it on the platform first, then
-- it can go. Fresh databases (0231 seeds, this removes) and existing ones end in the same state.
--
-- ROW-LEVEL SECURITY: both tables are FORCEd with no policy (the engine reaches them as
-- service_role), which also binds their owner, `migrator`. A plain DELETE would match nothing
-- and the NOT EXISTS below would see an empty platform_features and call every feature unused.
-- Lift the force for the statement and put it back ONLY where it was set -- the same pattern as
-- 0231's own seed step, and a database without a service_role never forced it at all.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

DO $$
DECLARE
    t text;
    forced text[] := '{}';
BEGIN
    FOREACH t IN ARRAY ARRAY['features', 'platform_features'] LOOP
        IF (SELECT relforcerowsecurity FROM pg_class WHERE oid = format('integrations.%I', t)::regclass) THEN
            EXECUTE format('ALTER TABLE integrations.%I NO FORCE ROW LEVEL SECURITY', t);
            forced := forced || t;
        END IF;
    END LOOP;

    DELETE FROM integrations.features f
    WHERE f.code NOT IN ('Valuations', 'Transactions', 'IncomeStatements', 'IncomeStatementItems')
      AND NOT EXISTS (
          SELECT 1 FROM integrations.platform_features pf WHERE pf.feature_code = f.code
      );

    FOREACH t IN ARRAY forced LOOP
        EXECUTE format('ALTER TABLE integrations.%I FORCE ROW LEVEL SECURITY', t);
    END LOOP;
END $$;
