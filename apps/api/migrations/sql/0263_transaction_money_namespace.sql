-- ============================================================================
-- 0263_transaction_money_namespace.sql -- move the one-off money actions into the transaction.*
-- namespace, and fold contribution.lodge into transaction.payment.
--
--   payment.one_off   -> transaction.payment      (now also the contribution money-in face)
--   withdrawal.one_off-> transaction.withdrawal
--   fee.adhoc         -> transaction.fee
--   transfer.create   -> transaction.transfer
--   contribution.lodge-> policy folds into transaction.payment; the key STAYS live as the internal
--                        lodge leaf that transaction.payment calls for a contribution source, so its
--                        own run/subject rows keep their key.
--
-- The action_key is the firm-policy identity (`actions.policies`, unique on
-- (tenant_id, action_key, provider)) and the join key for baton's runs/subjects (schema `baton`).
-- The firm-visible name is snapshotted on each run, so run history keeps its labels. baton.events
-- and baton.attempts carry no action_key, so they are untouched.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

-- LOAD-BEARING, not tidiness. actions.policies and baton.runs/subjects FORCE row-level security, and
-- the migrator is subject to it, so a bare UPDATE silently matches zero rows. service_role has
-- BYPASSRLS, and the migrator is a member of it; the role is reset at transaction end.
SET LOCAL ROLE service_role;

-- 1. Firm policy. Fold payment.one_off AND contribution.lodge onto transaction.payment. A firm that
--    configured BOTH keeps its payment.one_off policy -- drop the duplicate contribution.lodge row
--    first (matched on the same provider, NULLS NOT DISTINCT like the unique constraint) so the
--    rename cannot collide.
DELETE FROM actions.policies p
 WHERE p.action_key = 'contribution.lodge'
   AND EXISTS (
     SELECT 1
       FROM actions.policies q
      WHERE q.tenant_id = p.tenant_id
        AND q.action_key = 'payment.one_off'
        AND q.provider IS NOT DISTINCT FROM p.provider
   );

UPDATE actions.policies SET action_key = 'transaction.payment'
 WHERE action_key IN ('payment.one_off', 'contribution.lodge');
UPDATE actions.policies SET action_key = 'transaction.withdrawal'
 WHERE action_key = 'withdrawal.one_off';
UPDATE actions.policies SET action_key = 'transaction.fee'
 WHERE action_key = 'fee.adhoc';
UPDATE actions.policies SET action_key = 'transaction.transfer'
 WHERE action_key = 'transfer.create';

-- 2. Baton runs + subjects join key. contribution.lodge rows KEEP their key (still a live internal
--    leaf); only the four renamed keys move.
UPDATE baton.runs SET action_key = 'transaction.payment'    WHERE action_key = 'payment.one_off';
UPDATE baton.runs SET action_key = 'transaction.withdrawal' WHERE action_key = 'withdrawal.one_off';
UPDATE baton.runs SET action_key = 'transaction.fee'        WHERE action_key = 'fee.adhoc';
UPDATE baton.runs SET action_key = 'transaction.transfer'   WHERE action_key = 'transfer.create';

UPDATE baton.subjects SET action_key = 'transaction.payment'    WHERE action_key = 'payment.one_off';
UPDATE baton.subjects SET action_key = 'transaction.withdrawal' WHERE action_key = 'withdrawal.one_off';
UPDATE baton.subjects SET action_key = 'transaction.fee'        WHERE action_key = 'fee.adhoc';
UPDATE baton.subjects SET action_key = 'transaction.transfer'   WHERE action_key = 'transfer.create';

-- Hand the role back. SET LOCAL ROLE lasts the whole transaction, and alembic stamps alembic_version
-- in this same transaction -- as the current role, and service_role has no privilege on it. Without
-- this the data lands and the migration then dies on the stamp (see 0161 for the same trap).
RESET ROLE;
