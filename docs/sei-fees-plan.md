# SEI fees — what we need to build

Status: mostly delivered (Oct 2026). Covers (A) attaching ongoing fees to clients/accounts, (B)
charging ad-hoc fees, and (C) a fee riding a payment/withdrawal. Companion to `docs/sei-data-sources.md`
(§9.6 Fees) and `docs/sei.md`. The money actions themselves run on baton (`docs/baton.md`, the
`transaction.*` namespace); this is about the fee data and the SEI calls behind `transaction.fee`
and `fee.attach`.

## Delivered

- **Periodic fee-package read-sync** — `services/sei/models/platform/fee_packages.sql` + book-sink
  wiring; the firm's ongoing packages land in `platform.fee_packages` keyed on `feePackageProfileId`.
- **Ad-hoc fees (B)** — firms configure one-off fee types (`frequency='initial'`, admin **provider
  fee code** = the SEI `FEE_TYPE` code); the money drawer shows a required fee-type picker on SEI
  accounts; `sei_adhoc_fee` charges the typed amount against that `feeType`.
- **`fee.attach` for SEI (A)** — `sei.attach_fee` assigns the account's ongoing package via
  `assign_package_to_account`, reading SEI's numeric `accountId` from `retrieveAccountDetails`
  (`account_details`) and skipping an already-assigned package.
- **Fee on a payment (C)** — already wired (adviser fee override on the receipt).

**Verify against the SEI sandbox** (`docs/vendor-integration-tests.md`) before production: the
`fee.attach` calls (`retrieveAccountDetails` → `accountId`, `assign_package_to_account`) were built
from the spec, not exercised live (the dev box can't reach SEI — IP allowlist).

**Still open:** fee riding a **withdrawal** (the disbursement's `feesAndChargesDetails` /
`agentTransactionChargeMethodType` are unpopulated — §6); SEI real-time/dealing fees (a separate
axis); and the operational populate-run (restart the SEI dev service so the periodic packages land).

## TL;DR

SEI fees are **three separate things**, and we only half-support one of them:

| SEI concept                                | SEI id                                                       | SEI call                                  | portals today                                        |
| ------------------------------------------ | ------------------------------------------------------------ | ----------------------------------------- | ---------------------------------------------------- |
| **One-time ad-hoc charge**                 | `feeType` = a `FEE_TYPE` **code**                            | `initateOneTimeFeesAdhoc` ✅ wrapped      | leaf wired; **no fee-type data, picker stubbed off** |
| **Periodic fee package** (ongoing/adviser) | `feePackageProfileId`                                        | `feePackageAssignmentAccount` ✅ wrapped  | **not wired for SEI** (fee.attach is Seccl-only)     |
| **Real-time** (dealing) fee                | `realTimeFeeTypeId` / `feePkgId`                             | `maintainrealtimefee` ✅ wrapped          | not wired                                            |
| **Fee riding a receipt** (payment in)      | `feeDescriptionId` / `feeTypeId`+`feeRuleId`                 | `createCurrencyReceipt.feesAndCharges` ✅ | ✅ adviser fee wired                                 |
| **Fee riding a disbursement** (withdrawal) | `feesAndChargesDetails` + `agentTransactionChargeMethodType` | `createcashdisbursement` ✅ wrapped       | **fee fields not populated**                         |

The single biggest fact: **SEI never charges a bare amount.** Every fee names a type/profile id from
the firm's SEI catalogue. That catalogue arrives nightly over SFTP (`FeePackageRule`,
`FeePackageUsage`, `FeeGroup`, `FeeComputation` + the `Reference` code sets) and lands in `raw.*` —
and is **consumed by nothing**. There is no `services/sei/models/platform/fee_packages.sql`. That gap
is the root cause of every fee error we're seeing. **No schema rework is needed** — the work is a SEI
fee-catalogue sync + wiring the SEI calls + a UI picker.

---

## 1. How SEI actually does fees

Two independent axes, plus one-offs, plus overrides on a money movement.

### Periodic fee packages (the ongoing/adviser fee — the Seccl `adviserFeeId` equivalent)

- A **fee rule** (rate / basis / VAT / tier bands) belongs to a **fee package**, identified by a
  **`feePackageProfileId`**. Packages and rules are created in SEI (`feePackageAdmin` / `feeRuleAdmin`
  — we would not author these; the firm's SEI book owns them).
- You **attach** a package to an account by assigning its `feePackageProfileId`:
  - simple: `POST /feepackageassignmentaccount/feePackageAssignmentAccount` → `assign_package_to_account(accountId, feePackageProfileId, collectionPortfolioId, copyPackageFlag)` — **wrapped** (`pkgs/sei/src/sei/endpoints/fees.py:79`).
  - full control (schedule/frequency/collection method/min-max/advance-arrears): `POST /periodicaccountfeepackageassignment/...` — **not wrapped**.
  - stop it: `finalFeeDateAPI` (**wrapped**; there is no "unassign").
  - household tiering: fee **groups** (create / add account / remove) — all **wrapped**.
- **Read** the account's current package: `account_characteristics` (**wrapped**, returns
  `feePackageProfileId`), `retrievefeepackagedetails` (**wrapped**), `periodicfeepackageruledetails`
  (**wrapped**, keyed by `feePackageProfileId` → rules + tier bands).
- There is **no fee field on account enrolment** — enrol first, then attach.

### Real-time (transactional/dealing) fees

A second axis keyed on `realTimeFeeTypeId` / `feePkgId`, attached via `maintainrealtimefee`
(**wrapped**) or `realtimepackageaccountassociation` (not wrapped). Out of scope for the first cut.

### One-time ad-hoc charge

`POST /initiateonetimefeesadhocservice/initateOneTimeFeesAdhoc` (**wrapped**, `create_adhoc_fee`).
Mandatory: `firmId`, **`feeType`** (a `FEE_TYPE` code), `feeAmount`, `collectionMethod` (direct
charge only), `currencyCode`, + an account selector. `descriptionTypeCode`/`treatmentTypeCode` are
N/A here. This is a different id from the periodic path — a **fee TYPE code**, not a profile id.

### Fee riding a money movement

- **Receipt (payment in)** `createCurrencyReceipt` carries `receiptInstructions.feesAndCharges[]`
  (`feeDescriptionId`, `overridenFeeAmount`) and a richer `feesAndChargesDetails[]`
  (`feeTypeId`, `feeRuleId`, `overrideFeeAmount`, `feePercentage`, `vatFeeRuleId`). **We already use
  `feesAndCharges`** for the adviser fee ([cash_receipts.py:337-352](../apps/api/app/integrations/sei/cash_receipts.py#L337)).
- **Disbursement (withdrawal out)** `createcashdisbursement` carries the same
  `feesAndChargesDetails[]` **plus `agentTransactionChargeMethodType`** (the adviser/agent charge on
  the way out). **Wrapped, but we don't populate the fee fields.**

### The reference data (SFTP → lake)

SEI ships 4 nightly fee reports + the code dimension; all land in `raw.*`, **modelled by nothing**:

| SFTP report       | raw table                                   | rows         | what it is                                                                                                                                    |
| ----------------- | ------------------------------------------- | ------------ | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `FeePackageRule`  | `raw.fee_package_rule`                      | 808          | the tariff catalogue: profile, rate, basis, VAT, tier bands, `FEE_TYPE_DESCRIPTION`, `FEE_PACKAGE_PROFILE_ID`, `RESIDING_ORGANIZATION` (firm) |
| `FeePackageUsage` | `raw.fee_package_usage`                     | 7,281        | per-account assignment: account + `FEE_PACKAGE_PROFILE_ID` + schedule/discounts                                                               |
| `FeeGroup`        | `raw.fee_group`                             | 33           | household groups                                                                                                                              |
| `FeeComputation`  | `raw.fee_computation`                       | fee-run days | the computed fee (output, not reference)                                                                                                      |
| `Reference`       | `raw.reference` → `ref.codes` / `ref.firms` | —            | `FEE_TYPE` (181), `REAL_TIME_FEE_PACKAGE` (84); `ref.firms.firm_name ↔ RESIDING_ORGANIZATION`                                                 |

Also: `raw.accounts.FEE_PACKAGE_ID` (99.9% filled) and `raw.portfolio_groups` carry a package ref.
The SEI data fully supports reading an account's current fees — we just don't model it.

---

## 2. Current portals state

- **`platform.fee_packages`** (tenant book table; 0002 + 0042): the right shape (fixed/percentage/
  tiered `rule`, `charge_basis`, `payer`, `vatable`, `frequency`, `fee_type`, `external_id`, `data`).
  **Populated by the Seccl sync only** (`external_id` = Seccl fee-structure id); no SEI sync.
- **`platform.fee_package_platforms(external_id, external_name, platform_id, fee_package_id)`** (0042):
  a per-platform external-id table that already exists — relevant to the id decision below.
- **`platform.account_fee_packages`** (0110): the account↔package attachment (the UI checkbox list in
  `account-detail.svelte`). `platform.accounts.fee_package_id` is deprecated.
- **`instructions.fee_package_id`** (0172): the ad-hoc fee's type reference; validated against the
  firm's packages in `app/instructions.py::create_instruction`.
- **Ad-hoc fee UX**: amount-only. The picker was built (`feeTypeItems`,
  `pkgs/ui/.../instruction.ts:302`) then **deliberately stubbed** — the drawer hard-codes
  `fee_package_id: null` (`instruction-sheet.svelte:377`) and a test locks that in.
- **SEI ad-hoc backend**: fully wired end-to-end — `sei_adhoc_fee` → `_one_time_fee_type` (reads the
  instruction's `fee_package_id` → `platform.fee_packages.name/external_id`) → `one_time_fee_id`
  (matches `external_id` against `ref.codes` `FEE_TYPE` **code**) → `create_adhoc_fee`. It only fails
  because there's nothing in `fee_packages` to resolve, so it falls back to "the firm's single
  one-time initial-advice fee" and refuses when there are 0 or several.
- **fee.attach**: Seccl only (`seccl_attach_fee` → `adviserFeeId`). No SEI leaf.
- **Fee on a payment**: wired (adviser fee override on the receipt). **Fee on a withdrawal**: not.

---

## 3. The one real decision — what `external_id` holds for SEI

SEI has **two ids** and one `external_id` column, which is the inconsistency flagged in
`pkgs/sei/.../fees.py` (says `external_id` "should hold" `feePackageProfileId`) vs the live
`one_time_fee_id` (reads `external_id` as a `FEE_TYPE` **code**). They're for different operations:

- **one-time ad-hoc** needs a `FEE_TYPE` code;
- **periodic attach** needs a `feePackageProfileId`.

**Recommendation:** treat them as **different `fee_packages` rows** distinguished by `frequency`
(`one_time` vs `ongoing`), each row's `external_id` holding the SEI id that _that_ row's operation
needs (a `FEE_TYPE` code for one-time rows, a `feePackageProfileId` for periodic rows). `external_id`
then means "the SEI id for this fee package", consistent per row. This keeps the live ad-hoc path and
the `/catalog/fee-packages` picker working unchanged. (Alternative: put the provider id in
`fee_package_platforms` with `platform_id = SEI`, and keep `external_id` null on the book row — more
correct for a multi-provider package, more plumbing. Pick one before building the sync.)

---

## 4. Work item A — attaching ongoing fees to clients/accounts

Goal: a SEI firm can assign/see an account's ongoing (adviser/periodic) fee package, the way Seccl
does via `adviserFeeId`.

1. **Sync periodic packages into `platform.fee_packages`.** New
   `services/sei/models/platform/fee_packages.sql`, mirroring
   `services/seccl/models/platform/fee_packages.sql`:
   - `depends_on: raw.fee_package_rule` (+ `raw.fee_package_usage` for assignment), `key: external_id`.
   - `external_id = FEE_PACKAGE_PROFILE_ID`, `frequency = 'ongoing'`, `firm_key` from
     `RESIDING_ORGANIZATION → ref.firms.firm_id`, `name = FEE_TYPE_DESCRIPTION`/package description.
   - Derive `rule` (fixed/percentage/tiered), `charge_basis`, `vatable`, tier bands from the rule
     columns (SEI already publishes contiguous bands "next band at the next penny", like Seccl).
   - Land into `platform.fee_packages` for the tenant (how the Seccl sync writes the book — follow
     that path; `materialise.py` was removed, so find the current book-sink writer).
2. **A SEI `fee.attach` leaf.** Today `fee.attach` dispatches to Seccl only. Add a SEI branch that
   calls `FeesApi.assign_package_to_account(accountId, feePackageProfileId, collectionPortfolioId)`
   using the account's SEI numeric id + the chosen package's `external_id`. End a fee with
   `set_final_fee_date`. (Secondary: real-time dealing fees via `maintain_realtime_fees`.)
   - **Bug to fix:** `assign_package_to_account` sends `copyPackageFlag = 1 if copy else 0`
     (`fees.py:103`); the spec allows `1`/`2`/blank (default `2`) — `0` is undocumented. Send `2`.
3. **Read-back / reconciliation.** Use `account_characteristics` / `retrievefeepackagedetails` to show
   the account's current SEI package and flag drift from what we assigned (join key is
   `feePackageProfileId`; `raw.fee_package_usage` is the nightly truth).
4. **UI.** The account fee-package assignment UI (`account-detail.svelte` `onSaveFees`,
   `/catalog/fee-packages`) already exists for Seccl — once SEI rows are in `fee_packages`, extend it
   to SEI accounts (same list, same `account_fee_packages` table, SEI leaf on save).

## 5. Work item B — charging ad-hoc fees

Goal: raise a one-off fee on a SEI account by picking the fee type.

1. **Sync one-time fee types.** Model the firm's `FEE_TYPE` codes with `qualifier_1 = 'One Time'`
   (scoped to the firm via `fee_package_rule`, as `one_time_fee_id` already queries) into
   `platform.fee_packages` as `frequency = 'one_time'` rows, `external_id = FEE_TYPE code`,
   `name = description`. (This is the same sync job as A, a second row kind — or a dedicated
   fee-type catalogue if we don't want them in `fee_packages`; see §3.)
2. **Re-enable the picker.** In `instruction-sheet.svelte` for `kind === 'adhoc_fee'`: render
   `feeTypeItems(packages)` (already built), stop sending `fee_package_id: null` (`:377`), pass the
   chosen `fee_package_id`. **Make it required when the account's custodian is SEI** (optional for
   Seccl, which is amount-only). Update the guard test `instruction-sheet.test.ts:144-150`.
3. **Backend is already done** — `sei_adhoc_fee` consumes `fee_package_id`. Once (1) and (2) land it
   works. Soften the "single initial advice fee" auto-default (it's a guess that only ever worked for
   a firm with exactly one one-time fee) — prefer: no package chosen on a SEI account ⇒ ask, don't guess.

## 6. Work item C — a fee as part of a payment/withdrawal

SEI supports charging the fee on the money movement itself, as an alternative to a separate one-time
charge.

- **On a payment (receipt):** already wired for the adviser fee (`FeeOverride` → `feesAndCharges`).
  Optional extension: let the user attach a _named_ fee (map a chosen fee type → `feesAndChargesDetails`
  with `feeTypeId`/`feeRuleId`), not just the implicit adviser fee.
- **On a withdrawal (disbursement):** not wired. `createcashdisbursement` takes
  `feesAndChargesDetails[]` + `agentTransactionChargeMethodType`. Add a fee to `build_disbursement`
  and carry an `adviser_fee` (and optionally a chosen fee type) on the withdrawal instruction,
  mirroring the receipt path.
- **Design call:** when is a fee a standalone `transaction.fee` vs riding the payment/withdrawal?
  Suggest: adviser fee taken _from_ a contribution/withdrawal rides the money movement; a separate
  charge (e.g. an ad-hoc advice fee with no money movement) is `transaction.fee`.

---

## 7. Phasing

1. **Phase 1 — ad-hoc fees work (smallest, unblocks the reported error).** One-time fee-type sync
   (§5.1) + re-enable the picker (§5.2). Backend already there.
2. **Phase 2 — ongoing fees.** Periodic package sync (§4.1) + SEI `fee.attach` leaf (§4.2) +
   read-back (§4.3) + UI (§4.4).
3. **Phase 3 — fee on the money movement.** Withdrawal fee fields (§6) and the named-fee extension on
   payments.

Client wrappers to add only if needed: the detailed periodic assignment
(`periodicaccountfeepackageassignment`) if we need schedule control; `retrievegroupdetails` for group
reconciliation. We should **not** wrap `feePackageAdmin`/`feeRuleAdmin` unless firms will author
packages in portals (they're authored in SEI today).

## 8. Open decisions (need Mark)

1. **`external_id` meaning** (§3): per-row SEI id (recommended) vs `fee_package_platforms`.
2. **Do firms create fee packages in portals, or only in SEI?** Determines read-only sync vs wrapping
   `feePackageAdmin`/`feeRuleAdmin`. Assumption: **read-only sync.**
3. **One-time fee types as `fee_packages` rows** (recommended, reuses the picker) vs a separate
   fee-type catalogue + endpoint.
4. **Fee-on-movement vs standalone** split (§6 design call).

## 9. Bugs found en route

- `assign_package_to_account` sends `copyPackageFlag=0`; spec wants `1`/`2`/blank (§4.2).
- The "single one-time initial advice fee" auto-default guesses and fails ambiguously (§5.3).
