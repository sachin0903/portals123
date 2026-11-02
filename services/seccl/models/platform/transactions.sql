/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: external_id
  depends_on:
    [raw.transactions, raw.accounts, raw.transaction_codes, derived.book_costs, platform.nodes]
  tags: [seccl, entity, conformed]
  description: >
    Transactions, conformed — the hourly baseline overlaid with webhook deltas, keeping
    the business derivations (resolved ISIN, executed overrides, order grouping, charge
    split, origin, the custodian's own statement bucket) and publishing them under the
    names every provider uses.
  checks:
    - not_null: external_id
    - unique: external_id
*/

-- One row per transaction id. Destination-account doubling (the reversed In/Out
-- perspective for the receiving account) belongs to the balance layers, not this grain.
--
-- CANONICAL TYPE. Seccl's taxonomy is (transactionType, movementType, transactionSubType)
-- -- Order/Invest is a buy, Order/Raise is a sell, Payment/In is a deposit. Where the
-- combination is not recognised `type` is NULL rather than guessed into 'adjustment':
-- the materialiser records an unmapped transaction as a reject, and a reject is
-- recoverable where money filed under the wrong type is not. source_type and
-- source_subtype carry Seccl's own words either way.
-- NO WEBHOOK OVERLAY. The staging tables are event ENVELOPES, not records: verified against
-- staging, `staging.positions` carries only (_event_key, firm_id, id, account_id, position_type,
-- update_date, _offset, _ingested_at), `staging.clients` only (client_id, update_date) and
-- `staging.transactions` only (id, transaction_type, status, update_date). The previous
-- `union all by name` over the baseline therefore did the opposite of what it looked like: a
-- record with a webhook event was EXCLUDED from the baseline by the NOT EXISTS and replaced by
-- a stub whose every other column was NULL. It is latent today only because the streams are
-- empty. The stream is a change SIGNAL — what it should drive is a targeted re-fetch of the
-- changed id, not a substitution — so until that exists the baseline is the record.
WITH current_transactions AS (
    SELECT * FROM raw.transactions
),

-- >1 distinct account under one eventId => a model-driven (DFM) rebalance.
event_accounts AS (
    SELECT event_id, count(DISTINCT account_id) AS n_accounts
    FROM current_transactions
    WHERE event_id IS NOT NULL
    GROUP BY event_id
),

-- ── The custodian's own statement bucket, from the custodian's own reference data ─────────────
--
-- `/static/transactiontype` (raw.transaction_codes) carries `accountReportGroup` on every code, and
-- it is the SAME grouping Seccl's `/account/report` endpoint reports against: Payments In/Out,
-- Transfers In/Out, Product Transfers In/Out, Internal Transfers In/Out, Charges, Income,
-- Transactions Realised, Investments Realised in Period, N/A. So a statement's headline breakdown is
-- derived from the ledger with the vendor's mapping rather than read back out of a vendor REPORT
-- (which would have to be kept in step and cannot be filtered down to the rows behind a figure).
--
-- Hand-writing the map was the obvious alternative and would have been wrong twice: the live list
-- carries `Product Transfers Out`, which the vendor's own documented sample omits, and it is 77 codes
-- here against 64 in that sample. It grows; this reads it.
--
-- FIVE THINGS ABOUT THE JOIN, each measured against the dev corpus rather than assumed.
--
-- 1. A transaction does not carry the four-letter code. `/portfoliotransaction` publishes
--    (transactionType, transactionSubType, movementType) and no `transactionCode`, so the triple IS
--    the join key — which is also why raw.transaction_codes declares `(id, sub_type, movement_type)`
--    as its own key: `id` alone is not unique there either.
--
-- 2. ONE CODE ROW COVERS BOTH DIRECTIONS when the group is list-valued. `ACST` (account cash
--    transfer) and `ASTO` (account stock transfer) are registered `movementType = 'Out'` with
--    `accountReportGroup = ['Internal Transfers Out', 'Internal Transfers In']` — one row for a
--    movement that is Out of one account and In to the other. Joining on the full triple therefore
--    misses every INBOUND internal transfer (30 of them on the dev corpus). So the join ignores the
--    code's own direction for a list-valued group and the TRANSACTION's `movement_type` picks the
--    element. This is the same shape trap `_sei_predicate` records hitting three times: a uniform
--    shape over a relation that publishes two.
--
-- 3. AMBIGUITY IS REFUSED, NOT GUESSED. `('Payment','Deposit','In')` matches BOTH `CPIN` (Client
--    Payment → Payments In) and `HPDC` (Dividend Tax Reclaim → Income), and nothing on the
--    transaction distinguishes them — `/portfoliotransaction` carries no narrative. Those are
--    different lines of a statement (money the client paid in, against money the account earned), so
--    where the surviving rows disagree the group is NULL: the statement counts it under "other",
--    where it is visible and can be corrected, rather than filing it somewhere plausible. Pairs that
--    differ only by DIRECTION (`MGSI`/`MGSO`, `CTIN`/`CTOU`, `PTCI`/`PTCO`, BUY/SELL under
--    `Order/At Best`) are resolved by the triple and never reach this.
--
-- 4. `N/A` AND `N/a` ARE THE SAME ANSWER. The vendor's list spells it both ways — 17 codes and 3 —
--    which without normalising makes `Order/At Best` look like four disagreeing groups instead of
--    two, and pushes every market order into the refusal above. Normalised to NULL, which is what
--    "no statement bucket" means.
--
-- 5. THE COLUMN IS A VARCHAR THAT IS SOMETIMES A JSON ARRAY. `records_to_table` serialises any
--    nested value to a JSON string and widens a column whose scalar types disagree, so
--    `account_report_group` lands as `Payments In` on 75 codes and
--    `["Internal Transfers Out", "Internal Transfers In"]` on two. Both are parsed to a LIST here,
--    which is the same shape-tolerance `platform/accounts.sql` applies to `a.clients` — and the
--    reason this reads the wire column rather than the v1 extract's `account_report_groups`, which
--    was already a real list and would have bound against a column the live warehouse has not got.
code_groups AS (
    SELECT
        tc.transaction_type,
        tc.transaction_sub_type,
        -- A SINGLE-valued group keeps the code's own direction — `Charges` names none, and deriving
        -- one from the label would leave every charge unmatched. A LIST-valued group takes the
        -- direction its own label names, because one code row covers both legs (note 2).
        CASE
            WHEN tc.spans_directions
                THEN CASE WHEN grp LIKE '%Out' THEN 'Out' WHEN grp LIKE '%In' THEN 'In' END
            ELSE tc.movement_type
        END                                                              AS movement_type,
        -- N/A in either spelling is the ABSENCE of a bucket, not a bucket called "N/A" (note 4).
        CASE WHEN upper(grp) = 'N/A' THEN NULL ELSE grp END              AS report_group,
        tc.subscription
    FROM (
        SELECT
            transaction_type,
            transaction_sub_type,
            movement_type,
            subscription,
            -- One list either way. Scalar first, because that is 75 of the 77 codes.
            CASE
                WHEN starts_with(trim(cast(account_report_group AS varchar)), '[')
                    THEN coalesce(
                        json_extract_string(cast(account_report_group AS varchar), '$[*]'), []
                    )
                WHEN account_report_group IS NULL THEN []
                ELSE [cast(account_report_group AS varchar)]
            END                                                          AS groups,
            starts_with(trim(cast(account_report_group AS varchar)), '[') AS spans_directions
        FROM raw.transaction_codes
    ) AS tc,
    UNNEST(tc.groups) AS u(grp)
),

-- One resolved answer per (type, sub_type, movement_type) the ledger can present.
resolved_groups AS (
    SELECT
        transaction_type,
        transaction_sub_type,
        movement_type,
        -- Exactly one surviving answer, or none. `count(DISTINCT …) = 1` refuses CPIN/HPDC while
        -- letting the N/A-vs-N/a duplicate through as the single answer it really is (notes 3, 4).
        CASE
            WHEN count(DISTINCT report_group) = 1 THEN min(report_group)
        END                                                              AS report_group,
        -- Whether the code consumes an ISA/JISA subscription allowance. Same refusal rule: a pair
        -- that disagrees about it says nothing, because the remaining allowance is money.
        CASE
            WHEN count(DISTINCT subscription) = 1 THEN min(subscription)
        END                                                              AS is_subscription
    FROM code_groups
    GROUP BY 1, 2, 3
),

-- node_id is scalar-or-list: Seccl sends `["9"]` on clients and `41` on positions (the
-- quirk app/platforms.py::_SLICE and fanout._predicate both handle). Taking the first
-- element of the list form, or the scalar as-is, is what makes key one value.
derived AS (
    SELECT
        cast(t.id AS varchar)                                          AS external_id,
        coalesce(try_cast(cast(t.node_id AS varchar) AS varchar[])[1], cast(t.node_id AS varchar))
                                                                       AS key,
        cast(t.account_id AS varchar)                                  AS account_external_id,
        t.transaction_type                                             AS source_type,
        t.transaction_sub_type                                         AS source_subtype,
        t.movement_type,
        lower(t.status)                                                AS status,
        t.status                                                       AS status_raw,
        t.method,
        coalesce(t.currency, 'GBP')                                    AS currency,
        try_cast(t.transaction_date AS date)                           AS transaction_date,
        try_cast(t.information ->> 'eventExDate' AS date)              AS trade_date,
        try_cast(t.intended_settlement_date AS date)                   AS settlement_date,
        try_cast(t.completed_date AS timestamp)                        AS posted_at,
        try_cast(t.created_date AS timestamp)                          AS created_at,
        try_cast(t.audit_details ->> 'updateDate' AS timestamp)        AS source_updated_at,
        cast(t.asset_id AS varchar)                                    AS instrument_key,
        coalesce(cast(t.isin AS varchar), t.information ->> 'originalIsin') AS isin,
        t.asset_name,
        -- Every numeric is try_cast on BOTH sides of the coalesce, not taken at its
        -- landed type. Seccl sends some of these as strings on some records, so the
        -- landing layer widens the whole column to VARCHAR while its partner stays
        -- DOUBLE -- verified in production, where `executedQuantity` and `bookValue`
        -- land VARCHAR against a DOUBLE `quantity`/`transferValue`. An untyped
        -- coalesce fails to bind ("cannot mix VARCHAR and DOUBLE"), and which side
        -- drifts changes with the batch, so the cast has to be unconditional.
        coalesce(try_cast(t.executed_quantity AS decimal(28, 8)),
                 try_cast(t.quantity AS decimal(28, 8)))               AS quantity,
        try_cast(t.execution_details ->> 'price' AS decimal(20, 8))    AS unit_price,
        coalesce(try_cast(t.executed_amount AS decimal(20, 4)),
                 try_cast(t.amount AS decimal(20, 4)))                 AS gross_amount,
        try_cast(t.consideration AS decimal(20, 4))                    AS net_amount,
        list_sum([try_cast(x ->> 'amount' AS decimal(20, 4))
                  FOR x IN coalesce(try_cast(t.charges AS json[]), [])]) AS fee_amount,
        list_sum([try_cast(x ->> 'amount' AS decimal(20, 4))
                  FOR x IN coalesce(try_cast(t.charges AS json[]), [])
                  IF x ->> 'chargeType' = 'Dealing'])                  AS dealing_charges,
        list_sum([try_cast(x ->> 'amount' AS decimal(20, 4))
                  FOR x IN coalesce(try_cast(t.charges AS json[]), [])
                  IF x ->> 'chargeType' <> 'Dealing'])                 AS other_charges,
        try_cast(t.information ->> 'equalisationAmountPayCy' AS decimal(20, 4))
                                                                       AS equalisation_payment,
        -- Seccl's own per-movement book value, kept under a name that says whose it is. It is NOT
        -- the conformed `book_cost` any more: that is the POOLED figure from `derived.book_costs`
        -- (see the outer SELECT), and having both spellings of "book cost" in one model is how the
        -- unpooled one ended up being reported as a realised gain. `derived.book_costs` reads this
        -- as one of its cost inputs; nothing else should.
        try_cast(t.book_value AS decimal(20, 4))                       AS provider_book_value,
        try_cast(t.transfer_value AS decimal(20, 4))                   AS transfer_value,
        coalesce(cast(t.link_id AS varchar),
                 cast(t.core_id AS varchar), cast(t.id AS varchar))    AS order_key,
        t.event_id,
        cast(t.destination_account AS varchar)                         AS destination_account,
        t.is_pension_transfer,
        t.transfer_status,
        t.previous_provider,
        t.previous_provider_name,
        to_json(t)                                                     AS data
    FROM current_transactions AS t
)

SELECT
    d.external_id,
    d.key,
    -- `o.lineage`, NOT `coalesce(o.lineage, [])`, which is what this said. An EMPTY lineage is
    -- the fan-out's GLOBAL sentinel (`book_sink.predicate.conformed`: "an empty lineage means
    -- global reference data … which projects whole") and Seccl IS filtered by containment
    -- today — so a row whose node this provider's tree cannot resolve was projected into EVERY
    -- enabled tenant's slice rather than into none. NULL is what "no org" spells: the
    -- predicate tests it to false. (0 rows on the dev book, where all 376 accounts resolve a
    -- node; the hole is one unresolvable node wide, and it takes that account's positions,
    -- transactions and valuations with it.)
    o.lineage                                                        AS lineage,
    d.account_external_id,
    CASE
        WHEN d.source_type = 'Order' AND d.movement_type = 'Invest' THEN 'buy'
        WHEN d.source_type = 'Order' AND d.movement_type IN ('Raise', 'Sell') THEN 'sell'
        WHEN d.source_type = 'Payment' AND d.movement_type = 'In' THEN 'deposit'
        WHEN d.source_type = 'Payment' AND d.movement_type = 'Out' THEN 'withdrawal'
        WHEN d.source_type IN ('Product Transfer', 'Stock Transfer', 'Cash Transfer')
             AND d.movement_type = 'In' THEN 'transfer_in'
        WHEN d.source_type IN ('Product Transfer', 'Stock Transfer', 'Cash Transfer')
             AND d.movement_type = 'Out' THEN 'transfer_out'
        WHEN d.source_subtype ILIKE '%fee%' OR d.source_subtype ILIKE '%charge%' THEN 'fee'
        WHEN d.source_subtype ILIKE '%interest%' THEN 'interest'
        WHEN d.source_subtype ILIKE '%dividend%' OR d.source_subtype ILIKE '%income%'
             OR d.source_subtype ILIKE '%distribution%' THEN 'dividend'
        WHEN d.source_subtype ILIKE '%tax%' THEN 'tax'
        WHEN d.source_type = 'Cash' AND d.movement_type = 'In' THEN 'deposit'
        WHEN d.source_type = 'Cash' AND d.movement_type = 'Out' THEN 'withdrawal'
        WHEN d.source_type = 'Notional' THEN 'adjustment'
    END                                                                AS type,
    -- The custodian's own statement bucket, BESIDE the canonical type rather than instead of it: the
    -- twelve canonical members are deliberately coarser and cannot say "internal", where a cash
    -- transfer between two of one client's accounts and one from another provider are different lines
    -- of a statement. NULL where the reference list does not cover the combination or disagrees about
    -- it (notes 3 and 4) — the statement counts those under "other", visible rather than misfiled.
    rg.report_group                                                    AS report_group_kind,
    d.source_type,
    d.source_subtype,
    d.movement_type,
    d.instrument_key,
    d.isin,
    d.asset_name,
    d.quantity,
    d.unit_price,
    d.gross_amount,
    d.net_amount,
    -- WHAT THIS MOVEMENT CHARGED, and a cash fee's amount is the MOVEMENT'S OWN.
    --
    -- `d.fee_amount` is the sum of the `charges` array, which is where a TRADE carries its dealing and
    -- other charges. A cash fee — a platform fee, a DFM fee, an adviser fee — carries no `charges` at
    -- all: the fee IS the movement, and its figure is in `amount`. Verified across the dev corpus:
    -- `charges` is NULL on all 1,593 fee movements while `amount` holds every one of them
    -- (£184,639.90 of DFM fees, £24,634.50 of platform fees, £606.00 of ad-hoc adviser fees), so this
    -- column was NULL for every charge a firm actually pays and the drawer's charge breakdown had
    -- nothing to show on a fee row.
    --
    -- Keyed on the movement being a CHARGE by the custodian's own reference data rather than on a list
    -- of sub-types, and `abs()` because a cost is read as a magnitude beside the charges it sits with.
    coalesce(
        d.fee_amount,
        CASE WHEN rg.report_group = 'Charges' THEN abs(d.gross_amount) END
    )                                                                  AS fee_amount,
    NULL::decimal(20, 4)                                               AS tax_amount,
    -- What this movement did to the wrapper's annual subscription allowance, signed by direction:
    -- a client payment INTO an ISA consumes allowance, a withdrawal or transfer out reclaims it (on a
    -- flexible ISA), and both are flagged `subscription` by the vendor. DERIVED from the flag and the
    -- amount rather than read off a field, because `/portfoliotransaction` does not publish one —
    -- the vendor states a `subscriptionAmount` only on its own report rows, and depending on a column
    -- the wire does not carry is how a model fails to bind on the firm that has none.
    -- Remaining allowance is `platform.accounts.allowance_*`, not a sum of these.
    CASE
        WHEN rg.is_subscription AND d.movement_type = 'Out' THEN -abs(d.gross_amount)
        WHEN rg.is_subscription THEN abs(d.gross_amount)
    END                                                                AS subscription_amount,
    -- Cost basis and realised gain come from `derived.book_costs`.
    -- NOT from Seccl's per-movement `bookValue`, which is what this model reported until the
    -- recurrence existed. A holding's book cost is a POOLED RUNNING AVERAGE, so a disposal's cost
    -- is a function of every movement before it — and on a GIA it is not the pool at all for the
    -- units the 30-day share-matching rule identifies against later purchases (TCGA 1992 s.106A).
    -- Neither is expressible here: a running average is a recurrence, not a window function.
    --
    -- `abs(proceeds) - abs(bookValue)` agreed with the vendor's own `profitLoss` on the rows where
    -- its `bookValue` was already the pooled figure, and silently disagreed everywhere else.
    bcs.book_cost                                                      AS book_cost,
    bcs.realised_gain                                                  AS realised_gain,
    -- The pool after this movement, and how much of a disposal was matched rather than pooled.
    bcs.total_book_cost,
    bcs.book_price,
    bcs.matched_quantity,
    d.currency,
    d.transaction_date,
    d.trade_date,
    d.settlement_date,
    d.posted_at,
    d.status,
    d.status_raw,
    -- Seccl expresses a reversal as a Deleted status rather than a flag.
    (d.status = 'deleted')                                             AS is_reversal,
    d.order_key,
    min(d.transaction_date) OVER (PARTITION BY d.order_key)            AS order_date,
    d.method,
    d.dealing_charges,
    d.other_charges,
    d.equalisation_payment,
    d.provider_book_value,
    d.transfer_value,
    CASE
        WHEN d.dealing_charges IS NOT NULL AND d.other_charges IS NOT NULL
            THEN 'Dealing + Other Charge'
        WHEN d.dealing_charges IS NOT NULL THEN 'Dealing Charge'
        WHEN d.other_charges IS NOT NULL THEN 'Other Charge'
        WHEN d.source_subtype IN
            ('Platform Fee', 'Discretionary Management Fee', 'Adviser Fee', 'Adviser Adhoc Fee')
            THEN d.source_subtype
    END                                                                AS fee_type,
    CASE
        WHEN d.source_type = 'Order' AND d.event_id IS NULL THEN 'Individual instruction'
        WHEN d.source_type = 'Order' AND ea.n_accounts > 1 THEN 'DFM Rebalance'
        WHEN d.source_type = 'Order' AND ea.n_accounts = 1 THEN 'Adviser Rebalance'
    END                                                                AS origin,
    d.event_id,
    d.destination_account,
    d.is_pension_transfer,
    d.transfer_status,
    d.previous_provider,
    d.previous_provider_name,
    ad.wrapper_detail ->> 'wrapperType'                                AS product_key,
    cast(ad.lead_client_id AS varchar)                                  AS client_external_id,
    ad.name                                                            AS account_name,
    d.created_at,
    d.source_updated_at,
    coalesce(d.transaction_date, current_date)                         AS as_of_date,
    d.data
FROM derived AS d
LEFT JOIN event_accounts AS ea ON ea.event_id = d.event_id
LEFT JOIN platform.nodes AS o ON o.key = d.key
LEFT JOIN raw.accounts AS ad ON ad.id = d.account_external_id
-- `IS NOT DISTINCT FROM`, not `=`: a code registered with a NULL sub-type or direction has to match a
-- transaction that carries none, and `NULL = NULL` is not true. `resolved_groups` is one row per
-- triple by construction, so this cannot fan the ledger out — which a join straight to
-- `raw.transaction_codes` would, on every combination the vendor registers twice.
LEFT JOIN resolved_groups AS rg
       ON rg.transaction_type = d.source_type
      AND rg.transaction_sub_type IS NOT DISTINCT FROM d.source_subtype
      AND rg.movement_type IS NOT DISTINCT FROM d.movement_type
-- The pooled cost basis, one row per movement it applies to. LEFT join because the model covers
-- only what moves a HOLDING: a cash payment, a fee and an unsettled order have no pool.
LEFT JOIN derived.book_costs AS bcs ON bcs.transaction_id = d.external_id
