-- ============================================================================
-- 0133_account_money_facts.sql -- where money leaves from, and how much allowance is left.
--
-- Two additions to `platform.accounts`, both jsonb-or-columns on the existing table rather than
-- new tables, and both for facts the estate holds nowhere while surfaces already read them.
--
-- ---------------------------------------------------------------------------
-- 1. BANK DETAILS, on the account, with the client as the fallback.
-- ---------------------------------------------------------------------------
--
-- `crm.contacts.bank_account` has existed since 0002 — validated jsonb (`{name, bank, sort_code,
-- account_number}`, sort code normalised to `12-34-56`, account left-padded to 8) captured by
-- onboarding — and it **reaches no vendor**. A grep across `app/integrations/` finds no bank field
-- in any payload we send. Meanwhile SEI's `create_cash_recipient` drawer, the one place a sort code
-- is reachable, is free text typed by hand on a form marked "A pay-to recipient is where money can
-- be sent."
--
-- THE ACCOUNT IS THE RIGHT LEVEL, and the two custodians sit on opposite sides of it — which is
-- why account-with-client-fallback is the only rule that fits both:
--
--   SEI    per ACCOUNT. 682 `PaidTo` profiles over 283 accounts (124 with one, 151 with two, and a
--          house account with 213). 439 distinct (sort code, account) pairs, so 63 bank accounts
--          are shared across two or more accounts and one across ten. Methods in use: CHAPS 373,
--          BACS 307, SWIFT 2.
--   Seccl  per CLIENT. `clients.bankDetails` populated on 10/10 in the committed extract;
--          `accounts.bankDetails` on 0/33. A withdrawal body names no destination at all — it is
--          implicitly the client's verified account.
--
-- AN ARRAY, and `[0]` IS THE DEFAULT. SEI is the only provider needing more than one, so the
-- drawer shows the first and renders a picker only when `jsonb_array_length > 1`. Everything else
-- writes a single-element array.
--
-- WHY NOT A TABLE. The relationship is one-to-few and nothing queries INTO it — every reader wants
-- the account's list whole, alongside the account. `platform.accounts` already stores
-- `payments`, `withdrawals`, `fees` and `transfer` exactly this way, so a join table here would be
-- a second idiom for the same job. The vendor's own profile id lives in the object as
-- `provider_ref`, which is what `payerInfo.payerId` and `payToDetails` take — and keeping it there
-- rather than on `platform.links` is what removes the second table.
--
-- THE SHAPE, validated by `app/contacts.py::normalise_bank_account` per element (one normaliser,
-- not two):
--
--   { "name": "Mr E Kennedy", "bank": "Barclays",
--     "sort_code": "20-45-67", "account_number": "00123456", "iban": null,
--     "method": "bacs",              -- bacs | chaps | faster_payment | direct_debit | cheque | swift
--     "direction": "pay_to",         -- pay_to (out) | payer (in); SEI has a service for each
--     "reference": "MSP000762",
--     "mandate_accepted_on": "2026-01-14",
--     "provider": "sei", "provider_ref": "59220399",
--     "verification_status": "Active" }
--
-- `method` is a real property, not decoration: SEI's `PAY_TO_METHOD` has nine members and three
-- are in live use, Seccl's account `paymentMethods` is `Bank Transfer | Direct Debit`, and its
-- pay-out rail is `BACS Credit`. Direct Debit is a CLIENT-level mandate at Seccl and a
-- per-assignment rail at SEI, which is why `mandate_accepted_on` sits on the element rather than
-- on the account. `verification_status` is a passthrough — Seccl publishes it and says a
-- withdrawal to a Pending account "will go to checking" — with no column, no validation and
-- nothing branching on it.
--
-- ---------------------------------------------------------------------------
-- 2. THE ALLOWANCE, which two components render and nothing feeds.
-- ---------------------------------------------------------------------------
--
-- `Wrapper.allowanceUsed` / `allowanceLimit` are rendered by `wrapper-card.svelte` and
-- `account-stack.svelte` and supplied ONLY by `apps/client/src/lib/demo.ts`. Both custodians hold
-- the real figure: SEI on `Accounts.ISA_CONTRIBUTION_TO_DATE` (100% populated) and
-- `Clients.TOTAL_REMAINING_ISA_HEADROOM_AMOUNT` (58%), Seccl on the `clientProduct`'s remaining
-- allowance.
--
-- **THE READER'S RULE, and the reason this needed thinking about: max() per (contact, wrapper),
-- never sum().** The allowance is a `(client, wrapper)` fact, not an account fact. SEI states it
-- per account; Seccl states it once per `clientProduct`, and a client with two ISAs SHARES one —
-- verified: 60 of 921 client x wrapper pairs in the SEI drop hold more than one account (44 with
-- two, 12 with three, 4 with four), and at Seccl 10 clients hold 33 accounts under 10 client
-- products. So the column repeats the figure across an account group, and summing it would tell a
-- client they had used twice the allowance they have.
--
-- No `platform.client_products` table: the container derives. `GROUP BY contact_id, product_id`
-- on `platform.accounts` IS the client product, and nothing else on the vendor's record has a
-- consumer — declaration dates, nominees and LTA/LSA figures stay reachable in `links.data`.
-- ============================================================================

ALTER TABLE platform.accounts
  ADD COLUMN IF NOT EXISTS bank_accounts      jsonb,
  ADD COLUMN IF NOT EXISTS allowance_used     numeric(20, 4),
  ADD COLUMN IF NOT EXISTS allowance_limit    numeric(20, 4),
  ADD COLUMN IF NOT EXISTS allowance_tax_year text;

-- An ARRAY or nothing. A bare object would read as one bank account and then fail every
-- `->> 0` in the resolution helper, which is the shape of a bug nobody notices until a payment.
ALTER TABLE platform.accounts
  ADD CONSTRAINT accounts_bank_accounts_is_array
  CHECK (bank_accounts IS NULL OR jsonb_typeof(bank_accounts) = 'array');

COMMENT ON COLUMN platform.accounts.bank_accounts IS
  'Bank details for this account, as an ARRAY — [0] is the default and a picker appears only when '
  'there is more than one (SEI holds two on 151 of 283 accounts). Falls back to '
  'crm.contacts.bank_account, which is where Seccl and onboarding put it. Each element carries '
  'method (the rail), direction (pay_to | payer) and the vendor''s own provider_ref, which is what '
  'payerInfo.payerId takes. Validated per element by contacts.normalise_bank_account.';
COMMENT ON COLUMN platform.accounts.allowance_used IS
  'Subscription used this tax year. A (client, wrapper) fact repeated onto each of the group''s '
  'accounts — readers take max() per (contact, product), NEVER sum(): a client with two ISAs '
  'shares one allowance, and summing would report double what they have used.';
COMMENT ON COLUMN platform.accounts.allowance_limit IS
  'The wrapper''s annual limit as the custodian states it. Same max()-not-sum() rule.';
-- WRITTEN AS NULL BY EVERY PROVIDER, AND THAT IS THE STATE OF THE FACT rather than an omission.
-- SEI states no tax year on the subscription figures and deriving one from the drop date would be
-- a guess about which year they cover (`services/sei/models/platform/accounts.sql` says so at the
-- column); Seccl states one per `clientProduct` and none per account. So the column is declared,
-- published as a typed NULL by both conformed models — which the sink's projection requires, since
-- a column it reads and a provider does not publish aborts that whole tenant — and read by
-- nothing.
--
-- Kept rather than dropped, because the ABSENCE is what the reader rule above rests on: an
-- allowance is a per-tax-year quantity, and `max()` per (contact, wrapper) is the right answer
-- ONLY while nothing can tell one year from another. A reader that gains a year has to change that
-- rule, and it should find the column here saying so rather than conclude the year was never
-- modelled.
COMMENT ON COLUMN platform.accounts.allowance_tax_year IS
  'Which tax year allowance_used/allowance_limit cover. NULL on every row: SEI states no year and '
  'Seccl states one per clientProduct, so neither conformed model can fill it. Read by nothing — '
  'and the max()-not-sum() rule on the two figures above is correct only because of that.';
