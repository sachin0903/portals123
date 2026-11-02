/*
interlace:
  materialise: virtual
  strategy: merge
  key: [external_id, as_of_date]
  depends_on: [raw.scheme_detail]
  tags: [morgan_lloyd, entity, conformed]
  description: >
    A scheme's position as the ADMINISTRATOR holds it — the crystallisation split it computes,
    and the scheme's own bank balance, which is cash the client holds at a second place. Not a
    second opinion on what the account is worth: the fund total is the valuation we pushed.
  checks:
    - not_null: external_id
    - not_null: as_of_date
    - not_null: market_value
*/

-- MORGAN LLOYD IS NOT THE CUSTODIAN OF A SIPP, and every decision in this model follows from
-- that. The assets sit at SEI or Seccl; Morgan Lloyd administers the scheme. So of the six
-- figures `cam_getscheme` returns, only three are things we do not already know:
--
--   * the CRYSTALLISATION SPLIT. Morgan Lloyd computes it and no custodian in the estate does
--     (SEI's environment defines no pension wrapper at all). Drawdown eligibility turns on it.
--   * the SCHEME'S BANK BALANCE. Its own account, at the administrator, separate from the cash
--     in the custodian's account — and the figure a client cannot see anywhere today, because
--     every cash number in the portals is derived from custodian POSITIONS.
--   * the date the administrator last calculated, which is what the row is filed under.
--
-- The three fee percentages are the firm's own pricing and already live on its fee package.
--
-- **THE FUND TOTAL IS OUR OWN NUMBER COMING BACK.** `app/integrations/morgan_lloyd/valuations.py`
-- PUSHES the valuation to them from the custodian's holdings, so
-- `crystallised + uncrystallised` is the figure we last sent, re-derived on their side. It is
-- published as `market_value` because the sink and the book both require one and this is honestly
-- what the administrator holds — but it is NOT independent, and summing it with the custodian's
-- own `market_value` for the same account double-counts the investments. 0117 records that trap
-- at length; the rule is: take a VALUE from one source system, take CASH from all of them.
--
-- `merge`, NOT `replace`, and for the reason Seccl's model gives about itself: `cam_getscheme`
-- returns the CURRENT position only. There is no history to re-read, so each run contributes one
-- row per scheme and the key (scheme, date) accumulates the series a full refresh would destroy.
--
-- ONE ORG AXIS. Morgan Lloyd's tenancy is the single Microsoft tenant id the sync stamps on every
-- record, so `key` is that id and `lineage` is the one-element path holding it — the same
-- shape `platform/clients.sql` publishes, and the reason ML needs none of SEI's shape-aware
-- predicate. There is NOTHING TO JOIN for it: Morgan Lloyd publishes no node dimension and never
-- has, so a `LEFT JOIN platform.nodes` here is not a lookup with a fallback but a dependency on a
-- model that does not exist -- which fails the whole chain at apply, and a chain that cannot run
-- reads as "this firm has no data with this administrator".

WITH source AS (
    SELECT
        s.scheme_reference                                      AS scheme_reference,
        s.ml_tenant_id                                          AS ml_tenant_id,
        try_cast(s.crystallised_fund_amount AS decimal(20, 4))  AS crystallised_value,
        try_cast(s.uncrystallised_fund_amount AS decimal(20, 4)) AS uncrystallised_value,
        try_cast(s.total_bank_balance AS decimal(20, 4))        AS cash_value,
        -- `try_cast`, not `cast`: the vendor types this a Date and the transport is untyped text,
        -- so a value we cannot read becomes NULL and is dated by the fallback below rather than
        -- failing the whole model for every scheme.
        try_cast(s.funds_last_calculated_date AS date)          AS calculated_on,
        to_json(s)                                              AS data
    FROM raw.scheme_detail AS s
    WHERE s.scheme_reference IS NOT NULL
)
SELECT
    src.scheme_reference                          AS external_id,
    -- What the sink resolves the account by. It is the SCHEME reference, which is exactly what
    -- `platform.links(provider='morgan_lloyd', entity_type='account', link_type='primary')`
    -- holds — minted by `sync_client`'s `cam_createscheme` step. NOT the policy number: that is
    -- the `policy` link and only a valuation PUSH is addressed by it.
    src.scheme_reference                          AS account_external_id,
    src.ml_tenant_id                              AS key,
    [src.ml_tenant_id]                            AS lineage,
    -- The administrator's own calculation date where it gave us one, today otherwise. Dating a
    -- position by the day we read it is the same fallback Seccl's model takes, and it keeps the
    -- series honest about when the figure was asserted rather than filing it under NULL.
    coalesce(src.calculated_on, current_date)     AS as_of_date,
    -- The fund total the administrator holds -- see the note above about whose number this is.
    -- coalesce to 0 per side because a scheme with nothing crystallised publishes a NULL rather
    -- than a zero, and NULL + something is NULL: without this a half-populated scheme would
    -- publish no market_value at all and be rejected as "no value or date".
    coalesce(src.crystallised_value, 0) + coalesce(src.uncrystallised_value, 0)
                                                  AS market_value,
    -- THE NEW FACT. Can be NEGATIVE -- the vendor says so explicitly -- so it is never clamped:
    -- an overdrawn scheme is something an adviser has to see, and a floor at zero would hide it.
    src.cash_value,
    -- Morgan Lloyd publishes none of these; a typed NULL rather than a zero, which would claim
    -- a book cost of nothing and an unrealised gain of nothing on a real pension.
    NULL::decimal(20, 4)                          AS book_cost,
    NULL::decimal(20, 4)                          AS unrealised_gain,
    NULL::decimal(20, 4)                          AS opening_value,
    src.crystallised_value,
    src.uncrystallised_value,
    'GBP'                                         AS currency,
    NULL::timestamp                               AS source_updated_at,
    src.data
FROM source AS src
-- A scheme the administrator reports nothing about contributes nothing. Both halves NULL means
-- the action answered without figures, which is not the same as a fund worth zero.
WHERE src.crystallised_value IS NOT NULL OR src.uncrystallised_value IS NOT NULL
