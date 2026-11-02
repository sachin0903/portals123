-- ============================================================================
-- 0276_integrations_txn_ledger -- the ledger of transactions posted to Intelliflo, with an
-- attempt marker so a batch whose outcome is unknown can be told apart from one that was confirmed.
--
-- WHY THIS CREATES THE TABLE AND NOT JUST A COLUMN. integrations.transactions_sent was created on
-- the dev database by an earlier local migration (014_transactions_sent) and by no migration in
-- this repo, so a freshly migrated database (CI, production) did not have it and the Transactions
-- feature was blocked by the schema contract. Both cases end in the same state: the table is created
-- where it is missing, and the new columns are added where it already exists.
--
-- THE TWO STATES. The sync writes a row as `attempted` BEFORE it posts a batch and flips it to
-- `confirmed` once Intelliflo answers success, so a row left `attempted` means "the outcome is not
-- known" (a 500 after the rows were stored, a timeout, a crash). Only those rows are ever looked up
-- in Intelliflo. Every row that existed before this migration was written after a success, hence the
-- `confirmed` default.
--
-- SECURITY. Engine bookkeeping, like income_statement_items_sent: a plain table owned by service_role,
-- no row-level security (nothing in the admin API reads it).
-- ============================================================================

SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS integrations.transactions_sent (
    tenant_id text        NOT NULL,
    item_key  text        NOT NULL,
    sent_at   timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tenant_id, item_key)
);

ALTER TABLE integrations.transactions_sent
    ADD COLUMN IF NOT EXISTS status       text        NOT NULL DEFAULT 'confirmed',
    ADD COLUMN IF NOT EXISTS attempted_at timestamptz;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'integrations.transactions_sent'::regclass
          AND conname = 'transactions_sent_status_check'
    ) THEN
        ALTER TABLE integrations.transactions_sent
            ADD CONSTRAINT transactions_sent_status_check CHECK (status IN ('attempted', 'confirmed'));
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON integrations.transactions_sent TO service_role;
        IF pg_has_role(current_user, 'service_role', 'member') THEN
            ALTER TABLE integrations.transactions_sent OWNER TO service_role;
        END IF;
    END IF;
END $$;

-- The legacy bookkeeping id of the migration that first created the table, so a database that
-- already has it recorded and one that gets it here read the same.
INSERT INTO integrations.schema_migrations (migration_id)
VALUES ('014_transactions_sent.sql')
ON CONFLICT (migration_id) DO NOTHING;
