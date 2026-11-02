-- 0142: the audit trigger `crm.contact_notes` was already documented as having.
--
-- `contacts.delete_note`'s docstring defends its HARD delete with "`crm.contact_notes` carries the
-- audit trigger, so the deletion itself is still answerable" — and the table is attached to
-- nothing. Parsing every `audit.attach(ARRAY[...])` in this directory gives 0001's seven `auth.*`
-- tables, 0002's seven `crm.*`/`platform.*` ones and 0003's `platform.platform_links`;
-- `crm.contact_notes` is created at 0002:182 and appears on none of them, and no later migration
-- attaches it. `audit.actions` records that the route was CALLED and never captures a body, so
-- deleting a note destroyed its text with no trail of what it said.
--
-- The contrast is what settles it rather than a preference: `crm.beneficiaries` got an explicit
-- trigger in 0134 ("the same trigger every other client-facing table gets") for the identical
-- hard-delete argument. One of the two tables had the thing its comment claimed.
--
-- Shaped like 0134's, not `audit.attach`: attach is for a SET of tables at migration time and
-- this is one, and the DROP-then-CREATE makes a re-run idempotent.
DROP TRIGGER IF EXISTS contact_notes_audit ON crm.contact_notes;
CREATE TRIGGER contact_notes_audit
  AFTER INSERT OR UPDATE OR DELETE ON crm.contact_notes
  FOR EACH ROW EXECUTE FUNCTION audit.record();
