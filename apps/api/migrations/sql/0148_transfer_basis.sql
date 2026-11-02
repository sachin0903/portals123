-- ============================================================================
-- 0148_transfer_basis.sql -- cash vs in-specie, and full vs partial, as columns.
--
-- `cam_typeoftransfer` (Cash / In Specie) and `cam_transfertype` (Full / Partial) have been
-- required by Morgan Lloyd since the push landed, and they are the same two facts Seccl asks
-- as `transactionSubType` and SEI as `transferTypeId`. They lived in `platform.transfers.data`
-- because 0051's `transfer_type` CHECK mixed wrappers (`pension`/`isa`/`gia`) with bases
-- (`cash`/`in_specie`), so the column could not hold both. The raise drawer never wrote `data`,
-- so a normally raised transfer could not be pushed: the adapter refused rather than default,
-- because sending Cash for an in-specie transfer instructs the ceding scheme to liquidate.
--
-- COLUMNS, not a second jsonb key. `transfer_type` stays the wrapper, still derived from the
-- account's product. `pension_type` stays in `data` — it is the ceding scheme's product type,
-- a 20-odd option set we do not hold.
-- ============================================================================

ALTER TABLE platform.transfers
  ADD COLUMN IF NOT EXISTS basis text,
  ADD COLUMN IF NOT EXISTS extent text;

ALTER TABLE platform.transfers
  DROP CONSTRAINT IF EXISTS transfers_basis_check,
  DROP CONSTRAINT IF EXISTS transfers_extent_check;
ALTER TABLE platform.transfers
  ADD CONSTRAINT transfers_basis_check
    CHECK (basis IS NULL OR basis IN ('cash', 'in_specie')),
  ADD CONSTRAINT transfers_extent_check
    CHECK (extent IS NULL OR extent IN ('full', 'partial'));

COMMENT ON COLUMN platform.transfers.basis IS
  'How the money moves: cash (the ceding scheme sells) or in_specie. Distinct from transfer_type, '
  'which is the receiving wrapper (pension/isa/gia). Required to instruct any custodian; never '
  'defaulted, because the two values mean opposite things and both are valid.';
COMMENT ON COLUMN platform.transfers.extent IS
  'How much of the ceding pot moves: full or partial. Seccl is full-only, so a Seccl-custodied '
  'account with no scheme administrator stores full without asking.';

-- Seccl DOES document transferStatus in its Postman collection (With Previous Provider /
-- Onboarding Assets / Completed / Rejected / Deleted). 0056's comment said otherwise. We still
-- do not parse it into our saga — mapping it would invent a crosswalk the rest of the lifecycle
-- does not run on — so the column stays verbatim display.
COMMENT ON COLUMN platform.transfers.ceding_status IS
  'The custodian''s own progress words, verbatim and UNPARSED. Seccl documents a transferStatus '
  'enum in its Postman collection; we still do not map it onto Graphene''s saga. Display-only.';

-- AS service_role: platform.transfers has FORCE ROW LEVEL SECURITY and the migrator is not
-- BYPASSRLS, so an UPDATE as migrator matches zero rows and reports success. 0059 / 0127 / 0140.
SET LOCAL ROLE service_role;

UPDATE platform.transfers
   SET basis = CASE
         WHEN lower(regexp_replace(btrim(data ->> 'basis'), '[\s-]+', '_', 'g'))
              IN ('cash', 'in_specie')
           THEN lower(regexp_replace(btrim(data ->> 'basis'), '[\s-]+', '_', 'g'))
         WHEN transfer_type IN ('cash', 'in_specie') THEN transfer_type
         ELSE basis
       END
 WHERE basis IS NULL
   AND (
         lower(regexp_replace(btrim(COALESCE(data ->> 'basis', '')), '[\s-]+', '_', 'g'))
           IN ('cash', 'in_specie')
         OR transfer_type IN ('cash', 'in_specie')
       );

UPDATE platform.transfers
   SET extent = lower(regexp_replace(btrim(data ->> 'extent'), '[\s-]+', '_', 'g'))
 WHERE extent IS NULL
   AND lower(regexp_replace(btrim(COALESCE(data ->> 'extent', '')), '[\s-]+', '_', 'g'))
       IN ('full', 'partial');

RESET ROLE;
