-- Accounts and their links, from the `accounts` list in local.yaml.
-- An account's SEI account number is the external_id of its SEI ('sei') link, exactly as for a synced
-- account; its Intelliflo link carries the IO policy number (external_id) and the IO plan id
-- (data->>'id'). Rows created here are tagged seeded_local.
INSERT INTO platform.accounts (tenant_id, name, data)
SELECT %(graphene_tenant_id)s, a.account_number,
       jsonb_build_object('seeded_local', true, 'account_number', a.account_number)
FROM jsonb_to_recordset(%(accounts)s::jsonb) AS a(account_number text, policy_number text, plan_id text)
WHERE NOT EXISTS (
    SELECT 1 FROM platform.links s
    WHERE s.tenant_id = %(graphene_tenant_id)s AND s.entity_type = 'account'
      AND s.provider = 'sei' AND s.link_type = 'primary' AND s.external_id = a.account_number
);

INSERT INTO platform.links (tenant_id, entity_type, entity_id, provider, link_type, external_id, data)
SELECT %(graphene_tenant_id)s, 'account', x.id, 'sei', 'primary', a.account_number,
       jsonb_build_object('ACCOUNT_NUMBER', a.account_number, 'seeded_local', true)
FROM jsonb_to_recordset(%(accounts)s::jsonb) AS a(account_number text, policy_number text, plan_id text)
JOIN platform.accounts x
  ON x.tenant_id = %(graphene_tenant_id)s
 AND x.data ->> 'seeded_local' = 'true'
 AND x.data ->> 'account_number' = a.account_number
ON CONFLICT (tenant_id, provider, entity_type, entity_id, link_type) DO NOTHING;

INSERT INTO platform.links (tenant_id, entity_type, entity_id, provider, link_type, external_id, data)
SELECT %(graphene_tenant_id)s, 'account', s.entity_id, 'intelliflo', 'primary', a.policy_number,
       jsonb_build_object('id', a.plan_id, 'policyNumber', a.policy_number, 'seeded_local', true)
FROM jsonb_to_recordset(%(accounts)s::jsonb) AS a(account_number text, policy_number text, plan_id text)
JOIN platform.links s
  ON s.tenant_id = %(graphene_tenant_id)s AND s.entity_type = 'account'
 AND s.provider = 'sei' AND s.link_type = 'primary' AND s.external_id = a.account_number
ON CONFLICT (tenant_id, provider, entity_type, entity_id, link_type)
DO UPDATE SET external_id = EXCLUDED.external_id, data = EXCLUDED.data, updated_at = now();
