-- ============================================================================
-- 0064_merge_entity -- the one function the provider pipelines merge the book through.
--
-- THE POLICY, in one sentence: insert a row the first time the provider shows it, and after that
-- only update the columns the provider is allowed to own.
--
-- That is a much smaller rule than the one it replaces. `app/materialise.py` carries a per-column
-- catalogue of what a sync may and may not overwrite -- coalesce-preserving updates on
-- crm.contacts, six platform.models columns held back, products.enabled protected by
-- `DO UPDATE SET enabled = platform.products.enabled` -- because a provider row and a firm's
-- curation were fighting over the same columns. They are not fighting any more: the typed columns
-- are Graphene's, set once at insert from what the provider first published, and `data` is the
-- provider's verbatim payload, refreshed every run. Nothing to protect column by column, so
-- nothing to get wrong column by column.
--
-- ``p_updatable`` is the exception list, and there are exactly two:
--
--   platform.accounts     {data, value}  -- the balance is driven from the provider. Redundant
--                                           with platform.valuations, and deliberately so: a
--                                           number every screen shows should not depend on a
--                                           second derivation being correct.
--   platform.transactions {*}            -- fully provider-owned. Graphene never writes a
--                                           transaction: an instruction lands in its own table
--                                           (platform.transfers, contributions) or is executed
--                                           directly, so a provider status change is simply the
--                                           truth and there is no local edit to lose.
--
-- Everything else takes the default `{data}`.
--
-- WHY GENERIC rather than one function per entity: the policy is uniform, so writing it sixteen
-- times would be sixteen chances to mistype a column list, and the columns are already declared
-- -- in the catalogue, where they cannot drift from the tables they describe. A source column the
-- target does not have is ignored rather than erroring, which is what lets a provider publish
-- extra conformed columns (`status_raw`, `decision_maker`) without every target needing them.
-- ============================================================================

CREATE FUNCTION platform.merge_entity(
  p_source    regclass,        -- the landing table: inbound.{uid}__{entity}
  p_target    regclass,        -- the book table:    platform.orgs, crm.contacts, …
  p_conflict  text[],          -- the target's natural key, e.g. {tenant_id,provider,org_key}
  p_updatable text[] DEFAULT ARRAY['data']  -- provider-owned columns; {*} = all shared columns
)
RETURNS TABLE (inserted bigint, updated bigint)
LANGUAGE plpgsql
-- SECURITY INVOKER (the default, stated explicitly because it is a decision): this function needs
-- no privilege its caller lacks. It reads only pg_attribute, which is world-readable, and writes
-- only what the caller is already granted. SECURITY DEFINER would actively break it — the definer
-- context runs as the function's OWNER, the migrator, which FORCE ROW LEVEL SECURITY still binds,
-- so every insert failed with "new row violates row-level security policy". Both real callers
-- (book_writer, service_role) carry BYPASSRLS themselves, which is where that authority belongs.
--
-- search_path is still pinned: this builds dynamic SQL, and a caller-controlled path is how an
-- unqualified name in it gets resolved to something else's table.
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_shared   text[];
  v_set      text;
  v_sql      text;
BEGIN
  -- The columns both sides have. Ordered by the TARGET's attnum so INSERT's column list and its
  -- SELECT list cannot disagree.
  SELECT array_agg(t.attname ORDER BY t.attnum)
    INTO v_shared
    FROM pg_attribute t
    JOIN pg_attribute s
      ON s.attrelid = p_source AND s.attname = t.attname AND s.attnum > 0 AND NOT s.attisdropped
   WHERE t.attrelid = p_target AND t.attnum > 0 AND NOT t.attisdropped;

  IF v_shared IS NULL THEN
    RAISE EXCEPTION 'merge_entity: % and % share no columns', p_source, p_target;
  END IF;

  -- The assignment list: every updatable column that is actually shared and is not part of the
  -- key. `{*}` means all of them (the fully-provider-owned case).
  SELECT string_agg(format('%I = EXCLUDED.%I', c, c), ', ')
    INTO v_set
    FROM unnest(v_shared) AS c
   WHERE NOT (c = ANY (p_conflict))
     AND (p_updatable = ARRAY['*'] OR c = ANY (p_updatable));

  IF v_set IS NULL THEN
    RAISE EXCEPTION 'merge_entity: nothing updatable on % (updatable=%)', p_target, p_updatable;
  END IF;

  -- The DO UPDATE carries a WHERE so an unchanged row is not written at all. That is not a
  -- micro-optimisation: platform.links and platform.orgs both carry audit.record triggers, so a
  -- merge that rewrote every row every night would bury the audit trail in changes that did not
  -- happen -- and would do it with a NULL actor, since the pipeline has no principal.
  v_sql := format(
    'WITH merged AS ('
    '  INSERT INTO %s (%s) SELECT %s FROM %s'
    '  ON CONFLICT (%s) DO UPDATE SET %s'
    '    WHERE (%s) IS DISTINCT FROM (%s)'
    '  RETURNING (xmax = 0) AS was_insert'
    ') SELECT count(*) FILTER (WHERE was_insert), count(*) FILTER (WHERE NOT was_insert)'
    '  FROM merged',
    p_target,
    (SELECT string_agg(quote_ident(c), ', ') FROM unnest(v_shared) AS c),
    (SELECT string_agg(quote_ident(c), ', ') FROM unnest(v_shared) AS c),
    p_source,
    (SELECT string_agg(quote_ident(c), ', ') FROM unnest(p_conflict) AS c),
    v_set,
    -- The comparison covers only the updatable columns: a difference in a column the provider may
    -- not own is not a reason to write, and treating it as one would make every run report an
    -- update the moment a firm edited anything.
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

COMMENT ON FUNCTION platform.merge_entity(regclass, regclass, text[], text[]) IS
  'Insert-if-absent, then update only the provider-owned columns (default: data). Returns '
  '(inserted, updated). Unchanged rows are not written, so audit triggers do not fire on them.';

-- Explicit grants: no PUBLIC execute on a function that writes the book.
REVOKE ALL ON FUNCTION platform.merge_entity(regclass, regclass, text[], text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.merge_entity(regclass, regclass, text[], text[])
  TO book_writer, service_role;
