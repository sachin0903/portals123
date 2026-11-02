/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: external_id
  depends_on: [raw.transaction_header, raw.transaction_header_upd, raw.api_transactions, ref.codes, platform.tenancy, platform.accounts, platform.instruments]
  tags: [sei, entity, conformed]
  description: >
    Transactions — the SFTP history, plus API rows whose id the drop does not
    already carry. A shared id stays with the file.
  checks:
    - not_null: external_id
    - unique: external_id
    - not_null: transaction_date
*/

-- STRATEGY. full_merge. The query is the whole ledger: every SFTP partition,
-- plus API ids the file does not have. merge would keep an id the latest drop
-- no longer contains. replace drops the table while the lake is reading it.
-- The incremental half is raw.api_transactions. It stores how far its scan
-- reached (covered_through) and a later run asks only for the lookback.
--
-- SIGNED AMOUNTS. Every money column in the SEI feed is UNSIGNED, with the
-- direction held in a separate DEBIT_CREDIT_FLAG_n immediately after it:
--
--   NET_TRANSACTION_AMOUNT  -> DEBIT_CREDIT_FLAG_1
--   BASE_TRANSACTION_AMOUNT -> DEBIT_CREDIT_FLAG_2
--   PRICE                   -> DEBIT_CREDIT_FLAG_3          (verified on p04:
--   NET_FEE_AMT             -> DEBIT_CREDIT_FLAG_4           flags are only C/D
--   NET_ACCRUED_INTEREST_AMT-> DEBIT_CREDIT_FLAG_5           and both C and D
--   NET_TAXES_AMT           -> DEBIT_CREDIT_FLAG_6           rows are positive)
--   GROSS_TRANSACTION_AMOUNT-> DEBIT_CREDIT_FLAG_7
--   BOOK_VALUE_IN_ASSET_CURRENCY -> DEBIT_CREDIT_FLAG_12
--
-- So summing the raw column adds debits to credits and is wrong by construction.
-- The flags are resolved into the sign here, once, and not re-exported — a
-- downstream consumer should never have to know the convention existed.
--
-- The 15 flags are NOT a repeating group despite looking like one: each qualifies
-- a different companion column, so they are paired, not unpivoted.
-- GRAIN. One row per TRANSACTION_ID. An in-specie movement is several of those rows,
-- one per instrument. RELATED_ACTIVITY_ID (kept as text — see test_columns) is the
-- group, and to_json below carries it inside `data`.
--
-- THE API IS THE OTHER HALF, not a replacement. raw.api_transactions is the SWP
-- transaction-details pull. On a shared transaction_id the file wins: it carries the
-- debit/credit flags, the description code, and the *_Upd amendment. An id only the
-- API returned is appended, which is the stretch of drops SEI no longer keeps on
-- SFTP. The two id spaces join as integers; a recycled id (one measured collision)
-- stays with whichever row the file has. The API's own instrument id is a different
-- space from raw.assets, so the link is resolved on ISIN, then SEDOL.
-- TransactionHeader is a daily ACTIVITY file, not a daily snapshot: each drop
-- carries only that day's transactions (TRANSACTION_DATE is the drop date) and no
-- TRANSACTION_ID is ever redelivered — 11,235 rows across three drops, 11,235 distinct
-- ids, zero overlap between them. So the transaction history IS the union of the
-- partitions, and taking only max(dt) — which this model used to do — published a
-- one-day window and silently discarded everything before it.
--
-- The dedupe is a guard rather than a fix for anything observed: SEI redelivering a
-- date would otherwise double its transactions, so the latest drop of an id wins.
WITH base AS (
    SELECT h.*
    FROM raw.transaction_header h
    QUALIFY row_number() OVER (PARTITION BY h."TRANSACTION_ID" ORDER BY h.dt DESC) = 1
),
-- SEI issues corrections in a parallel *_Upd report keyed on TRANSACTION_ID. They are
-- NOT same-day: the 44 amendments in the corpus arrived in the 03-27 drop and correct
-- transactions dated 03-25. Matching them therefore needs the whole base history, not
-- the newest partition — windowed to max(dt) they matched nothing and the merge below
-- degenerated into appending 44 unrelated rows.
amendments AS (
    SELECT u.*
    FROM raw.transaction_header_upd u
    QUALIFY row_number() OVER (PARTITION BY u."TRANSACTION_ID" ORDER BY u.dt DESC) = 1
),
merged AS (
    SELECT * FROM amendments
    UNION ALL BY NAME
    SELECT b.* FROM base b
    WHERE NOT EXISTS (
        -- NOT EXISTS rather than NOT IN: one NULL id on the amendment side would
        -- make NOT IN return nothing and silently drop the whole baseline.
        SELECT 1 FROM amendments a WHERE a."TRANSACTION_ID" = b."TRANSACTION_ID"
    )
),
signed AS (
    SELECT
        m.*,
        CASE WHEN m."DEBIT_CREDIT_FLAG_1" = 'D' THEN -1 ELSE 1 END AS _s_net,
        CASE WHEN m."DEBIT_CREDIT_FLAG_2" = 'D' THEN -1 ELSE 1 END AS _s_base,
        CASE WHEN m."DEBIT_CREDIT_FLAG_4" = 'D' THEN -1 ELSE 1 END AS _s_fee,
        CASE WHEN m."DEBIT_CREDIT_FLAG_5" = 'D' THEN -1 ELSE 1 END AS _s_interest,
        CASE WHEN m."DEBIT_CREDIT_FLAG_6" = 'D' THEN -1 ELSE 1 END AS _s_tax,
        CASE WHEN m."DEBIT_CREDIT_FLAG_7" = 'D' THEN -1 ELSE 1 END AS _s_gross,
        -- The flag sits on the column after the amount, the same pairing as the cash
        -- figures. A stock transfer carries its value here and leaves net and gross at 0.
        CASE WHEN m."DEBIT_CREDIT_FLAG_12" = 'D' THEN -1 ELSE 1 END AS _s_book
    FROM merged m
),
-- Latest API copy of an id the file does not have. fetched_at is the only total order
-- the pull publishes; full_merge reads this query twice, so the pick cannot move.
api_latest AS (
    SELECT a.*
    FROM raw.api_transactions a
    QUALIFY row_number() OVER (PARTITION BY a.transaction_id ORDER BY a.fetched_at DESC) = 1
),
api_gap AS (
    SELECT a.*
    FROM api_latest a
    WHERE NOT EXISTS (
        SELECT 1 FROM merged m
        WHERE try_cast(m."TRANSACTION_ID" AS BIGINT) = a.transaction_id
    )
),
-- transaction_type on the API is the Reference DESCRIPTION, not the code the CASE below
-- maps. A firm-scoped row beats the platform-wide one; code is the tie-break.
api_coded AS (
    SELECT a.*, c.code AS type_code
    FROM api_gap a
    LEFT JOIN ref.codes c
        ON c.type = 'TRANSACTION_TYPE'
       AND c.description = a.transaction_type
       AND (c.firm_id IS NULL OR c.firm_id = '' OR c.firm_id = a.firm_id)
    QUALIFY row_number() OVER (
        PARTITION BY a.transaction_id
        ORDER BY
            CASE
                WHEN c.firm_id = a.firm_id THEN 0
                WHEN c.firm_id IS NULL OR c.firm_id = '' THEN 1
                ELSE 2
            END,
            c.code
    ) = 1
),
-- Prefer a populated ISIN. SEDOL only when the API sent no ISIN, so a SEDOL collision
-- cannot steal a row that named its ISIN. Lowest external_id wins a tie, and that id
-- is unique on platform.instruments.
api_ready AS (
    SELECT a.*, i.external_id AS resolved_instrument, i.isin AS resolved_isin, i.name AS resolved_name
    FROM api_coded a
    LEFT JOIN platform.instruments i
        ON (
            a.isin IS NOT NULL AND a.isin <> '' AND i.isin = a.isin
        ) OR (
            (a.isin IS NULL OR a.isin = '')
            AND a.sedol IS NOT NULL AND a.sedol <> ''
            AND i.sedol = a.sedol
        )
    QUALIFY row_number() OVER (PARTITION BY a.transaction_id ORDER BY i.external_id) = 1
),
-- DESCRIPTION_CODE_ID is a Reference code (6,576 of them). One description per code:
-- the firms in the drop agree, and a code whose descriptions ever diverge stays unnamed
-- rather than picking a firm at random. The file's own TRANSACTION_DESCRIPTION is what
-- the listing falls back to.
description_codes AS (
    SELECT code,
           CASE
               WHEN count(DISTINCT description) = 1 THEN any_value(description)
           END AS description
    FROM ref.codes
    WHERE type = 'DESCRIPTION_CODE_ID'
      AND nullif(trim(description), '') IS NOT NULL
    GROUP BY code
)
SELECT
    -- The conformed spine (docs/platform-model.md §3), published alongside the existing
    -- tenancy columns rather than instead of them — see platform/accounts.sql for why the
    -- fan-out switch is a separate step.
    s."TRANSACTION_ID"                                   AS external_id,
    -- THE ORG COMES FROM THE ACCOUNT. platform.accounts resolves key/lineage once (see its
    -- header) and every account-grained relation selects it, so a fact and the account it is
    -- about cannot disagree about which node owns them. Recomputed here it had diverged, and
    -- one of the spellings of "unknown" was `[]` — which the fan-out reads as GLOBAL.
    acc.key,
    acc.lineage,
    NULL::timestamp                                      AS source_updated_at,
    to_json(s)                                           AS data,
    s."TRANSACTION_ID"                                   AS transaction_id,
    s."ACCOUNT_NUMBER"                                   AS account_number,
    -- The join key the materialiser reads. platform.accounts publishes external_id =
    -- ACCOUNT_NUMBER, so this is that same key under the conformed name; without it every
    -- SEI transaction was rejected as "unknown account" before its type was even looked at.
    s."ACCOUNT_NUMBER"                                   AS account_external_id,
    s."TRANSACTION_TYPE"                                 AS transaction_type,
    -- source_type/source_subtype are SEI's OWN words, kept whatever `type` resolves to.
    -- TRANSACTION_DESCRIPTION is the type name the file already spells. DESCRIPTION_CODE_ID
    -- is the finer code (21121 contribution against 21122 relief, both Cash Receipt), and
    -- the listing prints source_subtype, so the code itself would show as "Interest: 20043".
    -- The Reference description is the subtype; the code stays on description_code_id.
    s."TRANSACTION_DESCRIPTION"                          AS source_type,
    dc.description                                       AS source_subtype,
    s."DESCRIPTION_CODE_ID"                              AS description_code_id,
    s."TRANSACTION_DESCRIPTION"                          AS description,
    s."NARRATIVE"                                        AS narrative,
    -- CANONICAL TYPE. SEI's TRANSACTION_TYPE is a 124-member code set published in the
    -- nightly Reference report (type = 'TRANSACTION_TYPE'), read from the p04 production
    -- drop rather than assumed. Codes with no unambiguous canonical equivalent resolve to
    -- NULL, NOT to 'adjustment': the materialiser records an unmapped transaction as a
    -- counted reject, and a reject is recoverable where money filed under the wrong type
    -- is not. That is the same discipline Seccl's model states.
    --
    -- Deliberately NULL: 5 Security Transfer, 88 Custody Transfer, 89 Custodian Account
    -- Transfer -- each is directionless, and SEI publishes the direction as a SEPARATE
    -- code (95/96, 106/107). Guessing 'transfer_in' for a directionless transfer inverts
    -- half of them. Code 8 (Cash Transfer) is the same name with no from/to, but the
    -- signed net is the direction, so that one is mapped. Also NULL: 20 Change, 108/109
    -- Free Form,
    -- 121/122 Loan Payable/Receivable, and the meeting/disclosure notices (53, 60, 62, 68,
    -- 73, 84-86), which are notifications rather than money movements.
    CASE s."TRANSACTION_TYPE"
        WHEN '1'   THEN 'buy'
        WHEN '2'   THEN 'sell'
        WHEN '3'   THEN 'transfer_out'      -- Free Delivery: stock out, free of payment
        WHEN '4'   THEN 'transfer_in'       -- Free Receipt
        WHEN '6'   THEN 'deposit'           -- Cash Receipt -- the contribution rail
        WHEN '7'   THEN 'withdrawal'        -- Cash Disbursement
        -- Code 8 is "Cash Transfer" with no from/to. The signed net is the direction:
        -- a credit arrived, a debit left. A zero net stays unmapped rather than guessed.
        WHEN '8'   THEN CASE
            WHEN s._s_net * coalesce(s."NET_TRANSACTION_AMOUNT", 0) < 0 THEN 'transfer_out'
            WHEN s._s_net * coalesce(s."NET_TRANSACTION_AMOUNT", 0) > 0 THEN 'transfer_in'
            ELSE NULL
        END
        WHEN '9'   THEN 'interest'
        WHEN '10'  THEN 'interest'
        WHEN '11'  THEN 'corporate_action'
        WHEN '12'  THEN 'fee'
        WHEN '13'  THEN 'fee'
        WHEN '14'  THEN 'fee'
        WHEN '15'  THEN 'adjustment'
        WHEN '18'  THEN 'dividend'          -- Capital Gains Distribution
        WHEN '19'  THEN 'dividend'          -- Cash Dividend
        WHEN '30'  THEN 'interest'          -- Interest Payment
        WHEN '83'  THEN 'tax'               -- Tax Reclaim
        WHEN '90'  THEN 'adjustment'        -- Position Type Transfer
        WHEN '91'  THEN 'adjustment'        -- POSK Transfer
        WHEN '92'  THEN 'adjustment'        -- FX-Spot
        WHEN '93'  THEN 'adjustment'
        WHEN '94'  THEN 'adjustment'
        WHEN '95'  THEN 'transfer_out'      -- Security Transfer -Transfer From
        WHEN '96'  THEN 'transfer_in'       -- Security Transfer -Transfer To
        WHEN '97'  THEN 'transfer_out'      -- Currency Transfer From
        WHEN '98'  THEN 'transfer_in'       -- Currency Transfer To
        WHEN '99'  THEN 'adjustment'
        WHEN '100' THEN 'adjustment'
        WHEN '101' THEN 'adjustment'
        WHEN '102' THEN 'adjustment'
        WHEN '103' THEN 'adjustment'
        WHEN '104' THEN 'adjustment'
        WHEN '105' THEN 'interest'          -- Deferred Interest Pay Up
        WHEN '106' THEN 'transfer_out'      -- Intra Account Cash Transfer From
        WHEN '107' THEN 'transfer_in'       -- Intra Account Cash Transfer To
        WHEN '110' THEN 'transfer_out'      -- Overlay Allocation Cash Transfer From
        WHEN '111' THEN 'transfer_in'       -- Overlay Allocation Cash Transfer To
        WHEN '112' THEN 'transfer_out'      -- Unit Cost Averaging Cash Transfer From
        WHEN '113' THEN 'transfer_in'       -- Unit Cost Averaging Cash Transfer To
        WHEN '114' THEN 'transfer_out'      -- Complex Trade Transfer From
        WHEN '115' THEN 'transfer_in'       -- Complex Trade Transfer To
        WHEN '116' THEN 'adjustment'
        WHEN '117' THEN 'fee'               -- Real Time Fee
        WHEN '118' THEN 'fee'               -- Money Market Redemption Fee
        WHEN '123' THEN 'adjustment'
        WHEN '124' THEN 'adjustment'
        WHEN '125' THEN 'adjustment'
        WHEN '126' THEN 'adjustment'
        -- The rest of 16-87 are corporate-action events in SEI's own taxonomy (the
        -- Reference descriptions are CA event names: Bonus Issue, Merger, Spin-Off...).
        -- The nine excluded codes are NOTIFICATIONS, not money movements -- a meeting
        -- notice or a trading-status change has no amount, and filing it as a corporate
        -- action would put it in a client's activity feed as though something settled.
        ELSE CASE
            WHEN try_cast(s."TRANSACTION_TYPE" AS INTEGER) BETWEEN 16 AND 87
                 AND s."TRANSACTION_TYPE" NOT IN (
                     '20',  -- Change
                     '53',  -- Annual General Meeting
                     '60',  -- Court Meeting
                     '62',  -- Disclosure
                     '68',  -- Extraordinary General Meeting
                     '73',  -- Ordinary General Meeting
                     '84',  -- Trading Status: Active
                     '85',  -- Trading Status: Delisted
                     '86'   -- Trading Status: Suspended
                 )
            THEN 'corporate_action'
            ELSE NULL
        END
    END                                                  AS type,
    -- Dates and amounts arrive typed from raw.transaction_header (sei_sync.schema).
    -- They used to be cast here, and SEI's 20260327 is unreachable by CAST, so every
    -- one of these three date columns was NULL for every row.
    s."TRANSACTION_DATE"                                 AS transaction_date,
    s."TRADE_DATE"                                       AS trade_date,
    s."SETTLEMENT_DATE"                                  AS settlement_date,
    s."INSTRUMENT_ID"                                    AS instrument_id,
    -- The conformed name for the same value: the materialiser resolves an instrument by
    -- `instrument_key` against platform.instruments' external_id.
    s."INSTRUMENT_ID"                                    AS instrument_key,
    -- The book denormalises the instrument onto a transaction for display (Seccl publishes both
    -- inline); SEI keeps it on its own report, and the transaction's instrument is resolved through
    -- instrument_key instead. Explicit NULLs, so the conformed column set stays fixed (§3).
    null::varchar                                                  AS isin,
    null::varchar                                                  AS asset_name,
    -- SEI publishes no lifecycle status. A row in the report has posted, and the
    -- transactions listing already shows a blank as settled. Writing the word keeps
    -- the transfers workbench on the same fact: blank was being read as submitted.
    'settled'                                                      AS status,
    -- §3's `order_key` — what ties an order's legs together at the custodian. Seccl publishes a
    -- link/core id; SEI's transaction header carries only TRANSACTION_ID, the row's OWN key, and
    -- nothing in this report groups the legs of one instruction. An explicit NULL rather than an
    -- absent column: the conformed column set is fixed (§3), and a consumer reading a column this
    -- provider omits is a Binder Error that aborts the whole tenant's chain. TRANSACTION_ID here
    -- would be WORSE than nothing — it is unique per leg, so it would claim every SEI trade is its
    -- own one-line instruction while looking like a real answer.
    null::varchar                                                  AS order_key,
    -- ── The statement layer: SEI publishes none of it, and says so ─────────────────────────────
    -- Explicit NULLs for the same reason `order_key` is one: the conformed column set is FIXED (§3)
    -- and `book_sink`'s transactions projection is shared by every provider, so a column this
    -- provider omits is a Binder Error that aborts the whole tenant's chain rather than a missing
    -- figure on one screen.
    --
    -- `report_group_kind` is the custodian's own statement bucket. Seccl publishes the mapping as
    -- reference data (`/static/transactiontype`'s `accountReportGroup`, which is what its own account
    -- report groups by); SEI's drop has no counterpart — the 124 TRANSACTION_TYPE codes carry
    -- direction as a separate code pair and nothing that names a statement section — so a firm's SEI
    -- movements fall under "other" on the breakdown, which is honest and visible. Inventing a mapping
    -- from the type codes would put money on a statement line no SEI report agrees with.
    null::varchar                                                  AS report_group_kind,
    -- The charge split. SEI states charges on its own instrument-charge report (MiFID costs), not per
    -- movement, so there is nothing to split a transaction's charge INTO here.
    null::decimal(20, 4)                                           AS dealing_charges,
    null::decimal(20, 4)                                           AS other_charges,
    null::varchar                                                  AS fee_type,
    -- Cost basis and the gain a disposal realised. SEI holds both, on `platform.taxlots` (the CGT
    -- cost-basis report) rather than on the transaction — a lot is matched to a disposal there, which
    -- is a different grain from this row. Left NULL until that join is built rather than approximated
    -- from the movement, because a wrong realised gain is a wrong CGT position.
    null::decimal(20, 4)                                           AS book_cost,
    null::decimal(20, 4)                                           AS realised_gain,
    -- An accumulation-fund distribution's capital half. SEI's drop does not separate it.
    null::decimal(20, 4)                                           AS equalisation_payment,
    -- What the movement did to a wrapper's annual subscription allowance. Remaining allowance is
    -- `platform.accounts.allowance_*`; SEI flags no code as subscribing on this report.
    null::decimal(20, 4)                                           AS subscription_amount,
    -- When the money actually moved. SEI's TRANSACTION_DATE is the drop/activity date and
    -- SETTLEMENT_DATE is the intended one; ACTUAL_SETTLEMENT_DATE is the only column that
    -- says it settled, which is what a reconciliation compares against.
    s."ACTUAL_SETTLEMENT_DATE"                           AS posted_at,
    s."TRANSACTION_CURRENCY"                             AS currency,
    s."QUANTITY"                                         AS quantity,
    s."UNIT_PRICE"                                       AS unit_price,
    s._s_net      * s."NET_TRANSACTION_AMOUNT"           AS net_amount,
    s._s_gross    * s."GROSS_TRANSACTION_AMOUNT"         AS gross_amount,
    -- Cashless movements (free receipt, free delivery, rights, spin-off) leave both
    -- cash columns at 0 and state the cost here, in the transaction currency. The book
    -- reads it only when net and gross are both zero.
    s._s_book     * s."BOOK_VALUE_IN_ASSET_CURRENCY"     AS book_value,
    s._s_base     * s."BASE_TRANSACTION_AMOUNT"          AS base_amount,
    s._s_fee      * s."NET_FEE_AMT"                      AS fee_amount,
    s._s_interest * s."NET_ACCRUED_INTEREST_AMT"         AS accrued_interest,
    s._s_tax      * s."NET_TAXES_AMT"                    AS tax_amount,
    -- sei_sync.schema now declares REVERSAL_FLAG BOOLEAN, so this is a boolean compared to
    -- 'Y' -- which DuckDB coerces, and test_platform_transactions.py pins. Left as a
    -- comparison rather than a bare reference because the corpus is unpopulated: if a future
    -- profile run sees real 'Y'/'N' text and drops back to VARCHAR, this still holds.
    s."REVERSAL_FLAG" = 'Y'                              AS is_reversal,
    -- Tenancy, inherited from the account.
    t.firm_id,
    t.subfirm_id,
    t.dsr_team_ids,
    t.adviser_firm_dsr_ids,
    -- The drop this row was last seen in. Amendments carry their own dt, so an amended
    -- transaction is stamped with the drop that corrected it, not the one that raised it.
    s.dt                                                 AS as_of_date
FROM signed s
LEFT JOIN description_codes dc ON dc.code = s."DESCRIPTION_CODE_ID"
LEFT JOIN platform.tenancy t ON t.account_number = s."ACCOUNT_NUMBER"
LEFT JOIN platform.accounts acc ON acc.external_id = s."ACCOUNT_NUMBER"
UNION ALL BY NAME
-- Same canonical CASE as the file arm, keyed on the Reference code the API's
-- description resolved to. An unresolved description stays NULL and the book
-- counts it as an unmapped type rather than filing it under a guess.
-- Amounts are already signed: the API net matches the file's flag-signed net,
-- so the debit/credit flags are not applied again. Quantity stays as the API
-- sent it (unsigned). instrument_id is the assets-report id, not the API's.
SELECT
    cast(a.transaction_id AS varchar)                    AS external_id,
    acc.key,
    acc.lineage,
    NULL::timestamp                                      AS source_updated_at,
    to_json(a)                                           AS data,
    cast(a.transaction_id AS varchar)                    AS transaction_id,
    a.account_number                                     AS account_number,
    a.account_number                                     AS account_external_id,
    a.type_code                                          AS transaction_type,
    a.transaction_type                                   AS source_type,
    NULL::varchar                                        AS source_subtype,
    NULL::varchar                                        AS description_code_id,
    a.transaction_type                                   AS description,
    a.transaction_description                            AS narrative,
    CASE a.type_code
        WHEN '1'   THEN 'buy'
        WHEN '2'   THEN 'sell'
        WHEN '3'   THEN 'transfer_out'
        WHEN '4'   THEN 'transfer_in'
        WHEN '6'   THEN 'deposit'
        WHEN '7'   THEN 'withdrawal'
        WHEN '8'   THEN CASE
            WHEN coalesce(a.local_net_transaction_amount, 0) < 0 THEN 'transfer_out'
            WHEN coalesce(a.local_net_transaction_amount, 0) > 0 THEN 'transfer_in'
            ELSE NULL
        END
        WHEN '9'   THEN 'interest'
        WHEN '10'  THEN 'interest'
        WHEN '11'  THEN 'corporate_action'
        WHEN '12'  THEN 'fee'
        WHEN '13'  THEN 'fee'
        WHEN '14'  THEN 'fee'
        WHEN '15'  THEN 'adjustment'
        WHEN '18'  THEN 'dividend'
        WHEN '19'  THEN 'dividend'
        WHEN '30'  THEN 'interest'
        WHEN '83'  THEN 'tax'
        WHEN '90'  THEN 'adjustment'
        WHEN '91'  THEN 'adjustment'
        WHEN '92'  THEN 'adjustment'
        WHEN '93'  THEN 'adjustment'
        WHEN '94'  THEN 'adjustment'
        WHEN '95'  THEN 'transfer_out'
        WHEN '96'  THEN 'transfer_in'
        WHEN '97'  THEN 'transfer_out'
        WHEN '98'  THEN 'transfer_in'
        WHEN '99'  THEN 'adjustment'
        WHEN '100' THEN 'adjustment'
        WHEN '101' THEN 'adjustment'
        WHEN '102' THEN 'adjustment'
        WHEN '103' THEN 'adjustment'
        WHEN '104' THEN 'adjustment'
        WHEN '105' THEN 'interest'
        WHEN '106' THEN 'transfer_out'
        WHEN '107' THEN 'transfer_in'
        WHEN '110' THEN 'transfer_out'
        WHEN '111' THEN 'transfer_in'
        WHEN '112' THEN 'transfer_out'
        WHEN '113' THEN 'transfer_in'
        WHEN '114' THEN 'transfer_out'
        WHEN '115' THEN 'transfer_in'
        WHEN '116' THEN 'adjustment'
        WHEN '117' THEN 'fee'
        WHEN '118' THEN 'fee'
        WHEN '123' THEN 'adjustment'
        WHEN '124' THEN 'adjustment'
        WHEN '125' THEN 'adjustment'
        WHEN '126' THEN 'adjustment'
        ELSE CASE
            WHEN try_cast(a.type_code AS INTEGER) BETWEEN 16 AND 87
                 AND a.type_code NOT IN (
                     '20', '53', '60', '62', '68', '73', '84', '85', '86'
                 )
            THEN 'corporate_action'
            ELSE NULL
        END
    END                                                  AS type,
    a.transaction_date                                   AS transaction_date,
    a.trade_date                                         AS trade_date,
    a.settlement_date                                    AS settlement_date,
    a.resolved_instrument                                AS instrument_id,
    a.resolved_instrument                                AS instrument_key,
    coalesce(nullif(a.isin, ''), a.resolved_isin)        AS isin,
    a.resolved_name                                      AS asset_name,
    -- Same as the file arm: a posted API row has no lifecycle, and it has settled.
    'settled'                                            AS status,
    NULL::date                                           AS posted_at,
    a.local_currency_code                                AS currency,
    a.transaction_quantity                               AS quantity,
    -- The API does not send a unit price. The narrative quotes one ("@ 5.1832 GBP") and
    -- the consideration divided by the units is that quote. A zero quantity is not a price.
    CASE
        WHEN a.transaction_quantity IS NULL OR a.transaction_quantity = 0
             OR a.local_transaction_gross_amount IS NULL
             OR a.local_transaction_gross_amount = 0
        THEN NULL
        ELSE cast(
            abs(a.local_transaction_gross_amount) / abs(a.transaction_quantity)
            AS decimal(20, 8)
        )
    END                                                  AS unit_price,
    a.local_net_transaction_amount                       AS net_amount,
    a.local_transaction_gross_amount                     AS gross_amount,
    -- Already signed. A free receipt is positive, a free delivery negative, and the
    -- cash columns on those rows are 0.
    a.local_book_value                                   AS book_value,
    a.base_net_transaction_amount                        AS base_amount,
    a.net_charges_amount                                 AS fee_amount,
    NULL::decimal(20, 4)                                 AS accrued_interest,
    a.net_taxes_amount                                   AS tax_amount,
    a.reversal_flag                                      AS is_reversal,
    t.firm_id,
    t.subfirm_id,
    t.dsr_team_ids,
    t.adviser_firm_dsr_ids,
    a.transaction_date                                   AS as_of_date
FROM api_ready a
LEFT JOIN platform.tenancy t ON t.account_number = a.account_number
LEFT JOIN platform.accounts acc ON acc.external_id = a.account_number
