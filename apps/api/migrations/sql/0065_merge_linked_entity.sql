-- ============================================================================
-- 0065_merge_linked_entity -- the merge for entities whose identity is a LINK, not a key.
--
-- 0064's platform.merge_entity() needs the target to have a natural key it can name in
-- ON CONFLICT. Three of the merged relations do:
--
--   platform.orgs          (tenant_id, provider, org_key)
--   platform.valuations    (account_id, as_of_date, source_system)
--   platform.transactions  (source_system, source_transaction_id)
--
-- The four that carry an identity of their own do NOT, and must not be given one:
-- crm.contacts, platform.accounts, platform.models, platform.instruments. Denormalising
-- `provider` + `external_id` onto the row with a unique constraint was considered and is wrong --
-- one contact legitimately exists at BOTH SEI and Morgan Lloyd, which is the planned shape, and a
-- per-provider unique key forces one row per provider, i.e. two contacts for one person.
-- platform.links (0003) is the polymorphic bridge built for exactly this: the provider payload
-- lives on links.data, so one entity holds a link -- and a payload -- per provider.
--
-- So identity here is resolved, never assumed: (tenant_id, provider, entity_type, external_id)
-- -> links.entity_id. A source row whose link exists updates; a source row with no link creates
-- the entity AND its link, in one statement, which is what makes this idempotent.
--
-- WHAT THIS MEANS FOR THE POLICY. For a linked entity, "only the provider's own data is
-- refreshed" means links.data is refreshed -- the entity's typed columns are Graphene's from
-- birth, set once from what the provider first published. p_updatable therefore defaults to EMPTY
-- here, where merge_entity defaults to {data}; its only member in practice is the
-- platform.accounts.value exception (the balance is driven from the provider).
--
-- WHAT IT DELETES. app/materialise.py's commonest silent failure was an unresolvable parent
-- counted as a reject -- "unknown account", "unavailable" -- which reads exactly like "this firm
-- has no data here". Insert-if-no-link cannot fail that way: a first sighting creates the row and
-- the link together.
--
-- AN ENTITY WITH A GLOBAL IDENTITY IS ADOPTED, NOT DUPLICATED, and that stays the caller's job:
-- platform.instruments is global and deduped by ISIN, so two tenants seeing the same ISIN must
-- reach the same row. The orchestrator pre-mints a link to the existing instrument before calling
-- this, and the NOT EXISTS below then correctly declines to insert a second one. Keeping the rule
-- there rather than parameterising it here is deliberate: it is one entity's rule, and this
-- function's only notion of identity is the link.
--
-- NOT ENFORCED YET, deliberately: nothing constrains platform.links to one entity per
-- (tenant_id, provider, entity_type, external_id). That IS the invariant, and a unique index is
-- the right home for it -- but this migration cannot assert it without a chance of failing on
-- existing rows it has not seen, and nothing here creates a violation (the insert reads the link
-- before writing). Logged as hygiene, with the index below carrying the read.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- The cast list both merges use.
--
-- Not cosmetic: the landing tables in `inbound` arrive over interlace's Arrow path, so a column
-- the book declares as an enum, a numeric(20,4) or a jsonb arrives as text. Postgres will not
-- assignment-cast text into platform.account_status -- "column status is of type
-- platform.account_status but expression is of type text" -- so an un-cast INSERT fails on the
-- first typed column of the first provider that lands. Casting to the TARGET's own type also
-- means a value the provider cannot honestly produce raises here, rather than arriving as a NULL
-- that later looks like data.
-- ---------------------------------------------------------------------------
CREATE FUNCTION platform._merge_cast_list(
  p_target regclass,
  p_cols   text[],
  p_alias  text
) RETURNS text
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
  SELECT string_agg(
           format('cast(%I.%I AS %s)', p_alias, a.attname,
                  format_type(a.atttypid, a.atttypmod)),
           ', ' ORDER BY a.attnum)
    FROM unnest(p_cols) AS u(name)
    JOIN pg_attribute a
      ON a.attrelid = p_target AND a.attname = u.name AND a.attnum > 0 AND NOT a.attisdropped
$$;

COMMENT ON FUNCTION platform._merge_cast_list(regclass, text[], text) IS
  'Internal: the SELECT list for a merge, each column cast to the target column''s own type.';

-- ---------------------------------------------------------------------------
-- merge_entity, replaced: the same policy, now casting its SELECT list (above) and never taking
-- the surrogate `id` from a provider. Both are corrections to 0064 rather than changes of
-- behaviour -- its tests pass either way, because a TEMP table in a test can declare the enum
-- column the real landing table cannot.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION platform.merge_entity(
  p_source    regclass,
  p_target    regclass,
  p_conflict  text[],
  p_updatable text[] DEFAULT ARRAY['data']
)
RETURNS TABLE (inserted bigint, updated bigint)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_shared text[];
  v_set    text;
  v_sql    text;
BEGIN
  SELECT array_agg(t.attname ORDER BY t.attnum)
    INTO v_shared
    FROM pg_attribute t
    JOIN pg_attribute s
      ON s.attrelid = p_source AND s.attname = t.attname AND s.attnum > 0 AND NOT s.attisdropped
   WHERE t.attrelid = p_target AND t.attnum > 0 AND NOT t.attisdropped
     AND t.attname <> 'id';

  IF v_shared IS NULL THEN
    RAISE EXCEPTION 'merge_entity: % and % share no columns', p_source, p_target;
  END IF;

  SELECT string_agg(format('%I = EXCLUDED.%I', c, c), ', ')
    INTO v_set
    FROM unnest(v_shared) AS c
   WHERE NOT (c = ANY (p_conflict))
     AND (p_updatable = ARRAY['*'] OR c = ANY (p_updatable));

  IF v_set IS NULL THEN
    RAISE EXCEPTION 'merge_entity: nothing updatable on % (updatable=%)', p_target, p_updatable;
  END IF;

  v_sql := format(
    'WITH merged AS ('
    '  INSERT INTO %s (%s) SELECT %s FROM %s s'
    '  ON CONFLICT (%s) DO UPDATE SET %s'
    '    WHERE (%s) IS DISTINCT FROM (%s)'
    '  RETURNING (xmax = 0) AS was_insert'
    ') SELECT count(*) FILTER (WHERE was_insert), count(*) FILTER (WHERE NOT was_insert)'
    '  FROM merged',
    p_target,
    (SELECT string_agg(quote_ident(c), ', ') FROM unnest(v_shared) AS c),
    platform._merge_cast_list(p_target, v_shared, 's'),
    p_source,
    (SELECT string_agg(quote_ident(c), ', ') FROM unnest(p_conflict) AS c),
    v_set,
    (SELECT string_agg(format('%s.%I', p_target::text, c), ', ')
       FROM unnest(v_shared) AS c
      WHERE NOT (c = ANY (p_conflict))
        AND (p_updatable = ARRAY['*'] OR c = ANY (p_updatable))),
    (SELECT string_agg(format('EXCLUDED.%I', c), ', ')
       FROM unnest(v_shared) AS c
      WHERE NOT (c = ANY (p_conflict))
        AND (p_updatable = ARRAY['*'] OR c = ANY (p_updatable)))
  );

  RETURN QUERY EXECUTE v_sql;
END;
$$;

-- ---------------------------------------------------------------------------
-- The linked merge.
-- ---------------------------------------------------------------------------
CREATE FUNCTION platform.merge_linked_entity(
  p_source      regclass,   -- the landing table: inbound.{uid}__{entity}, carrying external_id
  p_target      regclass,   -- the book table:    crm.contacts, platform.accounts, …
  p_entity_type text,       -- links.entity_type: 'contact' | 'account' | 'model' | 'instrument'
  p_tenant_id   int,
  p_provider    text,
  p_updatable   text[] DEFAULT ARRAY[]::text[]  -- entity columns the provider owns; {*} = all
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
  v_uid       boolean;
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
     AND t.attname <> 'id';

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

  -- `uid` is how every portal surface addresses a contact or an account, it is NOT NULL on
  -- crm.contacts with no default, and no provider publishes one -- so the merge mints it, the
  -- same expression app/materialise.py used. A target without the column skips this entirely.
  v_uid := EXISTS (
    SELECT 1 FROM pg_attribute
     WHERE attrelid = p_target AND attname = 'uid' AND attnum > 0 AND NOT attisdropped
  ) AND NOT ('uid' = ANY (v_shared));

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
    -- Cast once, here, so every statement downstream reads already-typed columns.
    (SELECT string_agg(
              format('cast(s.%I AS %s) AS %I', a.attname,
                     format_type(a.atttypid, a.atttypmod), a.attname),
              ', ' ORDER BY a.attnum)
       FROM unnest(v_shared) AS u(name)
       JOIN pg_attribute a ON a.attrelid = p_target AND a.attname = u.name),
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
    '  INSERT INTO %s (id, %s%s) SELECT __id, %s%s FROM new RETURNING id'
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
    CASE WHEN v_uid THEN ', uid' ELSE '' END,
    (SELECT string_agg(format('new.%I', c), ', ') FROM unnest(v_shared) AS c),
    CASE WHEN v_uid THEN ', encode(gen_random_bytes(8), ''hex'')' ELSE '' END
  );
  EXECUTE v_sql USING p_tenant_id, p_provider, p_entity_type INTO v_inserted;

  RETURN QUERY SELECT v_inserted, v_updated;
END;
$$;

COMMENT ON FUNCTION platform.merge_linked_entity(regclass, regclass, text, int, text, text[]) IS
  'Merge a provider slice into an entity whose identity is a platform.links row: update the '
  'link''s payload (plus any provider-owned columns), or create the entity and its link '
  'together. Returns (inserted, updated).';

-- The merge resolves identity by (tenant_id, provider, entity_type, link_type, external_id) on
-- every pass; 0003's index is (provider, external_id), which makes a tenant's own slice a filter
-- over every tenant's links for that provider.
CREATE INDEX links_resolve_idx
  ON platform.links (tenant_id, provider, entity_type, link_type, external_id);

REVOKE ALL ON FUNCTION platform._merge_cast_list(regclass, text[], text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform._merge_cast_list(regclass, text[], text)
  TO book_writer, service_role;

REVOKE ALL ON FUNCTION
  platform.merge_linked_entity(regclass, regclass, text, int, text, text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
  platform.merge_linked_entity(regclass, regclass, text, int, text, text[])
  TO book_writer, service_role;
