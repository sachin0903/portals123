-- ============================================================================
-- 0067_merge_entity_options -- three things the catalogue layer needs from the merge, and none is
-- a special case for it.
--
-- (1) A PARTIAL unique index has to be named to be inferred. `platform.products` is keyed
--     (tenant_id, product_type) WHERE tenant_id IS NOT NULL -- partial because three pre-0044 rows
--     still carry a NULL tenant_id -- and `ON CONFLICT (tenant_id, product_type)` alone does not
--     match it: Postgres answers "there is no unique or exclusion constraint matching the ON
--     CONFLICT specification", the same error the platforms registry gave before 0055. So the infer
--     clause takes an optional predicate.
--
-- (2) INSERT-ONLY is a real policy, not the absence of one. The firm's shelf is DISCOVERED from the
--     accounts it holds: discovery decides a product EXISTS, while whether the firm offers it
--     (`enabled`) and what the firm calls it (`name`) are the firm's own. Re-asserting either on
--     every sync would turn a nightly run into an override of those decisions -- which is exactly
--     what app/materialise.py's `DO UPDATE SET enabled = platform.products.enabled` was written to
--     prevent, expressed as a no-op update because the function had no way to say "do nothing".
--     Now an EMPTY p_updatable says it, and DO NOTHING is what it compiles to.
--
--     Empty used to raise ("nothing updatable"), which was right when {data} was the only sensible
--     default and an empty list could only be a mistake. It is now a distinct, declared choice, so
--     the raise moves to the case that is still a mistake: a p_updatable naming columns that the
--     source and target do not share.
--
-- (3) A COLUMN'S OWN DEFAULT is the fallback for a NOT NULL column the provider left empty. An
--     explicit NULL does not take a default -- so staging one for `platform.models.suspend_rebalances`
--     (NOT NULL DEFAULT false) failed the INSERT and took the whole entity with it, for one boolean
--     no provider publishes. Coalescing to the column's own default in `_merge_cast_list` fixes the
--     class rather than the instance: every NOT NULL column with a default now behaves as though the
--     column had not been named at all, which is what "the provider did not say" should mean.
--
--     A NOT NULL column with NO default is still a genuine failure and still raises -- crm.contacts
--     `uid` is that case, which is why merge_linked_entity mints one rather than defaulting it.
-- ============================================================================

-- The cast list, now also the DEFAULT list. `p_as_names` aliases each expression back to its own
-- column name, which is what the linked merge's source CTE needs (it selects into named columns
-- rather than positionally).
DROP FUNCTION IF EXISTS platform._merge_cast_list(regclass, text[], text);

CREATE FUNCTION platform._merge_cast_list(
  p_target   regclass,
  p_cols     text[],
  p_alias    text,
  p_as_names boolean DEFAULT false
) RETURNS text
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
  SELECT string_agg(
           CASE
             WHEN a.attnotnull AND d.adbin IS NOT NULL THEN
               format('coalesce(cast(%I.%I AS %s), %s)', p_alias, a.attname,
                      format_type(a.atttypid, a.atttypmod), pg_get_expr(d.adbin, d.adrelid))
             ELSE
               format('cast(%I.%I AS %s)', p_alias, a.attname,
                      format_type(a.atttypid, a.atttypmod))
           END
           || CASE WHEN p_as_names THEN format(' AS %I', a.attname) ELSE '' END,
           ', ' ORDER BY a.attnum)
    FROM unnest(p_cols) AS u(name)
    JOIN pg_attribute a
      ON a.attrelid = p_target AND a.attname = u.name AND a.attnum > 0 AND NOT a.attisdropped
    LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
$$;

COMMENT ON FUNCTION platform._merge_cast_list(regclass, text[], text, boolean) IS
  'Internal: the SELECT list for a merge -- each column cast to the target column''s own type, and '
  'coalesced to the target column''s own default when it is NOT NULL.';

REVOKE ALL ON FUNCTION
  platform._merge_cast_list(regclass, text[], text, boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform._merge_cast_list(regclass, text[], text, boolean)
  TO book_writer, service_role;

CREATE OR REPLACE FUNCTION platform.merge_entity(
  p_source         regclass,
  p_target         regclass,
  p_conflict       text[],
  p_updatable      text[] DEFAULT ARRAY['data'],
  -- The partial index's own predicate, e.g. 'tenant_id IS NOT NULL'. Empty means the key is total.
  p_conflict_where text DEFAULT ''
)
RETURNS TABLE (inserted bigint, updated bigint)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_shared text[];
  v_set    text;
  v_action text;
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

  IF cardinality(p_updatable) = 0 THEN
    -- Insert-only: the row's existence is the provider's to establish, everything on it is the
    -- firm's to keep. xmax = 0 still separates created from unchanged, so the ledger reads
    -- "created 5" on a firm's first sync and "0" on every one after.
    v_action := 'DO NOTHING';
  ELSIF v_set IS NULL THEN
    RAISE EXCEPTION 'merge_entity: nothing updatable on % (updatable=%) -- % publishes none of it',
      p_target, p_updatable, p_source;
  ELSE
    v_action := format(
      'DO UPDATE SET %s WHERE (%s) IS DISTINCT FROM (%s)',
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
  END IF;

  v_sql := format(
    'WITH merged AS ('
    '  INSERT INTO %s (%s) SELECT %s FROM %s s'
    '  ON CONFLICT (%s) %s %s'
    '  RETURNING (xmax = 0) AS was_insert'
    ') SELECT count(*) FILTER (WHERE was_insert), count(*) FILTER (WHERE NOT was_insert)'
    '  FROM merged',
    p_target,
    (SELECT string_agg(quote_ident(c), ', ') FROM unnest(v_shared) AS c),
    platform._merge_cast_list(p_target, v_shared, 's'),
    p_source,
    (SELECT string_agg(quote_ident(c), ', ') FROM unnest(p_conflict) AS c),
    CASE WHEN p_conflict_where = '' THEN '' ELSE 'WHERE ' || p_conflict_where END,
    v_action
  );

  RETURN QUERY EXECUTE v_sql;
END;
$$;

COMMENT ON FUNCTION platform.merge_entity(regclass, regclass, text[], text[], text) IS
  'Insert-if-absent, then update only the provider-owned columns (default: data; empty = never '
  'update). p_conflict_where names a partial index''s predicate. Returns (inserted, updated).';

REVOKE ALL ON FUNCTION
  platform.merge_entity(regclass, regclass, text[], text[], text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.merge_entity(regclass, regclass, text[], text[], text)
  TO book_writer, service_role;

-- ---------------------------------------------------------------------------
-- merge_linked_entity, replaced for one line: its source CTE now builds its cast list with
-- _merge_cast_list, so a NOT NULL column the provider left empty takes the column's own default
-- instead of failing the insert. Everything else is 0065's body unchanged -- that file remains the
-- explanation of WHY this function is shaped the way it is.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION platform.merge_linked_entity(
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

-- The 4-argument signature is now a distinct overload from the 5-argument one, and leaving both
-- would make `merge_entity(a, b, c, d)` ambiguous to nobody but confusing to everybody. The default
-- covers every existing caller.
DROP FUNCTION IF EXISTS platform.merge_entity(regclass, regclass, text[], text[]);

-- ============================================================================
-- One grant 0062 missed. It enumerated model_versions, model_allocations and model_commentary but
-- not `platform.model_platforms` -- the table that holds each platform's own reference for a model,
-- which is exactly what a provider sync writes. Enumerated grants are the right call (a future
-- table should have to be added on purpose) and this is the cost of it: found by merging, not by
-- reading.
-- ============================================================================
GRANT SELECT, INSERT, UPDATE ON platform.model_platforms TO book_writer;
