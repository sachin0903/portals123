/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: account_number
  depends_on: [platform.dsr_grants, raw.client_account_link]
  tags: [sei, tenancy]
  description: >
    The tenant-filter spine: one row per account carrying firm, subfirm and the
    account's DSR grants as LISTs. Every sei.* entity joins this, so one predicate
    shape filters them all.
*/

-- WHY LISTS. A tenant is identified by a firm plus 1..m subfirms and/or 1..m DSR
-- teams, and an account holds several grants at once. Three ways to model that:
--
--   1. numbered columns  — what SEI sends; unfilterable (dsr_team_id_1..10)
--   2. one row per grant — platform.dsr_grants; correct, but joining it to every fact
--                          multiplies rows and every downstream count needs a
--                          DISTINCT to stay honest
--   3. one LIST column   — this. The account stays one row and the filter is
--                          `list_has_any(dsr_team_ids, ['200119','409663'])`
--
-- (3) is what the fan-out wants: no join, no row multiplication, and the SAME
-- predicate works on every entity table, so app/fanout.py no longer needs a
-- per-table DSR column map. It mirrors how Seccl's node_id is matched.
--
-- Grain note: account grants are inherited by everything belonging to the account
-- (transactions, positions, valuations…), so the account is the natural spine.
-- Client-scope grants are carried alongside for client-level entities, and both
-- are unioned into `all_dsr_ids` for the permissive case.
WITH account_grants AS (
    SELECT
        subject_id                                          AS account_number,
        any_value(firm_id)                                  AS firm_id,
        any_value(subfirm_id)                               AS subfirm_id,
        list_sort(list_distinct(list(dsr_id)))              AS dsr_team_ids,
        list_sort(list_distinct(list(dsr_type_id)))         AS dsr_type_ids,
        list_sort(list_distinct(list(dsr_id) FILTER (WHERE identifies_firm)))
                                                            AS adviser_firm_dsr_ids,
        list_sort(list_distinct(list(dsr_name) FILTER (WHERE identifies_firm)))
                                                            AS adviser_firm_names
    FROM platform.dsr_grants
    WHERE scope = 'account' AND grantee = 'team'
    GROUP BY subject_id
),
client_grants AS (
    SELECT
        subject_id                                  AS client_id,
        list_sort(list_distinct(list(dsr_id)))      AS client_dsr_team_ids
    FROM platform.dsr_grants
    WHERE scope = 'client' AND grantee = 'team'
    GROUP BY subject_id
),
-- An account can have several owners; keep the account as the grain and carry the
-- owners as a list rather than fanning the spine out per client.
links AS (
    SELECT
        l."ACCOUNT_NUMBER"                              AS account_number,
        any_value(l."ACCOUNT_ID")                       AS account_id,
        any_value(l."FIRM_ID")                          AS firm_id,
        any_value(l."ACCOUNT_SUBFIRM")                  AS subfirm_id,
        list_sort(list_distinct(list(l."CLIENT_ID")))   AS client_ids,
        list_sort(list_distinct(list(c.client_dsr_team_ids))) AS nested_client_dsr
    FROM raw.client_account_link l
    JOIN (SELECT max(dt) AS dt FROM raw.client_account_link) m ON l.dt = m.dt
    LEFT JOIN client_grants c ON c.client_id = l."CLIENT_ID"
    GROUP BY l."ACCOUNT_NUMBER"
)
SELECT
    k.account_number,
    k.account_id,
    k.client_ids,
    coalesce(g.firm_id, k.firm_id)        AS firm_id,
    coalesce(g.subfirm_id, k.subfirm_id)  AS subfirm_id,
    coalesce(g.dsr_team_ids, [])          AS dsr_team_ids,
    coalesce(g.dsr_type_ids, [])          AS dsr_type_ids,
    -- The subset that names an advising firm — the grants that actually identify a
    -- tenant, as opposed to the operational ones (SEI Custody, Legal Entity,
    -- Client Service Team) every account in the firm shares. See docs/sei.md §3.
    coalesce(g.adviser_firm_dsr_ids, [])  AS adviser_firm_dsr_ids,
    coalesce(g.adviser_firm_names, [])    AS adviser_firm_names,
    -- Account grants plus the grants of every client on the account, for callers
    -- that want the permissive reading.
    list_sort(list_distinct(
        list_concat(coalesce(g.dsr_team_ids, []), coalesce(flatten(k.nested_client_dsr), []))
    ))                                    AS all_dsr_team_ids
FROM links k
LEFT JOIN account_grants g ON g.account_number = k.account_number
