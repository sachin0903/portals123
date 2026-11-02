-- Removes what the seed created: tagged links/accounts, and the stored group id (so the group lookup runs again). The platform row and features are left in place.
DELETE FROM platform.links
WHERE provider = 'intelliflo' AND entity_type = 'account' AND data ->> 'seeded_local' = 'true';

DELETE FROM platform.accounts WHERE data ->> 'seeded_local' = 'true';

UPDATE integrations.platforms SET group_id = NULL
WHERE platform_name = 'intelliflo' AND tenant_id = %(tenant_id)s;
-- BE-owned tables: only present once the scripts/ migrations have run.
DO $$
BEGIN
    IF to_regclass('integrations.income_statements') IS NOT NULL THEN
        DELETE FROM integrations.income_statements WHERE tenant_id = %(tenant_id)s;
    END IF;
    IF to_regclass('integrations.income_statement_items_sent') IS NOT NULL THEN
        DELETE FROM integrations.income_statement_items_sent WHERE tenant_id = %(tenant_id)s;
    END IF;
END $$;
