-- 0155_account_role_registered_contact — the adult who acts on a child's JISA.
--
-- A JISA's owner is the child (a real crm.contacts individual). The parent/guardian is not an
-- attorney and not a catch-all "other": they are the registered contact — who we KYC, who we
-- invite to the portal, and who can act on the child's account. ADD VALUE IF NOT EXISTS matches
-- 0109; the new member is not USED in this transaction.

ALTER TYPE crm.account_role ADD VALUE IF NOT EXISTS 'registered_contact';
COMMENT ON TYPE crm.account_role IS
  'owner / decision_maker / trustee / beneficiary / attorney / other / registered_contact '
  '(the adult on a JISA; KYC and portal invite target that role, not the child owner)';
