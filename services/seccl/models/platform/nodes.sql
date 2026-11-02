/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: external_id
  depends_on: raw.nodes
  tags: [seccl, entity, conformed]
  description: >
    Org units — the thing a tenant's slice is keyed on. Seccl's node hierarchy,
    published in the conformed shape every provider uses: key plus a root->leaf
    lineage, so one containment predicate filters every provider (see
    docs/platform-model.md §3).
  checks:
    - not_null: external_id
    - unique: external_id
*/

-- Supersedes platform.nodes. The recursion is unchanged -- what changes is the
-- vocabulary: `lineage` becomes `lineage` and `id` becomes both `external_id` and
-- `key`, because for an org unit those are the same thing. SEI's firm/subfirm/DSR
-- triple and Morgan Lloyd's single Microsoft tenant id publish the same two columns,
-- which is what lets app/fanout.py drop its per-provider predicate builder.
--
-- Fee tiers are still deliberately NOT attached: they are per-firm configuration, not
-- warehouse data.
WITH RECURSIVE tree AS (
    SELECT
        n.id,
        n.parent_node_id,
        n.firm_id,
        n.name,
        n.node_type,
        [n.id] AS lineage,
        1      AS depth
    FROM raw.nodes AS n
    WHERE n.parent_node_id IS NULL
    UNION ALL
    SELECT
        n.id,
        n.parent_node_id,
        n.firm_id,
        n.name,
        n.node_type,
        list_append(t.lineage, n.id),
        t.depth + 1
    FROM raw.nodes AS n
    JOIN tree AS t ON n.parent_node_id = t.id
)
SELECT
    cast(t.id AS varchar)             AS external_id,
    cast(t.id AS varchar)             AS key,
    [cast(x AS varchar) FOR x IN t.lineage] AS lineage,
    t.name,
    -- The CANONICAL vocabulary, assigned here rather than in OLTP. It used to be left raw for the
    -- materialiser to map, which meant the sink carried a per-provider CASE and the conformed
    -- column differed by provider: SEI already publishes 'firm'/'subfirm'/'team' from its own
    -- hierarchy, so the sink read `type_raw` and SEI had nothing of that name. §3's whole point
    -- is one shape, and a vocabulary is part of a shape.
    --
    -- An unknown word becomes 'node' rather than failing the row: an org unit that exists is better
    -- than a rejected slice, and the provider's own word survives in type_raw beside it.
    CASE lower(coalesce(t.node_type, ''))
        WHEN 'firm' THEN 'firm'
        WHEN 'subfirm' THEN 'subfirm'
        WHEN 'branch' THEN 'subfirm'
        WHEN 'team' THEN 'team'
        WHEN 'tenant' THEN 'tenant'
        ELSE 'node'
    END                                                     AS type,
    coalesce(t.node_type, 'node')     AS type_raw,
    cast(t.parent_node_id AS varchar) AS parent_key,
    cast(t.firm_id AS varchar)        AS firm_key,
    t.depth,
    -- Seccl node records carry no update stamp of their own, so the extract date is the
    -- best available assertion time. Documented rather than invented per row.
    NULL::timestamp                   AS source_updated_at,
    current_date                      AS as_of_date,
    to_json(t)                        AS data
FROM tree AS t
