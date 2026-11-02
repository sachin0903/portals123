-- The Intelliflo platform row for the tenant: approved, enabled, installed.
-- platforms is unique on (firm_id, platform_name, tenant_id), so update-then-insert instead of ON CONFLICT.
UPDATE integrations.platforms
SET firm_id       = %(firm_id)s,
    status        = 'approved',
    enabled       = true,
    installed     = true,
    provider_id   = %(provider_id)s,
    provider_name = %(provider_name)s,
    app_id        = %(app_id)s,
    group_id      = COALESCE(%(group_id)s, group_id),
    updated_at    = now()
WHERE platform_name = 'intelliflo' AND tenant_id = %(tenant_id)s;

INSERT INTO integrations.platforms
    (firm_id, platform_name, tenant_id, tenant_name, status, enabled, installed,
     provider_id, provider_name, app_id, group_id)
SELECT %(firm_id)s, 'intelliflo', %(tenant_id)s, %(tenant_name)s, 'approved', true, true,
       %(provider_id)s, %(provider_name)s, %(app_id)s, %(group_id)s
WHERE NOT EXISTS (
    SELECT 1 FROM integrations.platforms WHERE platform_name = 'intelliflo' AND tenant_id = %(tenant_id)s
);

INSERT INTO integrations.platform_features (platform_id, feature_code)
SELECT p.id, f.code
FROM integrations.platforms p
JOIN integrations.features f ON f.code IN ('Valuations', 'Transactions', 'IncomeStatements', 'IncomeStatementItems')
WHERE p.platform_name = 'intelliflo' AND p.tenant_id = %(tenant_id)s
ON CONFLICT (platform_id, feature_code) DO NOTHING;
