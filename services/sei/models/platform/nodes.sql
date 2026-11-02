/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: external_id
  depends_on: [ref.firms, ref.subfirms, ref.dsr_teams, platform.dsr_grants]
  tags: [sei, entity, conformed]
  description: >
    Org units — the thing a tenant's slice is keyed on. SEI's firm / subfirm / DSR-team
    hierarchy published in the conformed shape every provider uses (docs/platform-model.md
    §3), superseding platform.firms, platform.subfirms and platform.dsr_teams.
  checks:
    - not_null: external_id
    - unique: external_id
    - not_null: key
*/

-- Three tables become one. platform.{firms,subfirms,dsr_teams} each published a different
-- shape, which is why app/fanout.py needed _SEI_NO_SUBFIRM_TABLES / _SEI_SCALAR_DSR_TABLES /
-- _SEI_GLOBAL_TABLES to know which axes bound against which table. An org is an org; the
-- level it sits at is a column, not a table.
--
-- EXTERNAL_ID IS TYPE-QUALIFIED, key IS NOT. SEI's three id spaces are separate
-- namespaces that share a numeric range — subfirm 704766 and DSR team 704787 sit inside each
-- other's — so a bare id cannot be this table's primary key. `key` stays bare because it
-- is what a tenant's provider config holds and what lineage is matched against; qualifying
-- it would silently stop every configured key from matching.
--
-- ORG_PATH IS ROOT->LEAF ANCESTRY, and for SEI it is at most two deep: a subfirm and a DSR
-- team both hang directly off the firm. SEI models them as parallel axes rather than a
-- chain — an account has one subfirm AND several teams — so neither is the other's parent.
WITH firms AS (
    SELECT
        f.firm_id                          AS key,
        'firm'                             AS type,
        f.firm_name                        AS name,
        NULL::varchar                      AS parent_key,
        [f.firm_id]                        AS lineage,
        false                              AS identifies_firm,
        NULL::bigint                       AS account_count,
        f.as_of_date
    FROM ref.firms f
    WHERE f.firm_id IS NOT NULL
),
subfirms AS (
    SELECT
        s.subfirm_id                       AS key,
        'subfirm'                          AS type,
        s.subfirm_name                     AS name,
        s.firm_id                          AS parent_key,
        [s.firm_id, s.subfirm_id]          AS lineage,
        false                              AS identifies_firm,
        NULL::bigint                       AS account_count,
        s.as_of_date
    FROM ref.subfirms s
    WHERE s.subfirm_id IS NOT NULL
),
-- Team coverage comes from the grants rather than ref.dsr_teams, because a team's usefulness
-- to an operator picking a slice is how many accounts it actually covers — a firm can have
-- 400+ teams and most of them are operational, not advisory.
team_usage AS (
    SELECT
        dsr_id                                    AS key,
        firm_id,
        max(identifies_firm)                      AS identifies_firm,
        count(DISTINCT subject_id) FILTER (WHERE scope = 'account') AS account_count
    FROM platform.dsr_grants
    WHERE grantee = 'team'
    GROUP BY dsr_id, firm_id
),
teams AS (
    SELECT
        t.dsr_team_id                      AS key,
        'team'                             AS type,
        t.dsr_team_description             AS name,
        t.firm_id                          AS parent_key,
        [t.firm_id, t.dsr_team_id]         AS lineage,
        coalesce(u.identifies_firm, false) AS identifies_firm,
        coalesce(u.account_count, 0)       AS account_count,
        t.as_of_date
    FROM ref.dsr_teams t
    LEFT JOIN team_usage u ON u.key = t.dsr_team_id AND u.firm_id = t.firm_id
    WHERE t.dsr_team_id IS NOT NULL
),
united AS (
    SELECT * FROM firms
    UNION ALL BY NAME SELECT * FROM subfirms
    UNION ALL BY NAME SELECT * FROM teams
)
SELECT
    u.type || ':' || u.key                    AS external_id,
    u.key,
    -- `nullif(…, [])` because an EMPTY lineage is the fan-out's GLOBAL sentinel, and `nodes`
    -- is the one SEI relation the containment predicate ALREADY narrows on
    -- (`book_sink.predicate._SEI_NODE_TABLES`) — so a node whose firm is blank would be
    -- projected into every firm's org dimension today, not just after §6 step 5.
    nullif(list_filter(u.lineage, x -> x IS NOT NULL),
           []::VARCHAR[])                         AS lineage,
    u.name,
    u.type,
    u.parent_key,
    -- Does this org identify an advising firm (a candidate tenant) rather than an operational
    -- team? Naming is the only signal SEI gives, so it is guidance for the operator choosing a
    -- slice, never an automatic authorisation decision. See docs/sei.md §3.
    u.identifies_firm,
    u.account_count,
    -- The Reference report carries no per-row update stamp, so the drop date is the only
    -- assertion time available. Stated rather than invented.
    NULL::timestamp                                   AS source_updated_at,
    u.as_of_date,
    to_json(u)                                        AS data
FROM united u
