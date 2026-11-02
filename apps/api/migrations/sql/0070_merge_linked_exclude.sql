-- ============================================================================
-- 0070_merge_linked_exclude -- keep a column out of the ENTITY while the LINK still gets it.
--
-- Closes the one place the two paths disagreed, found by the differential parity test
-- (tests/test_pipeline_parity.py): the merge writes every column the source and target share, so
-- the provider's payload was landing on `crm.contacts.data` as well as `links.data`, where
-- app/materialise.py leaves the entity's column NULL.
--
-- WHY THAT MATTERS rather than being merely redundant. `links.data` is the payload's home precisely
-- because one entity can hold links to TWO providers -- the same client at SEI and at Morgan Lloyd --
-- and one column cannot hold two payloads. Left as it was, that column would be rewritten every
-- night by whichever provider's run finished last, on a table under an audit trigger: churn in the
-- trail, and a column whose contents depend on run order.
--
-- The exclusion is deliberately NOT applied to platform.{accounts,instruments,models}. Each has its
-- own `data`, and materialise.py has always written the payload there; excluding it would be a
-- change of behaviour dressed up as a fix, and `platform.accounts.data` has readers. Making the rule
-- uniform is a separate decision with its own migration.
--
-- The 6-argument signature is DROPPED rather than left beside this one: a defaulted seventh
-- parameter makes a 6-argument call match both, which Postgres refuses as "function is not unique".
-- Every caller passes the default.
-- ============================================================================

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

COMMENT ON FUNCTION
  platform.merge_linked_entity(regclass, regclass, text, int, text, text[], text[]) IS
  'Merge a provider slice into an entity whose identity is a platform.links row: update the '
  'link''s payload (plus any provider-owned columns), or create the entity and its link '
  'together. p_exclude keeps a column out of the ENTITY while the link still receives it. '
  'Returns (inserted, updated).';

REVOKE ALL ON FUNCTION
  platform.merge_linked_entity(regclass, regclass, text, int, text, text[], text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
  platform.merge_linked_entity(regclass, regclass, text, int, text, text[], text[])
  TO book_writer, service_role;

DROP FUNCTION IF EXISTS
  platform.merge_linked_entity(regclass, regclass, text, int, text, text[]);
