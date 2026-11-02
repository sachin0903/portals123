-- 0243_account_role_party — a person on a journey that opens no account.
--
-- An advice journey starts with one or two existing clients and no platform.accounts row.
-- crm.account_contacts still records who is in the meeting: account_id is null and the role is
-- `party`. That role is not an owner, and it is not in the KYC invite set, so these people are
-- not screened as account holders.
--
-- ADD VALUE IF NOT EXISTS matches 0155. The new member is not USED in this transaction.

ALTER TYPE crm.account_role ADD VALUE IF NOT EXISTS 'party';
COMMENT ON TYPE crm.account_role IS
  'owner / decision_maker / trustee / beneficiary / attorney / other / registered_contact '
  '(the adult on a JISA; KYC and portal invite target that role, not the child owner) / '
  'party (a journey participant with no account; not an owner and not KYC-relevant)';
