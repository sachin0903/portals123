-- ============================================================================
-- 0280_integration_feature_labels -- name the two income-statement features for what they are.
--
-- 0231 seeded their descriptions as sentences ("Create the monthly income statement in Intelliflo",
-- "Send daily income statement items to Intelliflo"). The Features toggles on an integration show
-- the description as the label, so they read as instructions beside Valuations and Transactions.
-- Renamed to match the code: "Income Statements" and "Income Statement Items".
--
-- A NEW migration rather than an edit to 0231: 0231 is on main and already applied in dev, and its
-- seed is ON CONFLICT DO NOTHING, so an edit would only change databases created from now on.
--
-- ROW-LEVEL SECURITY: integrations.features is FORCEd with no policy (the engine reaches it as
-- service_role), which also binds its owner, `migrator`. A plain UPDATE would match nothing. Lift
-- the force for the statement and put it back ONLY where it was set -- the same pattern as 0275.
--
-- Idempotent: it sets the labels by code, so a re-run changes nothing.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

DO $$
DECLARE
    forced boolean;
BEGIN
    SELECT relforcerowsecurity INTO forced FROM pg_class WHERE oid = 'integrations.features'::regclass;
    IF forced THEN
        ALTER TABLE integrations.features NO FORCE ROW LEVEL SECURITY;
    END IF;

    UPDATE integrations.features SET description = 'Income Statements'      WHERE code = 'IncomeStatements';
    UPDATE integrations.features SET description = 'Income Statement Items' WHERE code = 'IncomeStatementItems';

    IF forced THEN
        ALTER TABLE integrations.features FORCE ROW LEVEL SECURITY;
    END IF;
END $$;
