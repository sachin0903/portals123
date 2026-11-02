# SEI — data sources, models and coverage

A source map for the SEI custodian: what the nightly SFTP drop carries, what the SWP REST API
carries, where the two overlap (and disagree), what neither carries, what we could derive instead,
and — the question this document exists to answer — **whether our own schema holds enough to fill
in the API bodies that create a client, an account, a transfer, a payment, a withdrawal or a fee.**

`docs/sei.md` is the pipeline: how the drop is pulled, decrypted, typed and fanned out. This is the
_data_ — reports, columns, fill rates and gaps. `docs/platform-model.md` §3 is the conformed
contract both feed into; `docs/provider-pipeline.md` is how a firm's slice reaches its book.

---

## 0. How to reproduce every number here

Everything below was measured, not inferred. The corpus is the local production archive:

|                 |                                                                                      |
| --------------- | ------------------------------------------------------------------------------------ |
| Location        | `~/dev/sei/p04-production` (PGP), `~/dev/sei/p04-production-decrypted` (report CSVs) |
| Span            | 74 drops, **2025-10-29 → 2026-03-27**, 4,286 files                                   |
| Delivery group  | `GWP_UK3100` — SEI's delivery group, **not** a firm id                               |
| Firms inside it | **6** (`FIRM_ID` 3102, 3106, 3108, 3110, 3112, 3114)                                 |
| Reference drop  | 2026-03-27 unless a row says otherwise                                               |

The oversight workbook cited throughout is `~/dev/sei/Select Oversight v3_02122025.xlsm` — the daily
ops pack Select Platform Limited produces by hand, dated 2 Dec 2025.

Decrypt and profile with the service's own key material:

```python
from dotenv import load_dotenv; load_dotenv('services/sei/.env')
from sei_sync.settings import SeiSettings
from sei_sync.decrypt import unlocked
with unlocked(SeiSettings()) as dec:
    text = dec(open(path, 'rb').read()).decode('utf-8', errors='replace')
```

**One caution about scale before any of the counts are read.** The `Accounts` report is grained
account × portfolio × something further, so a row count is not an account count and the two differ
by an order of magnitude. On 2026-03-27: 14,462 rows, **1,104 accounts**. Where a figure below is
row-grained it says so.

> This corrects a figure repeated in `CLAUDE.md`: "934 accounts classified `Pension` with 830
> sub-classified SIPP". Those are **rows**. At account grain the same drop holds **64 pension
> accounts, 52 of them SIPPs** (2026-03-27: 876 pension rows / 776 SIPP rows → 64 / 52 accounts).
> The conclusion drawn from it — that a SIPP at SEI is `ACCOUNT_TYPE` 157 with the pension nature in
> `ACCOUNT_CLASSIFICATION_NAME`, so mapping the wrapper alone files every pension as a GIA — is
> unaffected and still correct. Only the magnitude was overstated.

---

## 1. The two sources

|            | SFTP drop                                                        | SWP REST API                                                  |
| ---------- | ---------------------------------------------------------------- | ------------------------------------------------------------- |
| Shape      | 47 pipe-delimited reports, PGP, nightly                          | 265 operations across ~200 Swagger files (`pkgs/sei/spec`)    |
| Transport  | SFTP, source-IP allowlisted                                      | mTLS + OAuth password grant + AppKey, source-IP allowlisted   |
| Direction  | **Read only**                                                    | Read **and write** — the only way to instruct SEI             |
| Grain      | Whole delivery group (6 firms), scoped by `FIRM_ID` inside       | Per firm, and for most reads **per account**                  |
| History    | Snapshot reports full; transaction reports are a **daily delta** | 36 months sliding, 30-day windows, `endDate` may not be today |
| Cost       | Free, complete, unthrottled                                      | One request per account per window                            |
| Our client | `services/sei` (`sei_sync`) → 47 `raw.*` models                  | `pkgs/sei` (12 endpoint groups) + 2 interlace models          |

**The rule: read the drop first.** Where the drop has something the API also has, the drop is the
better source — complete, free, already conformed, not rate-limited. The API is for the three things
the drop cannot do: **write**, **read back a write before tomorrow's drop**, and fetch the handful of
firm-configured ids no report carries (enrolment templates above all).

---

## 2. Entity map — high level

| Concept                     | SFTP report(s)                                                                                    | API operation(s)                                                                                | Conformed relation                                                                                  | Book table                                                                                          |
| --------------------------- | ------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| Firm / subfirm / DSR team   | `Reference` (FIRM_ID, SUBFIRM, DSR, DSR_TYPE_NAME), `ClientAccountLink`                           | `firmDsrs`, `eligible*Dsrs`                                                                     | `platform.nodes`                                                                                    | `platform.nodes`                                                                                    |
| Client (person / entity)    | `Clients`, `ContactDetails`                                                                       | `retrievePartyDetails`, `updateClientBasicProfile`                                              | `platform.clients`, `platform.contact_points`                                                       | `crm.contacts`                                                                                      |
| Client relationships        | `Relationships`                                                                                   | `partyrelationshipservices`                                                                     | `platform.relationships`                                                                            | — (lake only)                                                                                       |
| Account                     | `Accounts`, `ClientAccountLink`                                                                   | `retrieveAccountDetails`, `getAccountDetailsInfo`, `accountservice/accounts` (create)           | `platform.accounts`                                                                                 | `platform.accounts`                                                                                 |
| Product / wrapper           | `Accounts.ACCOUNT_TYPE` + classification, `Reference ACCT_TYPE`                                   | Reference code sets                                                                             | derived on `platform.accounts.product_key`                                                          | `platform.products`, `product_platforms`                                                            |
| Holdings                    | `EODPositions`, `EODPositionsSupplemental`, `CustodyNostroPositions`, `EODChangedPositions`       | `realtimeholdings`                                                                              | `platform.positions`                                                                                | — **not projected** (§10.1)                                                                         |
| Valuation                   | derived from `EODPositions`                                                                       | `historicmarketvalues`                                                                          | `platform.valuations`                                                                               | `platform.valuations`                                                                               |
| Tax lots / CGT              | `Taxlots`, `FSLTaxData`                                                                           | `portfolio/taxlots`                                                                             | `platform.taxlots`                                                                                  | — (lake only)                                                                                       |
| Instrument                  | `Assets`, `AssetInvClas`, `FundCutoff`, `InterestRateTypes`                                       | `assetsetup`. The historical-price POST is not a flow on i31, so `raw.asset_prices` is disabled | `platform.instruments`                                                                              | `platform.instruments`                                                                              |
| Instrument costs (MiFID)    | `CostsCharges`, `ProductGovernance`, EMT extract                                                  | —                                                                                               | `platform.instrument_charges`; `platform.emt_charges`; ongoing charge on `platform.instruments.ocf` | `platform.instruments.ocf`                                                                          |
| Transaction                 | `TransactionHeader(+Upd)`, `TransactionDetail(+Upd)`                                              | `transactiondetailsservice/transactions`                                                        | `platform.transactions`, `transaction_details`                                                      | `platform.transactions`                                                                             |
| Pending / corporate actions | `UpcomingActivities`, `ActiveBlocks`                                                              | `activitymanagement/activities`                                                                 | `platform.upcoming_activities`                                                                      | — (lake only)                                                                                       |
| Regular payments & receipts | `RecCashActivities`                                                                               | `createCurrencyReceipt`, `createcashdisbursement`, `retrieverecurringscheduledevents`           | `platform.cash_schedules`                                                                           | — **no target** (§9.4)                                                                              |
| Bank / pay-to profiles      | `PaidTo`                                                                                          | `createpaytorecipientforaccount2`, `createpayerrecipientforaccount`                             | `platform.bank_accounts` → `platform.accounts.bank_accounts`                                        | `platform.accounts.bank_accounts`                                                                   |
| Fees                        | `FeePackageRule`, `FeePackageUsage`, `FeeGroup`, `FeeComputation`                                 | `feepackageadmin`, `feePackageAssignmentAccount`, `retrievefeepackagedetails`                   | — **unmodelled**                                                                                    | `platform.fee_packages` (Seccl only)                                                                |
| Investment models           | `ModelAlloc`, `ModelsIntExt` (**both permanently empty**), `Accounts.MODEL_ID`, `PortfolioGroups` | `retrievestrategylist`, `PSM/model/assignment/*`                                                | — **unmodelled**                                                                                    | `platform.models` (Seccl only)                                                                      |
| Performance                 | `PortfolioPerformance` (**15 of 74 drops**)                                                       | `entityinvestmentperformance`                                                                   | — **unmodelled**                                                                                    | `platform.accounts.performance` (jsonb)                                                             |
| Transfers in / out          | — (`UpcomingActivities` only hints)                                                               | `accountCustodyTransfer/transferIn                                                              | transferOut`, `freereceipt`, `freedelivery`, `getPendingTransfersStatus`                            | —                                                                                                   | `platform.transfers` |
| Statements                  | `StatementPackage`, `StatementEvents`, `StatementInstance`                                        | `docmgmtservicesaccount`                                                                        | — **unmodelled**                                                                                    | `documents.*`                                                                                       |
| Users / roles               | `UserDetail` (population, no email), `UserTeamRoleDetail`, `RoleDetail`                           | `employeeservice/firmemployeeusers` (**the email**)                                             | `platform.users` + `ref.employee_users`                                                             | `auth.users` via `auth.merge_provider_user` (0123)                                                  |
| Reference codes             | `Reference` (182 code sets, 30,732 rows)                                                          | `system_codes`, `firm_default_attributes`                                                       | `ref.codes` + 5 dimensions                                                                          | —                                                                                                   |
| Enrolment templates         | **none — the one thing only the API has**                                                         | `enrollmentclienttemplates`, `enrollmentaccounttemplates` (lowercase proxy)                     | `ref.enrollment_templates`                                                                          | `product_platforms.account_open_reference` (account); client templates on `tenant_platforms.config` |

---

## 3. The SFTP inventory — all 47 reports

Counts are the 2026-03-27 drop. "Consumer" is the conformed model that reads it today.

### 3a. Consumed (16 of 47)

| Report                 | Cols |   Rows | Consumer                                                                                      |
| ---------------------- | ---: | -----: | --------------------------------------------------------------------------------------------- |
| `Reference`            |    7 | 30,732 | `ref.reference_current` → `ref.{codes,firms,subfirms,dsr_teams,dsr_types,transfer_providers}` |
| `Accounts`             |  151 | 14,462 | `platform.accounts` (1,104 accounts)                                                          |
| `Clients`              |  105 |  2,774 | `platform.clients` (824 clients)                                                              |
| `ContactDetails`       |   29 |  3,371 | `platform.contact_points`, `platform.clients`                                                 |
| `ClientAccountLink`    |   90 |  1,774 | `platform.dsr_grants` → `platform.tenancy` (the tenancy spine)                                |
| `Assets`               |  118 | 20,521 | `platform.instruments` (6,384 instruments)                                                    |
| `EODPositions`         |   83 | 25,932 | `platform.positions` → `platform.valuations`                                                  |
| `Taxlots`              |   44 | 60,062 | `platform.taxlots`                                                                            |
| `TransactionHeader`    |  147 |  2,743 | `platform.transactions`                                                                       |
| `TransactionHeaderUpd` |  147 |     44 | `platform.transactions` (amendments)                                                          |
| `TransactionDetail`    |   35 |  5,141 | `platform.transaction_details`                                                                |
| `CostsCharges`         |   33 | 38,826 | `platform.instrument_charges`                                                                 |
| `Relationships`        |   47 |  3,444 | `platform.relationships`                                                                      |
| `UpcomingActivities`   |   34 | 13,622 | `platform.upcoming_activities`                                                                |
| `RecCashActivities`    |   55 |    130 | `platform.cash_schedules`                                                                     |
| `PaidTo`               |   34 |    682 | `platform.bank_accounts` → `platform.accounts.bank_accounts`                                  |

### 3b. Ingested to `raw.*`, consumed by nothing (25)

Every one is in the warehouse and queryable; none has a conformed model, so none reaches a firm's
slice or the book. Ordered by what they would unblock.

| Report                     | Cols |    Rows | What it holds, and what it would unblock                                                                                                                                                                  |
| -------------------------- | ---: | ------: | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `EODPositionsSupplemental` |   26 |  25,932 | **The cash balance.** `TRADED_CASH_BALANCE_IN_FBC` / `SETTLED_CASH_BALANCE_IN_FBC`, plus unrealised gain and market value in firm base currency. Directly closes `platform.valuations.cash_value` (§8.1). |
| `FSLTaxData`               |   20 |   4,805 | **CGT position per holding** — book cost, unrealised gain (local + GBP), YTD realised, carry-forward loss, annual exemption. The only report giving realised gains.                                       |
| `FeePackageRule`           |   22 |     808 | Fee packages, rules, rates, basis, VAT, qualifiers. Would give SEI what Seccl's `fee_structures` gives it.                                                                                                |
| `FeePackageUsage`          |   49 |   7,281 | Per-account fee assignment, frequency, discounts, waivers, funding account, next computation/collection date.                                                                                             |
| `FeeGroup`                 |    8 |      33 | Fee groups and their member accounts.                                                                                                                                                                     |
| `FeeComputation`           |   36 | 0–2,453 | The computed fee itself. Populated on 34 of 74 drops (fee-run days).                                                                                                                                      |
| `PortfolioGroups`          |   20 |     391 | Group ↔ account ↔ portfolio, with `MODEL_ID` (91% populated) and fee package. The other place a model id lives.                                                                                           |
| `ProductGovernance`        |   26 |   6,471 | MiFID target market: investor type, expertise, loss-bearing, return profile, risk tolerance, distribution strategy. Suitability evidence.                                                                 |
| `CustodyNostroPositions`   |   24 |  26,084 | Positions at the trading-partner/nostro level — **"our" side of a stock reconciliation** (§7.3).                                                                                                          |
| `EODChangedPositions`      |   83 |   1,522 | Same 83 columns as `EODPositions`, only rows that moved. A cheap daily-change feed.                                                                                                                       |
| `EODPosValAggr`            |   22 |     962 | Pre-aggregated daily-average and total traded/settled value, account and firm level. Directly answers the oversight pack's AUM tiles.                                                                     |
| `AssetInvClas`             |   28 |  38,826 | Instrument classification hierarchies (12 schema levels) — asset-allocation breakdowns.                                                                                                                   |
| `StatementInstance`        |   68 |   2,504 | Every statement produced: recipient, address, delivery method, status, dates.                                                                                                                             |
| `StatementEvents`          |   35 |   2,426 | Statement schedules per account.                                                                                                                                                                          |
| `StatementPackage`         |   15 |      45 | The statement package catalogue.                                                                                                                                                                          |
| `ActiveBlocks`             |   17 |   1,740 | Blocked/pledged quantities with reason and notes — a withdrawal-gate input.                                                                                                                               |
| `TransactionDetailUpd`     |   35 |     132 | Amendments to transaction detail. `platform.transaction_details` reads only the base.                                                                                                                     |
| `RoleDetail`               |    6 | 219,183 | SEI's own permission catalogue (role → function → access).                                                                                                                                                |
| `UserDetail`               |   12 |   1,591 | SEI platform users, status, money-movement limit. **`USER_TYPE` splits it 706 Employee / 865 End Client / 20 Data Integration** — only the first is staff (§10.9). No email; the API has it.              |
| `UserTeamRoleDetail`       |    6 |   2,245 | User → team → role.                                                                                                                                                                                       |
| `InvestmentDecisionMaker`  |   13 |      27 | DFM / IDM identities for MiFIR. Populated on 9 of 74 drops.                                                                                                                                               |
| `FundCutoff`               |    6 |   5,194 | Per-instrument dealing cut-off times.                                                                                                                                                                     |
| `InterestRateTypes`        |   10 |       9 | Interest schedules for the 9 instruments that have them.                                                                                                                                                  |
| `PortfolioPerformance`     |   33 | 0–4,439 | Monthly/QTD/YTD/ITD returns, std deviation, contributions. **Populated on only 15 of 74 drops** (§8.3).                                                                                                   |
| `TransactionRegulatory`    |    4 | 0–1,270 | `TRANSACTION_ID` → decision maker / executor. **Populated on 6 of 74 drops.** The MiFIR link (§7.5).                                                                                                      |

### 3c. Structurally empty — declared, delivered, never populated (6)

Every one of the 74 drops is header + trailer only (~1.25 KB). These are **not** a pull failure and
not something to wait for; they are switched off for this delivery group.

`AccountOptionalFields` · `AssetOptFields` · `PartyOptionalFields` · `FxForward` ·
**`ModelAlloc`** · **`ModelsIntExt`**

The last two matter: **the drop carries no model catalogue and no model allocations.** Accounts carry
a `MODEL_ID` (85% populated, 49 distinct) and `PortfolioGroups` a second one (91%, 37 distinct), but
nothing names or decomposes them. A model list has to come from the API
(`retrievestrategylistservice` / `retrievestrategydetailservice`) or from the firm. The firm's
target holdings are recorded in admin, on the model's allocation history: each line names an
instrument and a percentage, cash is its own figure, and the total is 100%.

### 3d. Schema drift is real, and it is additive

`Accounts` went from **142 to 151 columns** between 2026-02-05 and 2026-03-27, gaining
`TOTAL_SIPP_AMOUNT`, `TOTAL_SIPP_AMOUNT_DATE`, `SIPP_CRYSTALLISED_VALUE`, `SIPP_UNCRYSTALLISED_VALUE`,
`ISA_REPAYMENT_AMOUNT` and four `PLEDGE_EXTERNAL_*` columns. No other report changed width over the
corpus. The raw layer absorbs this (`SELECT *`, merge on `(_source_file, _row)`), but two consequences
are ours to handle: `sei_sync.schema` needs regenerating when it happens, and a conformed model that
concluded "SEI does not carry this" may have been right when it was written and wrong now — which is
exactly what happened to the crystallisation split (§8.2).

### 3e. EMT cost and charge effective dates

Not an SFTP report. SEI's reporting UI exports a workbook of every effective-dated charge
snapshot in a window. The file held for Select Platform Limited is as-of 28 Sep 2026: about
89,000 rows and 11,420 ISINs, most of them repeated once per file-creation date.
`scripts/build_emt_charges.py` writes `services/sei/data/emt_charges.csv` with one row per ISIN,
keeping the snapshot whose file-creation date is latest. `platform.emt_charges` reads that file.
`ocf` on the row is the ongoing-charge actual, or the estimate when that snapshot published no
actual. Amounts stay as printed.

`platform.instruments.ocf` keeps an agreed CostsCharges figure when the nightly drop has one, and
uses this extract otherwise. A disagreement between firms is not a number, so the extract fills
that gap rather than a firm being picked.

---

## 4. Column-level mapping

Per conformed entity: the SFTP column, the conformed name, and the book column it merges into. Only
the mapped columns are listed — the whole source row is preserved in `data` (`to_json`), so nothing
is lost, but anything not listed is unqueryable without unpacking JSON.

### 4.1 `platform.clients` → `crm.contacts` (105 source columns, 25 mapped)

| `Clients` column                                                   | Conformed                                                                       | Book                                                                                                                     |      Fill |
| ------------------------------------------------------------------ | ------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ | --------: |
| `CLIENT_ID`                                                        | `external_id`, `client_id`                                                      | `platform.links.external_id`                                                                                             |      100% |
| `CLIENT_TYPE`                                                      | `client_type` (`5` Individual → `individual`; `6` Organization → `entity`)      | `crm.contacts.type` (`individual` / `organisation`). A child is an individual under 18, from `date_of_birth`, not a code |      100% |
| `ORGANIZATION_TYPE`                                                | `sub_type` (`14`/`10` trust, `2` charity, `4`/`11` company; anything else NULL) | `crm.contacts.sub_type`                                                                                                  |  3.5–5.4% |
| `COMPANY_NAME`                                                     | `entity_name` (organisations only)                                              | `crm.contacts.name`, preferred over the name parts                                                                       |  3.5–5.4% |
| `FIRST_NAME` / `MIDDLE_NAME` / `ACCOUNT_HOLDERS_SURNAME` / `TITLE` | `first_name` / `middle_name` / `surname` / `title`                              | same                                                                                                                     |      100% |
| `DATE_OF_BIRTH`                                                    | `date_of_birth`                                                                 | `dob`                                                                                                                    |      100% |
| `NATIONALITY`                                                      | `nationality` (list)                                                            | `nationality text[]`                                                                                                     |         — |
| `COUNTRY_OF_RESIDENCE` / `COUNTRY_OF_DOMICILE`                     | same                                                                            | `data`                                                                                                                   |         — |
| `NATIONAL_INSURANCE_NUMBER`                                        | `national_insurance_number`, `national_identifier` (+`_type='nino'`)            | `national_identifier`                                                                                                    |         — |
| `COMMENCEMENT_DATE`                                                | `commenced_on`                                                                  | `data`                                                                                                                   |         — |
| `RESIDING_SUBFIRM_ID`, `FIRM_ID`                                   | `key`, `lineage`, `firm_id`, `subfirm_id`                                       | `org_id` via `platform.nodes`                                                                                            |      100% |
| — (`ContactDetails`)                                               | `email`, `mobile`, `phone`, `address{}`                                         | `email`, `mobile`, `phone`, `address` jsonb                                                                              | 84% / 63% |
| `CLIENT_BASE_CURRENCY`                                             | `base_currency`                                                                 | `crm.contacts.currency`                                                                                                  |     ~100% |

`TITLE` is a Reference code (`ref.codes`, type `TITLE`), not the word. `1` is Mr and `2` is Mrs; `0` is unset and publishes NULL. A code the reference does not describe stays NULL rather than the number. A few descriptions are the same title our record already stores in a shorter form: `Mr.` is `Mr`, `Professor` is `Prof`, `Captain` is `Capt`, `The Reverend` is `Reverend`, `The Honorable` is `The Honourable`, and `H E Shaikh` is `H E Sheikh`. The dropdown offers Mr, Mrs, Miss, Ms, Dr and Prof, the titles every custodian can store. Seccl's published enum is Dr, Professor, Mrs, Miss, Mr, Ms, Mx, and enrolment sends Prof as Professor ([`docs/client-vocabularies.md`](client-vocabularies.md)). A title already on the contact that is still only digits is replaced with the resolved word on the next client merge; a word the firm has typed is left alone.

**Unmapped and worth mapping** (all present, all with an existing consumer in our schema):

| `Clients` column                                                                                                                           |      Fill | Our column                                                                                                                                                                                                                                                                                                    |
| ------------------------------------------------------------------------------------------------------------------------------------------ | --------: | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `VULNERABLE_FLG`                                                                                                                           | **99.8%** | `crm.contacts.is_vulnerable` — `1` yes / `2` no                                                                                                                                                                                                                                                               |
| `MARITAL_STATUS`                                                                                                                           |     40.7% | `crm.contacts.marital_status`. `0` is unset; Separated and Partnered have no book value                                                                                                                                                                                                                       |
| `GENDER`                                                                                                                                   |     40.1% | `crm.contacts.gender` — `1` male / `2` female. `0` is unset                                                                                                                                                                                                                                                   |
| `COUNTRY_OF_BIRTH`                                                                                                                         |     94.5% | `crm.contacts.country_of_birth` (ISO code)                                                                                                                                                                                                                                                                    |
| `EXTERNAL_PARTY_ID`                                                                                                                        |      100% | conformed `external_party_id`. Not a contact column: `CLIENT_ID` is already the link, and `retrievePartyDetails` accepts that as `entityId`. This is the other handle, `externalPartyId` — our contact uid when we enrolled the client, and usually the same number as `CLIENT_ID` when SEI already held them |
| `TAX_ID_TYPE` / `TAX_ID_NUMBER`                                                                                                            |     99.9% | `national_identifier_type` / `national_identifier` (richer than the NINO-only path)                                                                                                                                                                                                                           |
| `TOTAL_REMAINING_ISA_HEADROOM_AMOUNT`, `STOCK_AND_SHARE_ISA_SUBSCRIPTION_AMOUNT`, `CASH_ISA_SUBSCRIPTION_AMOUNT`, `ISA_WITHDRAWAL_TO_DATE` |       58% | nothing — **this is the ISA allowance check `docs/contributions.md` does not have**                                                                                                                                                                                                                           |
| `LEGAL_ENTITY_IDENTIFIER`                                                                                                                  |      5.3% | nothing                                                                                                                                                                                                                                                                                                       |
| `COUNTRY_OF_INCORPORATION`                                                                                                                 |  3.5–5.4% | nothing                                                                                                                                                                                                                                                                                                       |
| `FIRST_PASSPORT_NUMBER` + `COUNTRY_OF_CITIZENSHIP_FIRST_PASSPORT`                                                                          |         — | `passport_number` / `passport_country`. This drop has the country and no passport number                                                                                                                                                                                                                      |
| `CERTIFIED_FATCA_COMPLIANCE`                                                                                                               |         — | `fatca_status`. W-9 sets `us_person`; W-8 clears it. `CLIENT_SUBJECT_TO_FATCA` does not — it is false on a W-9                                                                                                                                                                                                |

Empty in this corpus, so not worth mapping: `CLIENT_RISK_RATING`, `FSA_CLASSIFICATION`,
`RELATIONSHIP_MANAGER_NAME`, `HOUSEHOLD_ID`.

### 4.2 `platform.accounts` → `platform.accounts` (151 source columns, 24 mapped)

| `Accounts` column                                             | Conformed                                       | Book                                                        |           Fill |
| ------------------------------------------------------------- | ----------------------------------------------- | ----------------------------------------------------------- | -------------: |
| `ACCOUNT_NUMBER`                                              | `external_id`, `account_number`                 | `platform.links.external_id`                                |           100% |
| `ACCOUNT_DISPLAY_NAME`                                        | `account_name`                                  | `name`                                                      |           100% |
| `ACCOUNT_TYPE` **+ classification**                           | `product_key`                                   | `product_id` via `platform.products`                        |           100% |
| `ACCOUNT_STATUS_CODE`                                         | `status_raw`, `status`                          | `status`                                                    |           100% |
| `ACCOUNT_BASE_CURRENCY`                                       | `base_currency`                                 | `currency`                                                  |           100% |
| `ACCOUNT_OPEN_DATE` / `LAST_TRADE_DATE` / `DATE_LAST_UPDATED` | `opened_on` / `last_traded_on` / `updated_on`   | `opened_at`                                                 |           100% |
| `PORTFOLIO_ID` / `PORTFOLIO_TYPE`                             | same                                            | `data`                                                      |           100% |
| `DORMANT_STATUS_FLAG` / `FROZEN_STATUS_FLAG`                  | `is_dormant` / `is_frozen`                      | `data`                                                      |           100% |
| `MIFID_CLASSIFICATION`                                        | `mifid_classification`                          | `data`                                                      |              — |
| `CAPACITY_TYPE`                                               | `is_discretionary`                              | `is_discretionary`                                          |              — |
| `FEE_PACKAGE_ID`                                              | `fee_package_id`                                | **nothing** — `fee_packages` is not a registered SEI entity |          99.9% |
| **`STRATEGY_ID`**                                             | **`model_key`**                                 | `model_id` via `link:model`                                 | **0%** ← §10.2 |
| `ClientAccountLink.CLIENT_ID`                                 | `owner_external_ids`, `lead_client_external_id` | `contact_id` via `link:contact`                             |            88% |

**Unmapped and material:**

| Column                                                                        |                   Fill | Why it matters                                                                                   |
| ----------------------------------------------------------------------------- | ---------------------: | ------------------------------------------------------------------------------------------------ |
| **`MODEL_ID`**                                                                | **85.4%**, 49 distinct | The model the account is actually managed to. `model_key` reads `STRATEGY_ID` instead (§10.2).   |
| `IMPLEMENTATION_MODEL_NAME`                                                   |      6.0%, 23 distinct | The readable half of the same fact.                                                              |
| `ISA_CONTRIBUTION_TO_DATE` / `ISA_WITHDRAWAL_TO_DATE`                         |                   100% | Subscription used this tax year — the contributions engine's missing allowance input.            |
| `TAX_WRAP_PROVIDER_ID` / `TAX_WRAP_EXTERNAL_ACCT_NUM`                         |                   6.6% | **The third-party pension administrator** — i.e. the Morgan Lloyd side of a SIPP, stated by SEI. |
| `SIPP_CRYSTALLISED_VALUE` / `SIPP_UNCRYSTALLISED_VALUE` / `TOTAL_SIPP_AMOUNT` | 0% _(new column, §3d)_ | The crystallisation split `0117` reads. Empty here; the column now exists (§8.2).                |
| `TERMINATING_EVENT` / `TERMINATION_DT`                                        |                      — | Closure reason.                                                                                  |
| `NEXT_INVESTMENT_REVIEW_DT` / `NEXT_ADMIN_REVIEW_DT` / `REQUIRED_REVIEW_TYPE` |                      — | Suitability review scheduling — a flows trigger with no source today.                            |
| `PLEDGE_*` (3 slots + 4 external)                                             |                      — | Charges over the account; a withdrawal-gate input.                                               |
| `INVESTMENT_RESTRICTIONS`, `ACCOUNT_RESTRICTION`                              |                      — | Dealing restrictions.                                                                            |
| `VAT_ELIGIBLE_FLAG`, `ACCOUNT_TAX_CATEGORY`, `TAX_YEAR_END_MONTH`             |                   100% | Fee VAT treatment.                                                                               |
| `SOURCE/INTRODUCER`, `SOLUTION`, `INVESTMENT_OBJECTIVE`                       |                      — | Segmentation the oversight pack reports on.                                                      |

Empty in this corpus: `PRODUCT_ID`, `ACCOUNT_RISK_RATING`, `PENSION_FUND_REF`,
`ACCOUNT_SPECIFIC_FEE_AMOUNT`, `STRATEGY_ID`, `STRATEGY_NAME`.

### 4.3 `platform.positions` (83 source columns, 12 mapped)

`EODPositions` is one row per account × portfolio × instrument × traded instrument × taxlot × day. `TAXLOT_ID` is sometimes blank; `TRD_INSTRUMENT_ID` is what keeps two listings of one instrument apart, and it is part of `external_id`, `position_key` and the supplemental join. Sixteen
`DEBIT_CREDIT_FLAG_n` columns qualify the money column immediately before each; the model applies
the sign for `MARKET_VALUE_IN_ASSET_CURRENCY` (`_1`), `DEALING_ACCRUED_INTEREST` (`_2`) and
`DEALING_COST` (`_5`) and leaves the other thirteen in `data`.

Mapped: `ACCOUNT_NUMBER`, `TAXLOT_PORTFOLIO_ID`, `TAXLOT_ID`, `INSTRUMENT_ID`, `SEDOL`,
`ASSET_CURRENCY`, `AS_OF_DATE`, `QUANTITY_HELD`, `ASSET_PRICE`, market value, accrued interest, cost.

**Unmapped and material:** `SETTLED_QTY` and every `SETTLED_*` money column (the oversight pack
reconciles on _settled_, we publish _traded_), `BLOCKED_QTY` + `COMMIT_AMOUNT`/`COMMIT_REASON` (a
withdrawal-gate input), `TRADED_BOOK_COST_BASE` / `SETTLED_BOOK_COST_BASE` (book cost stated by SEI —
we currently derive `book_cost` by summing `DEALING_COST`), `PRICE_DATE`, `GROSS_INCOME`,
`TRADED_ESTIMATED_ANNUAL_INCOME`, and `LOCAL_TO_BASE_EXCH_RATE` / `LOCAL_TO_FIRM_EXCH_RATE`.

**Cash is a position, on a currency instrument, and there is no flag on this report to find it by.**
Measured on 2026-03-27: 1,025 of 25,932 position rows sit on one of **7 currency instruments**,
carrying £10,216,716.87 of asset-currency market value. They are identifiable two ways — a join to
`Taxlots.INSTRUMENT_IS_CURRENCY_FLAG`, or `Assets.ASSET_TYPE = 'MRCXXXXXXX'` (142 such instruments
group-wide, all with a blank ISIN and a currency name).

> **Do not reach for "no ISIN means cash".** 8,755 of 20,521 asset rows have no ISIN and **8,486 of
> them are `MMFXXXXXXX` — money-market funds, which are not cash.** The heuristic that returns zero
> cash on Seccl returns a nine-figure over-count here. `ASSET_TYPE` is the stable signal.

### 4.4 `platform.transactions` (147 source columns, 26 mapped)

`TransactionHeader` merged with `TransactionHeaderUpd` on `TRANSACTION_ID` (amendments arrive two
days late and against the whole history, not the current partition). Debit/credit flags sign net,
base, fee, interest, tax, gross and the asset-currency book value.

The canonical `type` is mapped from the **124 real `TRANSACTION_TYPE` codes** in the `Reference`
report; direction-less codes (5, 8, 88, 89, meeting and trading-status notices) resolve to NULL
rather than being guessed. `DESCRIPTION_CODE_ID` is the subtype, and it is what separates a
contribution (21121) from its RAS relief payment (21122) inside the one Cash Receipt type — without
it the reconciliation counts the relief as a second contribution. The listing prints
`source_subtype`, so the code is resolved through `ref.codes` (`type = 'DESCRIPTION_CODE_ID'`) to
its description (`20043` → Interest Income). The code itself stays on `description_code_id`. A
code with no single description publishes a null subtype and the listing falls back to
`TRANSACTION_DESCRIPTION`.

The displayed amount is the signed net. Where net and gross are both zero — free receipt, free
delivery, rights, spin-off — it is the signed `BOOK_VALUE_IN_ASSET_CURRENCY` (flag 12). The base
book value stays unmapped.

**Unmapped and material:** `SETTLEMENT_DATE` vs `ACTUAL_SETTLEMENT_DATE` (98.4% populated — the
settlement-fail signal), `REALISED_GAIN_LOSS` / `BASE_REALISED_GAIN_LOSS` / `LT_`/`ST_` splits (CGT),
`BOOK_VALUE_IN_BASE_CURRENCY`, `EXECUTING_BROKER` + `EXECUTING_BROKER_ID`, `TRADE_DATETIME` and the four
`ELAPSED_TIME_*` columns (best-execution evidence), `CA_EVENT_ID` + `CA_EX_DATE`/`CA_RECORD_DATE`/
`CA_PAYABLE_DATE`/`CA_TERMS_RATES` (corporate actions), `NET_MIFID_CHARGE`, `SUPPRESSION_INDICATOR`,
`ADJUSTMENT_TYPE` + `ADJ_OLD_VALUE`/`ADJ_NEW_VALUE`, `PAY_TO_PROFILE_ID` (links a payment to a
`PaidTo` profile), `CUSTODIAN_ACCOUNT_NUM`, and the four `*_INI_BUS_PROC_REF`/`_TYPE` pairs that
carry the originating business process.

### 4.5 `platform.instruments` (118 source columns, 17 mapped)

Mapped: id, name, `ASSET_TYPE`, ISIN, SEDOL, CUSIP, currency, exchange, market/issuer country,
credit rating, interest rate, issue and maturity dates.

**Unmapped and material:** `TICKER`, `PRICE` + `PRICE_DATE` + `PRICING_CURRENCY` (a price series we
currently take only via positions), all five `MIFID_*` indicators, `ICB_SECTOR`/`SUPER_SECTOR`/
`SUB_SECTOR` and `REGION_CODE`/`SUPER_REGION_CODE` (asset-allocation dimensions), the four
`SETTLEMENT_CYCLE_*` columns, `MIN_*` dealing constraints, `TRADING_STATUS`, `OPEN_TO_NEW_INVESTMENT_FLG`,
`STAMP_DUTY_TYPE` / `PTM_LEVY_FLG`, and the three `LEVEL{1,2,3}_INSTRUMENT_HIERARCHY` columns.

### 4.6 `ref.*` — the Reference report

One report, 7 columns (`TYPE`, `CODE`, `DESCRIPTION`, `FIRM_ID`, `QUALIFIER_1..3`), **182 code sets,
30,732 rows**. It is the dimension table for almost every code in every other report, and it is where
the enrolment ids come from. Blank `FIRM_ID` means platform-wide; populated means firm-scoped.

The sets that matter most: `ACCT_TYPE` (4), `ACCOUNT_CLASSIFICATION` (54), `ACCOUNT_SUB_CLASSIFICATION`
(324), `TRANSACTION_TYPE` (124), `DESCRIPTION_CODE_ID` (6,576), `TREATMENT_CODE` (882), `DSR` (517),
`DSR_TYPE_NAME` (48), `SUBFIRM` (6), `FIRM_ID` (6), `TAX_ID_TYPE` (22), `TITLE` (61), `GENDER` (2),
`MARITAL_STATUS` (7), `FATCA_COMPLIANCE` (11), `PAY_TO_METHOD` (9), `FEE_TYPE` (181),
`REAL_TIME_FEE_PACKAGE` (84), `COUNTRY` (254), `CURRENCY` (142), `BROKER` (6,762), `ISSUER` (1,030).

**What it does not carry: `CLIENT_TEMPLATE` and `ACCOUNT_TEMPLATE`.** There is no code set and no
report for either, and both are required by enrolment — which is the entire justification for
`ref.enrollment_templates` reaching for the API.

---

## 5. The API surface

### 5.1 What `pkgs/sei` covers today

12 endpoint groups over the 265-operation spec: `accounts`, `activities`, `cash`, `cash_receipts`,
`clients`, `enrollment`, `fees`, `holdings`, `investment_models`, `reference`, `transfers`, plus
`_base` (mTLS + OAuth + AppKey). The firm-issued API sheet
(`Select-Marlborough_API_sheet_v2 151025.xlsx`, parsed into `pkgs/sei/vendor_manifest.json`)
authorises **26 services**; the merged spec documents 265.

### 5.2 API-sourced interlace models

| Model                      | Why it exists                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ref.enrollment_templates` | `clientTemplateId` / `accountTemplateId` are required by `POST /accountservice/accounts` and appear in no report and no Reference code set. Two requests per firm; the narrowest possible unblock for the whole enrolment path. The account template is per **shelf product**, not per wrapper, so its id belongs on `platform.product_platforms.account_open_reference`.                                                                                                                                                                   |
| `raw.api_transactions`     | The drop's transaction reports are a **daily delta** and SEI keeps ~5 days on SFTP, so any missed stretch is a permanent hole. Fans out per account (a firm-wide query is refused). Every fifteen minutes it asks for the days after the last covered day, through yesterday (SEI refuses today; there is no last-modified). `merge` is on `transaction_id`. A first scan starts at that firm's earliest `ACCOUNT_OPEN_DATE`. Ids the file does not already carry are appended to `platform.transactions`; a shared id stays with the file. |
| `raw.asset_prices`         | Disabled. `POST /assetpriceproxyservice/historicalassetprices` is the spec path, and on i31 the lowercase proxy is deployed with no flow for that resource (`404 unknown-resource`, every casing). The model returns an empty table and does not call SEI. The Assets report still carries the latest `PRICE`.                                                                                                                                                                                                                              |

A blank `SEI_API_BASE_URL` disables the others — a deployment with SFTP credentials and no
API credentials runs unchanged rather than failing nightly. `raw.asset_prices` stays off
with the URL set, until `historicalassetprices` is a flow on that proxy.

### 5.3 API reads that would add something the drop does not have

| Operation                                                                                                                         | Adds                                                                                                    |
| --------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `GET /entityinvestmentperformance/investmentPerformance`                                                                          | Returns for a party/account — the drop's `PortfolioPerformance` is populated on 15 of 74 days (§8.3)    |
| `GET /historicmarketvalueservice/historicmarketvalues` + `POST .../accountsHistoricMarketValues`                                  | A valuation **series**; the drop gives one snapshot a day and `platform.positions` windows to `max(dt)` |
| `GET /realtimeholdingsservice/realtimeholdings`                                                                                   | Intraday holdings; the drop is EOD only                                                                 |
| `POST /pmemodelassignmentservices/PSM/model/assignment/byAccount`, `retrievestrategylistservice`, `retrievestrategydetailservice` | **The model catalogue** — `ModelAlloc`/`ModelsIntExt` are permanently empty                             |
| `POST /pendingtransfersstatusservice/getPendingTransfersStatus`, `freereceiptstatus`, `freedeliverystatus`                        | Transfer status — the drop has no transfer entity at all                                                |
| `GET /retrievefeepackagedetails/...`, `periodicfeepackageruledetails`                                                             | Fee packages, if we would rather not model `FeePackageRule`/`Usage`                                     |
| `POST /tradesessionprovisionalexception/...`, `tradesessionsubmittedexception`                                                    | PME trade-session exceptions — the closest thing the API has to an exception feed (§7)                  |

---

## 6. SFTP ↔ API reconciliation

### 6.1 Where the two can be compared

| Fact           | Drop                       | API                         | Join key                             | Verdict                   |
| -------------- | -------------------------- | --------------------------- | ------------------------------------ | ------------------------- |
| Account master | `Accounts`                 | `retrieveAccountDetails`    | `ACCOUNT_NUMBER`                     | Comparable field by field |
| Client master  | `Clients`                  | `retrievePartyDetails`      | `EXTERNAL_PARTY_ID` (100% populated) | Comparable                |
| Holdings       | `EODPositions`             | `realtimeholdings`          | account + instrument                 | Comparable at EOD only    |
| Valuation      | derived from positions     | `historicmarketvalues`      | account + date                       | Comparable                |
| Fee packages   | `FeePackageRule` / `Usage` | `retrievefeepackagedetails` | `FEE_PACKAGE_PROFILE_ID`             | Comparable                |
| Tax lots       | `Taxlots`                  | `portfolio/taxlots`         | `TAXLOT_ID`                          | Comparable                |

### 6.2 Where they cannot — and this is measured, not assumed

**Transactions from the two sources are complements, not copies, and the gap is now attributed.**
A 07 Aug 2026 30-day sample looked disjoint because the calendars barely overlapped (API 10–31
Jul vs drop 30 Jul–6 Aug) and is superseded. Re-run 21 Sep 2026
(`scripts/reconcile_sftp_vs_api.py`, `docs/sei.md` §1a): on days the drop covers, **56,527 of
65,078 file ids (86.9%) join the API**. The 8,550 file-only ids are **not trades, not closed
accounts, and not paging**: 6,212 dummy zero-amount CA memos (type 19, `DUMMY_TRANSACTION_FLAG=Y`)
the API will not return even when asked; 1,357 live production SFTP vs i31; 735 FX-Spot that a
**1-day** API window returns and a 2-/30-day window omits; ~246 suppressed rows
(`SUPPRESSION_INDICATOR=2` / `includeSuppressedTransactionFlag`). Excluding dummy memos, 98.3%
of the p04 file joins. Closed/inactive 422s (385 accounts) are i31-unprovisioned actives and
contribute 5 file-only rows. API-only on covered days (4,854) is extra i31 activity, dominated
by Periodic Fee in March and September. The live Sep 2026 drop vs i31 is essentially disjoint —
that overlap was a historical p04-era slice, not a licence to replace tonight’s file.

### 6.3 What a reconciliation should actually assert

Given the above, the useful daily checks are:

1. **Account and client population** — every account in `retrieveAccountDetails` appears in
   `Accounts` and vice versa, per firm. Catches a fan-out or DSR-grant gap.
2. **Valuation** — `historicmarketvalues` for date _d_ against `sum(platform.positions.market_value)`
   for _d_. Catches a partial drop the trailer check passed.
3. **Cash** — `EODPositionsSupplemental.TRADED_CASH_BALANCE_IN_FBC` against the currency-instrument
   positions (§4.3). The two are computed differently by SEI and agreed to within FX on 2026-03-27
   (£10,035,637 FBC vs £10,216,717 asset-currency).
4. **The delta test** — `TransactionHeader` vs `raw.api_transactions` for date _d_ on
   `transaction_id`. After stripping dummy CA memos (`DUMMY_TRANSACTION_FLAG=Y`), expect ~98%
   of the historical file to join if the fetch used 1-day windows and
   `includeSuppressedTransactionFlag`. Residual gaps are production-vs-test and the API-only
   extra (fees, especially). Do not treat a 13% raw miss as "trades" or "closed accounts".

---

## 7. What neither source carries — the oversight pack

The workbook has ten sections. Four are fully sourceable from the drop today; six are not, and
**five of those six come from systems that are not SEI at all.** This matches the independent
assessment in `~/dev/sei/p04-production-decrypted/sachin.md`, reached from the same corpus.

| #   | Section                    | Sheet rows | Sourceable?                                                                  |
| --- | -------------------------- | ---------: | ---------------------------------------------------------------------------- |
| 1   | Client AUM                 |     15,943 | ✅ `EODPositions` + `Assets` + `Accounts`; `EODPosValAggr` pre-aggregates it |
| 2   | Cash reconciliation        |      8,193 | ❌ **external** — see below                                                  |
| 3   | Stock reconciliation       |        280 | ⚠️ partial — `CustodyNostroPositions` is "our" side only                     |
| 4   | Transfers / free movements |        289 | ❌ **external** (Freetrack) — API gives status, not the workflow             |
| 5   | Trades                     |      1,232 | ✅ `TransactionHeader` + `TransactionDetail`                                 |
| 6   | Static data                |        489 | ✅ `Clients` + `Accounts` + `ClientAccountLink`                              |
| 7   | Unmatched trades           |          3 | ❌ **external** (HSBC/CREST settlement)                                      |
| 8   | Unallocated cash           |          — | ❌ **external** (same feed as 2)                                             |
| 9   | MiFIR                      |         22 | ❌ **external** (regulatory reporting system)                                |
| 10  | Client issues log          |         13 | ❌ manual                                                                    |

### 7.1 Cash exceptions — external

Columns: `Account Name`, `Value Date`, `Entry Date`, `Item Type`, **`Our Amount`**, **`Their Amount`**,
`Days Aged`, `Operational Days Aged`, **`Calculated Risk weighting`**, `Reconciliation Note`,
`EDB Activity Id`, `Item ID`. The account names are nostro accounts (`SPL CA-S081-FX BOOKING CLR`),
not client accounts. Nothing in the drop carries a "their" amount, an aging bucket, a risk weighting
or a reconciliation note — this is a **reconciliation tool's** output (SEI's EDB), not a book extract.
The API has no reconciliation read either. **This is a feed to request from SEI.**

### 7.2 Unallocated cash — external, same feed

The workbook derives it from the cash-exception extract. Same answer.

### 7.3 Stock exceptions — half sourceable

Columns: `Account`, `Security Master ID/Trade ID`, **`Total "Their" Position`**,
`Total Transaction Exception`, **`Total "Our" Positions`**, `Variance`, price, and both values in
GBP. `CustodyNostroPositions` gives "our" side at the trading-partner account
(`SPL SEC ALLFUNDS 3106S81INC` appears there), so **the variance could be computed if the
counterparty's position were available.** It is not, from either source. The 3 breaks on 2 Dec 2025
totalled £4,949 and −€85,454.

### 7.4 Unmatched trades — external

Columns: `Settlement Reason`, `Trade Status` (FAILED / UNMATCHED), **`HSBC Reference`**,
`Buyer / Seller Code` (BIC), `Broker Description`, `Location Description`,
`Place of Settlement Description`. These are CREST/DTC settlement statuses from the **custodian's
settlement system**, keyed on an HSBC reference nothing in SEI publishes.
`TransactionHeader.ACTUAL_SETTLEMENT_DATE` (98.4% populated) would let us flag a trade that has not
settled on time — that is a _detection_, not the settlement reason, and it is the closest we can get.
The API's `transferssettlementservice/settlementservicemanualmatch` and
`transfersettlementservice/settletradewithoutsettlementrecord` **act** on unmatched settlements
without ever letting us **read** the list.

### 7.5 MiFIR — external, with one hook in the drop

Columns: `Transaction Reference Number` (`SEIFP0422929629`), `Executing Entity ID` (LEI),
`Report Status` (NEWT), `Venue`, `Instrument ID`, `Trading Date Time` to the microsecond,
`Non-Qualified Reason`. The **submission** is a regulatory reporting system's output. But
`TransactionRegulatory` (`FIRM_ID`, `TRANSACTION_ID`, `INVESTMENT_DECISION_MAKER_ID`,
`INVESTMENT_EXECUTOR_ID`) is the drop's own link between a transaction and its decision maker, and
`InvestmentDecisionMaker` names them. Both are sparse — 6 and 9 of 74 drops — so the join exists but
is not reliably populated.

### 7.6 Free movements / transfers — external workflow, API status

Columns: `Freetrack Trans Number`, `Counterparty Name`, `Altus Transfer (Yes/No)`, `FollowUp Date`,
`Account Notes` + note dates, `Asset Status`. **Freetrack is a transfer-tracking tool**, not SEI. The
API's `getPendingTransfersStatus` / `freereceiptstatus` / `freedeliverystatus` give the _status_ of a
transfer we instructed; they give no counterparty workflow, no follow-up date and no notes.
`UpcomingActivities` carries in-flight activity with `ACTIVITY_TYPE`/`ACTIVITY_STATUS` and would
detect an unsettled free receipt, but not the chase.

The transfers workbench reads posted movements from `platform.transactions`, not that pending-status
service. What it uses:

| What the row shows | Field                                                                                                                                                                                                                                                                                                                                                                                                 |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Stage              | `transfer_status` in the payload when SEI sent a pending-transfer code (`sei_stage`). A ledger row has neither that code nor a lifecycle. The model writes `status = 'settled'`, and a blank already in the book is the same fact: the custodian has the assets, so the stage is `funds_sent` (the badge says Completed). `submitted` is only an explicit pending status, or pending-transfer code 2. |
| Worth              | Signed net when there is cash. A free receipt posts net and gross at 0; the cost is `local_book_value` (API, already signed) or `BOOK_VALUE_IN_ASSET_CURRENCY` signed by flag 12 (file). Several receipts that share `RELATED_ACTIVITY_ID` are one transfer and the worth is their sum.                                                                                                               |
| In specie or cash  | `Free Receipt` / `Security Transfer -Transfer To` are in specie. `Currency Transfer To` and the other cash-transfer names are cash. Code 8 (`Cash Transfer`) has no from/to; the signed net says which way.                                                                                                                                                                                           |
| Ceding scheme      | `previous_provider_name` when present, otherwise the file's `ORIGINATOR`. The transaction API does not name the counterparty.                                                                                                                                                                                                                                                                         |

### 7.7 Summary of feeds to request from SEI

1. **Cash reconciliation / EDB exception extract** — unblocks sections 2 and 8 (8,193 rows/day).
2. **Counterparty stock positions** — completes section 3.
3. **Settlement exception feed** (or CREST/HSBC access) — unblocks section 7.
4. **MiFIR submission extract** — unblocks section 9; `TransactionRegulatory` populated daily would
   substantially help.
5. **`PortfolioPerformance` and `FeeComputation` populated on every drop** rather than 15 and 34 of 74.

---

## 8. Data we should generate

The Seccl precedent is instructive in both directions: Seccl **publishes** `bookValue`,
`transferBookValue`, `growth` and `openingValue` per position, so `platform.positions` reads them.
SEI publishes some of this and we derive the rest — and where we derive nothing, a real surface is
empty.

### 8.1 Daily cash balance and the currency it is in — FIXED

`platform.valuations.cash_value` was `NULL::decimal` for every SEI account while the book column
existed and every portal read it. It is SEI's own figure now, and finding it settled a bigger
question underneath.

**Cash is an asset type.** A currency holding is `ASSET_TYPE = 'MRCXXXXXXX'` with a blank ISIN and a
currency for a name — 7 instruments, 1,025 of 25,932 position rows, £10.2m on 2026-03-27 — and its
market value _is_ the account's cash. Checked against `EODPositionsSupplemental`'s own
`TRADED_CASH_BALANCE_IN_FBC`: the same 1,025 rows on both sides, agreeing on 1,021, and the four
that differ are exactly the USD-base rows (below).

**SEI publishes every money figure at three currency levels, and the model was reading the wrong
one.** This is the defect the cash work uncovered:

| Level            | Columns                                             | 2026-03-27 spread                                       |
| ---------------- | --------------------------------------------------- | ------------------------------------------------------- |
| Asset / local    | `ASSET_CURRENCY`, `MARKET_VALUE_IN_ASSET_CURRENCY`  | GBP 25,229 · USD 576 · EUR 75 · CHF 27 · HKD 19 · JPY 4 |
| **Account base** | `VALUATION_CURRENCY`, the `VALUATION_*` columns     | GBP 25,906 · **USD 26**                                 |
| Firm base        | `FIRM_BASE_CURRENCY`, the supplemental's `*_IN_FBC` | GBP for all six firms                                   |

The model published the **asset's** currency, and every reader SUMS these columns — `_HOLDINGS_SQL`'s
caller totals an account and `platform.valuations` is `sum(market_value) GROUP BY account, date`. So
703 of 25,932 positions were being added to sterling as though dollars were pounds.

It now publishes the **account's** base currency, because that is what `platform.accounts.base_currency`
holds and a total shown against an account must be in the account's own money. Firm currency would
be wrong in a way that is easy to miss: two accounts in this drop (MSP000073, MSP000074) are
USD-based, and SEI says so itself by publishing two rates — `LOCAL_TO_BASE_EXCH_RATE` differs from
`LOCAL_TO_FIRM_EXCH_RATE` on exactly those 26 rows.

**The gain is SEI's, converted, not derived.** No cost column reproduces
`UNREALIZED_GAIN_LOSS_IN_FBC` — the closest pairing holds on 24,260 of 24,884 rows, and the
clean-vs-dirty market value was the obvious explanation and is not it either. One row derives
+2,770.40 where SEI says −13,767.56. So SEI's figure is carried into the account's currency by
SEI's own two rates, a relationship verified rather than assumed:
`local_value × LOCAL_TO_FIRM_EXCH_RATE = TRADED_MARKET_VAL_IN_FBC` holds on all 25,932 rows with
zero failures, and the ratio is 1 on the 25,906 rows where the currencies coincide.

**And one column the plan for this work named does not exist in practice:**
`TRADED_BOOK_COST_BASE` / `SETTLED_BOOK_COST_BASE` look like the book cost and are **0% populated in
every drop of the corpus**. `VALUATION_COST` (96%) carries it.

### 8.2 Crystallisation split — read the Accounts columns onto the latest day

`SIPP_CRYSTALLISED_VALUE` and `SIPP_UNCRYSTALLISED_VALUE` arrived on the Accounts report
between the 2026-02-05 and 2026-03-27 drops (§3d). They are 0% populated for the six firms
in that corpus. `platform.valuations` reads them from the latest Accounts snapshot and
stamps them on the **latest valuation day only** — a historical day must not wear today's
split. Blank columns stay NULL. 0 would say the fund is wholly one side. "This firm has
not filled them in" is a different sentence from "SEI cannot express this", and only the
first one lets a later drop reach the book.

### 8.3 Performance series — derive, do not wait

`PortfolioPerformance` is populated on **15 of 74 drops** and carries a rich shape when it is
(monthly returns 1–12, 36- and 60-month, QTD/YTD/FTD/ITD, 12-month standard deviation, contributions).
`platform.accounts.performance` is a jsonb column nothing writes. Two routes:

- **Derive** a daily/monthly TWR from `platform.positions` (a valuation series) net of the external
  cash flows in `platform.transactions` — we hold both, at daily grain, from day one of the corpus.
- **Read** `GET /entityinvestmentperformance/investmentPerformance` per party.

Derive is preferable: it works for every firm every day, and it is the same computation the client
portal's performance chart already assumes exists.

### 8.4 Daily valuation series

`platform.positions` takes `max(dt)` — **only the latest day** — so `platform.valuations` has exactly
one row per account and no series, even though the warehouse holds 74 days of positions. Removing the
window (and keying valuations on `(account, as_of_date)`, which it already is) would produce the
series for free. This is the single highest-value derived dataset here: it feeds performance,
the portal's chart, opening value, and the AUM tiles the oversight pack computes by hand.

**A day SEI did not value is omitted, not coalesced to £0.** `VALUATION_CLEAN_MARKET_VALUE` is blank
on 22 of 125,392 lots; two account-days (G086002, 15–16 Sep 2026, firm 3110) have no valued lot at
all — quantity and cost are present, price and clean market value are not. `sum` of those NULLs is
NULL, and `not_null: market_value` then blocked promotion of every other account. The book column is
NOT NULL, so inventing 0 would show an empty account that still holds the units.

### 8.5 Book cost and realised gains

`platform.valuations.book_cost` is `sum(DEALING_COST)` over positions. SEI states it directly in three
better places: `EODPositions.{TRADED,SETTLED}_BOOK_COST_BASE`, and `FSLTaxData.BOOK_COST` at the
holding grain with `UNREALIZED_GAIN_LOSS_GBP`, `YTD_REALIZED_GAIN_LOSS`, `CARRY_FORWARD_LOSS` and
`ANNUAL_EXEMPTION_AMOUNT`. `FSLTaxData` (4,805 rows, 98.7% populated) is the **only** source of
realised gains in the whole estate.

> `docs/sei.md` records that `FSLTaxData` "has never arrived". That is true of the i31 environment.
> **It is delivered on p04, on every drop.** The stub-table handling stays correct either way, but the
> report is not absent and the model is worth writing.

### 8.5a The wrapper allowance — FIXED, and the limit is not the statutory one

`Wrapper.allowanceUsed` / `allowanceLimit` are rendered by `wrapper-card.svelte` and
`account-stack.svelte` and were fed **only** by `apps/client/src/lib/demo.ts`. SEI had stated the
figures all along, on 1,614 of 2,774 client rows.

**The limit is `used + remaining`, from SEI's own two columns, and not a constant.** That sum is
20,000 for 1,124 clients and 9,000 for 82 — the ISA and JISA allowances — but **26,000 for 28 more
and 22,000 for 20**, because a flexible ISA lets a withdrawal be replaced on top of the annual
limit. Hard-coding 20,000 would have shown those clients over-subscribed.

**It is a `(client, wrapper)` fact, so the read takes `max()` and never `sum()`.** SEI states it per
CLIENT. Used is `ISA_CONTRIBUTION_TO_DATE` and remaining is `TOTAL_REMAINING_ISA_HEADROOM_AMOUNT`.
The stocks-and-shares subscription column is zero on this drop, so it is not the used figure. The
conformed layer carries the pair onto each of that client's accounts of the wrapper because the portal's
card groups by wrapper. 60 of 921 client × wrapper pairs hold more than one account, so summing a
shared £5,000 subscription across two ISAs would report £10,000 used of a £40,000 limit. `max()` is
right precisely because every account of the group carries the same copied number.

Gated on the RESOLVED product, not the wrapper code — so a SIPP, which SEI files as
`ACCOUNT_TYPE` 157, is not handed an ISA's headroom. A GIA and a House Account get nothing, and an
absent limit omits both keys rather than sending zero: `wrapper-card.svelte` draws no bar without a
limit, and a zero limit would draw a **full** one. Headroom is also stored as
`allowance_remaining`, so a reader can show what is left without subtracting, and without
treating a remaining-only figure as a limit.

**The tax year comes from account details, not the file.** `retrieveAccountDetails` returns
`isaJisaContributionDetails` with `taxYear` (`2026-2027`), the client-level year-to-date and
the remaining, for both the ISA and the JISA, on every such account. `raw.api_allowances`
keeps that block and `platform.accounts` uses the side that matches the wrapper. A zero
year-to-date is kept. The statutory `isaClientLevelContributionLimit` is not stored as the
limit: a flexible ISA's used + remaining is 26,000 while that field stays 20,000. When the
API read has not run, the file's two columns are the fallback and the year stays NULL.

**A SIPP has no contribution allowance in either source.** The file's `TOTAL_SIPP_AMOUNT`,
`SIPP_CRYSTALLISED_VALUE` and `SIPP_UNCRYSTALLISED_VALUE` are unpopulated. Account details
returns the ISA/JISA block as null. `GET /crm/sipp-accounts/{accountId}/contributions` is
not deployed (the gateway answers "unable to identify proxy"). The statutory pension
allowance is not written in its place.

**Seccl states the remaining subscription, not a statutory limit.**
`GET /portfolio/summary/{firm}/{clientId}` carries `products[].remainingSubscriptionAmount`
— client-level for an ISA, and often per `accountId` for a JISA. The portfolio list's `id`
is that client id. A product row that carries `clientProductId` is matched to the account's
wrapper on that id, so the remaining still lands when the summary row is not keyed by the
client the account names. It does not publish the
annual limit. `subscriptionAmount` on a transaction is how much that movement changed the
allowance, so used + remaining is the limit, and only when both exist. A remaining figure
with no such movement is stored as `allowance_remaining` alone. The tax year is the current
UK year, because the figure is the live allowance. SEI's year is the account-details
`taxYear`, and stays NULL when that read has not supplied the figures.

### 8.6 Cheap derived facts already sitting in the warehouse

| Derived                    | From                                                                    | Unblocks                             |
| -------------------------- | ----------------------------------------------------------------------- | ------------------------------------ |
| Settlement-fail flag       | `TransactionHeader.SETTLEMENT_DATE` vs `ACTUAL_SETTLEMENT_DATE`         | the detectable half of §7.4          |
| Blocked / pledged quantity | `EODPositions.BLOCKED_QTY`, `ActiveBlocks`, `Accounts.PLEDGE_*`         | the withdrawal gate                  |
| ISA subscription used      | `Clients.ISA_CONTRIBUTION_TO_DATE`, `Accounts.ISA_CONTRIBUTION_TO_DATE` | contribution allowance checks        |
| Asset allocation           | `AssetInvClas` (12 schema levels) + positions                           | the allocation donut                 |
| Fee accrual to date        | `FeePackageUsage` (next computation/collection date) + `FeeComputation` | fee forecasting                      |
| Account-level daily AUM    | `EODPosValAggr`                                                         | oversight §1, at no computation cost |

---

## 9. Does our schema cover the API write paths?

This is the question the document was written for. For each SEI operation that changes something,
where does every field come from — and what do we not hold?

Legend: ✅ held · ⚠️ held but in `data` jsonb / not modelled as a first-class field · ❌ not held.

### 9.1 Create a client + account — `POST /accountservice/accounts`

The enrolment body is **149 leaves, depth 8**, with a `clientList[]` and an `accountList[]`. It is a
journey, not a form, which is why `app/platform_contracts.py` deliberately does not offer it as a
drawer. Of the required fields:

| Required field                                                                 | Source                                                                            | Status                                                                                                                             |
| ------------------------------------------------------------------------------ | --------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `firmId`                                                                       | `platform.tenant_platforms.config.firm_id`                                        | ✅                                                                                                                                 |
| `clientList[].newClient.clientTypeId`                                          | `crm.contacts.type` → `Reference CLIENT_TYPE`                                     | ✅                                                                                                                                 |
| `clientList[].newClient.clientTemplateId`                                      | `ref.enrollment_templates` (API only)                                             | ✅ _(model exists; not surfaced in the UI)_                                                                                        |
| `individualDetails.firstName` / `lastName`                                     | `crm.contacts.first_name` / `surname`                                             | ✅                                                                                                                                 |
| `individualDetails.dateOfBirth`                                                | `crm.contacts.dob`                                                                | ✅                                                                                                                                 |
| `individualDetails.certifiedFATCAComplianceId`                                 | —                                                                                 | ❌ **no column anywhere**                                                                                                          |
| `address.addressLine1` / `addressLine2` / `city` / `postalCode` / `isoCountry` | `crm.contacts.address` jsonb                                                      | ✅ line 2 and postcode when held; country defaults to GB. A GB address with no postcode is refused                                 |
| `phone.phoneMethodId` / `localPhoneNumber`                                     | `crm.contacts.mobile`, else `phone`                                               | ✅ mobile is method 2, a landline is method 1. Subscriber digits only; the address country is the prefix. One number               |
| `email.emailAddress`                                                           | `crm.contacts.email`                                                              | ✅                                                                                                                                 |
| `taxInformation.identifier.taxIdTypeId` / `identifierISOCountry`               | a UK National Insurance number                                                    | ✅ type **7**, country **GB**. ISA and pension always; a GIA when nationality or tax residency includes the UK                     |
| `taxInformation.identifier.taxIdNumber`                                        | `national_identifier` when that type is a NINO, else `data.uk_national_insurance` | ✅ the fact-find does not ask twice when the nationality identifier is already a NINO                                              |
| `teamServicingRelationshipList[].servicingRelationshipId` + `teamId`           | tenant `dsr_ids`, `custody_dsrs`, and `model_dsrs` for the account's model        | ✅ the teams the firm named; a team the book has never granted is left off                                                         |
| `employeeServicingRelationshipList[].employeeEntityId`                         | the book's employee grant; the adviser's Team-page choice only if they hold it    | ✅ the Team page lists those employee ids and SEI's names. An id that has never held the relationship is not sent — SEI refuses it |
| `accountList[].accountTypeId`                                                  | `platform.products.product_type` → `Reference ACCT_TYPE`                          | ✅                                                                                                                                 |
| `accountList[].accountSubTypeId`                                               | `Reference ACCOUNT_SUB_TYPE`                                                      | ⚠️ no column; would default per product                                                                                            |
| `accountList[].accountClassificationId`                                        | `Reference ACCOUNT_CLASSIFICATION` (firm-scoped, 54)                              | ❌ **this is what makes a SIPP a SIPP at SEI and we hold no field for it**                                                         |
| `accountList[].firmInvestmentCapacityId`                                       | `platform.accounts.investment_capacity`                                           | ✅ `3` Discretionary / `4` Advisory / `5` Execution Only. Account-details `firmCapacity` is a different field                      |
| `accountList[].accountTemplateId`                                              | `platform.product_platforms.account_open_reference`                               | ✅ set on the product in admin. The pipeline writes `external_id`, a different value                                               |
| `accountList[].accountName`                                                    | `platform.accounts.name`                                                          | ✅                                                                                                                                 |
| `accountList[].accountBaseCurrency`                                            | `platform.accounts.currency`                                                      | ✅                                                                                                                                 |
| `accountList[].portfolios[].principalPortfolio.portfolioName`                  | —                                                                                 | ❌ **we have no portfolio concept** (§10.3)                                                                                        |

**Verdict: the client half of enrolment is a `crm.contacts` mapping; the account half is not.**
FATCA is a column (0134). Classification is derivable per firm from `type` + `is_pension` (0134),
not a contact field. What still cannot be filled from the contact: `mailingLabelName` (derive from
title+name — BPM-required, no column), `countryOfBirth` as ISO (`country_of_birth` is a display
name), `suffixId` / `formalSalutation`, the adviser→SEI `employeeEntityId`, and a portfolio name
(invented at open). DSR grants and `accountTemplateId` are firm/product, not person.

**The phone mismatch is smaller now**: the mobile maps to `PHONE_METHOD` 2. Enrolment takes that
one number, so `crm.contacts.phone` is not sent. Neither side splits an area code.

> **Live probe, 22 Sep 2026.** Enrolment is no longer unreachable on i31. `POST /accountservice/accounts`
> returns a BPM handle (`ENR-…`) immediately. `client_templates` and `account_templates` both
> answer 200. **Select (firm 3106)** is the firm to open against, not Blackfinch 3102: client
> template 2981271 _Select Platform - Client (Individual)_ and GIA templates including 2981459
> _Select - GIA - No Fees_ (`accountType` 157). Blackfinch still lists: two client rows
> (including 2981599 _for API_) and 2981050 _AIM ISA_ / 2981051 _AIM IHT_ / 2981600 _ISA (for API)_.
> Two of those are ISAs, which is why the id is per shelf product rather than per wrapper. The
> account-template path is all-lowercase `/enrollmentaccounttemplates/…`; Swagger's camelCase
> `enrollmentAccountTemplates` 404s `Unable to identify proxy for host: sei-default` (same class
> of bug as `getAccountDetailsinfo`). `usedBy` is mandatory (code 2196). `mailingLabelName` is
> required by the BPM and belongs on `individualDetails` (a `newClient`-level copy is ignored,
> then code 2436).
>
> **3106 is not 3102.** `residingOrgId` 3106 is code 2048 ("invalid"); 704254 (Select Platform -
> Onshore) on the _client_ is 2345 ("invalid for this external party"). Clients in the drop have
> `RESIDING_SUBFIRM_ID` NULL; accounts sit at 704254. Split them: client `residingOrgId` = firm
> 3106, account = subfirm 704254. Classification is 48527 Individual (3102's is 48518). DSR tags
> are a different set — no Product Team 59317; client teams 59262 / 59260 / **59282** (SEI Custody,
> required as 2399) / 59258; account teams 59261 / 59259 / 59284 / 59283 / 59257. `getFirmDsrs`
> 500s on i31; ids are on `ClientAccountLink`. A GIA POST with that set minted **MSP001050**
> (`ENR-43007`); status is _Enrollment Inprocess_ with warning 991749 **DV2** ("not complete"),
> and `retrieveAccountDetails` is SUCCESS with `accountStatus` _WIP Incomplete_. The Blackfinch
> GIA **100293** (`ENR-43004`) parked the same way as DV1. STP is still blocked on remaining
> template fields, not on the AppKey, the template listing or the create call.

### 9.2 Update a client — `POST /updateclientbasicsprofileservice/updateClientBasicProfile`

24 documented fields. We hold: `maritalStatus` ✅ (0053), `occupation` ✅ (0116), `employer` ⚠️
(`crm.contacts.data`), `gender` ✅ (0052), `dateOfBirth` ✅, `title` ✅, `vulnerableClientFlag` ✅
(0054), `initialSourceOfFunds` ✅ (0116 `source_of_funds`), `baseCurrency` ✅ (`crm.contacts.currency`),
`politicalExposureIndicator` ⚠️ (deliberately in `data` — see `CLAUDE.md` on why `pep` is not a
column), `dateDeceased` ❌, `clientSinceDate` ❌, `clientRiskRating` ❌, `highRisk` ❌,
`firstCountryPassport.*` ❌, `formalSalutation` / `mailingLabelName` ❌, `clientRestriction` ❌.

**Verdict: mostly covered.** The passport pair is the notable absence, and SEI publishes it back to
us in `Clients` — so the round trip exists and only our column is missing.

### 9.3 Transfers — `transferIn` / `transferOut` / `freereceipt` / `freedelivery`

| Required field                                                                                   | Source                                                                                                  | Status                                                                                                                                  |
| ------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `account.accountNumber`                                                                          | `platform.links` (`provider='sei'`, `link_type='account'`)                                              | ✅                                                                                                                                      |
| `externalAccount.externalAccountNumber` / `Name` / `Type`                                        | `platform.transfers.policy_number` / `ceding_provider`                                                  | ⚠️ we hold a name and a policy number, not an account **type**                                                                          |
| `primarySigningAuthority.primarySigningAuthorityName` + `externalPrimaryTaxInfo`                 | the client's name, and their National Insurance number (`national_identifier` where the type is `nino`) | ✅ for a UK resident. SEI's description is the tax number at the ceding scheme (SSN or tax number); for a UK resident that is the NINO. |
| `assetDetails[].assetIdentifierValue` + `assetIdentificationType` + `issueCountry` + `unitsType` | —                                                                                                       | Not sent. i31 accepted a transfer-in with `assetDetails` omitted. The raise drawer does not collect holdings.                           |
| `deliveryInformation.requestDate` / `requestedSettlementDate`                                    | `platform.transfers.expected_date`                                                                      | ⚠️ one date, two needed                                                                                                                 |
| `deliverTo.name` / `countryRegion`                                                               | `platform.transfers.data`                                                                               | ⚠️                                                                                                                                      |

The ceding counterparty is in the drop. `ref.transfer_providers` is Reference `TRANSFER_AGENT_ID` (the transfer agent, which is the counterparty id a transfer-in names), plus `CUSTODIAN`, `CASH_AGENT` and `TAX_WRAP_PROVIDER`. Those four are global: `firm_id` is null. `platform.transfer_providers` is that list, one row per code (`masterparty_id`), with the kinds in `data`. A code listed under two kinds is one organisation. Origo and ATG are not in the file, so `rails` stays empty. `BROKER` is the trading book and stays in `ref.codes`.

**Verdict: a transfer-in does not need asset lines.** i31 accepted one with `assetDetails`
omitted, full or partial, so the missing child table is not what blocks that call.
`platform.transfers` is still a header — one `total_value`, a crystallised/uncrystallised split,
a ceding provider — modelled on the Morgan Lloyd pension-transfer shape. The raise does not
collect holdings. SEI's free receipt and free delivery remain **per asset line**, which is the
shape the oversight pack's Free Movements sheet also shows (206 open asset transfers against 19
account transfers on 2 Dec 2025). A `platform.transfer_assets` child table is that receipt's
shape, not a requirement of `transferIn`.

Also worth noting: `docs/contributions.md` records the SEI leg as blocked on "the account's SEI
account-number/portfolio-id links". The account-number half is solved — `platform.links` holds it as
soon as the merge runs. **The portfolio id is still genuinely missing** (§10.3).

### 9.4 Payments in (contributions) — `POST /currencyreceiptservices/createCurrencyReceipt`

This is the path we have actually built (`app/integrations/sei/cash_receipts.py`).

| Required field                                                    | Source                                                                                 | Status                                                                                                  |
| ----------------------------------------------------------------- | -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `accountNumber`                                                   | `platform.links`                                                                       | ✅                                                                                                      |
| **`portfolioId`**                                                 | —                                                                                      | ❌ **§10.3**                                                                                            |
| `receiptInstructions.currencyCode`                                | `platform.contributions.currency`                                                      | ✅                                                                                                      |
| `receiptInstructions.receiptMethod`                               | `platform.contributions.paid_by`                                                       | ⚠️ our vocabulary, not `Reference PAY_TO_METHOD`                                                        |
| `receiptInstructions.descriptionTypeCode`                         | `app/integrations/sei/reference.py`                                                    | ✅ — 21121–21126 for the UK RAS pair, read from `Reference`                                             |
| `receiptInstructions.treatmentTypeCode`                           | same                                                                                   | ✅ — 4958–4963                                                                                          |
| `receiptInstructions.receiptAmount`                               | `platform.contributions.amount`                                                        | ✅                                                                                                      |
| `payerInfo.payerId`                                               | —                                                                                      | ❌ **requires a payer profile created first** (`createpayerrecipientforaccount`)                        |
| `payerInfo.originationStatementText` / `destinationStatementText` | —                                                                                      | ❌                                                                                                      |
| `processingScheduleDetails.recurring.*`                           | `platform.contributions.frequency`                                                     | ⚠️ five frequencies, SEI's recurrence grammar is richer (daily/weekly/monthly with day-type resolution) |
| `feesAndCharges[]`                                                | the instruction's `adviser_fee`, fee type from the firm's real-time initial advice fee | ✅ a payment with an adviser fee; omitted when the fee is zero                                          |

**Verdict: a one-off receipt works; a regular one does not.** Two blockers — the portfolio id, and
the payer profile. `RecCashActivities` (55 columns) is precisely the report describing what SEI
already holds for regular receipts, and `platform.cash_schedules` conforms it — **with no OLTP table
to merge into.** Building that table would give the contributions engine the standing-order side it
has never had. `PaidTo` is pay-to (money out), not a payer profile. Payers are not in that file.

### 9.5 Payments out (withdrawals) — `POST /currencydisbursementservices/createcashdisbursement`

The largest body in the set: `MemoDisbursement` and `CurrencyDisbursement`, each with a
`paymentType` union of six rails (ACH, cheque, **BACS**, **CHAPS**, **Faster Payment**, wire).

| Required field                                                                 | Source                                             | Status                                                                          |
| ------------------------------------------------------------------------------ | -------------------------------------------------- | ------------------------------------------------------------------------------- |
| `accountNumber`                                                                | `platform.links`                                   | ✅                                                                              |
| **`portfolioId`**                                                              | —                                                  | ❌ §10.3                                                                        |
| `descriptionTypeCode` / `treatmentTypeCode`                                    | `app/integrations/sei/reference.py`                | ✅ ISA/JISA withdrawal `20317` / `2410`; other wrappers `20023` / `2403`        |
| `currencyCode`                                                                 | `platform.instructions` / account currency         | ✅                                                                              |
| `amountType.grossValue`                                                        | `platform.instructions.amount`                     | ✅ UK gross value                                                               |
| `paymentType`                                                                  | the pay-to profile's rail                          | ✅ BACS method 5, Faster Payment method 10, SWIFT method 7                      |
| `payToDetails.payToProfileId`                                                  | `platform.accounts.bank_accounts` (`provider_ref`) | ✅ enrolment creates the profile from the client's bank; PaidTo copies the rest |
| `processingScheduleDetails` / `processingDateOptions.raiseCashMethodOptions.*` | —                                                  | ❌ we have no concept of "raise cash to fund this"                              |
| `wireInfo.{clientInitiatedFlag,callbackRequiredFlag}`                          | —                                                  | ❌                                                                              |

**Verdict: a one-off withdrawal is instructed** when the account has a SEI pay-to
profile (`provider_ref`). Enrolment creates that profile from the bank details collected for
the client, attached to the party just opened, and stores the profile id on the bank row
the withdrawal reads. `withdrawal.one_off` sends `createCashDisbursement` with `grossValue`
and that profile, on the same rail as the profile: BACS, Faster Payment, or SWIFT. It
does not create another one. A lost answer is committed onto the instruction in its
own transaction before the call, so a rolled-back request still cannot send the money
again: the open-activities list does
not carry our reference, so it cannot prove the first call failed to land. A definite
refusal (an HTTP 4xx) clears that hold. The one-time fee body has no reference field
long enough for the instruction id, so the same hold is what stops a second charge. A pension contribution category is not part of this call.
`PaidTo` is conformed onto `platform.accounts.bank_accounts`: each standing profile is a
`pay_to` entry whose `provider_ref` is `PROFILE_ID`, with the method, sort code, account
number, IBAN, BIC and receiving bank the file carries. An ad hoc profile is not copied.
Correspondence and the intermediate bank stay on the profile model's `data`. Payer profiles
are not in the file, and enrolment does not create one. Saving a bank account later does not
create a profile. CHAPS, ACH, cheque and wire are not sent.

### 9.6 Fees

| Operation                           | Required                                                               | Status                                                                                                                                                                |
| ----------------------------------- | ---------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `feePackageAssignmentAccount`       | `accountId`, `feePackageProfileId`                                     | ⚠️ `platform.fee_packages.external_id` would hold the profile id — **but `fee_packages` is not a registered SEI entity, so no SEI fee package ever reaches the book** |
| `feepackageadmin/periodic` (create) | `packageDescription`, `locationContext`, `currency`, `feePackageOwner` | ⚠️ `platform.fee_packages` holds name/currency; **no owner, no location context**                                                                                     |
| `createfeegroupservice`             | `feeGroupName`, `firmId`                                               | ❌ no fee-group concept                                                                                                                                               |
| `maintainrealtimefeeservice`        | `realTimeFeeTypeId`, `feePkgId`, `feePkgName`                          | ❌ no real-time fee concept (`Reference REAL_TIME_FEE_PACKAGE` has 84 members)                                                                                        |

**Verdict: fees are the least-covered area.** `platform.fee_packages` exists and Seccl populates it;
SEI's three fee reports (808 rules + 7,281 usages + 33 groups) are ingested and modelled by nothing.
The book table's shape (fixed/percentage/tiered `rule`, effective window, `applies_to`, charge basis,
payer, vatable) is a reasonable fit for `FeePackageRule` — the same conforming work Seccl's
`fee_structures` already got.

### 9.7 Models

| Operation                                                             | Required                                                                     | Status                                                                               |
| --------------------------------------------------------------------- | ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `PSM/model/assignment/byAccount`                                      | `firmId`, `accountNumber`, `portfolioId`                                     | ✅ name and type for each distinct `MODEL_ID`. No target return, charge, or holdings |
| `PSM/model/assignment/implementation` \| `allocation` \| `investment` | `firmId`, `accountNumber`, `portfolioName`, `modelId`, `toleranceMultiplier` | ❌ writes. `toleranceMultiplier` has no published source                             |

`platform.models` is discovered from `Accounts.MODEL_ID`. `ModelAlloc` and `ModelsIntExt` are
permanently empty, and `retrievestrategylistservice` is a different object (a strategy, model
type 5) whose gateway rejects both envelopes. `byAccount` is how an unnamed model gets its
name. It does not describe the holdings, so no model version is minted from the drop. Target
holdings are entered in admin (the model's allocation history): an instrument and a percentage
per line, cash as its own figure, totalling 100%.

### 9.8 Coverage summary

| Path                     | Can we call it? | Blocking gap                                                                   |
| ------------------------ | --------------- | ------------------------------------------------------------------------------ |
| Create client + account  | ❌              | FATCA id, account classification, adviser→employee link, portfolio             |
| Update client            | ✅ mostly       | passport pair                                                                  |
| Update account details   | ✅              | —                                                                              |
| Change account status    | ✅              | —                                                                              |
| Transfer in / out        | ❌              | portfolio and counterparty; asset lines are not required on a full transfer-in |
| Cash receipt (one-off)   | ✅              | portfolio id                                                                   |
| Cash receipt (recurring) | ❌              | payer profile, recurrence grammar                                              |
| Cash disbursement        | ❌              | amount basis and statement text; the pay-to profile id is on the account       |
| Fee package assign       | ❌              | no SEI fee package in the book                                                 |
| Model assign             | ❌              | no SEI model in the book, no portfolio                                         |

---

## 10. Defects and gaps found while writing this

> **Status (07 Sep 2026).** 10.2, 10.4's sibling and the two below it are **fixed**; 10.1 is
> half-fixed and 10.3–10.6 are scheduled. A fifth defect, found while resolving these and worse than
> any of them, is recorded as **10.0**.

### 10.0 The product alias was applied on the shelf and not on the account — FIXED

Not an SEI defect at all: a `pkgs/book-sink` one that cost Seccl its entire pension book, found
while answering "are our products mapped correctly?".

`PRODUCT_ALIASES = {"PENSION": "SIPP"}` was applied when DISCOVERING a product (`_PRODUCTS`,
`_PRODUCT_PLATFORMS`, `_MODEL_PRODUCTS`, `_APPLIES_TO`) and nowhere on the account, while
`platform.accounts`' product parent joined `CAST(products.product_type AS text) = s.product_key` —
the provider's raw wrapper string. Seccl calls the wrapper `PENSION`; the book stores `SIPP`. They
never matched. Measured on the dev book:

```
 product_type | with_product | total
--------------+--------------+-------
 GIA          |          193 |   193
 ISA          |           66 |    66
 JISA         |           17 |    17
 TPPENSION    |            5 |     5
 (null)       |            0 |    98   <- every Seccl pension
```

**98 of 379 accounts (26%) carried `product_id = NULL`**, and five surfaces gate on the
`product_type` that resolves through it — so all five were dark for every real SIPP: drawdown
answered _"Drawdown applies to pension accounts only"_, `provider_positions` never rendered the 0117
crystallisation and scheme-cash block, the ML policy-valuation push refused, and the contributions
pension filter and the MPAA allowance gate both missed. It is the **same defect** the comment on
that entity's parents already describes as fixed; it was fixed one join short.

`_ACCOUNTS` now stages `product_type` (the canonical type) beside `product_key` (the provider's own
word, kept because it is what makes a reject legible), and the parent joins the former — which is
what `product_platforms`, its sibling reading the same relation, already did. Pinned at both levels,
because the two halves fail differently: `pkgs/book-sink/tests/test_entities.py` executes the
projection **over rows** and asserts `PENSION → SIPP`, and `apps/api/tests/test_book_sink.py` merges
a staged pension account against a real Postgres and asserts it reaches the `SIPP` product. Both
were confirmed to fail before the change.

### 10.1 SEI projects no `positions` into a tenant's lake — FIXED

> **Half fixed (07 Sep 2026).** The _reader_ is no longer single-custodian: `_visible_seccl_accounts`
> is `_visible_accounts`, filters `pl.provider = ANY(providers.custodians())`, carries the provider
> on every row, and `lake_holdings.by_firm` groups on `(provider, firm)` — so `_firm_read` calls the
> right catalogue instead of the literal `"seccl"`. The withdrawal gate and the ML valuation push
> took the same resolution. A tripwire in `tests/test_lake_holdings.py` AST-scans the three modules
> for a custodian named in a string constant, so this cannot come back quietly.
>
> **The second half is done too.** SEI's `platform.positions` published six of the fifteen columns
> `_HOLDINGS_SQL` reads, so setting `LAKE_ONLY["sei"] = ("positions",)` on its own would have turned
> an empty page into a Binder Error — an "unavailable" one. The model is conformed to all fifteen
> (in the ACCOUNT's base currency, §8.1) and `positions` is now registered, so a SEI firm's holdings
> table, allocation donut, cash figure and value chart read its own slice.

`book_sink.entities.LAKE_ONLY['sei'] = ()` and no registered SEI entity reads `positions`:

```
seccl        -> accounts, clients, fee_packages, instruments, models, orgs, positions,
                third_party_providers, transactions, transfer_providers, valuations
sei          -> accounts, clients, instruments, orgs, transactions, valuations
morgan_lloyd -> beneficiaries, clients, transfers, valuations
```

So a firm whose only custodian is SEI has **no `tenant_{uid}.positions`**. Every holdings surface
reads exactly that relation (`app/lake/holdings.py`, `crm._lake_metrics`), so the client portal shows
an empty holdings table, an empty allocation donut and no cash figure beside a real OLTP `value` —
the failure `app/lake/fixture.py` was written to prevent for Seccl, unprevented here because the
fixture is a Seccl extract. And `app/instructions.py`'s withdrawal gate **refuses**, correctly but
permanently. Fix: `LAKE_ONLY["sei"] = ("positions",)`.

### 10.2 `platform.accounts.model_key` reads a column SEI never populates — FIXED

`services/sei/models/platform/accounts.sql:159` maps `STRATEGY_ID → model_key`. Measured across the
corpus:

| Drop       | `STRATEGY_ID` |           `MODEL_ID` | `IMPLEMENTATION_MODEL_NAME` |
| ---------- | ------------: | -------------------: | --------------------------: |
| 2025-10-29 |     0 / 3,444 |                2,971 |                         270 |
| 2026-01-01 |    0 / 10,377 |                8,839 |                         328 |
| 2026-02-05 |    0 / 10,466 |                8,897 |                         379 |
| 2026-03-27 |    0 / 14,462 | 12,346 (49 distinct) |           871 (23 distinct) |

`STRATEGY_ID` and `STRATEGY_NAME` are **0% for five months**. So `model_key` is always NULL, the
`link:model` parent never resolves, and `platform.accounts.model_id` is NULL for every SEI account —
which reads as "this firm runs no discretionary models" and makes `GET /crm/accounts`' `discretionary`
flag (which is `carries a model_id`) false for the whole book. This is the same class of failure as
the `cam_schemerefs`/`cam_schemes` mix-up: the right kind of identifier from the wrong column, silent
because an empty column and an absent fact look identical. `MODEL_ID` is the column
(`PortfolioGroups.MODEL_ID` corroborates at 91%), and the model catalogue still has to come from the
API before the link can resolve.

### 10.3 There is no portfolio concept in the book, and four API paths need one — FIXED

> **Fixed (07 Sep 2026).** `0132_platform_portfolios` is the child table `docs/api-integrations.md`
> §1187 already prescribed, with a conformed `services/sei/models/platform/portfolios.sql` and a
> `book_sink` entity registered for SEI alone. The account parent is REQUIRED, so a portfolio whose
> account did not materialise is a counted reject rather than a row nothing can address.
>
> Two things the data settled. The Accounts report is **not** one row per portfolio — 14,462 rows
> for 1,163 distinct (account, portfolio) pairs, with 362 of those pairs appearing exactly seven
> times — so the model deduplicates and `unique: external_id` is what makes a regression a build
> failure. And a `PORTFOLIO_ID` is globally unique and belongs to exactly one account (zero of the
> 1,163 appear under two), which is why the identity is the provider's own id and not a composite.
>
> `LINKED_PORTFOLIO_ID` is stored as the **vendor's** id rather than a book FK: the merge inserts a
> provider's whole slice in one statement, so the Income row cannot see the Capital row's
> freshly-minted id, and a self-referencing bigint would have been permanently NULL.
>
> It is now prefilled into every SEI drawer that takes a `portfolioId` (§10.8).

`portfolioId` is required by `createCurrencyReceipt`, `createcashdisbursement`,
`currencytransfercreate` and every `PSM/model/assignment/*`. SEI's account grain is
**account × portfolio** — `Accounts` is 14,462 rows for 1,104 accounts, and `PORTFOLIO_TYPE` splits
Capital (189) from Income (190). `platform.accounts` collapses this by a deterministic pick and keeps
`portfolio_id` in the conformed relation, **but the book table has no column for it**, so nothing that
resolves an account through `platform.links` can name a portfolio. `docs/sei.md` §5 already flags that
portfolio detail "wants its own `platform.portfolios` model"; this is the consequence, and it blocks
every money-movement path.

### 10.4 `platform.valuations.cash_value` is NULL for SEI and need not be — FIXED

§8.1. Two sources already in the warehouse.

### 10.5 The crystallisation comment is now historically, not permanently, true

§8.2. The columns arrived on 2026-03-27.

### 10.6 `FSLTaxData` is delivered on p04

`docs/sei.md` §2 records it as never delivered (true of i31). On p04 it arrives on every drop, 4,805
rows, 98.7% populated — and it is the only realised-gains source in the estate (§8.5).

### 10.7 The 934/830 pension figures in `CLAUDE.md` are row-grained

§0. The account-grain figures are 64 pension / 52 SIPP. The mapping decision they justify is
unchanged and correct.

---

### 10.8 Every Seccl and SEI drawer opened blank — FIXED

`platform_actions.defaults_for` returned `{}` for anything but Morgan Lloyd, so no Seccl or SEI form
prefilled anything — including `create_cash_recipient`, the one drawer in the estate that can reach a
sort code, marked _"A pay-to recipient is where money can be sent."_ An operator typed the
destination bank account by hand, from nothing.

Three pieces, in precedence order (least specific first):

| Source                             | Fills                                                                                     |
| ---------------------------------- | ----------------------------------------------------------------------------------------- |
| `platform.tenant_platforms.config` | `firmId` — required by **17 of SEI's 26** authorised services — and `descriptionTypeCode` |
| `platform.providers.config`        | Morgan Lloyd's connection values, as before                                               |
| the ACCOUNT                        | `accountNumber`, its principal `portfolioId`, `currencyCode`, and the BACS trio           |

**Matched on the field LEAF, not the path.** SEI documents its fields as dotted paths into its
JSON:API envelope, and eleven services name `accountNumber` with no two agreeing on the path —
`createCashDisbursement.MemoDisbursement.accountNumber`, `data.attributes.searchCriteria.accountNumber`,
and a bare `accountNumber` are all the same fact. The exact name is tried first (Morgan Lloyd's flat
`provider` would otherwise collide with a leaf), then the leaf; and a leaf appearing twice in one
action fills both, because `createCashDisbursement` carries an account number under two envelopes
and they name the same account.

**`treatmentTypeCode` is deliberately NOT prefilled.** `contribution_treatment_codes` is a map keyed
by contribution category, because the treatment code is what encodes personal-vs-employer at SEI. A
generic drawer does not know the category, and picking one would put an employer contribution's code
on a personal one — so the box stays empty and SEI's own 422 names the code, which
`app/integrations/sei/reference.py` can then suggest.

An account that is not the firm's is **ignored rather than refused**: the contract is still worth
returning, and prefilling from another firm's account would be the actual harm.

### 10.9 `UserDetail` is two thirds not-staff, and nothing said so — FIXED

The users work turned up a fact the report's name actively hides. `UserDetail` is 1,591 rows, and
`USER_TYPE` is a `Reference` code set with exactly three members:

| code | meaning          | rows | synced                       |
| ---- | ---------------- | ---- | ---------------------------- |
| 1    | Employee         | 706  | **yes**                      |
| 2    | End Client       | 865  | no — these are INVESTORS     |
| 3    | Data Integration | 20   | no — SFTP/API machine logins |

Taking the report at its name would have put 865 investors into every firm's **staff** list, and
worse: an End Client's email plausibly matches the login that client already holds for the client
portal, so the attach rule in `auth.merge_provider_user` would have bound a custodian's
staff-shaped id onto a client's identity and the adviser picker would have offered a firm its own
clients as advisers.

The scope is corroborated rather than asserted. Of the 233 distinct `ACCOUNT_DSR_EMP_ID` values in
`ClientAccountLink` — the ids an account's servicing relationship actually names — the 37 that
appear in `UserDetail` at all are **37 of 37 Employee**. The type published is exactly the type an
account names.

Two more measurements worth keeping:

- **Departed staff dominate.** 470 of the 706 employees are already `USER_STATUS = 'N'`. They are
  still published, because a departed adviser is the name on every account they advised; dropping
  them would leave that history attributed to nobody.
- **Employees are concentrated.** 3102 has 546, 3106 has 130, and the other four firms have 30
  between them. So a firm enabled on 3102 gets 546 rows in its members list — which is why the
  list orders a firm's own people first and tags the rest rather than sorting by email alone.

Per firm: 3102 546/528/4, 3106 130/295/3, 3108 11/42/3, 3110 10/0/3, 3112 0/0/4, 3114 9/0/3
(Employee / End Client / Data Integration).

## 11. What to do next, in order

**Done (07 Sep 2026)** — items 1–3, 5 and part of 6 of the original list, plus one defect that was
not on it and mattered more than any of them:

|              |                                                                                                                     |
| ------------ | ------------------------------------------------------------------------------------------------------------------- |
| §10.0        | the product alias — 98 of 379 accounts had a NULL `product_id`, i.e. every Seccl pension                            |
| §10.1        | SEI's `positions` conformed to all fifteen columns the lake reader needs, and projected                             |
| §10.2        | `model_key` reads `MODEL_ID`, not the always-empty `STRATEGY_ID`                                                    |
| §10.3        | `platform.portfolios` (0119) — the `portfolioId` four SEI write paths require                                       |
| §10.4 / §8.1 | `cash_value`, and money moved to the ACCOUNT's base currency                                                        |
| §8.1         | the valuation SERIES — `platform.valuations` was structurally one point per account                                 |
| §10.8        | every Seccl and SEI drawer prefills from data instead of opening blank                                              |
| —            | the display path is no longer hardcoded to Seccl in eight places                                                    |
| —            | the staff profile + `provider_refs` on `auth.users` (0118), bank details (0120), FATCA (0121), product roles (0122) |
| §10.9        | the SEI staff sync (0123) — `platform.users` + `auth.merge_provider_user`, Employee only, attach-by-email           |

**Still open, in order:**

1. ~~**The adviser↔custodian mapping.**~~ **DONE** — both halves.

   _Automatic:_ the SEI pipeline syncs the firm's employee users into `auth.users`
   (`services/sei/models/platform/users.sql` → `auth.merge_provider_user`, 0123). An exact
   email match ATTACHES the SEI id to the login that already holds it; anything else arrives as
   a disabled placeholder with an `inactive` platform membership — readable by the firm, granting
   nothing. Both portals tag a person with the custodians that know them.

   _Manual:_ the members **Edit drawer** (platform `/team`) carries a picker per enabled custodian,
   for the people the email match declines to guess at — a firm's SEI address and its Graphene
   login address are frequently different. `provisioning.set_provider_ref` is one implementation
   behind two routes (`PUT /users/{user_id}/provider-ref/{provider}` for the firm,
   `/admin/tenants/…` for operations), because what a mapping IS must not differ by who set it.

   **The picker reads the firm's own lake slice, not a live vendor call, which reverses what 0118
   planned** (`app/lake/staff.py`): the pipeline already fetches `firmemployeeusers` nightly, the
   lake copy is the CONFORMED one — scoped to the firm and filtered to employees, where the API
   response carries no `userType` at all and would offer a firm its own clients as advisers — and
   SEI allowlists its API by source IP, so a live call works from the pipeline's host and not
   necessarily from the API's. `pkgs/sei`'s own `ReferenceApi.firm_employee_users` therefore has no
   in-app caller and stays as part of that client's faithful vendor surface.

   **The API answers this better than the SFTP does, which was not the assumption.**
   `GET /employeeservice/firmemployeeusers` returns `uniqueUserId`, **`emailId`**, a structured
   name, `phone`, `loginUserName` and `crdNumber` — paged, scoped by `processingOrgId` (the
   **processing organisation** from `firm-default-attr-cache`, not the firm id: 3102 → 3100)
   plus `serviceId` (a product-service filter; `1` is the widest set on i31, a DSR tag 400s).
   The nightly `UserDetail` report
   has twelve columns, no address, and one `USER_DISPLAY_NAME` holding `"Armasoiu, Maria"`.

   So the floor on MATCHING rises, without the rule changing. "Never inferred" was written against
   display NAMES and it stands — only 37 of the 233 distinct `ACCOUNT_DSR_EMP_ID` values in
   `ClientAccountLink` appear in `UserDetail` at all, so a name match would be wrong 84% of the
   time. An email is an identity rather than a label, so an exact match on one, and exactly one
   (the bar `aml.match_contact` already sets for a person), is a defensible **proposal** for an
   operator to accept. A miss stays ordinary: a firm's SEI address and its Graphene login address
   are often different, so "no proposal" is the common case and never an error.

2. ~~**SEI's model catalogue.**~~ **DONE — and it needed no API call.** The plan said
   `retrievestrategylistservice`; the catalogue is instead DISCOVERED from the accounts that name
   a model (`services/sei/models/platform/models.sql`), exactly as `platform.products` is
   discovered from the same report. `ModelsIntExt` (16 columns) and `ModelAlloc` (22) really are
   0 rows on all 74 drops, but `Accounts.MODEL_ID` is **85% populated with 49 distinct values**,
   and a model no account names is a model no surface asks about.

   Measured while doing it, and it partly corrects §10.2: `IMPLEMENTATION_MODEL_NAME` — which
   §10.2 moved `model_name` onto — is populated on only **6% of rows (23 of the 49 models)**, and
   the other 94% carry an EMPTY STRING rather than a NULL. It is still the right column
   (`STRATEGY_NAME` is 0%), but **26 of the 49 models have no name anywhere**: `PortfolioGroups`
   carries `MODEL_ID` for 37 of them and its name columns are the GROUP/PORTFOLIO/ACCOUNT names
   (a person's name), and the ids are not a `Reference` code set (zero overlap). Those land with
   their id as the name — the sink's projection already coalesces for exactly this — so a firm can
   rename one and the nightly run never overwrites it.

   Registering `models` registers FOUR entities (the model, `model_platforms`,
   `model_allocations`, `model_commentary`), so the conformed model publishes every column their
   projections ask of a provider as TYPED nulls, with `allocations` an empty array: each SEI model
   is counted under `no allocations published` and mints no version, which is right — a version is
   a decision about a set of holdings and SEI has published none.

3. **The performance series.** `platform.valuations` now carries the history, so a TWR can be
   derived from it net of the external flows in `platform.transactions`. Today's chart is a VALUE
   chart and `platform.accounts.performance` is a jsonb column nothing writes.
4. **The ISA allowance read** — the columns exist (0120) and `wrapper-card.svelte` renders the bars;
   what is left is publishing the figure from `ISA_CONTRIBUTION_TO_DATE` / Seccl's `clientProduct`
   and taking **`max()` per (contact, wrapper), never `sum()`**.
5. **Pickers for Seccl and SEI** (`FIELD_LOOKUPS` has a Morgan Lloyd key and nothing else) and
   **Seccl's nested blocks** (`bankDetails` and `addressDetail` are `$ref`s, so they land in
   `Contract.nested` and never become fields).
6. **Request the five external feeds** in §7.7 from SEI.
7. **`platform.transfer_assets`** — SEI's free receipt and delivery are per ASSET LINE where
   `platform.transfers` is a header, which is why the oversight pack shows 206 open asset transfers
   against 19 account transfers.
