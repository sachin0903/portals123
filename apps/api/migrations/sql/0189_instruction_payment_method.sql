-- A withdrawal may pay by a different rail from the one saved on the bank account, and the
-- firm may pay the rail fee itself. `rail_fee` stays the provider's charge. `pass_rail_fee`
-- says whether that charge is reserved from the client's cash.

SET LOCAL lock_timeout = '5s';

ALTER TABLE platform.instructions
  ADD COLUMN payment_method text,
  ADD COLUMN pass_rail_fee boolean NOT NULL DEFAULT true,
  ADD CONSTRAINT instructions_payment_method_known
    CHECK (payment_method IS NULL OR payment_method IN ('bacs', 'faster_payment', 'swift')),
  ADD CONSTRAINT instructions_payment_method_withdrawal_only
    CHECK (payment_method IS NULL OR kind = 'withdrawal');

COMMENT ON COLUMN platform.instructions.payment_method IS
  'Rail this withdrawal pays over (bacs, faster_payment, swift). Null on older rows and on every other kind.';

COMMENT ON COLUMN platform.instructions.pass_rail_fee IS
  'When false the firm pays rail_fee and it is not reserved from the client cash.';

COMMENT ON COLUMN platform.instructions.rail_fee IS
  'Provider payment-rail charge. Reserved from the client only when pass_rail_fee is true.';
