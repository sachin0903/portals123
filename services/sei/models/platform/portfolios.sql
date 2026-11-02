/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: external_id
  depends_on: [raw.accounts, platform.tenancy, platform.accounts]
  tags: [sei, entity, conformed]
  description: >
    Portfolios — the container BELOW an account. SEI's Capital/Income split, which four of
    its write paths take a `portfolioId` for.
  checks:
    - not_null: external_id
    - unique: external_id
    - not_null: account_external_id
    - not_null: key
*/

-- `portfolioId` is REQUIRED by `createCurrencyReceipt`, `createcashdisbursement`,
-- `currencytransfercreate` and every PSM model-assignment path, and the estate has never been able
-- to supply one — `docs/contributions.md` records the SEI contribution leg as blocked on "the
-- account's SEI account-number/portfolio-id links", of which only the account number was ever
-- solved. This is the other half.
--
-- SEI ONLY. Seccl's hierarchy is firm -> node -> client -> clientProduct -> account -> position
-- with nothing below the account; it models the same Capital/Income split by opening a SECOND
-- ACCOUNT, and `clientProduct` / `portfolioGroup` are both ABOVE an account rather than inside one.
-- So Seccl publishes no such relation and must not be made to.
--
-- ============================================================================================
-- THE GRAIN. The Accounts report is not one row per portfolio, and assuming so multiplies.
-- ============================================================================================
--
-- Measured on the 2026-03-27 drop: 14,462 rows, **1,163 distinct (account, portfolio) pairs**, and
-- 1,163 distinct `PORTFOLIO_ID`s. So the report repeats each pair — up to seven times, and 362 of
-- the 1,163 pairs appear exactly seven times — because it is grained account x portfolio x
-- something further, the same shape `platform.accounts` deduplicates for its own row.
--
-- A `PORTFOLIO_ID` is GLOBALLY UNIQUE and belongs to exactly one account: zero of the 1,163 appear
-- under two account numbers. That is what makes `external_id` the key rather than a composite.
--
-- ============================================================================================
-- WHAT SEI ACTUALLY PUBLISHES HERE, measured rather than assumed.
-- ============================================================================================
--
--   PORTFOLIO_TYPE        189 Capital (13,463 rows) / 190 Income (999). An investment-PURPOSE
--                         split, not a tax wrapper — the distinction that made it and ACCOUNT_TYPE
--                         indistinguishable from their names alone (see platform/accounts.sql).
--   LINKED_PORTFOLIO_ID   on exactly the Income rows, naming the Capital portfolio it belongs
--                         with. Resolves within the SAME account on 999 of 999 rows.
--   PORTFOLIO_PURPOSE     the 10-member Reference set (Client Directed, Advisor Guided, Managed
--                         Account...) and it is **effectively unpopulated**: blank on 12,588 rows
--                         and '0' on 1,874. Published as stated — `nullif` on the zero, because a
--                         code of 0 is not a member of the set and storing it would look like an
--                         answer.
--   GROUP_ID              **0% populated**, as is PORTFOLIO_AGGREGATION_SERVICE_ACCOUNT and
--                         ACCOUNT_SUB_TYPE 7 "Portfolio Aggregation". So nothing here rolls
--                         several accounts up: a portfolio is a bucket inside one account and
--                         there is no aggregation container to model.
WITH latest AS (SELECT max(dt) AS dt FROM raw.accounts),
current AS (
    SELECT a.*
    FROM raw.accounts a
    JOIN latest ON a.dt = latest.dt
    -- One row per portfolio, deterministically. Ordered by the account's own last-updated so the
    -- surviving row is the freshest statement about it, then by ACCOUNT_NUMBER so the pick does
    -- not move between runs on equal dates.
    QUALIFY row_number() OVER (
        PARTITION BY a."PORTFOLIO_ID"
        ORDER BY a."DATE_LAST_UPDATED" DESC NULLS LAST, a."ACCOUNT_NUMBER"
    ) = 1
)
SELECT
    -- The conformed spine (docs/platform-model.md §3). The tenancy axes come from the ACCOUNT, via
    -- platform.tenancy, because a portfolio has no grant of its own — it is reached through the
    -- account that owns it, and a portfolio projected without its account would be unaddressable.
    p."PORTFOLIO_ID"                                AS external_id,
    -- THE ORG COMES FROM THE ACCOUNT. platform.accounts resolves key/lineage once (see its
    -- header) and every account-grained relation selects it, so a fact and the account it is
    -- about cannot disagree about which node owns them. Recomputed here it had diverged, and
    -- one of the spellings of "unknown" was `[]` — which the fan-out reads as GLOBAL.
    acc.key,
    acc.lineage,
    p."DATE_LAST_UPDATED"::timestamp                AS source_updated_at,
    to_json(p)                                      AS data,

    -- The join key the merge resolves the account by. Same value `platform.accounts` publishes as
    -- its own `external_id`, under the conformed name.
    p."ACCOUNT_NUMBER"                              AS account_external_id,
    p."PORTFOLIO_ID"                                AS portfolio_id,
    p."PORTFOLIO_NAME"                              AS name,

    -- Canonical, from the two codes this environment uses. An unlisted code resolves to NULL
    -- rather than being guessed: `portfolio_type` is what a caller reads to decide which portfolio
    -- an income instruction goes to, and a wrong word there sends money to the wrong bucket.
    CASE p."PORTFOLIO_TYPE"
        WHEN '189' THEN 'capital'
        WHEN '190' THEN 'income'
    END                                             AS portfolio_type,
    p."PORTFOLIO_TYPE"                              AS portfolio_type_raw,

    -- THE DEFAULT a write goes to when the caller names none. Capital, and stated as "is the
    -- Capital one" rather than "is not Income", so a third code arriving later is NOT silently
    -- promoted to principal.
    (p."PORTFOLIO_TYPE" = '189')                    AS is_principal,

    -- The vendor's own pairing. Not resolved to a book id here — see 0119 for why a
    -- self-referencing FK would be permanently NULL.
    nullif(trim(p."LINKED_PORTFOLIO_ID"), '')       AS linked_external_id,
    -- Published as stated, minus the '0' that is not a member of the code set.
    nullif(nullif(trim(p."PORTFOLIO_PURPOSE"), ''), '0') AS purpose,

    p."DATE_PORTFOLIO_OPENED"                       AS opened_on,
    p."DATE_PORTFOLIO_CLOSED"                       AS closed_on,

    t.firm_id,
    t.subfirm_id,
    t.dsr_team_ids,
    t.adviser_firm_dsr_ids,
    p."DATE_LAST_UPDATED"                           AS as_of_date
FROM current p
LEFT JOIN platform.tenancy t ON t.account_number = p."ACCOUNT_NUMBER"
LEFT JOIN platform.accounts acc ON acc.external_id = p."ACCOUNT_NUMBER"
WHERE nullif(trim(p."PORTFOLIO_ID"), '') IS NOT NULL
