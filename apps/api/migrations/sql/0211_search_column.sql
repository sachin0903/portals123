-- ============================================================================
-- 0211_search_column — one `_search` column per searchable table.
--
-- 0182 indexed `search.normalise(<fields>)` as an expression, so each table's searchable field
-- list lived twice: once in the index, once in every predicate in app/routers/search.py, and the
-- planner only used the index when the two lists matched exactly. `_search` stores that folded
-- text once, under the same name everywhere; the trigram GIN index and every predicate read it.
--
-- Still no search catalogue: the column sits on the source row, so its RLS policy is the one
-- that already guards the row. Long prose (task descriptions, message bodies, review notes)
-- keeps its `simple` full-text index from 0182 — trigrams are for names, codes and identifiers.
--
-- STORED, not VIRTUAL: PostgreSQL 18 cannot index a virtual generated column. Adding a stored
-- column rewrites each table under ACCESS EXCLUSIVE; lock_timeout makes the migration fail fast
-- instead of queueing traffic behind a long transaction. The indexes are built CONCURRENTLY by
-- the Alembic wrapper, which also drops the 0182 expression indexes this replaces.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

-- The number values only. `phone_numbers::text` is the whole jsonb document, so a list box
-- matching `_search` with LIKE would treat the keys and labels (`label`, `number`, `home`,
-- `work`) as hits and return every contact that has a phone of that shape.
CREATE OR REPLACE FUNCTION search.phone_text(numbers jsonb)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
RETURN (
  SELECT NULLIF(btrim(string_agg(COALESCE(elem ->> 'number', ''), ' ')), '')
    FROM jsonb_array_elements(COALESCE(numbers, '[]'::jsonb)) AS elem
);

GRANT EXECUTE ON FUNCTION search.phone_text(jsonb) TO authenticated, service_role;

COMMENT ON FUNCTION search.phone_text(jsonb) IS
  'The number values from crm.contacts.phone_numbers, joined for search.normalise. Labels and '
  'json keys are left out so a list search for "home" or "work" is not every contact with a phone.';

-- Not the legacy `phone`: 0165 moved its value into `phone_numbers` and left the column with no
-- reader or writer for one expand/contract release. A generated column that named it would make
-- the contract step's DROP COLUMN fail (or, with CASCADE, silently drop `_search`).
ALTER TABLE crm.contacts ADD COLUMN _search text GENERATED ALWAYS AS (
  search.normalise(
    uid, name, first_name, middle_name, surname, email,
    mobile_phone, search.phone_text(phone_numbers), national_identifier
  )
) STORED;

ALTER TABLE platform.accounts ADD COLUMN _search text GENERATED ALWAYS AS (
  search.normalise(uid, name, platform_identifier, currency)
) STORED;

ALTER TABLE platform.products ADD COLUMN _search text GENERATED ALWAYS AS (
  search.normalise(uid, code, name)
) STORED;

ALTER TABLE documents.documents ADD COLUMN _search text GENERATED ALWAYS AS (
  search.normalise(uid, name, document_type, source)
) STORED;

ALTER TABLE tasks.tasks ADD COLUMN _search text GENERATED ALWAYS AS (
  search.normalise(uid, title, category, subject_id, source_ref)
) STORED;

ALTER TABLE flows.executions ADD COLUMN _search text GENERATED ALWAYS AS (
  search.normalise(uid, subject_type, subject_id::text)
) STORED;

ALTER TABLE flows.flows ADD COLUMN _search text GENERATED ALWAYS AS (
  search.normalise(uid, code, name, description)
) STORED;

ALTER TABLE messaging.groups ADD COLUMN _search text GENERATED ALWAYS AS (
  search.normalise(uid, name)
) STORED;

ALTER TABLE reviews.requests ADD COLUMN _search text GENERATED ALWAYS AS (
  search.normalise(uid, case_no::text, action_key, event_type, subject_type, subject_id)
) STORED;

ALTER TABLE auth.tenants ADD COLUMN _search text GENERATED ALWAYS AS (
  search.normalise(uid, name, alias, website)
) STORED;

COMMENT ON COLUMN crm.contacts._search IS
  'Search text: search.normalise of the identity, name, email, mobile and other phone numbers '
  '(not their labels) and national identifier. Generated; trigram-indexed. Read by Spotlight '
  'and the list search boxes.';
COMMENT ON COLUMN platform.accounts._search IS
  'Spotlight search text: uid, name, platform identifier and currency. Generated; trigram-indexed.';
COMMENT ON COLUMN platform.products._search IS
  'Spotlight search text: uid, code and name. Generated; trigram-indexed.';
COMMENT ON COLUMN documents.documents._search IS
  'Spotlight search text: uid, name, document type and source. Generated; trigram-indexed.';
COMMENT ON COLUMN tasks.tasks._search IS
  'Spotlight search text: uid, title, category, subject and source reference. Generated; '
  'trigram-indexed. The description is full-text indexed separately (0182).';
COMMENT ON COLUMN flows.executions._search IS
  'Spotlight search text: uid and subject. Generated; trigram-indexed.';
COMMENT ON COLUMN flows.flows._search IS
  'Spotlight search text: uid, code, name and description. Generated; trigram-indexed.';
COMMENT ON COLUMN messaging.groups._search IS
  'Spotlight search text: uid and name. Generated; trigram-indexed. Message bodies are full-text '
  'indexed separately (0182).';
COMMENT ON COLUMN reviews.requests._search IS
  'Spotlight search text: uid, case number, action, event and subject. Generated; '
  'trigram-indexed. Justification and decision note are full-text indexed separately (0182).';
COMMENT ON COLUMN auth.tenants._search IS
  'Firm search text: uid, name, alias and website. Generated; trigram-indexed.';

-- ----------------------------------------------------------------------------
-- The audit trigger diffs to_jsonb(NEW) against to_jsonb(OLD) and logs every key that changed.
-- `_search` changes whenever any field it folds does, so every audited write would log a second
-- copy of the same edit. It joins the key list the trail already drops. Reissued from 0186 with
-- that one addition; the downgrade leaves it, since a key the row no longer has is never logged.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION audit.record() RETURNS trigger
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = 'audit', 'auth', 'pg_catalog' AS $fn$
DECLARE
  v_new     jsonb := CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END;
  v_old     jsonb := CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END;
  v_subject jsonb := COALESCE(v_new, v_old);
  -- never copy secrets / opaque pointers into the trail (key_hash is a hash, not the
  -- secret, but it stays out of logs on principle; credentials_ref points at a vault).
  v_redact  text[] := ARRAY['key_hash', 'credentials_ref', 'workos_api_key_ct', 'cookie_password_ct', 'secret_access_key_ct', 'password_ct', 'secret_ct',
    -- derived, not data: 0211's generated search text restates columns already in the row.
    '_search'];
  v_entity  bigint;
  v_tenant  int;
  v_detail  jsonb;
  k         text;
BEGIN
  -- entity_id only when the PK is integer-shaped; uuid/composite PKs live in detail.
  BEGIN v_entity := (v_subject ->> 'id')::bigint; EXCEPTION WHEN others THEN v_entity := NULL; END;

  -- Stamp the AFFECTED row's tenant (correct for cross-tenant admin writes); fall back
  -- to the actor's tenant for tables that carry no tenant_id (e.g. auth.*), which now also
  -- covers an unattributed aml.* row whose tenant_id is NULL.
  BEGIN
    v_tenant := COALESCE(NULLIF(v_subject ->> 'tenant_id', '')::int, auth.tenant_id());
  EXCEPTION WHEN others THEN v_tenant := auth.tenant_id();
  END;

  v_new := v_new - v_redact;
  v_old := v_old - v_redact;

  IF TG_OP = 'UPDATE' THEN
    v_detail := '{}'::jsonb;   -- only the columns that actually changed (redacted ones excluded)
    FOR k IN SELECT jsonb_object_keys(v_new) LOOP
      IF v_new -> k IS DISTINCT FROM v_old -> k THEN
        v_detail := v_detail || jsonb_build_object(k, jsonb_build_object('old', v_old -> k, 'new', v_new -> k));
      END IF;
    END LOOP;
    v_detail := jsonb_build_object('id', v_subject -> 'id', 'changed', v_detail);
  ELSIF TG_OP = 'INSERT' THEN
    v_detail := jsonb_build_object('id', v_subject -> 'id', 'row', v_new);
  ELSE
    v_detail := jsonb_build_object('id', v_subject -> 'id', 'row', v_old);
  END IF;

  INSERT INTO audit.log (tenant_id, user_id, action, entity_type, entity_id, detail)
  VALUES (
    v_tenant,
    auth.uid(),
    TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME || '.' || lower(TG_OP),
    TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME,
    v_entity,
    v_detail
  );
  RETURN NULL;  -- AFTER trigger: return value is ignored
END;
$fn$;
