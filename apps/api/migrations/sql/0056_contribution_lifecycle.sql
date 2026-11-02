-- ============================================================================
-- 0056_contribution_lifecycle.sql — the provider status mirrors, and the two terminal states
-- the pipeline could not reach.
--
-- 0051 landed the ledger; this closes the lifecycle docs/contributions.md §2 describes and the
-- workbench could not drive:
--
--   client sends money -> SEI transaction -> adviser categorises -> ML contribution
--   (Application) -> ML + HMRC (Active) -> ML (Received) -> SEI relief payment ->
--   reconciliation -> complete OR cancelled
--
-- Three gaps, each of which made a step unreachable rather than merely unpolished:
--
-- 1. **No provider status.** 0051 stores `ml_contribution_id` but not `cam_status`, and Morgan
--    Lloyd's `cam_updatecontributionapplication` is documented as valid ONLY while the record
--    is at Application. With nothing to gate on, the API could only send the call and let
--    Dynamics reject it -- which is a 502 that reads as an outage rather than a 409 that says
--    "this contribution has moved on". The mirror is the raw option-set INTEGER, not a
--    translated label: `morgan_lloyd.enums.ContributionStatus` is the vendor's vocabulary and
--    re-spelling it here would create a second one to keep in step.
--
-- 2. **No `cancelled`.** The status CHECK had eight pipeline stages and `manual_intervention`,
--    so an adviser who classified a credit as `not_a_contribution` had nowhere to put it: the
--    row stayed in the queue forever, or the classification was thrown away and the credit
--    resurfaced as unclassified on the next read. Both lose the adviser's decision, which is
--    the one thing an HMRC-reportable record must not do.
--
-- 3. **No match provenance.** `transactions.expectation_id` (wired by 0051) records THAT a
--    forecast was fulfilled, not whether a human or the matcher decided it. For money moving
--    on a client's pension that distinction is the difference between an automated tolerance
--    and someone's judgement, and only one of them is defensible in a file review.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

-- ============================================================================
-- platform.contributions — the Morgan Lloyd status mirror + the cancelled terminal.
-- ============================================================================

ALTER TABLE platform.contributions
  ADD COLUMN ml_status          int,
  ADD COLUMN ml_status_at       timestamptz,
  ADD COLUMN cancelled_at       timestamptz,
  ADD COLUMN cancelled_reason   text;

COMMENT ON COLUMN platform.contributions.ml_status IS
  'Morgan Lloyd cam_status on the cam_contribution record, mirrored verbatim as the option-set '
  'integer: 809130000 Application | 809130001 Active | 809130002 Stopped | 809130003 Received '
  '(morgan_lloyd.enums.ContributionStatus). NULL until the contribution is pushed. '
  'cam_updatecontributionapplication is only valid at Application, which app/integrations/'
  'morgan_lloyd/contributions.py gates on -- so this is a control, not a display field.';
COMMENT ON COLUMN platform.contributions.ml_status_at IS
  'When ml_status was last observed. A mirror with no read time cannot be told apart from a '
  'stale one, and the workbench has to say how fresh the provider answer is.';
COMMENT ON COLUMN platform.contributions.cancelled_reason IS
  'Why the contribution was cancelled, in the adviser''s words. Required by the CHECK below: a '
  'cancelled HMRC-reportable record with no reason is not an audit trail.';

-- The four values ML can hold. A CHECK rather than a lookup table: the option set is the
-- vendor''s, we do not own it, and a FK would imply we could add to it.
ALTER TABLE platform.contributions
  ADD CONSTRAINT contributions_ml_status_known CHECK (
    ml_status IS NULL OR ml_status IN (809130000, 809130001, 809130002, 809130003)
  );

-- Widen the pipeline. `cancelled` is terminal and off-pipeline like `manual_intervention`:
-- it is not stage 9, it is "this one stopped". The workbench excludes both from the pipeline
-- strip's counts for exactly that reason.
--
-- Drop-then-add rather than a second CHECK: two overlapping constraints on one column make the
-- failure message ambiguous, and the table is small enough that the revalidation is free.
ALTER TABLE platform.contributions
  DROP CONSTRAINT contributions_status_check;
ALTER TABLE platform.contributions
  ADD CONSTRAINT contributions_status_check CHECK (
    status IN ('identified', 'awaiting_confirmation', 'confirmed',
               'ml_recorded', 'ras_applied', 'ras_received',
               'tax_relief_received', 'reconciled',
               'manual_intervention', 'cancelled')
  );

-- A cancelled contribution carries its reason and its timestamp, and nothing else does. The
-- second half matters as much as the first: without it a row could be silently un-cancelled by
-- a status update that left cancelled_at pointing at a decision that was reversed.
ALTER TABLE platform.contributions
  ADD CONSTRAINT contributions_cancelled_explained CHECK (
    (status = 'cancelled') = (cancelled_at IS NOT NULL)
    AND (cancelled_at IS NULL OR cancelled_reason IS NOT NULL)
  );

-- The workbench's cancelled/terminal filter, and the "still open" count on the KPI strip.
CREATE INDEX contributions_ml_status_idx ON platform.contributions(tenant_id, ml_status)
  WHERE ml_status IS NOT NULL;

-- ============================================================================
-- platform.transfers — the same mirror, against Morgan Lloyd's OWN transfer vocabulary.
--
-- Worth stating plainly, because the workbench prototype assumed otherwise: ML's transfer
-- progress is a documented SIX-value option set (morgan_lloyd.enums.TransferStatus), not the
-- nine-stage cross-custodian saga the prototype rails render. That saga was written against
-- `0003_pensions_ledger.sql`, a schema that was superseded by 0051 and never existed. So the
-- mirror is ML's real set and `status` stays Graphene's own saga, derived from it.
--
--   0 Awaiting forms | 1 ML to request transfer | 2 Awaiting action from ceding scheme
--   3 Funds received | 4 Funds sent            | 5 No longer transferring
-- ============================================================================

ALTER TABLE platform.transfers
  ADD COLUMN ml_status      int,
  ADD COLUMN ml_status_at   timestamptz,
  ADD COLUMN ceding_status  text;

COMMENT ON COLUMN platform.transfers.ml_status IS
  'Morgan Lloyd transferstatus / cam_transferstatus, mirrored verbatim: 0 Awaiting forms | '
  '1 ML to request transfer | 2 Awaiting action from ceding scheme | 3 Funds received | '
  '4 Funds sent | 5 No longer transferring (morgan_lloyd.enums.TransferStatus). Distinct from '
  'the transfer record''s own cam_status, which is AssetStatus.';
COMMENT ON COLUMN platform.transfers.ceding_status IS
  'The custodian''s own progress words, verbatim and UNPARSED. Seccl publishes a transferStatus '
  'on its transaction feed but documents no value set -- it appears nowhere in the 8.5MB '
  'vendored Postman collection -- so encoding it as an enum would be inventing a vocabulary. '
  'Display-only until a real value set is observed.';

ALTER TABLE platform.transfers
  ADD CONSTRAINT transfers_ml_status_known CHECK (
    ml_status IS NULL OR ml_status BETWEEN 0 AND 5
  );

-- `funds_sent` is the one genuinely missing stage, and it is missing because ML distinguishes
-- it: 3 Funds received (the ceding scheme has paid ML) and 4 Funds sent (ML has passed it to
-- the custodian) are different weeks of chasing. Collapsing them, as the previous CHECK forced,
-- made a transfer look stalled while the money was already in motion.
--
-- Nothing else from the prototype's saga is added: `settlement_date_advised`, `ml_complete` and
-- `adviser_notified` have no provider fact behind them at any of the three custodians.
ALTER TABLE platform.transfers
  DROP CONSTRAINT transfers_status_check;
ALTER TABLE platform.transfers
  ADD CONSTRAINT transfers_status_check CHECK (
    status IN ('requested', 'submitted', 'in_progress', 'assets_received',
               'funds_sent', 'completed', 'cancelled', 'manual_intervention')
  );

-- ============================================================================
-- platform.transaction_expectations — who matched it.
--
-- NULL means the matcher did, which is the common case and the reason it is nullable rather
-- than defaulted: a sentinel "system" user id would make an automated tolerance look like a
-- person's decision in the audit trail.
-- ============================================================================

ALTER TABLE platform.transaction_expectations
  ADD COLUMN matched_by uuid REFERENCES auth.users(id),
  ADD COLUMN matched_transaction_id bigint
    REFERENCES platform.transactions(id) ON DELETE SET NULL;

COMMENT ON COLUMN platform.transaction_expectations.matched_by IS
  'The operator who matched this expectation by hand. NULL = the automatic matcher '
  '(app/matching.py) did it within tolerance. The distinction is the point: only one of the '
  'two is a human decision, and a file review has to be able to tell them apart.';
COMMENT ON COLUMN platform.transaction_expectations.matched_transaction_id IS
  'The realised credit this forecast was fulfilled by. Denormalised from '
  'platform.transactions.expectation_id, which stays the authoritative side -- this one exists '
  'so a break report can read the expectation and its match in one pass, and so unmatching '
  'leaves a trail rather than silently clearing a column on the fact table.';

-- ============================================================================
-- Nothing to do for RLS, grants, audit or updated_at: all four are attached to the TABLE by
-- 0051 (policies are row-level and column-agnostic; `audit.attach` triggers on the whole row;
-- the GRANTs are table-wide). A new column on an already-forced RLS table is covered the
-- moment it exists -- which `test_schema_invariants.py` re-derives from the catalogue anyway.
--
-- One thing does need saying: ml_status and cancelled_reason are NOT secret markers, so they
-- are deliberately absent from audit.record()'s redact list. A cancelled reason is evidence.
-- ============================================================================
