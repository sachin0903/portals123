-- ============================================================================
-- 0113_uid_defaults.sql -- every `uid` column defaults to nanoid(12), and the merge stops
-- minting its own.
--
-- `0110_nanoid` installed the function; this is what makes it the answer. Two halves:
--
-- 1. A DEFAULT on every uid column. Setting a default is a catalogue change -- Postgres does not
--    rewrite the table and does not touch a single existing row -- so this is cheap on the big
--    ones and, deliberately, changes nothing about the rows already there. Old uids keep their
--    old shape (`app/ids.py`: existing rows are never rewritten; a document's bytes live at an
--    object-storage key containing its uid, and `audit.actions.path_params` holds historic ones).
--
--    This is NOT belt-and-braces for the application, which keeps minting through
--    `ids.new_id()` -- a value the application chose is one it can return and assert on, where a
--    value the database chose has to be read back. It is the floor under every OTHER writer: a
--    migration backfill, the provider pipeline writing as `book_writer`, an operator in psql. For
--    `messaging.{conversations,groups,attachments}` it is more than a floor -- none of the three
--    INSERT statements in `app/messaging.py` and `app/messaging_groups.py` names `uid` at all, so
--    the default is the only thing that fills it.
--
-- 2. `platform.merge_linked_entity` stops minting a uid. It was appending
--    `encode(gen_random_bytes(8), 'hex')` -- 16 hex characters, which is neither nanoid nor even
--    the right LENGTH -- so `platform.accounts.uid`, the column `0111` has just made NOT NULL,
--    got one shape from the portals and another from the nightly provider run. With a default in
--    place the procedure does not need to know the shape at all: it omits the column and the
--    database fills it. The `v_uid` probe goes with it (its only reader was the minting), and the
--    rest of the function is unchanged from `0070_merge_linked_exclude`.
--
-- `auth.tenants.uid` is the ONE column deliberately left with no default, for the reason
-- `app/ids.py::tenant_uid` spells out: it is the only uid that is not just a URL. It becomes the
-- firm's S3 bucket name and its `tenant_{uid}` / `book_{uid}` / `inbound_{uid}` SQL schema names,
-- where `-` and uppercase are illegal -- both of which nanoid's alphabet contains. A default here
-- would mint an unusable tenant on any INSERT that forgot to pass one, and the failure would
-- surface as a broken bucket rather than as a bad identifier. Provisioning owns that kind.
-- ============================================================================

-- ---- 1. The default, on every uid column but the tenant's -------------------
ALTER TABLE aml.hits                          ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE aml.profiles                      ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE aml.searches                      ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE auth.api_keys                     ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE auth.users                        ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE crm.applications                  ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE crm.contacts                      ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE crm.kyc_checks                    ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE documents.attachments             ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE documents.dataset_versions        ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE documents.datasets                ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE documents.documents               ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE documents.form_fields             ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE documents.form_responses          ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE documents.forms                   ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE documents.request_items           ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE documents.requests                ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE documents.shares                  ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE documents.template_versions       ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE documents.templates               ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE email.template_versions           ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE email.templates                   ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE flows.automations                 ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE flows.case_listeners              ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE flows.cases                       ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE flows.executions                  ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE flows.flow_versions               ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE flows.flows                       ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE flows.timers                      ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE messaging.attachments             ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE messaging.conversations           ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE messaging.groups                  ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE notifications.notifications       ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE notifications.rule_versions       ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE notifications.rules               ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE platform.accounts                 ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE platform.contributions            ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE platform.instructions             ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE platform.transaction_expectations ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE platform.transfers                ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE tasks.tasks                       ALTER COLUMN uid SET DEFAULT public.nanoid(12);
-- ---- 2. The merge no longer mints a uid ------------------------------------
-- Verbatim from 0070_merge_linked_exclude apart from the uid handling: the `v_uid` declaration and
-- probe are gone, and the INSERT's column and value lists lose the two slots that carried it.
CREATE OR REPLACE FUNCTION platform.merge_linked_entity(
  p_source      regclass,   -- the landing table: inbound.{uid}__{entity}, carrying external_id
  p_target      regclass,   -- the book table:    crm.contacts, platform.accounts, …
  p_entity_type text,       -- links.entity_type: 'contact' | 'account' | 'model' | 'instrument'
  p_tenant_id   int,
  p_provider    text,
  p_updatable   text[] DEFAULT ARRAY[]::text[],  -- entity columns the provider owns; {*} = all
  -- Columns to keep OUT of the entity, even though the source and target share them. One caller:
  -- crm.contacts.`data`. The projection has to stage `data` because that is where the LINK's
  -- payload comes from, and the payload belongs THERE and nowhere else -- one entity can hold links
  -- to two providers, and one column cannot hold two payloads. Left in, a client at both SEI and
  -- Morgan Lloyd would have this column rewritten nightly by whichever provider ran last, on a
  -- table that carries an audit trigger.
  --
  -- NOT applied to platform.{accounts,instruments,models}: each has its own `data` and
  -- app/materialise.py has always written the payload there, so excluding it would be a change of
  -- behaviour dressed as a fix. The rule is worth making uniform later; this closes the one place
  -- the two paths disagree.
  p_exclude     text[] DEFAULT ARRAY[]::text[]
)
RETURNS TABLE (inserted bigint, updated bigint)
LANGUAGE plpgsql
-- SECURITY INVOKER for the reason 0064 spells out: the definer context runs as the owner
-- (migrator), which FORCE ROW LEVEL SECURITY still binds, so every insert would be refused. Both
-- real callers (book_writer, service_role) carry BYPASSRLS themselves.
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_shared    text[];
  v_updatable text[];
  v_src       text;     -- the de-duplicated source, as a parenthesised subquery
  v_data      text;     -- the expression for links.data
  v_order     text := '';
  v_seq       text;
  v_ident     "char";
  v_set       text;
  v_sql       text;
  v_inserted  bigint;
  v_updated   bigint;
  -- The link's own identity, shared by every statement below. Written once so the update pass and
  -- the insert pass cannot disagree about what "already linked" means.
  -- The parameters are cast rather than left to inference: in an INSERT ... SELECT a bare $1 in
  -- the select list has no context to be inferred from and Postgres refuses the statement.
  v_match     text := 'l.tenant_id = $1::int AND l.provider = $2::text'
                      ' AND l.entity_type = $3::text AND l.link_type = ''primary''';
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_attribute
     WHERE attrelid = p_source AND attname = 'external_id' AND attnum > 0 AND NOT attisdropped
  ) THEN
    RAISE EXCEPTION 'merge_linked_entity: % publishes no external_id, so it has no identity',
      p_source;
  END IF;

  -- Shared columns in the TARGET's attnum order, never `id`: the surrogate key is Graphene's and
  -- a provider column of that name must not reach it.
  SELECT array_agg(t.attname ORDER BY t.attnum)
    INTO v_shared
    FROM pg_attribute t
    JOIN pg_attribute s
      ON s.attrelid = p_source AND s.attname = t.attname AND s.attnum > 0 AND NOT s.attisdropped
   WHERE t.attrelid = p_target AND t.attnum > 0 AND NOT t.attisdropped
     AND t.attname <> 'id'
     -- Excluded from the ENTITY only: `v_data` below reads the source's `data` for the link
     -- independently of this list, so the payload still lands where it belongs.
     AND NOT (t.attname = ANY (p_exclude));

  IF v_shared IS NULL THEN
    RAISE EXCEPTION 'merge_linked_entity: % and % share no columns', p_source, p_target;
  END IF;

  SELECT array_agg(c) INTO v_updatable
    FROM unnest(v_shared) AS c
   WHERE p_updatable = ARRAY['*'] OR c = ANY (p_updatable);

  -- The id is pre-allocated from the target's own sequence (see the insert below), which an
  -- IDENTITY column declared ALWAYS would refuse. Every merged target is BY DEFAULT; say so
  -- rather than failing three statements later on a generic error.
  SELECT a.attidentity INTO v_ident
    FROM pg_attribute a WHERE a.attrelid = p_target AND a.attname = 'id';
  IF v_ident = 'a' THEN
    RAISE EXCEPTION 'merge_linked_entity: %.id is GENERATED ALWAYS and cannot be pre-allocated',
      p_target;
  END IF;
  v_seq := pg_get_serial_sequence(p_target::text, 'id');
  IF v_seq IS NULL THEN
    RAISE EXCEPTION 'merge_linked_entity: % has no id sequence', p_target;
  END IF;

  -- links.data is the provider's verbatim payload. Cast to jsonb even when the source column
  -- already is: comparing as jsonb is what makes the IS DISTINCT FROM guard immune to a
  -- re-serialisation that changed key order and nothing else.
  v_data := CASE
    WHEN EXISTS (SELECT 1 FROM pg_attribute
                  WHERE attrelid = p_source AND attname = 'data'
                    AND attnum > 0 AND NOT attisdropped)
    THEN 'cast(s.data AS jsonb)'
    ELSE 'NULL::jsonb'
  END;

  -- One row per external_id. A provider publishing the same id twice is real (the conformed layer
  -- found 6,123 duplicate assets when `unique` checks were added), and inserting both would
  -- create two entities for one thing -- the exact failure links exist to prevent. When the
  -- source carries the conformed spine's source_updated_at, the survivor is the provider's latest
  -- statement rather than an arbitrary one.
  IF EXISTS (
    SELECT 1 FROM pg_attribute
     WHERE attrelid = p_source AND attname = 'source_updated_at'
       AND attnum > 0 AND NOT attisdropped
  ) THEN
    v_order := ', s.source_updated_at DESC NULLS LAST';
  END IF;

  -- external_id is cast to text because that is what links holds: a provider whose id is numeric
  -- would otherwise fail the join with "operator does not exist: text = bigint".
  v_src := format(
    '(SELECT DISTINCT ON (s.external_id) cast(s.external_id AS text) AS __ext, %s AS __data, %s'
    '   FROM %s s WHERE s.external_id IS NOT NULL'
    '  ORDER BY s.external_id%s)',
    v_data,
    -- Cast once, here, so every statement downstream reads already-typed columns -- and coalesced
    -- to the target column's own default where it is NOT NULL, which is what stops a provider's
    -- absent boolean from failing the whole entity (see the header).
    platform._merge_cast_list(p_target, v_shared, 's', true),
    p_source,
    v_order
  );

  -- ---- Update pass -------------------------------------------------------
  -- Two writes, one statement: the link's payload always, and the entity columns the provider
  -- owns when there are any. Counted as one update per external_id (UNION, not sum) because a
  -- provider changing a balance changes the payload that contains it -- summing would report
  -- every account twice.
  SELECT string_agg(format('%I = src.%I', c, c), ', ') INTO v_set
    FROM unnest(coalesce(v_updatable, ARRAY[]::text[])) AS c;

  v_sql := format(
    'WITH src AS %s, link_upd AS ('
    '  UPDATE platform.links l SET data = src.__data FROM src'
    '   WHERE %s AND l.external_id = src.__ext'
    '     AND l.data IS DISTINCT FROM src.__data'
    '  RETURNING src.__ext AS ext'
    ')%s SELECT count(*)::bigint FROM ('
    '  SELECT ext FROM link_upd%s) u',
    v_src,
    v_match,
    CASE WHEN v_set IS NULL THEN '' ELSE format(
      ', ent_upd AS ('
      '  UPDATE %s t SET %s FROM src JOIN platform.links l'
      '    ON %s AND l.external_id = src.__ext'
      '   WHERE t.id = l.entity_id AND (%s) IS DISTINCT FROM (%s)'
      '  RETURNING src.__ext AS ext'
      ')',
      p_target, v_set, v_match,
      (SELECT string_agg(format('t.%I', c), ', ') FROM unnest(v_updatable) AS c),
      (SELECT string_agg(format('src.%I', c), ', ') FROM unnest(v_updatable) AS c)
    ) END,
    CASE WHEN v_set IS NULL THEN '' ELSE ' UNION SELECT ext FROM ent_upd' END
  );
  EXECUTE v_sql USING p_tenant_id, p_provider, p_entity_type INTO v_updated;

  -- ---- Insert pass -------------------------------------------------------
  -- The entity and its link are inserted in ONE statement, so there is no window in which a row
  -- exists unlinked -- which the next run would see as unlinked and insert again.
  --
  -- The id is drawn from the target's sequence in `new` so both inserts can name it: an
  -- INSERT ... RETURNING cannot return a column it did not insert, and external_id is precisely
  -- the column we are refusing to put on the entity. `new` is MATERIALIZED for that reason and it
  -- is load-bearing -- inlined, nextval() would be evaluated once per reference and the link
  -- would point at an id the entity does not have.
  v_sql := format(
    'WITH src AS %s, new AS MATERIALIZED ('
    '  SELECT src.*, nextval(%L) AS __id FROM src'
    '   WHERE NOT EXISTS (SELECT 1 FROM platform.links l'
    '                      WHERE %s AND l.external_id = src.__ext)'
    '), ins AS ('
    '  INSERT INTO %s (id, %s) SELECT __id, %s FROM new RETURNING id'
    '), lnk AS ('
    '  INSERT INTO platform.links'
    '    (tenant_id, entity_type, entity_id, provider, link_type, external_id, data)'
    '  SELECT $1::int, $3::text, __id, $2::text, ''primary'', __ext, __data FROM new'
    '  RETURNING id'
    ') SELECT count(*)::bigint FROM ins',
    v_src,
    v_seq,
    v_match,
    p_target,
    (SELECT string_agg(quote_ident(c), ', ') FROM unnest(v_shared) AS c),
    (SELECT string_agg(format('new.%I', c), ', ') FROM unnest(v_shared) AS c)
  );
  EXECUTE v_sql USING p_tenant_id, p_provider, p_entity_type INTO v_inserted;

  RETURN QUERY SELECT v_inserted, v_updated;
END;
$$;
