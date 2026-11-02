/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: external_id
  depends_on: [raw.accounts, derived.account_terms, platform.nodes, raw.portfolio_summaries, raw.transactions]
  tags: [seccl, entity, conformed]
  description: >
    Accounts, conformed. Seccl had no accounts model at all — the concept existed only
    inside positions and transactions as enrichment columns — so this is the first place
    an account is a row of its own.
  checks:
    - not_null: external_id
    - unique: external_id
    - not_null: key
*/

-- ONE SOURCE. `/account/list` (raw.accounts) already returns everything this model needs:
-- wrapper_detail, lead_client_id, the clients[] party list, and the valuation block. The
-- per-account `/account/{id}` fan-out (raw.account_details) is a separate, optional pull, and
-- depending on it made this model unbuildable whenever that pull had not run -- which is how
-- the first end-to-end attempt failed. Verified against staging: 376 accounts.
-- node_id is scalar-or-list: Seccl sends `["9"]` on clients and `41` on positions (the
-- quirk app/platforms.py::_SLICE and fanout._predicate both handle). Taking the first
-- element of the list form, or the scalar as-is, is what makes key one value.
--
-- THE WRAPPER ALLOWANCE. Seccl states it on `GET /portfolio/summary/{firm}/{clientId}`,
-- in `products[]`. `remainingSubscriptionAmount` is the amount still available, at the
-- portfolio (client) for an ISA and sometimes per `accountId` for a JISA. The summary
-- list's `id` is that client id (Seccl's own attribute list). `clientProductId` is the
-- match when a summary row is not keyed by the client the account names — the product
-- id on the wrapper is the same id the products array carries. Seccl does not state the
-- annual limit. `subscriptionAmount` on a transaction is how much that movement
-- changed the allowance, so used + remaining is the limit — the same sum SEI publishes,
-- which is what stays right for a flexible ISA. A tax year is the current UK year because
-- the figure is the live allowance, not a historical one.
WITH tax_year AS (
    SELECT CASE
        WHEN current_date >= make_date(year(current_date), 4, 6)
            THEN make_date(year(current_date), 4, 6)
        ELSE make_date(year(current_date) - 1, 4, 6)
    END AS starts_on
),
allowance_rows AS (
    SELECT
        cast(ps.id AS varchar) AS client_id,
        upper(json_extract_string(item, '$.wrapperType')) AS wrapper_type,
        nullif(json_extract_string(item, '$.accountId'), '') AS account_id,
        nullif(json_extract_string(item, '$.clientProductId'), '') AS client_product_id,
        try_cast(json_extract_string(item, '$.remainingSubscriptionAmount') AS decimal(20, 4))
            AS remaining
    FROM raw.portfolio_summaries AS ps,
    LATERAL unnest(coalesce(try_cast(ps.products AS json[]), [])) AS u(item)
),
account_keys AS (
    SELECT
        cast(a.id AS varchar) AS account_id,
        coalesce(
            cast(a.lead_client_id AS varchar),
            coalesce(json_extract_string(a.clients, '$[*]'), [])[1]
        ) AS client_id,
        nullif(a.wrapper_detail ->> 'clientProductId', '') AS client_product_id,
        upper(coalesce(
            a.wrapper_detail ->> 'wrapperType',
            nullif(a.account_type, 'Wrapper')
        )) AS product_key
    FROM raw.accounts AS a
),
subscription_used AS (
    SELECT
        cast(t.account_id AS varchar) AS account_id,
        sum(try_cast(t.subscription_amount AS decimal(20, 4))) AS used
    FROM raw.transactions AS t
    CROSS JOIN tax_year AS y
    WHERE try_cast(t.transaction_date AS date) >= y.starts_on
      AND try_cast(t.subscription_amount AS decimal(20, 4)) IS NOT NULL
    GROUP BY 1
),
-- Client-level remaining (an ISA, or a JISA Seccl did not split by account) shares one
-- used figure. Summing per account and then max()-ing on the read would drop a sibling.
client_wrapper_used AS (
    SELECT ak.client_id, ak.product_key, sum(su.used) AS used
    FROM account_keys AS ak
    JOIN subscription_used AS su ON su.account_id = ak.account_id
    WHERE ak.product_key IN ('ISA', 'JISA')
    GROUP BY 1, 2
),
allowance AS (
    SELECT
        ak.account_id,
        ar.remaining,
        CASE
            WHEN ar.account_id IS NOT NULL THEN su.used
            ELSE cwu.used
        END AS used
    FROM account_keys AS ak
    JOIN allowance_rows AS ar
        ON ar.wrapper_type = ak.product_key
       AND (
            ar.account_id = ak.account_id
            OR (
                ar.account_id IS NULL
                AND ar.client_product_id IS NOT NULL
                AND ar.client_product_id = ak.client_product_id
            )
            OR (
                -- A client-keyed row that names a product belongs to that wrapper only.
                -- Attaching it to every account of the client is how a sibling's leftover
                -- replaced this account's own remaining.
                ar.account_id IS NULL
                AND ar.client_id = ak.client_id
                AND (
                    ar.client_product_id IS NULL
                    OR ar.client_product_id = ak.client_product_id
                )
            )
       )
    LEFT JOIN subscription_used AS su ON su.account_id = ak.account_id
    LEFT JOIN client_wrapper_used AS cwu
        ON cwu.client_id = ak.client_id AND cwu.product_key = ak.product_key
    WHERE ak.product_key IN ('ISA', 'JISA')
    -- A product id is more specific only when it is THIS wrapper's. A sibling product, or a
    -- second summary that merely carries some clientProductId, joins on the client and must
    -- not outrank the client's own remaining.
    QUALIFY row_number() OVER (
        PARTITION BY ak.account_id
        ORDER BY CASE
            WHEN ar.account_id IS NOT NULL THEN 0
            WHEN ar.client_product_id IS NOT NULL
                 AND ar.client_product_id = ak.client_product_id THEN 1
            WHEN ar.client_id = ak.client_id AND ar.client_product_id IS NULL THEN 2
            WHEN ar.client_id = ak.client_id THEN 3
            ELSE 4
        END
    ) = 1
)
SELECT
    cast(a.id AS varchar)                                    AS external_id,
    coalesce(try_cast(cast(a.node_id AS varchar) AS varchar[])[1], cast(a.node_id AS varchar))
                                                             AS key,
    -- `o.lineage`, NOT `coalesce(o.lineage, [])`, which is what this said. An EMPTY lineage is
    -- the fan-out's GLOBAL sentinel (`book_sink.predicate.conformed`: "an empty lineage means
    -- global reference data … which projects whole") and Seccl IS filtered by containment
    -- today — so a row whose node this provider's tree cannot resolve was projected into EVERY
    -- enabled tenant's slice rather than into none. NULL is what "no org" spells: the
    -- predicate tests it to false. (0 rows on the dev book, where all 376 accounts resolve a
    -- node; the hole is one unresolvable node wide, and it takes that account's positions,
    -- transactions and valuations with it.)
    o.lineage                                              AS lineage,
    a.name                                                   AS account_name,
    -- Seccl's wrapper vocabulary ('GIA', 'ISA', 'SIPP', ...) already matches
    -- platform.product_type; the raw value is kept so an unmapped wrapper is visible
    -- rather than silently NULL.
    -- wrapperType FIRST, and account_type only when it says something. Verified against
    -- staging: `account_type` is the literal 'Wrapper' on all 376 accounts -- it is Seccl's
    -- account CLASS, not the wrapper type -- so the old coalesce order resolved every single
    -- account's product to 'Wrapper' and the real values (GIA 192, PENSION 97, ISA 65,
    -- JISA 17, TPPENSION 5) were never read.
    coalesce(a.wrapper_detail ->> 'wrapperType', nullif(a.account_type, 'Wrapper')) AS product_key,
    a.account_type                                           AS account_type_raw,
    -- draft | active | closed is the canonical set (platform.account_status). Seccl
    -- says Active / Pending, and 'Pending' is an account that exists but has not
    -- completed onboarding -- which is what 'draft' means canonically.
    CASE lower(a.status)
        WHEN 'active' THEN 'active'
        WHEN 'pending' THEN 'draft'
        WHEN 'closed' THEN 'closed'
    END                                                      AS status,
    a.status                                                 AS status_raw,
    coalesce(a.currency, 'GBP')                              AS base_currency,
    -- Discretionary management is a property of the wrapper in Seccl, and it is what
    -- makes an account model-managed rather than advised.
    try_cast(a.wrapper_detail ->> 'discretionary' AS boolean) AS is_discretionary,
    try_cast(a.wrapper_detail ->> 'advised' AS boolean)       AS is_advised,
    try_cast(a.wrapper_detail ->> 'trust' AS boolean)         AS is_trust,
    a.wrapper_detail ->> 'assetAllocationId'                 AS model_key,
    a.wrapper_detail ->> 'assetAllocationName'               AS model_name,
    a.wrapper_detail ->> 'clientProductId'                   AS product_external_id,
    -- The parties on the account, tolerant of BOTH shapes Seccl uses. The documented
    -- (Postman) shape is an array of objects carrying owner/decisionMaker flags; staging
    -- actually sends an array of bare id strings (`["02659H7"]`), and reading it as objects
    -- yielded a list of NULLs -- which is why every account materialised with no client on the
    -- first end-to-end run. Objects first, then scalars, then empty.
    coalesce(
        nullif(
            list_filter([x ->> '$.id' FOR x IN coalesce(try_cast(a.clients AS json[]), [])],
                        y -> y IS NOT NULL),
            []
        ),
        coalesce(json_extract_string(a.clients, '$[*]'), []),
        []
    )                                                        AS owner_external_ids,
    -- `lead_client_id` is NULL throughout staging, so the first owner is the lead in practice.
    coalesce(
        cast(a.lead_client_id AS varchar),
        coalesce(json_extract_string(a.clients, '$[*]'), [])[1]
    )                                                        AS lead_client_external_id,
    NULL::date                                               AS opened_on,
    NULL::date                                               AS closed_on,
    NULL::date                                               AS last_traded_on,
    -- The fee package the firm has priced this account against (`platform.fee_packages`). It comes
    -- from the account's own terms, not from `/account/list` -- which is why this was NULL.
    t.adviser_fee_package_key                                AS fee_package_key,
    NULL::varchar                                           AS mifid_classification,
    -- Used is NULL when no transaction carried a subscription amount: inventing 0 would say
    -- the client had subscribed nothing. The limit is used + remaining, and only when both
    -- exist — a remaining figure alone is not a limit (a flexible ISA's remaining can exceed
    -- the statutory allowance). Repeated onto each account of the wrapper; readers take max().
    al.used                                                  AS allowance_used,
    CASE
        WHEN al.used IS NOT NULL AND al.remaining IS NOT NULL THEN al.used + al.remaining
    END                                                      AS allowance_limit,
    al.remaining                                             AS allowance_remaining,
    CASE
        WHEN al.remaining IS NOT NULL THEN
            strftime(y.starts_on, '%Y') || '/' || strftime(y.starts_on + INTERVAL 1 YEAR, '%y')
    END                                                      AS allowance_tax_year,
    false                                                    AS is_dormant,
    false                                                    AS is_frozen,
    -- The account list carries no audit block of its own (verified against staging), so the
    -- extract date is the only assertion time available.
    -- ── The account's own terms ────────────────────────────────────────────────────────────────
    -- What it is charged, and what money moves on it by standing arrangement. All three are shaped
    -- for the book's existing jsonb columns (`platform.accounts.{fees,payments,withdrawals}`, created
    -- by 0002 and never written to until now) -- see `derived/account_terms.sql`, which is where the
    -- shaping and the reasoning live.
    --
    -- LEFT JOIN, so a firm whose per-account fan-out has not landed yet gets NULL terms and an account
    -- that still builds. The surfaces read a NULL as "not recorded", which is honest; the alternative
    -- was making this model depend on a 376-call pull, which is what made the first end-to-end
    -- attempt unbuildable.
    coalesce(t.fees, [])                                     AS fees,
    coalesce(t.payments, [])                                 AS payments,
    coalesce(t.withdrawals, [])                              AS withdrawals,
    -- Seccl holds bank details on the client, not the account. The shared accounts
    -- projection still reads the column; NULL here leaves the client's bank as the
    -- fallback and does not clear an entry the firm typed on the account.
    NULL::JSON                                               AS bank_accounts,
    t.adviser_charge,
    t.adviser_tiered_charges,
    t.model_charge,
    t.auto_disinvest,
    NULL::timestamp                                          AS source_updated_at,
    current_date                                             AS as_of_date,
    to_json(a)                                               AS data
FROM raw.accounts AS a
CROSS JOIN tax_year AS y
LEFT JOIN derived.account_terms AS t
    ON t.account_external_id = cast(a.id AS varchar)
LEFT JOIN platform.nodes AS o
    ON o.key = coalesce(try_cast(cast(a.node_id AS varchar) AS varchar[])[1], cast(a.node_id AS varchar))
LEFT JOIN allowance AS al ON al.account_id = cast(a.id AS varchar)
