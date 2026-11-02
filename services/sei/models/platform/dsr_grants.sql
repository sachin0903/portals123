/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: [scope, grantee, subject_id, dsr_type_id, dsr_id]
  depends_on: [raw.client_account_link, ref.dsr_teams, ref.dsr_types]
  tags: [sei, tenancy]
  description: >
    Every DSR grant, long form — one row per (subject, grant). The de-exploded
    replacement for client_account_link's 80 numbered slot columns.
*/

-- SEI stores DSR grants as FOUR families of ten numbered slots on
-- client_account_link — 80 columns, no other report carries them:
--
--   {CLIENT,ACCOUNT}_DSR_{TEAM,EMP}_{TAG,ID}_1..10
--
-- TAG is the DSR *type* (ref.dsr_types), ID is the team or employee
-- (ref.dsr_teams). The slot NUMBER is positional storage, not a rank, and is
-- discarded. All four families are unpivoted here so nothing in the feed is
-- silently dropped; `grantee` ('team' | 'employee') keeps the two grains apart,
-- because tenancy is a team-level question and mixing in per-adviser grants
-- would double-count accounts.
--
-- platform.tenancy pivots this back into LIST columns for filtering; this table is the
-- auditable long form — what the admin picker reads and what you query to answer
-- "why does this tenant see this account".
WITH latest AS (
    SELECT max(dt) AS dt FROM raw.client_account_link
),
current AS (
    SELECT l.* FROM raw.client_account_link l JOIN latest ON l.dt = latest.dt
),
slots AS (
    SELECT
        c."CLIENT_ID"        AS client_id,
        c."ACCOUNT_NUMBER"   AS account_number,
        c."ACCOUNT_ID"       AS account_id,
        c."FIRM_ID"          AS firm_id,
        c."CLIENT_SUBFIRM"   AS client_subfirm,
        c."ACCOUNT_SUBFIRM"  AS account_subfirm,
        g.scope,
        g.grantee,
        g.dsr_type_id,
        g.dsr_id
    FROM current c,
    UNNEST([
        {'scope':'account','grantee':'team','dsr_type_id':c."ACCOUNT_DSR_TEAM_TAG_1",'dsr_id':c."ACCOUNT_DSR_TEAM_ID_1"},
        {'scope':'account','grantee':'team','dsr_type_id':c."ACCOUNT_DSR_TEAM_TAG_2",'dsr_id':c."ACCOUNT_DSR_TEAM_ID_2"},
        {'scope':'account','grantee':'team','dsr_type_id':c."ACCOUNT_DSR_TEAM_TAG_3",'dsr_id':c."ACCOUNT_DSR_TEAM_ID_3"},
        {'scope':'account','grantee':'team','dsr_type_id':c."ACCOUNT_DSR_TEAM_TAG_4",'dsr_id':c."ACCOUNT_DSR_TEAM_ID_4"},
        {'scope':'account','grantee':'team','dsr_type_id':c."ACCOUNT_DSR_TEAM_TAG_5",'dsr_id':c."ACCOUNT_DSR_TEAM_ID_5"},
        {'scope':'account','grantee':'team','dsr_type_id':c."ACCOUNT_DSR_TEAM_TAG_6",'dsr_id':c."ACCOUNT_DSR_TEAM_ID_6"},
        {'scope':'account','grantee':'team','dsr_type_id':c."ACCOUNT_DSR_TEAM_TAG_7",'dsr_id':c."ACCOUNT_DSR_TEAM_ID_7"},
        {'scope':'account','grantee':'team','dsr_type_id':c."ACCOUNT_DSR_TEAM_TAG_8",'dsr_id':c."ACCOUNT_DSR_TEAM_ID_8"},
        {'scope':'account','grantee':'team','dsr_type_id':c."ACCOUNT_DSR_TEAM_TAG_9",'dsr_id':c."ACCOUNT_DSR_TEAM_ID_9"},
        {'scope':'account','grantee':'team','dsr_type_id':c."ACCOUNT_DSR_TEAM_TAG_10",'dsr_id':c."ACCOUNT_DSR_TEAM_ID_10"},
        {'scope':'client','grantee':'team','dsr_type_id':c."CLIENT_DSR_TEAM_TAG_1",'dsr_id':c."CLIENT_DSR_TEAM_ID_1"},
        {'scope':'client','grantee':'team','dsr_type_id':c."CLIENT_DSR_TEAM_TAG_2",'dsr_id':c."CLIENT_DSR_TEAM_ID_2"},
        {'scope':'client','grantee':'team','dsr_type_id':c."CLIENT_DSR_TEAM_TAG_3",'dsr_id':c."CLIENT_DSR_TEAM_ID_3"},
        {'scope':'client','grantee':'team','dsr_type_id':c."CLIENT_DSR_TEAM_TAG_4",'dsr_id':c."CLIENT_DSR_TEAM_ID_4"},
        {'scope':'client','grantee':'team','dsr_type_id':c."CLIENT_DSR_TEAM_TAG_5",'dsr_id':c."CLIENT_DSR_TEAM_ID_5"},
        {'scope':'client','grantee':'team','dsr_type_id':c."CLIENT_DSR_TEAM_TAG_6",'dsr_id':c."CLIENT_DSR_TEAM_ID_6"},
        {'scope':'client','grantee':'team','dsr_type_id':c."CLIENT_DSR_TEAM_TAG_7",'dsr_id':c."CLIENT_DSR_TEAM_ID_7"},
        {'scope':'client','grantee':'team','dsr_type_id':c."CLIENT_DSR_TEAM_TAG_8",'dsr_id':c."CLIENT_DSR_TEAM_ID_8"},
        {'scope':'client','grantee':'team','dsr_type_id':c."CLIENT_DSR_TEAM_TAG_9",'dsr_id':c."CLIENT_DSR_TEAM_ID_9"},
        {'scope':'client','grantee':'team','dsr_type_id':c."CLIENT_DSR_TEAM_TAG_10",'dsr_id':c."CLIENT_DSR_TEAM_ID_10"},
        {'scope':'account','grantee':'employee','dsr_type_id':c."ACCOUNT_DSR_EMP_TAG_1",'dsr_id':c."ACCOUNT_DSR_EMP_ID_1"},
        {'scope':'account','grantee':'employee','dsr_type_id':c."ACCOUNT_DSR_EMP_TAG_2",'dsr_id':c."ACCOUNT_DSR_EMP_ID_2"},
        {'scope':'account','grantee':'employee','dsr_type_id':c."ACCOUNT_DSR_EMP_TAG_3",'dsr_id':c."ACCOUNT_DSR_EMP_ID_3"},
        {'scope':'account','grantee':'employee','dsr_type_id':c."ACCOUNT_DSR_EMP_TAG_4",'dsr_id':c."ACCOUNT_DSR_EMP_ID_4"},
        {'scope':'account','grantee':'employee','dsr_type_id':c."ACCOUNT_DSR_EMP_TAG_5",'dsr_id':c."ACCOUNT_DSR_EMP_ID_5"},
        {'scope':'account','grantee':'employee','dsr_type_id':c."ACCOUNT_DSR_EMP_TAG_6",'dsr_id':c."ACCOUNT_DSR_EMP_ID_6"},
        {'scope':'account','grantee':'employee','dsr_type_id':c."ACCOUNT_DSR_EMP_TAG_7",'dsr_id':c."ACCOUNT_DSR_EMP_ID_7"},
        {'scope':'account','grantee':'employee','dsr_type_id':c."ACCOUNT_DSR_EMP_TAG_8",'dsr_id':c."ACCOUNT_DSR_EMP_ID_8"},
        {'scope':'account','grantee':'employee','dsr_type_id':c."ACCOUNT_DSR_EMP_TAG_9",'dsr_id':c."ACCOUNT_DSR_EMP_ID_9"},
        {'scope':'account','grantee':'employee','dsr_type_id':c."ACCOUNT_DSR_EMP_TAG_10",'dsr_id':c."ACCOUNT_DSR_EMP_ID_10"},
        {'scope':'client','grantee':'employee','dsr_type_id':c."CLIENT_DSR_EMP_TAG_1",'dsr_id':c."CLIENT_DSR_EMP_ID_1"},
        {'scope':'client','grantee':'employee','dsr_type_id':c."CLIENT_DSR_EMP_TAG_2",'dsr_id':c."CLIENT_DSR_EMP_ID_2"},
        {'scope':'client','grantee':'employee','dsr_type_id':c."CLIENT_DSR_EMP_TAG_3",'dsr_id':c."CLIENT_DSR_EMP_ID_3"},
        {'scope':'client','grantee':'employee','dsr_type_id':c."CLIENT_DSR_EMP_TAG_4",'dsr_id':c."CLIENT_DSR_EMP_ID_4"},
        {'scope':'client','grantee':'employee','dsr_type_id':c."CLIENT_DSR_EMP_TAG_5",'dsr_id':c."CLIENT_DSR_EMP_ID_5"},
        {'scope':'client','grantee':'employee','dsr_type_id':c."CLIENT_DSR_EMP_TAG_6",'dsr_id':c."CLIENT_DSR_EMP_ID_6"},
        {'scope':'client','grantee':'employee','dsr_type_id':c."CLIENT_DSR_EMP_TAG_7",'dsr_id':c."CLIENT_DSR_EMP_ID_7"},
        {'scope':'client','grantee':'employee','dsr_type_id':c."CLIENT_DSR_EMP_TAG_8",'dsr_id':c."CLIENT_DSR_EMP_ID_8"},
        {'scope':'client','grantee':'employee','dsr_type_id':c."CLIENT_DSR_EMP_TAG_9",'dsr_id':c."CLIENT_DSR_EMP_ID_9"},
        {'scope':'client','grantee':'employee','dsr_type_id':c."CLIENT_DSR_EMP_TAG_10",'dsr_id':c."CLIENT_DSR_EMP_ID_10"}
    ]) AS u(g)
    WHERE g.dsr_id IS NOT NULL
)
-- DISTINCT is load-bearing: client_account_link has one row per client ON an
-- account, so a joint account repeats its ACCOUNT-level grants once per owner.
-- Without it a two-owner account reports twice the grants it has and any
-- coverage count is inflated (8,433 -> 5,102 rows on the p04 drop).
SELECT DISTINCT
    s.scope,
    s.grantee,
    CASE s.scope WHEN 'account' THEN s.account_number ELSE s.client_id END AS subject_id,
    s.firm_id,
    CASE s.scope WHEN 'account' THEN s.account_subfirm ELSE s.client_subfirm END AS subfirm_id,
    s.dsr_type_id,
    t.dsr_type,
    t.identifies_firm,
    s.dsr_id,
    d.dsr_team_description AS dsr_name
FROM slots s
LEFT JOIN ref.dsr_types t ON t.dsr_type_id = s.dsr_type_id AND t.firm_id = s.firm_id
LEFT JOIN ref.dsr_teams d ON d.dsr_team_id  = s.dsr_id      AND d.firm_id = s.firm_id
