-- A JISA registered contact is a permanent relationship between two CRM contacts, not merely an
-- account-party role. Parent/guardian is deliberately not inferred: a guardian need not be the
-- child's parent, while "registered contact" is the legal capacity both profiles need to state.
--
-- THE TYPE IS REPLACED RATHER THAN EXTENDED, and that is the whole point of this migration.
-- `ALTER TYPE ... ADD VALUE` leaves the new member unusable until the adding transaction COMMITS
-- (the rule 0104, 0109 and 0152 each state), and every migration here runs inside ONE Alembic
-- transaction -- so 0169's backfill could not name it, whatever revision it sat in. Postgres
-- exempts a type CREATED in the current transaction, so creating this one afresh makes the member
-- legal for the rest of the upgrade.
--
-- THE TEST SUITE CANNOT CATCH THE ADD VALUE FORM, which is why this reached a real deployment:
-- every database in the suite is built from scratch, where 0002 creates `crm.relationship_type`
-- in the same transaction and the added member is therefore already exempt. It fails only on a
-- database that had 0002 committed -- that is, on every deployment there is.
--
-- The member list below is 0002's, plus the new one. A member still in USE but missing from it
-- fails the cast loudly instead of dropping a row.

SET LOCAL lock_timeout = '5s';

ALTER TYPE crm.relationship_type RENAME TO relationship_type_v1;

CREATE TYPE crm.relationship_type AS ENUM (
  'spouse','partner','parent','child','dependant','director','shareholder',
  'trustee','beneficiary','attorney','adviser','other','registered_contact');

-- Rewrites crm.relationships and rebuilds its indexes and the (from, to, type) unique constraint
-- under their own names. No RLS policy on that table names this column, so none has to be dropped
-- and recreated -- a policy that did would block the ALTER outright rather than silently.
ALTER TABLE crm.relationships
  ALTER COLUMN relationship_type TYPE crm.relationship_type
  USING relationship_type::text::crm.relationship_type;

DROP TYPE crm.relationship_type_v1;

COMMENT ON TYPE crm.relationship_type IS
  'spouse / partner / parent / child / dependant / director / shareholder / trustee / '
  'beneficiary / attorney / adviser / other / registered_contact (adult acting for a JISA child)';
