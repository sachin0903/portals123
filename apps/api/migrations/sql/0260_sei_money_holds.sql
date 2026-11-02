-- 0260_sei_money_holds — a SEI payment that may already have left, remembered
-- outside the request that sent it.
--
-- The booking inserts the pending row in the request transaction. That
-- transaction still holds the unique key, so another connection cannot copy
-- the row until the request ends. Claiming the flag on the request itself
-- rolls back with it, and the retry mints a new instruction uid and pays
-- again. This row is committed before the money call. A later instruction of
-- the same kind on the account is refused until SEI has definitely refused,
-- or until the held instruction is submitted.

SET LOCAL lock_timeout = '5s';

CREATE TABLE platform.sei_money_holds (
  tenant_id int NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
  -- Both keys, as 0035 requires: the plain one declares the link, the
  -- composite one keeps it inside this firm.
  account_id int NOT NULL REFERENCES platform.accounts(id) ON DELETE CASCADE,
  kind text NOT NULL,
  origin_uid text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, account_id, kind)
);

COMMENT ON TABLE platform.sei_money_holds IS
  'One SEI money-out in flight for an account and a kind. Committed before the '
  'provider call, so a rolled-back request cannot send the payment again.';

-- The account link has to name this firm's account. RI checks bypass RLS.
ALTER TABLE platform.sei_money_holds
  ADD CONSTRAINT sei_money_holds_account_fkey
  FOREIGN KEY (tenant_id, account_id)
  REFERENCES platform.accounts (tenant_id, id)
  ON DELETE CASCADE;

ALTER TABLE platform.sei_money_holds ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.sei_money_holds FORCE ROW LEVEL SECURITY;

-- Written only on the service connection that talks to SEI. A firm session
-- never reads it: the refusal is the action error, not a row on the book.
GRANT SELECT, INSERT, UPDATE, DELETE ON platform.sei_money_holds TO service_role;
