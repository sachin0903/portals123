-- Backfill the permanent CRM edge from every JISA registered contact to its child owner.
-- The account role remains the operational authority used by account scoping; this relationship
-- is the person-to-person statement both client records render.
--
-- Naming `registered_contact` as a `crm.relationship_type` is legal because 0168 CREATES that enum
-- rather than adding a member to the committed one; its header carries the argument.
--
-- THE ROLE FILTER IS COMPARED AS TEXT for the other half of the same rule. `crm.account_role` got
-- its `registered_contact` member from 0155 via `ALTER TYPE ... ADD VALUE`, so `rc.role =
-- 'registered_contact'` casts the literal to that enum -- unusable in the transaction that added
-- it, which is any deployment upgrading from before 0155 (0156 names the value too and is safe
-- only because it sits inside a function body, resolved when the function runs rather than when it
-- is created). Casting the COLUMN to text names no enum value and reads the same.

SET LOCAL ROLE service_role;

INSERT INTO crm.relationships (
  tenant_id,
  from_contact_id,
  to_contact_id,
  relationship_type
)
SELECT DISTINCT
  rc.tenant_id,
  rc.contact_id,
  owner.contact_id,
  'registered_contact'::crm.relationship_type
FROM crm.account_contacts rc
JOIN crm.account_contacts owner
  ON owner.tenant_id = rc.tenant_id
 AND owner.account_id = rc.account_id
 AND owner.role = 'owner'
JOIN platform.accounts a
  ON a.tenant_id = rc.tenant_id
 AND a.id = rc.account_id
JOIN platform.products p
  ON p.tenant_id = a.tenant_id
 AND p.id = a.product_id
WHERE rc.role::text = 'registered_contact'
  AND p.product_type = 'JISA'
  AND rc.contact_id <> owner.contact_id
ON CONFLICT (from_contact_id, to_contact_id, relationship_type) DO NOTHING;

RESET ROLE;
