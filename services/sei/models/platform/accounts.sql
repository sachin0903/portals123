/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: external_id
  depends_on: [raw.accounts, raw.api_allowances, platform.tenancy, platform.clients, platform.bank_accounts]
  tags: [sei, entity, conformed]
  description: >
    Accounts — the tenant-scoping root. Carries firm_id, subfirm_id and
    dsr_team_ids from platform.tenancy, so one predicate shape filters it and every
    fact hanging off it.
  checks:
    - not_null: external_id
    - unique: external_id
    - not_null: key
*/

-- Naming follows Seccl's entity layer (clients / positions / transactions) so the two
-- custodians present the same concepts to the portals under the same names:
-- snake_case, ids as *_id, the tenancy triple on every row.
--
-- Only the fields the portals actually use are surfaced; the raw report has 152
-- columns and `raw.accounts` keeps all of them for anything ad hoc.
-- GRAIN. The Accounts report is NOT one row per account: the p04 drop carries
-- 14,462 rows for 1,104 accounts (1,163 account×portfolio pairs), so it is account ×
-- portfolio × something further — SEI repeats the account block per related record.
-- This model publishes ONE ROW PER ACCOUNT, which is what `key: account_number` and
-- every downstream join assume; portfolio-level detail deserves its own
-- platform.portfolios model rather than silently multiplying this one.
--
-- THIS MODEL IS THE ORG OF RECORD FOR EVERYTHING ACCOUNT-GRAINED. `key`/`lineage` are
-- resolved here once — the account's tenancy grant, then this report's own FIRM_ID for the
-- 127 of 1,104 accounts with no ClientAccountLink row — and `platform.{positions, valuations,
-- transactions, portfolios, cash_schedules, upcoming_activities}` SELECT them rather than
-- recomputing, so a fact and the account it is about cannot disagree about which node owns
-- them. They each held their own copy of the expression and the copies had DIVERGED: four
-- coalesced to their own report's FIRM_ID and four did not, so one unlinked account published
-- a real lineage on some relations and `[]` on the others — and `[]` is the fan-out's GLOBAL
-- sentinel, so under containment those rows would project to every tenant on the platform.
-- Nothing reads that yet (the live predicate still narrows on the firm/subfirm/DSR triple,
-- which those models keep taking from platform.tenancy), which is why it went unnoticed; §6
-- step 5 is the change that makes it load-bearing.
--
-- A child does NOT fall back to its own report's FIRM_ID. An account absent from here is an
-- unresolvable parent — the merge counts that as a reject already — and projecting a row whose
-- parent is unprojected is the failure `book_sink.predicate` names for the node dimension.
--
-- The pick is deterministic (most recently updated, then lowest portfolio id) so a
-- rebuild cannot quietly swap which row represents an account. That ordering only
-- works because DATE_LAST_UPDATED is a real DATE in raw.accounts: while it was cast
-- here with try_cast it was NULL for every row (SEI writes 20251106, which CAST
-- cannot read), so the first sort key did nothing and the pick silently degenerated
-- to the lowest portfolio id.
WITH latest AS (
    SELECT max(dt) AS dt FROM raw.accounts
),
current AS (
    SELECT a.* FROM raw.accounts a JOIN latest ON a.dt = latest.dt
    QUALIFY row_number() OVER (
        PARTITION BY a."ACCOUNT_NUMBER"
        ORDER BY a."DATE_LAST_UPDATED" DESC NULLS LAST, a."PORTFOLIO_ID"
    ) = 1
)
SELECT
    -- The conformed spine (docs/platform-model.md §3). lineage carries the firm, the
    -- subfirm and every DSR team granted on the account, so one containment test can
    -- eventually replace _sei_predicate's three narrowing axes. It is published alongside
    -- the existing tenancy columns rather than instead of them: switching the fan-out over
    -- changes AND-of-axes into OR-of-keys, which widens a slice configured on more than one
    -- axis, so that is a deliberate step of its own (§6 step 5) with a config migration.
    a."ACCOUNT_NUMBER"                          AS external_id,
    -- The account's own FIRM_ID is the fallback: 127 of 1,104 accounts have no
    -- ClientAccountLink row and so resolve to no tenancy grant, and an account with no
    -- org fans out to nobody. Same reasoning as platform.clients.
    coalesce(t.subfirm_id, t.firm_id, a."FIRM_ID")  AS key,
    -- NULL, never `[]`, when nothing answers. An EMPTY lineage is the fan-out's GLOBAL
    -- sentinel (`book_sink.predicate.conformed`: "an empty lineage means global reference
    -- data … which projects whole"), so a row whose owning node is unknown would project to
    -- EVERY tenant rather than to none — the over-projection that comment is written against,
    -- arrived at from the other direction. `not_null: key` above means this arm cannot fire on
    -- today's corpus; it is here so that "unknown" has one spelling the day it can.
    nullif(list_distinct(list_filter(
        list_concat([coalesce(t.firm_id, a."FIRM_ID"), t.subfirm_id],
                    coalesce(t.dsr_team_ids, [])),
        x -> x IS NOT NULL
    )), []::VARCHAR[])                          AS lineage,
    a."DATE_LAST_UPDATED"::timestamp            AS source_updated_at,
    to_json(a)                                  AS data,
    a."ACCOUNT_NUMBER"                          AS account_number,
    a."ACCOUNT_DISPLAY_NAME"                    AS account_name,
    a."ACCOUNT_TYPE"                            AS account_type,
    a."ACCOUNT_STATUS_CODE"                     AS account_status,
    -- THE WRAPPER, and the pension nature that sits beside it.
    --
    -- `ACCOUNT_TYPE`, not `PORTFOLIO_TYPE` — settled from the i31 Reference report rather than
    -- guessed. `PORTFOLIO_TYPE` is 189 Capital / 190 Income, an investment-PURPOSE split and not
    -- a tax wrapper, which is what made the two indistinguishable from the column names alone.
    --
    -- 176 House Account is deliberately UNMAPPED: it is the firm's own account, not a client
    -- product, and there is no `product_type` member for one. `book_sink` does not require
    -- `product_id`, so such an account keeps a NULL rather than being filed as something it is not.
    --
    -- **A SIPP IS NOT AN `ACCT_TYPE` AT SEI, AND ASSUMING IT WOULD BE MIS-FILED 830 PENSIONS.**
    --
    -- The note that stood here said this environment defines no pension wrapper, so "an unmapped
    -- code arriving later (a SIPP) is a real event — it lands as NULL". Both halves were wrong.
    -- A SIPP never arrives as a new `ACCT_TYPE`; it arrives as **157 Investment Account** with
    -- the pension nature carried in a DIFFERENT code set entirely, which the i31 drop shows in
    -- full:
    --
    --     ACCOUNT_CLASSIFICATION_NAME      Pension                           934 accounts
    --     ACCOUNT_SUB_CLASSIFICATION_NAME  Pension - HMRC Approved - SIPP    830
    --                                      Pension - HMRC Approved - SSAS      4
    --     ACCOUNT_QUALIFIER_NAME           SIPP                               14
    --
    -- Every one of those 934 is `ACCOUNT_TYPE` 157, so mapping on the wrapper alone filed all of
    -- them as GIA — silently, which is the outcome the old comment claimed to be avoiding. It
    -- matters well beyond a label: `product_type` is what every "is this a pension?" reader gates
    -- on, so a SIPP filed as a GIA cannot be drawn down ("Drawdown applies to pension accounts
    -- only"), cannot take a pension contribution, and is tested against the wrong annual
    -- allowance.
    --
    -- The classification is read FIRST and only on an explicit SIPP. SSAS is left unmapped for
    -- the reason 176 House Account is — `platform.product_type` has no member for one, and a
    -- NULL that shows up as `unmodelled wrapper` in the merge ledger is honest where filing it as
    -- a GIA is not. The other 100 pension-classified accounts (Offshore Bond, Other) keep the
    -- wrapper map: a bond held inside a pension is not a SIPP, and guessing between TPOFFBOND and
    -- SIPP on a description would be inventing an answer the drop does not give.
    --
    -- `ACCT_TYPE` still decodes the four wrapper codes in play: 157 Investment Account, 172
    -- Stocks and Shares ISA, 173 Stocks and Shares Junior ISA, 176 House Account. Mapped on the
    -- CODE and not the description — the code is the stable identifier and carries a blank
    -- FIRM_ID, so it is platform-wide.
    CASE
        WHEN a."ACCOUNT_SUB_CLASSIFICATION_NAME" ILIKE '%SIPP%' THEN 'SIPP'
        WHEN a."ACCOUNT_QUALIFIER_NAME" = 'SIPP' THEN 'SIPP'
        WHEN a."ACCOUNT_SUB_CLASSIFICATION_NAME" ILIKE '%SSAS%' THEN NULL
        WHEN a."ACCOUNT_TYPE" = '157' THEN 'GIA'
        WHEN a."ACCOUNT_TYPE" = '172' THEN 'ISA'
        WHEN a."ACCOUNT_TYPE" = '173' THEN 'JISA'
    END                                         AS product_key,
    -- Seccl's client product is a wrapper id on the account. SEI has no equivalent, and the
    -- book pipeline reads this column from every custodian, so it is published as NULL.
    NULL::varchar                               AS product_external_id,
    -- THE LIFECYCLE, onto platform.account_status (draft | active | closed). `ACCT_STATUS` in the
    -- same report: 1 Pre-Open, 3 Open, 5 Pre-Close, 6 Closed, 8/9/10 Work In Progress
    -- (Incomplete / Complete / Submitted).
    --
    -- Pre-Close is ACTIVE, and it is the one judgement here rather than a lookup: the account is
    -- being wound down but still exists and still holds assets, and SEI has a distinct terminal
    -- code (6) for when it does not. Calling it closed would hide 192 live accounts from every
    -- screen that filters on status. The distinction is not lost — `status_raw` keeps SEI's own
    -- code, exactly as Seccl's model keeps its own word beside the canonical one.
    --
    -- An unmapped code publishes NULL, and NULL is not neutral: `book_sink` does
    -- `coalesce(s.status, 'active')` for parity with app/materialise.py, so it would read as a
    -- positive claim that the account is open. That is why this is a closed list rather than a
    -- `try_cast`, and why leaving the column absent was never the conservative option.
    CASE a."ACCOUNT_STATUS_CODE"
        WHEN '1' THEN 'draft'
        WHEN '3' THEN 'active'
        WHEN '5' THEN 'active'
        WHEN '6' THEN 'closed'
        WHEN '8' THEN 'draft'
        WHEN '9' THEN 'draft'
        WHEN '10' THEN 'draft'
    END                                         AS status,
    a."ACCOUNT_STATUS_CODE"                     AS status_raw,
    a."ACCOUNT_BASE_CURRENCY"                   AS base_currency,
    -- Dates and flags arrive typed from raw.accounts (sei_sync.schema); nothing to
    -- cast or decode here.
    a."ACCOUNT_OPEN_DATE"                       AS opened_on,
    a."LAST_TRADE_DATE"                         AS last_traded_on,
    a."DATE_LAST_UPDATED"                       AS updated_on,
    a."PORTFOLIO_ID"                            AS portfolio_id,
    a."PORTFOLIO_TYPE"                          AS portfolio_type,
    a."COUNTRY_CODE"                            AS country_code,
    a."DORMANT_STATUS_FLAG"                     AS is_dormant,
    a."FROZEN_STATUS_FLAG"                      AS is_frozen,
    a."MIFID_CLASSIFICATION"                    AS mifid_classification,
    a."STRATEGY_ID"                             AS strategy_id,
    a."STRATEGY_NAME"                           AS strategy_name,
    -- `MODEL_ID`, NOT `STRATEGY_ID`. This mapped to STRATEGY_ID and the note above it said "SEI's
    -- strategy IS that model" — which was reasonable from the column names and is contradicted by
    -- every drop we hold. Measured across the p04 corpus (74 drops, 2025-10-29 -> 2026-03-27):
    --
    --     drop        STRATEGY_ID   MODEL_ID   IMPLEMENTATION_MODEL_NAME
    --     2025-10-29    0 / 3,444      2,971         270
    --     2026-01-01    0 / 10,377     8,839         328
    --     2026-02-05    0 / 10,466     8,897         379
    --     2026-03-27    0 / 14,462    12,346 (49)    871 (23 distinct)
    --
    -- STRATEGY_ID and STRATEGY_NAME are 0% populated for five months; MODEL_ID is 85% with 49
    -- distinct values, and `PortfolioGroups.MODEL_ID` corroborates at 91%. So `model_key` was always
    -- NULL, the `link:model` parent never resolved, and `platform.accounts.model_id` was NULL for
    -- the whole SEI book — which reads as "this firm runs no discretionary models" and makes
    -- `GET /crm/accounts`' `discretionary` flag false for every account.
    --
    -- The same shape as the cam_schemerefs/cam_schemes mistake: the right KIND of identifier taken
    -- from the wrong column, silent because an always-empty column and an absent fact look identical.
    --
    -- STRATEGY_ID stays published above: it is in the conformed contract, lake-side readers select
    -- it, and the day SEI starts populating it we want to see that rather than have it hidden.
    a."MODEL_ID"                                AS model_key,
    a."IMPLEMENTATION_MODEL_NAME"               AS model_name,
    a."FEE_PACKAGE_ID"                          AS fee_package_id,
    -- ── The account's own TERMS: SEI publishes none of them, and says so ───────────────────────
    -- Explicit typed NULLs, because `book_sink`'s accounts projection is shared by every provider and
    -- a column this one omits is a Binder Error that aborts the whole tenant's chain (§3: a conformed
    -- entity publishes the column SET; a provider without a figure publishes NULL and states why).
    --
    -- `fees` is what an account is CHARGED. SEI's charges live on its own instrument-charge report
    -- (MiFID ex-ante costs, per instrument) and in the fee package `FEE_PACKAGE_ID` names — neither of
    -- which is a per-account charge schedule, and the package's own pricing is not conformed yet. An
    -- empty list would read as "this account is charged nothing"; NULL reads as "not recorded".
    NULL::struct(name varchar, type varchar, frequency varchar, rate decimal(12, 8),
                 amount decimal(20, 2), basis varchar)[]    AS fees,
    -- `payments`/`withdrawals` are STANDING arrangements. SEI's drop carries executed movements only;
    -- a regular payment is configured through its enrolment service and published nowhere we read.
    NULL::struct(amount decimal(20, 2), payment_day integer, method varchar,
                 movement_type varchar, auto_disinvest varchar, frequency varchar)[] AS payments,
    NULL::struct(amount decimal(20, 2), payment_day integer, method varchar,
                 movement_type varchar, auto_disinvest varchar, frequency varchar)[] AS withdrawals,
    -- The tenancy triple. Present on every sei.* entity, always these names.
    t.firm_id,
    t.subfirm_id,
    t.dsr_team_ids,
    t.adviser_firm_dsr_ids,
    t.adviser_firm_names,
    t.client_ids,
    -- The conformed ownership shape (§3): the list, plus the lead the book links the account to.
    -- SEI has no "lead" of its own — ownership is the DSR grant graph — so the first of the account's
    -- own client ids is it, which is the same rule Seccl's model applies to its owner list.
    t.client_ids                                AS owner_external_ids,
    list_extract(t.client_ids, 1)               AS lead_client_external_id,
    -- Discretionary management is not something SEI states about an account. NULL rather than false:
    -- the book's column is nullable precisely so "not known" and "not discretionary" stay different
    -- answers, and every "is this discretionary?" reader treats NULL as not-known.
    null::boolean                               AS is_discretionary,

    -- ============================================================================================
    -- THE WRAPPER ALLOWANCE, carried from the CLIENT and only for the wrappers that have one.
    -- ============================================================================================
    --
    -- `Wrapper.allowanceUsed` / `allowanceLimit` are rendered by `wrapper-card.svelte` and
    -- `account-stack.svelte` and were fed ONLY by `apps/client/src/lib/demo.ts`, while SEI has
    -- stated the figures per client all along (`platform.clients`, 1,614 of 2,774 rows).
    --
    -- **THE LIMIT IS SEI'S, NOT THE STATUTORY ONE.** used + remaining is 20,000 for 1,124 clients
    -- and 9,000 for 82 (the ISA and JISA allowances) — but it is 26,000 for 28 more and 22,000 for
    -- 20, because a FLEXIBLE ISA lets a withdrawal be replaced on top of the annual limit. So
    -- hard-coding 20,000 would tell those clients they had over-subscribed, and the sum of what SEI
    -- says has gone in and what SEI says may still go in is the only figure that is right for all
    -- of them.
    --
    -- ONLY ON ISA AND JISA. A GIA has no allowance and a House Account is the firm's own, so a bar
    -- on either would be inventing a constraint. Gated on the resolved `product_key` rather than the
    -- wrapper code, so a SIPP filed as an Investment Account (which is how SEI files one — see the
    -- product mapping above) is not accidentally given an ISA's headroom.
    --
    -- A SIPP HAS NO CONTRIBUTION ALLOWANCE IN EITHER SOURCE. The file declares
    -- `TOTAL_SIPP_AMOUNT`, `SIPP_CRYSTALLISED_VALUE` and `SIPP_UNCRYSTALLISED_VALUE`, and all
    -- three are unpopulated. Account details returns `isaJisaContributionDetails` as null on a
    -- SIPP (measured on i31, firm 3106). The SIPP profile service is not on the gateway. The
    -- statutory pension allowance is not written in its place.
    --
    -- THE API READ WINS WHEN IT HAS A FIGURE, and the file is the fallback. Account details
    -- states the client-level year-to-date and remaining for BOTH wrappers on every ISA and
    -- every JISA, so an ISA account reads the ISA side only. A zero year-to-date is a figure,
    -- not a missing one: this tax year's subscription really is zero on most of the book, and
    -- the tile then shows £0 used of the limit with the full amount left. The two sources are
    -- not mixed: a partial API block does not borrow the file's other half. The file's used
    -- figure is `platform.clients.isa_subscription_used`, which is
    -- `ISA_CONTRIBUTION_TO_DATE` — the stocks-and-shares subscription column is not it.
    --
    -- THE LIMIT IS STILL USED + REMAINING, not the statutory field on the API block. That
    -- field is 20,000 on a flexible ISA whose replacement room makes the sum 26,000.
    --
    -- REPEATED across the client's accounts of that wrapper, deliberately: it is a
    -- (client, wrapper) fact and the portal groups by wrapper. Every reader must take **max() per
    -- (contact, product), never sum()** — a client with two ISAs shares one allowance.
    --
    -- The tax year is the API's (`2026-2027`) and only when that read supplied the pair. The
    -- file states no year, and deriving one from the drop date would be a guess.
    CASE
        WHEN product_key = 'ISA'
             AND (al.isa_year_to_date IS NOT NULL OR al.isa_remaining IS NOT NULL)
            THEN al.isa_year_to_date
        WHEN product_key = 'JISA'
             AND (al.jisa_year_to_date IS NOT NULL OR al.jisa_remaining IS NOT NULL)
            THEN al.jisa_year_to_date
        WHEN product_key IN ('ISA', 'JISA') THEN cl.isa_subscription_used
    END                                         AS allowance_used,
    CASE
        WHEN product_key = 'ISA'
             AND al.isa_year_to_date IS NOT NULL
             AND al.isa_remaining IS NOT NULL
            THEN al.isa_year_to_date + al.isa_remaining
        WHEN product_key = 'JISA'
             AND al.jisa_year_to_date IS NOT NULL
             AND al.jisa_remaining IS NOT NULL
            THEN al.jisa_year_to_date + al.jisa_remaining
        WHEN product_key = 'ISA'
             AND (al.isa_year_to_date IS NOT NULL OR al.isa_remaining IS NOT NULL)
            THEN NULL::decimal(20, 4)
        WHEN product_key = 'JISA'
             AND (al.jisa_year_to_date IS NOT NULL OR al.jisa_remaining IS NOT NULL)
            THEN NULL::decimal(20, 4)
        WHEN product_key IN ('ISA', 'JISA')
             AND cl.isa_subscription_used IS NOT NULL
             AND cl.isa_headroom_remaining IS NOT NULL
            THEN cl.isa_subscription_used + cl.isa_headroom_remaining
    END                                         AS allowance_limit,
    -- Headroom on its own. `allowance_limit - allowance_used` is the same number when both
    -- exist, and is not computable when one of them does not. Readers that only have a
    -- remaining figure (Seccl, when no subscription movement has been stated) must not
    -- invent a limit to get here.
    CASE
        WHEN product_key = 'ISA'
             AND (al.isa_year_to_date IS NOT NULL OR al.isa_remaining IS NOT NULL)
            THEN al.isa_remaining
        WHEN product_key = 'JISA'
             AND (al.jisa_year_to_date IS NOT NULL OR al.jisa_remaining IS NOT NULL)
            THEN al.jisa_remaining
        WHEN product_key IN ('ISA', 'JISA') THEN cl.isa_headroom_remaining
    END                                         AS allowance_remaining,
    CASE
        WHEN product_key = 'ISA'
             AND al.isa_year_to_date IS NOT NULL
             AND al.isa_remaining IS NOT NULL
            THEN al.tax_year
        WHEN product_key = 'JISA'
             AND al.jisa_year_to_date IS NOT NULL
             AND al.jisa_remaining IS NOT NULL
            THEN al.tax_year
    END                                         AS allowance_tax_year,

    -- Pay-to profiles from PaidTo, aggregated in platform.bank_accounts so this
    -- grain stays one row per account. NULL when the latest file names none.
    -- Ad hoc profiles are not reusable destinations and are left out. The sink
    -- merges the array without replacing entries the firm typed.
    pay.bank_accounts                           AS bank_accounts,

    a.dt                                        AS as_of_date
FROM current a
LEFT JOIN platform.tenancy t ON t.account_number = a."ACCOUNT_NUMBER"
-- The lead client's allowance. Joined through `platform.clients` rather than `raw.clients` because
-- that report repeats the whole client block once per ADDRESS_PURPOSE (2,774 rows for 824 clients)
-- and the conformed model has already reduced it to one row per client.
LEFT JOIN platform.clients cl ON cl.client_id = list_extract(t.client_ids, 1)
LEFT JOIN raw.api_allowances al
    ON al.account_number = a."ACCOUNT_NUMBER"
   AND al.firm_id = a."FIRM_ID"
LEFT JOIN (
    SELECT account_external_id,
           to_json(list(entry ORDER BY profile_id)) AS bank_accounts
    FROM platform.bank_accounts
    WHERE NOT coalesce(is_ad_hoc, false)
      AND entry IS NOT NULL
    GROUP BY account_external_id
) pay ON pay.account_external_id = a."ACCOUNT_NUMBER"
