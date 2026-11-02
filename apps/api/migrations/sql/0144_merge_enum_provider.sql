-- ============================================================================
-- 0144_merge_enum_provider -- the THREE SECURITY DEFINER merge functions compare
-- `platform.links.provider` against a bound TEXT, and 0131 made that column an enum.
--
-- `operator does not exist: platform.provider_code = text`, from inside a PL/pgSQL EXECUTE — so
-- it surfaces as a per-entity MERGE REJECT rather than as a failed migration, and a rejected
-- entity reads as "this firm has no data with this custodian". That is the failure mode this
-- estate has lost the most time to (docs/provider-pipeline.md), and it would have been every
-- entity for every provider on the first nightly run after 0131.
--
-- THE PARAMETER STAYS `text`, and the CAST moves to the comparison. Retyping `p_provider` to
-- `platform.provider_code` would be tidier to read and would force every caller to cast at the
-- call site instead — `book_sink.merge` passes a plain Python string, so the failure would move
-- from one place inside the function to seven places outside it. One cast where the comparison
-- is, and the pipeline is unchanged.
--
-- Reproduced verbatim from the live definitions with only those four comparisons changed, because
-- these bodies are the merge POLICY (0064-0070, 0113) and re-deriving them by hand is how a
-- policy acquires a second spelling.
-- ============================================================================

CREATE OR REPLACE FUNCTION platform.merge_linked_entity(p_source regclass, p_target regclass, p_entity_type text, p_tenant_id integer, p_provider text, p_updatable text[] DEFAULT ARRAY[]::text[], p_exclude text[] DEFAULT ARRAY[]::text[])
 RETURNS TABLE(inserted bigint, updated bigint)
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'public'
AS $function$
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
  v_match     text := 'l.tenant_id = $1::int AND l.provider = $2::platform.provider_code'
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
    '  SELECT $1::int, $3::text, __id, $2::platform.provider_code, ''primary'', __ext, __data'
    '    FROM new'
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
$function$;

CREATE OR REPLACE FUNCTION platform.merge_model_versions(p_source regclass, p_tenant_id integer, p_provider text)
 RETURNS TABLE(inserted bigint, updated bigint)
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'public'
AS $function$
DECLARE
  v_sql   text;
  v_count bigint;
BEGIN
  -- Dynamic only because the landing table's name carries the tenant uid; every value is bound.
  v_sql := format($fmt$
    WITH staged AS (
      SELECT l.entity_id                        AS model_id,
             max(cast(s.cash_allocation AS numeric)) AS cash,
             max(cast(s.as_of_date AS date))         AS as_of_date,
             max(cast(s.asset_mix AS text))          AS asset_mix,
             -- The set, ordered by its own content so two runs that publish the same allocation in
             -- a different order compare equal.
             array_agg(
               coalesce(s.external_asset_id, s.name) || ':'
                 || round(cast(s.percentage AS numeric), 6)::text
               ORDER BY coalesce(s.external_asset_id, s.name),
                        round(cast(s.percentage AS numeric), 6)::text)  AS fingerprint
        FROM %1$s s
        JOIN platform.links l
          ON l.tenant_id = $1 AND l.provider = $2::platform.provider_code AND l.entity_type = 'model'
         AND l.link_type = 'primary' AND l.external_id = s.model_external_id
       GROUP BY l.entity_id
    ),
    latest AS (
      SELECT v.model_id, v.cash_allocation,
             array_agg(
               coalesce(a.external_asset_id, a.name) || ':' || round(a.percentage, 6)::text
               ORDER BY coalesce(a.external_asset_id, a.name),
                        round(a.percentage, 6)::text)  AS fingerprint
        FROM platform.model_versions v
        JOIN platform.model_allocations a ON a.model_version_id = v.id
       WHERE v.tenant_id = $1
         AND v.version = (SELECT max(v2.version) FROM platform.model_versions v2
                           WHERE v2.model_id = v.model_id)
       GROUP BY v.model_id, v.cash_allocation
    ),
    changed AS (
      SELECT st.* FROM staged st
      LEFT JOIN latest c ON c.model_id = st.model_id
       WHERE c.model_id IS NULL
          OR c.fingerprint IS DISTINCT FROM st.fingerprint
          OR round(coalesce(c.cash_allocation, 0), 6)
             IS DISTINCT FROM round(coalesce(st.cash, 0), 6)
    ),
    minted AS (
      INSERT INTO platform.model_versions
        (tenant_id, model_id, version, effective_from, cash_allocation, asset_mix, note,
         published_at)
      SELECT $1, ch.model_id,
             coalesce((SELECT max(v.version) FROM platform.model_versions v
                        WHERE v.model_id = ch.model_id), 0) + 1,
             ch.as_of_date, ch.cash, cast(ch.asset_mix AS jsonb),
             'Synced from the provider',
             -- Published on arrival, not a draft: this allocation is what the custodian is already
             -- trading to, so filing it unpublished would misreport what is live.
             now()
        FROM changed ch
      RETURNING id, model_id
    ),
    lines AS (
      INSERT INTO platform.model_allocations
        (tenant_id, model_version_id, instrument_id, external_asset_id, isin, name, percentage,
         sort_order)
      SELECT $1, m.id, i.entity_id, s.external_asset_id, s.isin, s.name,
             cast(s.percentage AS numeric), cast(s.sort_order AS integer)
        FROM minted m
        JOIN platform.links l
          ON l.entity_id = m.model_id AND l.tenant_id = $1 AND l.provider = $2::platform.provider_code
         AND l.entity_type = 'model' AND l.link_type = 'primary'
        JOIN %1$s s ON s.model_external_id = l.external_id
        -- The instrument, when this tenant has a link for it. NULL is fine: the allocation is still
        -- the model's, and a name is enough to render the line.
        LEFT JOIN platform.links i
          ON i.tenant_id = $1 AND i.provider = $2::platform.provider_code AND i.entity_type = 'instrument'
         AND i.link_type = 'primary' AND i.external_id = s.external_asset_id
      RETURNING 1
    )
    SELECT count(*)::bigint FROM minted
  $fmt$, p_source);

  EXECUTE v_sql USING p_tenant_id, p_provider INTO v_count;
  RETURN QUERY SELECT coalesce(v_count, 0::bigint), 0::bigint;
END;
$function$;

CREATE OR REPLACE FUNCTION platform.merge_model_commentary(p_source regclass, p_tenant_id integer, p_provider text)
 RETURNS TABLE(inserted bigint, updated bigint)
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'public'
AS $function$
DECLARE
  v_sql   text;
  v_count bigint;
BEGIN
  v_sql := format($fmt$
    WITH added AS (
      INSERT INTO platform.model_commentary (tenant_id, model_id, title, body, published_at)
      SELECT $1, l.entity_id, 'Provider commentary', s.body, now()
        FROM %1$s s
        JOIN platform.links l
          ON l.tenant_id = $1 AND l.provider = $2::platform.provider_code AND l.entity_type = 'model'
         AND l.link_type = 'primary' AND l.external_id = s.model_external_id
       WHERE NOT EXISTS (
         SELECT 1 FROM platform.model_commentary c
          WHERE c.tenant_id = $1 AND c.model_id = l.entity_id AND c.body = s.body)
      RETURNING 1
    )
    SELECT count(*)::bigint FROM added
  $fmt$, p_source);

  EXECUTE v_sql USING p_tenant_id, p_provider INTO v_count;
  RETURN QUERY SELECT coalesce(v_count, 0::bigint), 0::bigint;
END;
$function$;
