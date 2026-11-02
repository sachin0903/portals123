/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: external_id
  depends_on: [raw.accounts, raw.api_models, platform.tenancy]
  tags: [sei, entity, conformed]
  description: >
    The investment models SEI's accounts name — DISCOVERED from the accounts that use them,
    because SEI's own two model reports are declared and permanently empty.
  checks:
    - not_null: external_id
    - unique: external_id
    - not_null: key
    - not_null: firm_id
*/

-- Without this relation `platform.accounts.model_key` resolves against nothing. That column reads
-- `MODEL_ID` (§10.2 fixed it from the always-empty `STRATEGY_ID`) and its `link:model` parent needs
-- a catalogue to point at, so **85% of SEI accounts carried a model the book could not name** — the
-- same shape as the 311-of-376 Seccl accounts whose `model_id` was NULL until `_upsert_account`
-- gained the parameter.
--
-- ============================================================================================
-- DISCOVERED FROM THE ACCOUNTS, BECAUSE SEI'S OWN MODEL REPORTS ARE EMPTY.
-- ============================================================================================
--
-- The drop declares two reports that should carry a model catalogue, and both are **0 rows on all
-- 74 drops** of the p04 corpus:
--
--   ModelsIntExt  16 columns -- MODEL_NAME, MODEL_TYPE, MODEL_STATUS, RISK_RATING_ID,
--                               INVESTMENT_OBJECTIVE_ID, MODEL_SHORT_NAME…   0 rows
--   ModelAlloc    22 columns -- ALLOCATION_ID, TARGET_PCT, INSTRUMENT_ID…    0 rows
--
-- So the catalogue is discovered from what is IN USE, exactly as `platform.products` and
-- `platform.product_platforms` are discovered from the same report. That is not a lesser answer
-- for the purpose: the only thing `model_key` needs is a row to resolve against, and a model no
-- account names is a model no surface asks about.
--
-- Measured on 2026-03-27 (14,462 account rows):
--
--   MODEL_ID                   85% populated, **49 distinct**
--   IMPLEMENTATION_MODEL_NAME   6% populated,   23 distinct   <-- the only name in the drop
--   STRATEGY_ID / STRATEGY_NAME 0%
--   CURRENCY_SWEEP_MODEL_ID     0%
--
-- **So 26 of the 49 models have no name anywhere in the file.** `PortfolioGroups` carries
-- `MODEL_ID` but its name columns are the group, the portfolio and the account. The ids are
-- not a `Reference` code set. The name comes from `raw.api_models`: one assignment read per
-- distinct model (`byAccount` against a portfolio that already names it). That read returns
-- the name and the model type. It does not return a target return, an ongoing charge, or the
-- target holdings — those reports are the empty `ModelsIntExt` and `ModelAlloc` above — so
-- `allocations` stays an empty array and no model version is minted.
--
-- An unnamed model is still published with its id and no name. The sink coalesces `name` to
-- `external_id`, and replaces that placeholder on a later run once this read has a name. A
-- name a firm has since typed, different from the id, is left alone.
--
-- ============================================================================================
-- WHAT SEI DOES NOT PUBLISH, AND WHY IT IS A TYPED NULL RATHER THAN AN ABSENT COLUMN.
-- ============================================================================================
--
-- Registering `models` registers FOUR entities: the model itself plus `model_platforms`,
-- `model_allocations` (a procedure) and `model_commentary`, all sourced from this one relation.
-- Every column their projections read must therefore exist here even where SEI says nothing —
-- an absent column is a Binder Error that aborts the whole tenant's projection and lands as
-- "this firm has no data", which is the trap `platform.valuations`' crystallisation columns
-- record.
--
-- And each NULL is CAST. A bare `NULL` types as INTEGER in DuckDB, so a downstream join or cast
-- against a VARCHAR column silently never matches — the `key` trap that made Seccl's join to
-- `platform.nodes` fail before it was caught.
--
--   `allocations`  an empty JSON ARRAY, not NULL: `_MODEL_LINES_FROM` unnests
--                  `json_extract(allocations, '$[*]')` and `_MODELS` measures its length, so an
--                  empty array yields zero allocation lines and an `allocation_count` of 0. The
--                  sink already treats that as an OBSERVATION rather than a reject ("a model with
--                  no published allocation is not an error — the model still exists and accounts
--                  still name it"), so every SEI model is counted under `no allocations published`
--                  and no model VERSION is minted. That is correct: a version is a decision about
--                  a set of holdings, and SEI has published no holdings to decide about.
--   `commentary`   NULL, so `_MODEL_COMMENTARY_FROM`'s `WHERE s.body IS NOT NULL` excludes it.
--   `product_key`  an empty LIST. `_WRAPPER_LIST` coalesces a list, then a scalar, then `[]`, so
--                  this publishes no wrappers rather than a wrong one — SEI states which accounts
--                  use a model and never which wrappers a model is FOR, and inferring it from the
--                  accounts that happen to use it today would turn a coincidence into a rule.

WITH used AS (
    SELECT
        a."MODEL_ID"                                   AS model_id,
        a."FIRM_ID"                                    AS firm_id,
        -- The name where the drop has one. `max()` rather than `any_value()` so two rows for one
        -- model settle deterministically, and `nullif` so a blank does not beat a NULL — 94% of
        -- rows carry an empty string here, and one of those winning would name a model "".
        max(nullif(trim(a."IMPLEMENTATION_MODEL_NAME"), '')) AS model_name,
        max(a."DATE_LAST_UPDATED"::timestamp)          AS source_updated_at,
        max(a.dt)                                      AS as_of_date,
        count(*)                                       AS account_rows
      FROM raw.accounts a
     WHERE nullif(trim(a."MODEL_ID"), '') IS NOT NULL
       AND nullif(trim(a."FIRM_ID"), '') IS NOT NULL
     -- Grouped by BOTH: a model id belongs to the firm whose accounts name it, and the tenant
     -- predicate narrows on `firm_id`. Two firms naming one id would be two rows, which is the
     -- honest shape — `unique: external_id` would then fail and tell us, rather than one firm's
     -- model quietly carrying the other's name.
     GROUP BY a."MODEL_ID", a."FIRM_ID"
)
SELECT
    -- The conformed spine (docs/platform-model.md §3).
    u.model_id                                         AS external_id,
    coalesce(t.subfirm_id, u.firm_id)                  AS key,
    -- `nullif(…, [])` because an EMPTY lineage is the fan-out's GLOBAL sentinel — see
    -- platform/accounts.sql. A model is discovered from the accounts that name it, so it is
    -- NOT account-grained and keeps its own resolution; only the spelling of "unknown" moved.
    nullif(list_distinct(list_filter(
        list_concat([u.firm_id, t.subfirm_id], coalesce(t.dsr_team_ids, [])),
        x -> x IS NOT NULL
    )), []::VARCHAR[])                                 AS lineage,
    u.source_updated_at                                AS source_updated_at,
    u.as_of_date                                       AS as_of_date,
    to_json(u)                                         AS data,

    -- The SEI tenancy axis the predicate narrows on.
    u.firm_id                                          AS firm_id,

    -- The assignment read's name when it has one; the file's name otherwise. A blank from
    -- either source must not beat the other.
    coalesce(nullif(trim(api.model_name), ''), u.model_name) AS name,
    -- SEI publishes none of these about a model, apart from the type the assignment read
    -- names ("Implementation Model"). Typed NULLs — see the header. There is no target
    -- return and no ongoing charge on that read, so those stay NULL rather than a guess.
    CAST(NULL AS VARCHAR)                              AS aim,
    nullif(trim(api.model_type), '')                   AS model_type,
    CAST(NULL AS VARCHAR)                              AS decision_maker,
    CAST(NULL AS VARCHAR)                              AS risk_rating,
    CAST(NULL AS DECIMAL(8, 4))                        AS model_charge,
    CAST(NULL AS BOOLEAN)                              AS vatable,
    CAST(NULL AS BOOLEAN)                              AS suspend_rebalances,
    CAST(NULL AS DECIMAL(9, 6))                        AS cash_allocation,
    -- ACTIVE, and derived rather than defaulted: this relation is built from the accounts that
    -- name a model TODAY, so a model appearing here is one in current use. A retired model stops
    -- appearing, and `strategy: full_merge` means it stops being published — which is the same
    -- statement `ref.enrollment_templates` makes for a withdrawn template.
    'active'                                           AS status,
    CAST(NULL AS VARCHAR)                              AS status_raw,
    -- The wrappers a model is offered in: unstated by SEI, so an empty list. See the header.
    CAST([] AS VARCHAR[])                              AS product_key,
    -- The version-shaped payload the sink's siblings expand. Empty array / NULL, so no version is
    -- minted and no commentary row is written.
    '[]'                                               AS allocations,
    CAST(NULL AS VARCHAR)                              AS asset_mix,
    CAST(NULL AS VARCHAR)                              AS commentary,
    CAST(NULL AS VARCHAR)                              AS available_to_key,
    CAST(NULL AS VARCHAR)                              AS available_to_org_name
  FROM used u
  LEFT JOIN raw.api_models api
    ON api.model_id = u.model_id
   AND api.firm_id = u.firm_id
  -- The subfirm/DSR axes come from an ACCOUNT that names this model, so the model lands in the
  -- same slice its accounts do. LEFT JOIN and `max()`: a model used across two subfirms has no
  -- single one, and `lineage` containment then matches on the firm axis, which is right — a
  -- firm's model is the firm's whichever of its teams uses it.
  LEFT JOIN (
      SELECT firm_id, max(subfirm_id) AS subfirm_id, max(dsr_team_ids) AS dsr_team_ids
        FROM platform.tenancy
       GROUP BY firm_id
  ) t ON t.firm_id = u.firm_id
