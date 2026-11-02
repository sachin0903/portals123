-- ============================================================================
-- 0277_integrations_items_ledger -- an attempt marker on the ledger of income statement items posted
-- to Intelliflo, so a batch whose outcome is unknown is never blindly re-sent.
--
-- WHY. Intelliflo accepts exact duplicate income statement items, has no delete, and gives items no
-- reference to look up. A re-sent item therefore double-counts in the statement total for good. The
-- sync writes a row as `attempted` BEFORE it posts a batch and flips it to `confirmed` once IO answers
-- success. A row left `attempted` means "the outcome is not known" (a 500 after the items were stored,
-- a timeout, a crash); the next run compares those rows with what IO holds (by createdAt day and
-- content) before it sends anything. A batch IO only partly stored is parked as `held`, and is
-- resolved by hand (intelliflo-items-review) because nothing can safely decide it automatically.
--
-- Every row that existed before this migration was written after a success, hence the `confirmed`
-- default.
--
-- `fingerprint` is the item's content as IO would report it (policy number, type, gross, net), so the
-- settle step can match without a reference. `batch_id` groups the items of one POST.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

ALTER TABLE integrations.income_statement_items_sent
    ADD COLUMN IF NOT EXISTS status       text        NOT NULL DEFAULT 'confirmed',
    ADD COLUMN IF NOT EXISTS attempted_at timestamptz,
    ADD COLUMN IF NOT EXISTS batch_id     text,
    ADD COLUMN IF NOT EXISTS fingerprint  text;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'integrations.income_statement_items_sent'::regclass
          AND conname = 'income_statement_items_sent_status_check'
    ) THEN
        ALTER TABLE integrations.income_statement_items_sent
            ADD CONSTRAINT income_statement_items_sent_status_check
            CHECK (status IN ('attempted', 'confirmed', 'held'));
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        -- The marker is written, flipped and (on a 4xx) cleared, so UPDATE and DELETE join SELECT/INSERT.
        GRANT SELECT, INSERT, UPDATE, DELETE ON integrations.income_statement_items_sent TO service_role;
    END IF;
END $$;
