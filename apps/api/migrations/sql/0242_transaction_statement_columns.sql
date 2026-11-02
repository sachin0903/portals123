-- 0242_transaction_statement_columns -- the nine facts the conform step derives and the book threw away.
--
-- `platform.transactions` is the conformed fact table every statement surface reads. Nine columns
-- the provider layer already computes were dropped at the staging boundary (`book_sink`'s
-- `_TRANSACTIONS` selected `fee_amount` and nothing else of them), so a question with a real answer
-- upstream could not be asked downstream:
--
--   * "what did this trade actually cost?" -- the DEALING charge is what a client asks about, and it
--     was folded into one `fee_amount` alongside the platform's own monthly fee.
--   * "when did this dividend go ex?" -- Seccl publishes `information.eventExDate` and the conform
--     step lifts it into `trade_date`; the book had no column, so a dividend's ex-date reached no
--     surface at all.
--   * "what gain did this disposal realise?" -- proceeds minus book cost, which needs the book cost.
--   * "did this movement count toward the wrapper's subscription allowance?" -- the per-movement
--     amount. Remaining allowance lives on `platform.accounts.allowance_*`, filled by the
--     custodian; this column is the row's own contribution, not a figure to sum into headroom.
--
-- ── `report_group_kind` is the one that changes what can be BUILT ────────────────────────────────
--
-- A statement's headline breakdown -- payments in/out, transfers in/out, internal movement in/out,
-- charges, income -- is a bucketing of transaction codes, and Seccl PUBLISHES that bucketing as
-- reference data: `/static/transactiontype` carries `accountReportGroup` on all 77 codes, which is
-- the same grouping its own `/account/report` endpoint reports against. So the breakdown is derived
-- from the ledger with the vendor's own mapping rather than read back from a vendor report we would
-- then have to keep in step -- and rather than hand-written, which would have been wrong twice over
-- (the live list carries `Product Transfers Out`, absent from the vendor's own documented sample).
--
-- The canonical `platform.transaction_type` cannot answer it: twelve members deliberately coarser
-- than this, with no way to say "internal" -- a cash transfer between two of the same client's
-- accounts is `transfer_in`/`transfer_out` exactly like one from another provider, and those are
-- different lines of a statement. This column sits BESIDE `type` rather than replacing it: `type` is
-- ours and provider-agnostic (it is what `signed_amount` and the family filter turn on), this is the
-- custodian's own reporting vocabulary, and conflating them would make the canonical enum a
-- provider's.
--
-- TEXT, not an enum. The vocabulary is the vendor's and it grows: an unknown value must land and be
-- visible (the statement counts it under "other") rather than fail an insert, which is the same rule
-- the merge applies to an unmapped `type`. A provider that publishes no such grouping leaves it
-- NULL, which is honest -- SEI and Morgan Lloyd have no equivalent today.
--
-- Expand-only: nine nullable columns and one index. No backfill -- the next provider run
-- repopulates every row it owns, and a NULL in the meantime reads as "not yet resynced" rather than
-- as a wrong figure.
--
-- CONCURRENTLY is deliberately NOT used, following the repo convention set out in 0024: these
-- migrations run inside Alembic's transaction, where CONCURRENTLY is illegal. `lock_timeout` keeps
-- the short exclusive build from queueing behind a long transaction -- it fails fast and the deploy
-- retries instead of holding a lock on the table every provider run writes to.

SET LOCAL lock_timeout = '5s';

ALTER TABLE platform.transactions
  -- The custodian's own statement bucket for this movement. See the note above.
  ADD COLUMN IF NOT EXISTS report_group_kind text,
  -- The charge split. `fee_amount` stays the TOTAL (nothing that reads it changes); these two say
  -- what it was made of. Seccl splits its `charges[]` array by `chargeType = 'Dealing'`; a provider
  -- that publishes one undifferentiated figure leaves both NULL and only the total is stated.
  ADD COLUMN IF NOT EXISTS dealing_charges numeric(20,4),
  ADD COLUMN IF NOT EXISTS other_charges numeric(20,4),
  -- What KIND of charge, in the custodian's words ('Platform Fee', 'Adviser Fee', 'Dealing + Other
  -- Charge'). Text for the same reason as above: it is a vendor vocabulary.
  ADD COLUMN IF NOT EXISTS fee_type text,
  -- The cost basis of the units this movement disposed of or acquired.
  ADD COLUMN IF NOT EXISTS book_cost numeric(20,4),
  -- What a DISPOSAL realised: proceeds less the cost of the units that left. Stored rather than
  -- derived on read from `book_cost`, because the arithmetic is the PROVIDER's: Seccl computes it as
  -- `value - bookValue` and also publishes an `adjustedProfitLoss` for the cases where those two are
  -- not the whole story, and a read-time formula in this repo would quietly disagree with the
  -- custodian's own CGT reporting. NULL on anything that is not a disposal -- an acquisition realises
  -- nothing, which is different from realising zero.
  ADD COLUMN IF NOT EXISTS realised_gain numeric(20,4),
  -- When the entitlement was struck, as against when the money moved (`transaction_date`) or settled
  -- (`settlement_date`). §3's name is `trade_date` and this matches it so staging stays a column
  -- copy, but what it MEANS depends on the row: on an order it is the execution date, on a dividend
  -- it is the EX-DATE (Seccl's `information.eventExDate`), which is the date that decides whether a
  -- holder was entitled at all. The detail drawer labels it per row type for exactly that reason.
  ADD COLUMN IF NOT EXISTS trade_date date,
  -- The capital half of an accumulation-fund distribution. NOT income: an equalisation payment is a
  -- return of the capital embedded in the price paid, so it belongs in payments-in while the REST of
  -- the same transaction is income. One transaction, two statement lines -- see
  -- `services/seccl/models/platform/transactions.sql`, where the split is computed.
  ADD COLUMN IF NOT EXISTS equalisation_payment numeric(20,4),
  -- What this movement contributed to (or reclaimed from) the wrapper's annual subscription
  -- allowance. Seccl flags the codes that count with `subscription: true` and states the amount per
  -- transaction. Remaining allowance is `platform.accounts.allowance_*`, not a sum of these.
  ADD COLUMN IF NOT EXISTS subscription_amount numeric(20,4);

COMMENT ON COLUMN platform.transactions.report_group_kind IS
  'The custodian''s own statement bucket (Seccl: accountReportGroup off /static/transactiontype), '
  'resolved to one value per row. Beside `type`, never instead of it: `type` is ours and '
  'provider-agnostic, this is the vendor''s reporting vocabulary. NULL where a provider publishes none.';
COMMENT ON COLUMN platform.transactions.equalisation_payment IS
  'The capital (non-income) half of an accumulation-fund distribution. The income half is '
  '`amount` - this, which is why one such transaction appears on two lines of a statement.';
COMMENT ON COLUMN platform.transactions.subscription_amount IS
  'What this movement contributed to (or reclaimed from) the wrapper''s annual subscription '
  'allowance. A per-movement fact for the drawer; remaining allowance is platform.accounts.';

-- The statement's own access pattern: one account's movements bucketed by group. Partial, because a
-- row with no group contributes to no bucket and there is no reason to index the NULLs.
CREATE INDEX IF NOT EXISTS transactions_report_group_idx
  ON platform.transactions (account_id, report_group_kind)
  WHERE report_group_kind IS NOT NULL;
