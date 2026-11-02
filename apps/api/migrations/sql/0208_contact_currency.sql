-- A client's base currency. Accounts already carry theirs (`platform.accounts.currency`,
-- from ACCOUNT_BASE_CURRENCY / Seccl's account currency). The person did not, so a USD
-- client was labelled sterling wherever the book summed or printed them.
--
-- Seccl and Morgan Lloyd publish GBP. SEI publishes CLIENT_BASE_CURRENCY. The column
-- default covers a client the custodian has not denominated, and the backfill copies an
-- account currency only when every one of that client's accounts agrees — a mixed book
-- stays GBP until the next provider sync writes the client's own code.

SET LOCAL lock_timeout = '5s';

ALTER TABLE crm.contacts
  ADD COLUMN currency text NOT NULL DEFAULT 'GBP'
    REFERENCES ref.currencies(code);

COMMENT ON COLUMN crm.contacts.currency IS
  'The client''s base currency (ISO 4217). Seccl and Morgan Lloyd publish GBP; SEI publishes CLIENT_BASE_CURRENCY.';

-- crm.contacts is FORCE-RLS. The migrator is not BYPASSRLS, so this UPDATE would
-- report success and change nothing without service_role (0153's precedent).
SET LOCAL ROLE service_role;
UPDATE crm.contacts AS c
   SET currency = sub.currency
  FROM (
    SELECT contact_id, min(upper(currency)) AS currency
      FROM platform.accounts
     WHERE contact_id IS NOT NULL
       AND nullif(currency, '') IS NOT NULL
       AND upper(currency) IN (SELECT code FROM ref.currencies)
     GROUP BY contact_id
    HAVING count(DISTINCT upper(currency)) = 1
  ) AS sub
 WHERE c.id = sub.contact_id
   AND sub.currency IS DISTINCT FROM c.currency;
RESET ROLE;

-- A custodian code the seed does not list must not fail the contact upsert. The book
-- pipeline (book_writer) and service_role store GBP, which is what the backfill did.
-- Any other role still hits the foreign key, so an application write of a typo is refused.
-- book_writer bypasses RLS but still needs schema usage and the table privilege
-- to see the seed. USAGE does not grant the other ref tables.
GRANT USAGE ON SCHEMA ref TO book_writer;
GRANT SELECT ON ref.currencies TO book_writer;

CREATE OR REPLACE FUNCTION crm.coerce_unknown_contact_currency()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
  IF NEW.currency IS NOT NULL AND EXISTS (
    SELECT 1 FROM ref.currencies WHERE code = NEW.currency
  ) THEN
    RETURN NEW;
  END IF;
  IF current_user IN ('book_writer', 'service_role') THEN
    NEW.currency := 'GBP';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER contacts_currency_known
  BEFORE INSERT OR UPDATE OF currency ON crm.contacts
  FOR EACH ROW
  EXECUTE FUNCTION crm.coerce_unknown_contact_currency();
