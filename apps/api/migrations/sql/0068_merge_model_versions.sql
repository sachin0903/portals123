-- ============================================================================
-- 0068_merge_model_versions -- a rebalance is one reviewable change, so a version is minted only
-- when the allocation actually changed.
--
-- This is the one catalogue layer the generic merge cannot express, and the reason is worth stating:
-- every other entity is "one staged row -> one book row keyed by something". A model VERSION is
-- neither. It is a decision about a SET of rows (the allocation) compared against the set the book
-- already holds, and its key is `max(version) + 1` — which no staged column can carry.
--
-- Without the comparison, every sync would mint a version and the history whose whole purpose is to
-- show WHEN a model was rebalanced would just count sync runs.
--
-- IT CASTS WHAT IT READS. The landing table is created by the delivery, not by this function, and
-- the merge's own cast list cannot help: that is built from the TARGET's column types, and these
-- columns (percentage, sort_order, cash_allocation) exist on no target -- they are consumed here.
--
-- THE FINGERPRINT is the set of (holding, percentage) plus cash, which is what a rebalance changes:
-- not the asset names, not the sort order, not the provider's own timestamps. Rounded to 6 places
-- because the percentages are numeric and a re-serialisation that changed the last digit is not a
-- rebalance.
--
-- WHAT IT DOES NOT DO: update. A published version is a historical record of what the model was, so
-- it is written once and never touched again — the `updated` count it returns is always 0, and that
-- is a fact about the entity rather than a gap in the function.
-- ============================================================================

CREATE FUNCTION platform.merge_model_versions(
  p_source    regclass,  -- one row per allocation LINE: inbound.{uid}__model_allocations
  p_tenant_id int,
  p_provider  text
)
RETURNS TABLE (inserted bigint, updated bigint)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
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
          ON l.tenant_id = $1 AND l.provider = $2 AND l.entity_type = 'model'
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
          ON l.entity_id = m.model_id AND l.tenant_id = $1 AND l.provider = $2
         AND l.entity_type = 'model' AND l.link_type = 'primary'
        JOIN %1$s s ON s.model_external_id = l.external_id
        -- The instrument, when this tenant has a link for it. NULL is fine: the allocation is still
        -- the model's, and a name is enough to render the line.
        LEFT JOIN platform.links i
          ON i.tenant_id = $1 AND i.provider = $2 AND i.entity_type = 'instrument'
         AND i.link_type = 'primary' AND i.external_id = s.external_asset_id
      RETURNING 1
    )
    SELECT count(*)::bigint FROM minted
  $fmt$, p_source);

  EXECUTE v_sql USING p_tenant_id, p_provider INTO v_count;
  RETURN QUERY SELECT coalesce(v_count, 0::bigint), 0::bigint;
END;
$$;

COMMENT ON FUNCTION platform.merge_model_versions(regclass, int, text) IS
  'Mint a model version + its allocations only when the (holding, percentage) set or cash changed. '
  'Returns (inserted, 0) — a published version is history and is never updated.';

REVOKE ALL ON FUNCTION platform.merge_model_versions(regclass, int, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.merge_model_versions(regclass, int, text)
  TO book_writer, service_role;

-- ============================================================================
-- The provider's standing commentary, deduplicated on its TEXT. Seccl carries one note per model,
-- so re-syncing must not append a copy of it every night — and the note is not keyed by anything
-- else, which is why this is a NOT EXISTS on the body rather than an upsert.
-- ============================================================================

CREATE FUNCTION platform.merge_model_commentary(
  p_source    regclass,  -- one row per model with a note: inbound.{uid}__model_commentary
  p_tenant_id int,
  p_provider  text
)
RETURNS TABLE (inserted bigint, updated bigint)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
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
          ON l.tenant_id = $1 AND l.provider = $2 AND l.entity_type = 'model'
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
$$;

COMMENT ON FUNCTION platform.merge_model_commentary(regclass, int, text) IS
  'Insert the provider''s note for a model unless that exact body is already recorded.';

REVOKE ALL ON FUNCTION platform.merge_model_commentary(regclass, int, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.merge_model_commentary(regclass, int, text)
  TO book_writer, service_role;
