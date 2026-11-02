-- 0204_isa_inherit_gbp — an ISA/JISA with no currencies list is GBP, not the firm's whole list.
--
-- The catalogue Python path already defaulted ISA/JISA to GBP. The book-sink merge INSERT
-- omits allowed_currencies (NULL), and the trigger then copied auth.tenants.allowed_currencies.
-- A firm that allows EUR therefore failed the merge with `ISA products must allow GBP only`.

SET LOCAL lock_timeout = '5s';

CREATE OR REPLACE FUNCTION auth.validate_product_currencies() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, platform, auth, ref
AS $$
DECLARE
  tenant_currencies text[];
BEGIN
  IF NEW.allowed_currencies IS NULL THEN
    IF NEW.product_type IN ('ISA', 'JISA') THEN
      NEW.allowed_currencies := ARRAY['GBP']::text[];
    ELSE
      SELECT allowed_currencies
        INTO tenant_currencies
        FROM auth.tenants
       WHERE id = NEW.tenant_id;
      NEW.allowed_currencies := COALESCE(tenant_currencies, ARRAY['GBP']::text[]);
    END IF;
  END IF;

  IF NEW.product_type IN ('ISA', 'JISA')
     AND NEW.allowed_currencies IS DISTINCT FROM ARRAY['GBP']::text[] THEN
    RAISE EXCEPTION 'ISA products must allow GBP only' USING ERRCODE = '23514';
  END IF;

  IF cardinality(NEW.allowed_currencies) <> (
    SELECT count(DISTINCT code) FROM unnest(NEW.allowed_currencies) configured(code)
  ) THEN
    RAISE EXCEPTION 'allowed_currencies contains duplicate currency codes'
      USING ERRCODE = '23514';
  END IF;

  IF EXISTS (
    SELECT 1
      FROM unnest(NEW.allowed_currencies) configured(code)
      LEFT JOIN ref.currencies known ON known.code = configured.code
     WHERE known.code IS NULL
  ) THEN
    RAISE EXCEPTION 'allowed_currencies contains an unknown currency code'
      USING ERRCODE = '23514';
  END IF;

  IF NEW.tenant_id IS NOT NULL THEN
    SELECT allowed_currencies
      INTO tenant_currencies
      FROM auth.tenants
     WHERE id = NEW.tenant_id;
    IF EXISTS (
      SELECT 1
        FROM unnest(NEW.allowed_currencies) configured(code)
       WHERE NOT configured.code = ANY(tenant_currencies)
    ) THEN
      RAISE EXCEPTION 'product currencies must be allowed by its firm'
        USING ERRCODE = '23514';
    END IF;
  END IF;

  RETURN NEW;
END;
$$;
