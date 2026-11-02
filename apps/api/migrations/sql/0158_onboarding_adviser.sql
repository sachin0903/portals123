-- 0158_onboarding_adviser — application kickoff recorded the selected adviser only on
-- crm.applications, while every client/adviser surface reads crm.client_advisers. Repair only the
-- definitive hole: contacts with no adviser relationship at all. Where several historical
-- applications name different advisers, the newest application is the best surviving statement;
-- an existing relationship is never reinterpreted by a data migration.
--
-- FORCE RLS: the migrator sees no rows unless it runs as service_role.

SET LOCAL ROLE service_role;

WITH candidates AS (
  SELECT DISTINCT ON (ac.contact_id)
    ac.tenant_id,
    ac.contact_id,
    a.adviser_id
  FROM crm.account_contacts ac
  JOIN crm.applications a
    ON a.id = ac.application_id
   AND a.tenant_id = ac.tenant_id
  JOIN auth.users u
    ON u.id = a.adviser_id
   AND u.tenant_id = a.tenant_id
   AND u.is_adviser IS TRUE
   AND u.deleted_at IS NULL
  WHERE a.adviser_id IS NOT NULL
    AND NOT EXISTS (
      SELECT 1
      FROM crm.client_advisers ca
      WHERE ca.tenant_id = ac.tenant_id
        AND ca.contact_id = ac.contact_id
    )
  ORDER BY ac.contact_id, a.created_at DESC, a.id DESC
)
INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role)
SELECT tenant_id, contact_id, adviser_id, 'lead'
FROM candidates
ON CONFLICT (contact_id, adviser_id) DO NOTHING;

RESET ROLE;
