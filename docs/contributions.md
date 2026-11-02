# Contributions and the money ledger

How a pension contribution gets from an adviser's intent to a custodian's expectation to a
back-office record — and, just as importantly, what SEI and Morgan Lloyd need that we do not
yet hold.

Landed in `0051_contributions_ledger`; the lifecycle completed in
`0056_contribution_lifecycle`. Surfaces: the platform portal's `/contributions` workbench
(nav: **Pensions**), the `/transfers` workbench, and the "Record a contribution" drawer on a
pension account. All three are shared components under `@graphene/ui/book/{contributions,
transfers}` — the portal pages are thin mounts, so the admin portal can render the same
surfaces read-only over a firm's book.

---

## 1. The three records

```
  expectation (forecast)   ->   transaction (realised)   ->   contribution (domain)
  platform.transaction_          platform.transactions        platform.contributions
    expectations                   .expectation_id  ─────────┘  .transaction_id
```

**`platform.transaction_expectations`** is the forecast: money of this size is due on this
account by this date. It is deliberately _not_ a realised inbound credit. Matching is a join
(`transactions.expectation_id`), not a mutable state column.

The **outbound** exception is a Graphene-originated row in `platform.transactions` itself
(`source_system = 'graphene'`, `status` pending → submitted → posted), written when an adviser
books a contribution, transfer, payment or withdrawal. That row is what is synced to SEI /
Seccl / Morgan Lloyd; it is never the credit `transaction_id`. See
[`docs/transaction-status.md`](transaction-status.md).

That FK is finally wired here. `0002_platform_crm` declared `expectation_id` with the comment
"FK -> integrations.transaction_expectations wired in 0003"; 0003 became `platform.links` and
the constraint never landed.

**`platform.contributions`** is the domain record: the HMRC decision (category + tax year), the
relief-at-source arithmetic, and the contributor identity. **`platform.transfers`** is its
sibling for money coming in from a ceding scheme.

### Schema placement

`docs/data-architecture.md` Decision 7 originally routed the SIPP domain to a `pensions.*`
schema, with only the ledger in `platform`. That is superseded: contributions and transfers
live in `platform` alongside the accounts and transactions they reference, so one schema owns
the whole money story and there is one fewer grant/RLS surface to keep in step. The decision
record is amended to match.

---

## 2. The flow

Money reaches a contribution from two directions, and both are supported. The difference is
only which record exists first:

```
A  client sends money -> custodian feed -> platform.transactions -> adviser CLASSIFIES it
                      -> ML contribution (Application) -> ML+HMRC (Active) -> ML (Received)
                      -> SEI relief payment -> reconciliation -> complete | cancelled

B  adviser books it (SEI ACR lodged) -> client sends money -> transaction MATCHED to the
                      expectation -> (already classified) -> ... as above
```

**Path A — the credit arrives first.** `GET /contributions/unclassified` is the queue: incoming
credits on pension accounts with no domain record and no matched expectation against them.
`POST /contributions/from-transaction` turns one into a contribution at `confirmed`, taking the
amount from the credit rather than the caller — an adviser classifies what arrived, they do not
get to say a £450 credit was a £500 contribution, because that is the figure a basic-rate
reclaim is computed from. No expectation is raised: a forecast for money that has already
landed is a row the matcher can never resolve.

**Path B — the booking comes first.** `POST /contributions` writes the contribution and its
expectation and lodges the Anticipated Cash Receipt (§4). When the credit lands,
`POST /contributions/match` joins them. The account record no longer mounts a Contribute drawer
for this: a SIPP **Payment** asks contribution type and, for a personal contribution, shows
relief at source under that choice. An optional adviser-fee section sits at the foot of the
form. A SIPP does not Withdraw — that is Drawdown, a DCube session
(`POST /crm/accounts/{uid}/drawdown-session` → `cam_setmemberdetails`), not a custodian cash
instruction. Relief in advance stays on the classify panel — it is about money that has
landed.

### The booking drawer asks four questions

`contribution-form.svelte` still collects the category (personal or employer), the amount, whether it is
ad-hoc or regular — and, when regular, the period — plus the date the money is **expected by**.
Nothing else, at the standard drawer width. Three controls were removed because each asked an
adviser to state something we either already know or cannot yet know:

- **The contribution date.** On a book-ahead the payment has not been made, so a date typed here is
  a guess written into the column the tax year is derived from. `create_contribution`
  now stores **NULL** rather than today's date, and `matching.link` stamps the real one off the
  credit when it arrives — guarded on `IS NULL`, so a date an adviser _did_ state (or the one path A
  reads off the credit directly) is never overwritten by the settlement of the credit matched to it.
  `unlink` takes back only the date it stamped, identified by its still matching the credit's own.
  A blank date is not a gap: the expectation carries the due date.
- **"Paid by".** It restated the category — a personal contribution is paid by the client, an
  employer contribution by the employer — so the two controls could only ever disagree. `paid_by` is
  still on the wire (Morgan Lloyd needs it); it is derived, not asked.
- **The tax year.** Derived from the date, so choosing one before the date is known could only
  contradict it. `tax_year_of(today)` is the basis for a booking, which is correct: a booking belongs
  to the year it is made in.

Ad-hoc vs regular is presentation, not a wire value: `frequencyFor` resolves ad-hoc to `single` and
regular to the chosen period, because `cam_frequency` has no value meaning "regular" on its own.
`contribution-form.test.ts` pins the removals — a form that grows one of them back can record
something untrue.

**The workbench's classify panel asks LESS** (`classify-panel.svelte`, §8). It lost the same three
controls this drawer did — `paid_by` derived by `paidByFor(category)`, `tax_year` not sent at all
(`categorise` COALESCEs it, so omitting it leaves the derived value standing) — and in Sept 2026
lost ad-hoc/regular, the period and the contributor block as well, leaving the contribution type as
the whole form. That is a divergence on purpose, and it is not the two surfaces spelling one
question differently: **booking is about money that has not arrived and classifying is about money
that has**, so a frequency is a real property of a standing arrangement being set up and a wrong
answer about a credit that has already landed. The questions they still share are still spelled the
same way.

The **category list** is the other deliberate difference. Booking offers the creatable pair only;
classifying keeps `transfer_in` and `not_a_contribution` reachable (`OTHER_CONTRIBUTION_OUTCOMES`,
a quiet "Something else?" row beneath the button group), because a credit that is neither a personal
nor an employer contribution has to be disposable — without those routes it resurfaces in the
unclassified queue for ever. When one is chosen the panel states it rather than rendering a button
group with nothing selected, which is what `Segmented` does with a value outside its options.

**`third_party_contribution` is offered by neither surface** (Sept 2026). It is a payer variation
dressed as a category: for relief purposes a grandparent's payment into a grandchild's SIPP is the
member's own contribution, so the tax question is binary — the member's money (relievable, relief at
source) or the employer's (gross, no relief) — and who moved it is a fact about the payment. The
value stays in `ContributionCategory`, in `CONTRIBUTION_CATEGORIES` and in 0051's CHECK, so rows
classified that way before the change still read back, still render and still push; the pin test
asserts the two offered lists partition the vocabulary **minus that one member**, so a sixth
category still cannot be added and left unreachable.

### Matching (`app/matching.py`)

**Auto-matching is exact-amount only, deliberately.** A near match is _offered_
(`GET …/match-candidates`) and a human confirms it with `POST …/attach-transaction`; only an
unambiguous exact-amount credit inside `MATCH_WINDOW_DAYS` (14) of the due date is applied
automatically. Two identical credits in the window — a client who paid twice, a duplicated feed
row — are reported under `needs_review`, never resolved by picking one.

The reason is that a wrong match is not a wrong number on a screen: the contribution advances,
is recorded to Morgan Lloyd as Received when the account is an SEI SIPP, and a relief reclaim
is made to HMRC against a payment that was something else. A Seccl account, including a Seccl
SIPP, is administered by Seccl, and classifying a contribution there does not call Morgan Lloyd. A manual match stamps `matched_by`; the automatic one leaves it NULL,
so a file review can tell a tolerance from a person's decision.

### The Morgan Lloyd status mirror

`platform.contributions.ml_status` mirrors `cam_status` verbatim as the option-set integer
(`morgan_lloyd.enums.ContributionStatus`):

| `cam_status` | value     | Graphene stage |
| ------------ | --------- | -------------- |
| Application  | 809130000 | `ml_recorded`  |
| Active       | 809130001 | `ras_applied`  |
| Received     | 809130003 | `ras_received` |
| Stopped      | 809130002 | `cancelled`    |

The two vocabularies are different lengths on purpose: ML tracks the back-office record,
Graphene tracks the whole journey. `tax_relief_received` and `reconciled` have no ML equivalent
at all — they are facts about SEI's relief credit and our own three-way compare.

**`cam_updatecontributionapplication` is valid only at Application**, which is what the mirror
is _for_. `PATCH /contributions/{uid}/morgan-lloyd` re-reads the status from the vendor before
sending — the mirror is only as fresh as the last refresh — and answers **409** past
Application rather than letting Dynamics return a generic failure that surfaces as a 502
outage. `POST …/refresh-morgan-lloyd` pulls the status; it is what moves a contribution through
Application → Active → Received, because those transitions happen at the scheme administrator
and at HMRC, so asking is the only honest way to learn them.

Status updates are **monotonic**: a nightly read returning Application on a contribution
already seen at Received must not undo real progress. Stopped is the exception and cancels from
anywhere — the scheme administrator stopping a contribution is authoritative, not stale.

### Cancelling

`POST /contributions/{uid}/cancel` requires a reason (a DB CHECK enforces it: a cancelled
HMRC-reportable record with no reason is not an audit trail), cancels the open expectation so
it stops appearing in the matcher's queue, and best-effort stops the Morgan Lloyd record. Past
`ras_applied` it is refused with 409 — there is a reclaim in flight at HMRC, and cancelling
locally without reversing it leaves the two disagreeing.

Classifying a credit as `not_a_contribution` writes the row and cancels it in one step. The row
matters: it is what stops the credit resurfacing in the queue on every read, which is the
alternative to throwing the adviser's decision away.

### Reconciliation and relief

Both are **derived on read** (`app/matching.py::reconcile` / `relief_position`), not stored.
`platform.reconciliation_breaks` is deliberately still unbuilt: a break is a statement about
the current rows, so a stored copy goes stale the moment either side is corrected — and then
the breaks table needs reconciling against the ledger it exists to reconcile. What a stored
break would add is workflow (assignment, resolution notes), worth having once somebody is
assigned to work it.

Relief received is summed at **account** grain, because SEI reclaims in a monthly batch against
the scheme rather than per contribution — splitting a batch credit across the contributions
that earned it is an allocation, not a fact.

### Status codes

Matching `crm.py::account_drawdown_session`, so every provider-backed action behaves alike:

| Code | Means                                                                    |
| ---- | ------------------------------------------------------------------------ |
| 400  | Bad request (unknown category, non-positive amount)                      |
| 404  | Not the caller's firm's. Never 403, which would confirm the row exists   |
| 409  | State forbids it (already recorded; past classification)                 |
| 422  | The **provider** needs something we don't hold — body names exactly what |
| 502  | The vendor refused. The request was fine, the upstream wasn't            |
| 503  | Credentials aren't configured. A deployment state, not a client error    |

### Status vocabularies

`platform.contributions.status` — `identified → awaiting_confirmation → confirmed →
ml_recorded → ras_applied → ras_received → tax_relief_received → reconciled`, plus two
**off-pipeline terminals**: `manual_intervention` and `cancelled` (0056). Neither is a stage
nine — they are ways a contribution stops — so the workbench excludes both from its counts
rather than appending them. Classification is only editable up to `confirmed`; past that it has
been acted on downstream and amending it silently would desynchronise the back office (409).

**Above the statuses sit STAGES, and the stage is what an adviser reads** (Sept 2026;
`contributions.py::STAGES`/`stage_of`, mirrored by `book/contributions/status.ts::STAGES`, the
two pinned against each other by `tests/test_contributions.py::TestStages`). A status is the
engine's word for where a contribution has got to; a stage is the word on the screen, and there
are fewer of them because several statuses answer the same question:

| Stage                     | Statuses                              | Caption                  | Counts        |
| ------------------------- | ------------------------------------- | ------------------------ | ------------- |
| To classify               | `identified`, `awaiting_confirmation` | Custodian feed           | transactions  |
| With scheme administrator | `ml_recorded`                         | Morgan Lloyd             | contributions |
| Relief claimed            | `ras_applied`                         | Morgan Lloyd → HMRC      | contributions |
| Relief in transit         | `ras_received`                        | HMRC → Morgan Lloyd      | contributions |
| Relief credited           | `tax_relief_received`                 | Morgan Lloyd → custodian | contributions |
| Not lodged                | `confirmed`, `manual_intervention`    | Unclassified             | transactions  |
| Completed                 | `reconciled`                          | —                        | contributions |
| Cancelled                 | `cancelled`                           | —                        | contributions |

Three properties are load-bearing. It is a **total** function of `status` alone — a status with
no stage would be counted by no metric, badged by nothing and unreachable by any filter option,
three silent absences from one omission. **No status belongs to two stages**, because the KPI
strip sums a stage's amounts and a row in two stages is the same money reported twice. And it is
NOT derived from the expectation as well, which was tried: an `ml_recorded` row whose SEI
expectation had failed would then read as "not lodged", hiding a contribution that IS with the
administrator.

`GET /contributions`'s `status` filter and its facet both speak stage keys, for the reason
`_contact_status` gives on the clients listing: an option the screen does not use returns
nothing and reads as an empty book rather than as a mismatched vocabulary.

`platform.transfers.status` — `requested → submitted → in_progress → assets_received →
funds_sent → completed`, plus `cancelled` and `manual_intervention`. See §7 for why
`funds_sent` is the only stage 0056 added.

`platform.transaction_expectations.status` — `expected | instructed | matched | reconciled |
cancelled | failed`. Orthogonal to the contribution's, and they can legitimately disagree: a
classified contribution whose expectation never reached SEI is a real state, and the workbench
shows both badges because hiding it means an adviser never chases the payment.
After a lodge, the activity is read back (`GET /activitymanagementservices/activity/{id}`)
and the state is stored on the Graphene transaction (`data.sei`) and on the expectation's
response. That read runs again every fifteen minutes while the row is still submitted.
It does not move the expectation to `matched` — that still waits for the credit in the feed.
`raw.api_transactions` is the fifteen-minute history pull: every account, only the days not yet covered, merged on `transaction_id`.

---

## 3. Relief at source, and where the maths lives

Personal and third-party contributions are paid **net** of basic-rate relief, which the scheme
reclaims; employer contributions are paid **gross** (the employer takes the corporation-tax
deduction instead). `gross = net ÷ 0.8`, `relief = gross − net`.

The arithmetic exists **twice on purpose**:

- `apps/api/app/contributions.py` is authoritative. A client cannot be trusted with the number
  that determines an HMRC reclaim, and `gross_amount`/`relief_amount` are _stored_ rather than
  recomputed on read — the relief rate is a point-in-time fact, and recomputing a 2026/27
  contribution at a future year's rate would silently restate it.
- `apps/platform/src/lib/contributions/relief.ts` drives the live preview, which is what makes
  choosing a category feel consequential rather than clerical.

`tests/test_contributions.py` and `relief.test.ts` run the same cases, so a change to one
without the other fails.

---

## 4. What the providers need that we don't hold

This is the standing record of the gap. ✅ held · ⚠️ partial · ❌ missing.

### SEI — `POST /currencyreceiptservices/createCurrencyReceipt`

There is no `receiptType = ACR` field: an **Anticipated Cash Receipt is a receipt with a future
`dueDate` and no `payerInfo`**. Adding a payer profile is what turns the same call into a BACS
Direct Debit — out of scope, because a SEI Payer Profile is data we do not hold.

| Field                  |     | Source                                                                                                                                                                                                                                                                        |
| ---------------------- | --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `accountNumber`        | ❌  | `platform.links`, provider `sei`, link type `primary`. **Nothing writes these yet.**                                                                                                                                                                                          |
| `portfolioId`          | ✅  | The numeric entity id (at most nine digits). Enrolment stores a global id (`3108-SWP-I31-59103919`); the receipt takes the trailing id (`59103919`). A nightly `platform.portfolios` row is already that id.                                                                  |
| `firmId`               | ⚠️  | `platform.tenant_platforms.config.firm_id`. Required in practice: our SEI users are Processing-Organisation level (`i31pographeneapi50`), and omitting it returns error 1001.                                                                                                 |
| `descriptionTypeCode`  | ⚠️  | `platform.tenant_platforms.config.contribution_description_code`. No lookup **endpoint** — but the nightly Reference report publishes the set (see below).                                                                                                                    |
| `treatmentTypeCode`    | ⚠️  | A **pension** uses `contribution_treatment_codes.{personal,employer,third_party}`. An ISA or JISA does not: it sends Reference `1401` (ISA Current Year Subscription) with description `20009`, and `isaContribution`. A GIA sends `1451` (Addition to Account) with `20009`. |
| `currencyCode`         | ✅  | `platform.accounts.currency`                                                                                                                                                                                                                                                  |
| `receiptAmount`        | ✅  | The contribution                                                                                                                                                                                                                                                              |
| `treatmentCodeTaxYear` | ✅  | `contributions.tax_year`                                                                                                                                                                                                                                                      |
| `once.dueDate`         | ✅  | `transaction_expectations.due_date`                                                                                                                                                                                                                                           |
| `receiptMethod`        | ⚠️  | An ACR collects nothing, so this describes how the money is expected to arrive. Select's working memo receipt is cheque (`3`) with `memoFlag` true, not BACS (`5`). A BACS receipt that should collect needs a payer profile first.                                           |
| Recurring schedule     | ❌  | A regular contribution currently lodges one expectation, not a recurrence. SEI wants `startDate` + `monthly.interval`/`dayType`/`monthDayNum`.                                                                                                                                |
| `payerInfo.*`          | ❌  | Out of scope (ACR only)                                                                                                                                                                                                                                                       |

#### The two codes are not unobtainable — they are in the file drop

This was recorded here for months as the key blocker, on the grounds that "no lookup endpoint
exists in the published spec set". That is true of the **API** and false of the **nightly
drop**: the `Reference` report carries both sets in full (`TYPE = 'TREATMENT_CODE'`, 758 rows;
`TYPE = 'DESCRIPTION_CODE_ID'`, 6,108 rows). Read from the p04 production corpus, the UK
relief-at-source pair is:

| Concept                        | `treatmentTypeCode` | `DESCRIPTION_CODE_ID` |
| ------------------------------ | ------------------- | --------------------- |
| Member Net Contribution        | 4958                | 21121                 |
| Member Basic Rate Tax Relief   | 4959                | **21122**             |
| Employee Gross Contribution    | 4960                | 21123                 |
| Employer Gross Contribution    | 4961                | 21124                 |
| Employee Net Contribution      | 4962                | 21125                 |
| Employee Basic Rate Tax Relief | 4963                | **21126**             |

plus `20009` Contribution and `20012` Transfer In. `TREATMENT_CODE` rows carry a blank
`FIRM_ID` (platform-wide); `DESCRIPTION_CODE_ID` is firm-scoped and was present for every firm
in the corpus (3102, 3106, 3108, 3110, 3112, 3114).

The write side and the read side are the same six concepts split across the two directions.
`treatmentTypeCode` goes **out** on `createCurrencyReceipt`; `DESCRIPTION_CODE_ID` comes
**back** on every `TransactionHeader` row and is the only thing separating a contribution from
the relief payment that follows it — both arrive as transaction type **6 (Cash Receipt)** on the
same account, often days apart. Without it the reconciliation counts the relief as a second
contribution and double-counts the member's annual allowance.

They are still **not defaults**. `app/integrations/sei/reference.py` holds them as constants and
the 422 now _names_ the code an operator should configure, but nothing fills the gap silently:
SEI validates the description/treatment pair against the transaction type _and_ the caller's
role, and a wrong pair returns error 1006 — a rejection at the custodian, on a client's pension.

**Until the codes are configured, booking still works** — the contribution and expectation are
written, and the response says the custodian was not told and why. `POST …/lodge-expectation`
answers 422 with the list, and with the Reference report's value for the missing code.

### Morgan Lloyd — `POST cam_createcontribution`

The docs head this section "Create Pension Contribution", but the **action is
`cam_createcontribution`**; there is no `cam_createpensioncontribution`. And `cam_contribution`
(the application) is a different entity from `cam_pensioncontribution` (the received record
returned by Get Received Contributions), with a _different_ `cam_contributiontype` option set —
`morgan_lloyd.enums` keeps them as `ContributionType` vs `ReceivedContributionType`.

| Field                                    |     | Source                                                                                                                                                 |
| ---------------------------------------- | --- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `schemeReference`                        | ⚠️  | `platform.links`, provider `morgan_lloyd`, entity `account`. Only for linked accounts.                                                                 |
| `cam_contributiontype`                   | ⚠️  | ML has **three** values; the portal has **five**. `transfer_in` and `not_a_contribution` are refused, not coerced to `OTHER`.                          |
| `cam_contributor` (^ personal)           | ⚠️  | `platform.links`, entity `contact`. Push the client first (`POST /crm/contacts/{id}/morgan-lloyd`).                                                    |
| `cam_frequency`                          | ✅  | `contributions.frequency` — widened to the five real periods for exactly this reason.                                                                  |
| `cam_amount`, `cam_startdate`            | ✅  | The contribution                                                                                                                                       |
| `cam_status`                             | ✅  | Derived: `Received` once matched to a transaction, else `Application`.                                                                                 |
| `cam_employerothername` (* non-personal) | ✅  | `contributions.employer_name` — added by this change; it is not on the client record, because the payer of an employer contribution is not the member. |
| Contributor address block                | ✅  | `contributions.address_line1`/`city`/`county`/`postcode`/`phone`/`email`                                                                               |
| `cam_prefundedrasamount`                 | ✅  | `contributions.prefunded_ras_amount`                                                                                                                   |
| `cam_investorref`                        | ⚠️  | Sent as `accounts.uid`; confirm against a real policy number.                                                                                          |

The contribution screens do not run an eligibility check. Relievable limit, relevant-UK-individual
status, the annual allowance, age 75 and pension protection are not calculated here and do not
block a booking or a classification. Relief at source — the basic-rate gross-up — is still shown
and stored.

---

## 5. Configuring a firm for SEI

```jsonc
// platform.tenant_platforms.config, provider = 'sei'
{
  "firm_id": "3102",
  "contribution_description_code": "1091",
  "contribution_treatment_codes": {
    "personal": "4402",
    "employer": "4403",
    "third_party": "4404",
  },
}
```

The codes above are **illustrative** — SEI provisions them per firm and validates the
description/treatment pair against the transaction type _and_ the caller's role. Ask SEI for
the firm's configured set; a wrong pair returns error 1006.

Which SEI environment the API calls is `SEI_ENVIRONMENT` (default `i31`); credentials are the
`SEI_{ENV}_*` variables read by `pkgs/sei` (see its README).

**SEI restricts by source IP**, so the lodge cannot succeed from a laptop — it fails as a 502
with an explanatory message, not a 500. That is the expected local behaviour.

---

## 6. Code map

| Concern                                   | Where                                                                                    |
| ----------------------------------------- | ---------------------------------------------------------------------------------------- |
| Schema + RLS                              | `migrations/sql/0051_contributions_ledger.sql`, `0056_contribution_lifecycle.sql`        |
| Service (validation, relief maths, lodge) | `apps/api/app/contributions.py`, `app/transfers.py`                                      |
| Matching, unclassified queue, recon       | `apps/api/app/matching.py`                                                               |
| HTTP + status codes                       | `apps/api/app/routers/contributions.py`                                                  |
| SEI seam                                  | `apps/api/app/integrations/sei/` over `pkgs/sei`                                         |
| SEI reference codes (verified)            | `apps/api/app/integrations/sei/reference.py`                                             |
| SEI canonical transaction types           | `services/sei/models/platform/transactions.sql` (124 codes, from the drop)               |
| Morgan Lloyd seam                         | `apps/api/app/integrations/morgan_lloyd/contributions.py` over `pkgs/morgan-lloyd`       |
| Permissions                               | `contributions:read`/`:write`, `transfers:read`/`:write` in `app/permissions.py`         |
| Contributions workbench                   | `@graphene/ui/book/contributions` — mounted at `apps/platform/src/routes/contributions/` |
| Detected credits -> queue rows            | `@graphene/ui/book/contributions` → `detected.ts`                                        |
| Server-side listing contract              | `apps/api/app/listing.py`, `@graphene/ui/book/listing` → `query.ts`                      |
| Transfers workbench                       | `@graphene/ui/book/transfers` — mounted at `apps/platform/src/routes/transfers/`         |
| Transfer drawer                           | `@graphene/ui/book/transfers` → `transfer-form.svelte`                                   |
| Booking form (unmounted)                  | `@graphene/ui/book/contributions` → `contribution-form.svelte`                           |
| Account-record SIPP payment               | `@graphene/ui/book/accounts` → `instruction-sheet.svelte` (type + relief at source)      |

## 7. Transfers

The same three records, a different domain — and a vocabulary worth stating plainly, because
the workbench prototype got it wrong. `apps/platform/src/lib/transfers/types.ts` declared a
nine-status cross-custodian saga citing `0003_pensions_ledger.sql`: a migration superseded by
0051 that **never shipped**. The rail was rendering invented states over fixture data.

What the providers actually publish, checked before the schema was widened:

- **Morgan Lloyd** documents six values (`morgan_lloyd.enums.TransferStatus`): Awaiting forms ·
  ML to request transfer · Awaiting action from ceding scheme · Funds received · Funds sent ·
  No longer transferring. Distinct from the record's own `cam_status` (`AssetStatus`).
- **SEI** has no transfer entity in Morgan Lloyd's sense. When SEI itself is chasing an
  in-specie transfer, its pending-transfer codes drive the saga (`sei_stage`): 2 is Submitted,
  3 and 10 are At ceding scheme, 4, 6 and 7 are Completed, 1, 5 and 8 need attention, 9 and
  18 are Cancelled. Code 7 (`COMPLETE`) is Completed — the custodian has the assets — not the
  reconciled Complete. That read only lists `transferIn`. A cash receipt and a Morgan Lloyd
  free receipt also return an activity id, and that id is not in the list, so Refresh from SEI
  is not offered for them; they are followed on the activity and the transaction feed. Cash is
  an anticipated receipt using the firm's transfer description and treatment codes, not the
  contribution ones. An in-specie transfer SEI itself is chasing is `transferIn`; when Morgan
  Lloyd already asked the ceding scheme, the SEI leg is a free receipt.
- **Seccl** documents a `transferStatus` enum: With Previous Provider, Pending and Placed are
  Submitted; Onboarding Assets and the documentation and sell-down statuses are With ceding
  scheme; Pending Cancellation stays Submitted and is shown as Cancelling in the badge (Cancellation requested on the rail) — the
  request is not the transfer moving on, and it is not Cancelled until Seccl says Rejected,
  Deleted or Cancelled; Awaiting Crystallisation Information is Needs attention
  (Seccl is waiting for the crystallisation split, which is ours to supply); Completed is
  Completed (`funds_sent`), not the reconciled Complete; Rejected, Deleted and Cancelled are
  Cancelled. The words stay on `ceding_status`.
  Seccl and SEI-only rails skip `assets_received`. That stage is Morgan Lloyd only, shown as
  At administrator: the ceding scheme has paid Morgan Lloyd, and the action there is
  to generate an anticipated cash receipt with the custodian. The flow ends at Completed, the
  custodian. The reconciled `completed` status is not a further step: reconciliation is the
  section under the flow, and a transfer the custodian initiated has nothing left for Graphene
  to complete. An in-specie transfer lists its holdings after the chain of custody, and the
  queue is one row for the transfer rather than one row per instrument. Seccl groups on
  `link_id`: the product transfer is the row, and the stock transfers on that link are its
  holdings (name, ISIN, units, and that stock transaction's status). Stock lines that share a
  link and have no product transfer still collapse to one row. The line under the client in
  the table is the ceding scheme's policy number. SEI's file drop groups the same way when
  TransactionHeader carries `RELATED_ACTIVITY_ID` (Free Receipt and Security Transfer To).
  The transaction-details API does not return those movements, so nothing further is synced
  from it. A raise does not collect holdings, and a transfer-in does not send them. The
  lines on the transfer detail come from the custodian.
  SEI's pending-transfer read does not publish a status per holding.
  `isPensionTransfer` means safeguarded rights (Pension Schemes Act 2015 s.48(8)), not "this is
  a pension".

`0056` therefore adds exactly one status — **`funds_sent`** — because it is the one distinction
ML makes that we could not represent: "Funds received" (the ceding scheme has paid ML) and
"Funds sent" (ML has passed it to the custodian) are different weeks of chasing, and collapsing
them made a transfer look stalled while the money was moving. Nothing else from the prototype's
saga was adopted: `settlement_date_advised`, `ml_complete` and `adviser_notified` have no
provider fact behind them at any of the three custodians.

The prototype's ops views (settlement board, break resolution, the status crosswalk) are kept
in `@graphene/ui/book/transfers` and clearly labelled as such — none has a real backing yet, and
none is mounted by the portal.

## 8. The workbench surfaces

**Contributions is one queue, not tabs.** A credit the custodian reported and a contribution
already moving through Morgan Lloyd are both "a payment that needs an adviser to know about
it", so they share a list: a detected credit appears at status `identified` (Detected), which is
the stage 0051 declared and nothing had ever produced (`book/contributions/detected.ts`).
Splitting them across tabs meant the count you needed was always on the tab you were not
looking at. Classifying a detected row calls `POST /contributions/from-transaction`; the panel
routes on the uid prefix (`credit:`) and knows nothing else about it.

Relief-at-source and reconciliation are **not** rendered as views, and since Sept 2026 the
classify panel does not render the reconciliation either. Both are computed server-side and both
are real, but neither is this surface's question: the money has already arrived (that is why the
row is in the queue), `from-transaction` deliberately raises no forecast to compare it against,
and relief is reclaimed by the scheme in a monthly batch nobody on this screen can chase. The
components stay exported (`ReliefMonitor`, `ReconciliationPanel`) and `matching.reconcile` is
untouched.

**The contributions workbench leads with six money figures, and nothing else.**

- Each metric is one STAGE (§2), stating the **amount** with the row count demoted to a
  sub-line: a count answers "how many rows" where £3 stuck at the administrator and £30,000
  stuck at the administrator are the same 1. The caption names who has it, because half the
  stages are waiting on somebody else. The figure is a `Money` (pounds at full weight, pence
  smaller and muted — the same component the queue's Amount column uses), and it sits tight against
  its own count: a full line-gap between them read as two unrelated numbers stacked rather than one
  statement ("£38,400, across 9 contributions").
  The `ml_recorded` stage reads **"Pending relief"**, not "With scheme administrator" — the
  administrator HOLDING it is a custody fact and not what anybody is waiting on. The stage KEY is
  unchanged, so a shared filtered-queue URL keeps working and `stage_of` needed no edit.
- **The figures are the WHOLE BOOK, computed server-side** (`contributions.py::stage_totals`, on
  the listing envelope as `summary`). The portal is handed one page, so deriving them there made
  "£38,400 is with the scheme administrator" mean "of the twenty-five rows on screen" — a
  statement about the scroll position in the shape of a figure a compliance reviewer would quote.
  Taken **before the filters** as well as before the page cut, because the strip is how you
  navigate to a stage and narrowing to one must not empty the other five. The detected credits are
  added on top of To classify client-side: they have no `platform.contributions` row
  yet and come from a second, unpaged endpoint, so the summary cannot know them.
- **The pager counts the detected credits too, and shows them once** (`detectedOnPage`). The
  API's `total` is the booked rows only, and the queue lists the detected credits above them, so
  "1 result" sat under a list of two — and the same detected rows were repeated at the top of
  every page. They are narrowed by the same search and filters the server applied to the booked
  rows, shown on the first page only, and added to the pager's total and range; the page count
  stays the server's, because the server's page boundaries are what `offset` walks.
- **`?focus=` accepts a transaction uid** (`resolveFocus`). The Action required strips on
  Transactions, Accounts and Clients link with the TRANSACTION's uid
  (`{source_system}:{source_transaction_id}`, the ledger's own key); a detected row here is
  `credit:{transaction id}`. Nothing matched, so "Categorise contribution" opened the workbench
  with nothing selected. A focus that names no row is looked up against the credits' ledger
  identity and the detected row is selected; a contribution's own uid still matches directly.
- **The clickable pipeline strip is gone.** It was a second row of the same counts AND a second
  way to narrow by stage — two answers to one question, which had already drifted (it folded
  `ras_applied` and `ras_received` into one stage; the KPI strip counted them separately). Its
  job as a control moved into the queue's own toolbar as a status filter, beside the filters it
  belongs with. `PipelineStrip` survives as a component and now reads the same `METRIC_STAGES`.
- **No "Match payments" button.** `POST /contributions/match` is a reconciliation sweep — safe
  to re-run, no per-row decision in it — so it is a job rather than an adviser's act, and the
  button only asked somebody to remember to press it. The endpoint and the page's form action
  stay, for ops and for the scheduled matcher §9 still lists as unbuilt.
- **The client's name is a link to their record**, in the queue and on the panel's heading. The
  commonest next question about a row is "who is this?", and answering it used to mean leaving the
  workbench, searching the client, and losing both the queue position and the selected row. A real
  `<a href>`, not a click handler, so middle-click and "open in new tab" work — which is the whole
  point of asking without abandoning the queue; `DataTable` already exempts an in-cell anchor from
  `onRowClick`, so the rest of the row still selects for classification. The href is INJECTED
  (`ContributionLinks`), so an admin read-only mount with no client route renders plain text rather
  than a dead link. This needed `ct.uid AS contact_uid` on `_SELECT`, which had `account_uid` and no
  client counterpart — the same omission `docs/api.md` → "Addressing" records for the transactions
  projection and `GET /documents/requests`, both of which linked the serial as a result.
- The queue shows date, then client (the transaction reference under the name) and account,
  amount, type, status and booked. The columns of a known size are set in rem and the two names
  share the rest, so a wide table gives its room to the names rather than to a band of white
  beside every pill; a name cuts off at its column, whole on hover. Booked steps out below
  64rem, and the account folds under the client below 56rem, where the panel is open. **Frequency, tax year
  and expectation came off**: the first two never vary within a row being triaged, and the
  expectation badge answered "has the custodian been told", which is now the Not lodged stage.

**The classify panel asks one question and offers one act.**

- The question is the **contribution type** — personal or employer, as a two-way switch sized to
  its own labels, with transfer-in and not-a-contribution reachable underneath so a credit that
  is neither can be disposed of. `third_party_contribution` is **not offered**: for relief
  purposes a grandparent's payment into a grandchild's SIPP is the member's own contribution, so
  the tax question is binary and who moved the money is a fact about the payment. The value
  stays in the vocabulary and in the 0051 CHECK, so existing rows read back and still push.
- **Classifying records it.** Saving and pushing to Morgan Lloyd were two presses with a
  `confirmed` state in between that badges as done while nobody downstream has been told. They
  were never two decisions. The chain lives in the platform page's form action, not in the API:
  the two endpoints stay separate, the vendor's own status codes arrive unflattened, and a
  failed push cannot roll back a classification that is correct and ours. A failure is a warning
  on a success, and the row stays in **Not lodged** — which is what that metric is for.
- Once the administrator holds it the only act is **Cancel**, and past `ras_applied` not even
  that (a reclaim in flight at HMRC cannot be unwound here, so the API 409s and the control
  hides). "Refresh from Morgan Lloyd" stays because it is a READ, not a third act.
- **A DISPOSAL IS ALWAYS REVERSIBLE** (`contributions.py::DISPOSALS`). `not_a_contribution` is
  written AND cancelled in one call, landing on a terminal status `CLASSIFIABLE` excludes — so a
  mis-click was permanent: the credit left the queue (the point) with no way back but SQL.
  Reopening one is safe for a stated reason rather than a convenient one — **Morgan Lloyd never
  held it**: no `cam_contribution` (`UNPUSHABLE`), no lodged expectation, no relief reclaimed,
  which is the entire argument the status gate is built on. The Graphene pending deposit _did_
  exist once create started writing one (`0179`); classifying TO a disposal now cancels that
  row too (`outbound.cancel_origin`), and classifying back OUT restores it to `pending`. A row
  that somehow reached Morgan Lloyd is refused anyway. Classifying TO a disposal cancels it and
  classifying back OUT un-cancels it; `categorise` did neither before, so a reclassified row sat
  in the pipeline as live work and a corrected one kept "Cancelled — classified as not a
  contribution" on its record.
- **The panel's own fields**: the settled view states the payer, the **transaction date** (the date
  the MONEY moved — what `matching.link` stamps off the credit and what a reconciliation reads
  against; "contribution date" invited it to be read as the date somebody decided to contribute)
  and **Lodged on** (`ml_recorded_at` — the stage vocabulary calls its absence "Not lodged", so the
  record can say when that stopped being true). Frequency and tax year came off: neither is read
  anywhere, and a details view is for facts somebody acts on rather than every column the row holds.
- **Above the classification, both views state what the queue's row says and the heading does
  not**: the adviser, with the face the book gives them ("Unassigned" when there is none), the
  transaction reference, the date when no money has moved yet, when it was booked, and the
  employer an employer contribution goes to Morgan Lloyd under. Opening a payment never loses a
  fact the table had.
- **No relief block on a disposal**, in either mode: a transfer in and a not-a-contribution earn no
  relief and never reach Morgan Lloyd, so a gross-up beside one states an amount nobody will
  reclaim — and it moves as the adviser changes the category, which is when it is most likely to be
  misread as following from the new answer.
- **The payment's provenance replaces the expectation badge** — `date · type / subtype · method`,
  each segment dropped when the custodian did not say it (SEI publishes no `method`; Seccl's is
  "BACS Credit", "Direct Debit"). That badge asked "has the custodian been told to expect this",
  which for money that has ALREADY ARRIVED is a question about a forecast nobody raised, so it
  rendered a bare "—" on every detected row. The same fact now has a stage and a metric.
- The heading is **"Classify Payment"**, not "Classify this credit": the row is a payment whether
  or not a `platform.contributions` row is behind it yet, and two headings for one act made the
  panel look like it changed function between rows.
- **Ad-hoc/regular, the period and the five-field contributor block are gone.** A credit that
  has landed is one payment, and marking it "quarterly" is what `cam_frequency` would then carry
  for ever; the contributor is the client's employer, which is a fact about the person (see
  below). The vendor marks the contributor's address and email optional, so those four fields
  asked for something nobody requires, once per payment, of somebody who did not know it.

**The employer lives on the client (`0126`).** `crm.contacts.employer_name` is the default
contributor name for an employer contribution, read by `create_contribution`, `categorise` and
`classify_transaction` alike; `platform.contributions.employer_name` keeps its own copy, because
that is what was actually filed for that payment and a client changing employer in March must
not restate what was sent in February. It sits beside **Annual earnings** in an **Employment**
block on the client record, shown only for a client who holds a pension. That label is
`relevant_uk_earnings`. The column keeps the statutory name; the friendlier word belongs on
the label. It is a fact about the client, sent to Morgan Lloyd as annual earnings. The
contribution screens do not test a contribution against it.

**The nav calls it Scheme administration → SIPP Contributions.** It sat under Money movement
beside Transfers and Transactions, on the reading that it classifies a payment-in — true, and
not what the surface is for: a contribution's whole life after classification runs at Morgan
Lloyd and at HMRC, and none of those legs is a custodian movement. The route stays
`/contributions`, matching the API and the permission, and keeping the `?focus=` deep links the
Action-required strips hand off to.

**The transfers workbench is the same shape as contributions.** A KPI strip of the consolidated
stages (amount first, count underneath; Complete and Cancelled are filters, not cards), then
the queue beside a 420px detail panel. The custodian filter is offered only when the firm
has more than one custodian switched on. Totals are `summary` on `GET /transfers`, computed over
the whole book before the filters and the page cut. A transfer synced from Seccl or SEI stays in
that queue and takes the stage its own status maps to — With Previous Provider is Submitted.
A Seccl stock migration has no transfer status: a completed transaction is Completed, and one
still pending at Seccl is Submitted. Cash versus in-specie is the custodian's subtype, not the
transaction type: a Seccl Product Transfer is `Cash` or `In Specie`, and a Stock Transfer is
in-specie. Stock lines on a Seccl `link_id`, and SEI lines that share `RELATED_ACTIVITY_ID`,
are holdings of that one transfer, not further rows. The table's reference under the client
is the policy number. Those synced rows are read-only. The panel leads with the transaction,
the mapped status, the client and account (both links) with the wrapper, and the amount with cash or
in-specie under it, then the custodian, its reference and its own status. Under that is the
rail for the stages that route uses — Seccl and SEI skip At administrator. Then the
chain of custody (ceding scheme, policy, scheme administrator, custodian), reconciliation,
and the note. Section headings are the same small capitals as the contribution panel.
Reconciliation is Requested against Received. An SEI SIPP, which Morgan Lloyd administers,
has three columns: Requested, Received and Transferred. Seccl does not publish a received amount: `transferValue` is the only figure,
and `amount`, `executedAmount` and `consideration` are zero on the transfers in the book.
`bookValue` is a CGT cost, filled while a stock migration is still open, so it is not a
receipt. Received stays blank until the custodian has booked the movement (Completed);
it is then that same `transferValue`. A transfer raised in Graphene can differ, because
Received is the matched transaction. A product transfer Seccl still has with the previous
provider can be cancelled, including one that arrived on the feed. A stock migration has
no transfer status, and SEI has no cancel call. One footer action. The note is stored on our record
(`platform.transfers.note`, or `platform.transactions.adviser_note` for a synced row). Seccl's transfer-history call records a chasing event ("Client action
complete"), and SEI's transfer calls have no note field, so the note is not sent to either. The
page-level saga strip is gone — it duplicated the strip and only filtered the page on screen.

**Raising a transfer is a drawer off an account, not a page.** Cash vs in-specie is asked of
every account (a `Segmented`, never defaulted — Cash instructs the ceding scheme to liquidate).
Full vs partial appears unless the account is at Seccl (Seccl is full-only, including a Seccl
SIPP; the column is stored as `full` without asking). A pension asks drawdown status
(No Drawdown / Partial Crystallisation / Full Crystallisation — stored as uncrystallised /
partial / crystallised) and whether it contains safeguarded rights (Pension
Schemes Act 2015 s.48(8); unchecked is No). The ceding scheme's product type
(`cam_pensiontype` 1–20) is asked of an SEI SIPP. The crystallised amount appears only when
some of the pot has been crystallised; the uncrystallised remainder is derived so the three
figures cannot disagree. Morgan Lloyd still requires both split amounts, so an SEI SIPP
refuses a blank split. Ceding provider
(searchable over `platform.transfer_providers`, a typed name accepted for a scheme not yet
registered) and the provider's reference are always asked. A Seccl account sends the client's
address as `transferAccountAddress`. The drawer starts from the address on the client record
("Use current client address"); turning that off opens address lookup, with the line fields
available as a manual override, because Seccl will not place a product transfer without line 1,
postcode and country. Safeguarded rights are a checkbox at the bottom of the drawer, on every
pension. When that box is ticked the drawer asks the advising firm's FCA reference. Account
details asks the same checkbox and reference on a pension transfer. An in-specie raise does not list holdings. A direct SEI transfer also asks the
ceding account type Select expects — not the wrapper — and the reference of a form already
uploaded to SEI. `transfer_type` is derived from the account's product
(`transfers.transfer_type_for`). `/transfers/new` is gone.

Raising writes the transfer and its expectation first, then instructs the provider.
The calls, and where each field comes from, are [`docs/api-integrations.md`](api-integrations.md) §4.7.
A Seccl account is placed at Seccl. An SEI account is a transfer-in or an anticipated
receipt. An SEI SIPP is recorded at Morgan Lloyd; the SEI anticipated receipt is the
detail action once Morgan Lloyd has completed the transfer (funds sent), not while
Morgan Lloyd is still holding the cash. A provider refusal leaves the transfer at
Raised with `provider_error`, so the same call can be retried from the detail, and
does not discard the row. An accept that comes back without an id is held at
`manual_intervention` instead, and the detail does not offer a second place or a
refresh. Later Seccl and SEI progress is applied from the transaction
sync when the workbench is read (`apply_synced_progress`), joined on the provider id
stored when the instruction was accepted. Morgan Lloyd's conformed transfers are not
merged onto `platform.transfers` — that overwrite would ignore the monotonic stage
rule — so an SEI SIPP takes Morgan Lloyd's progress from the refresh until the
custodian's own transaction arrives.

A Seccl SIPP is custodied and administered by Seccl. An SEI SIPP is custodied by SEI and
administered by Morgan Lloyd, so the derived split and the pension type are required even
though SEI itself does not take them. A GIA or an ISA is Seccl or SEI according to the
account's custodian. A product mapping that names Morgan Lloyd on anything else does not
create a Morgan Lloyd leg. `form_spec_for` (Python) and `formSpecFor` (the drawer) share that
table; `custodian`, `scheme_administrator` and `is_pension` already on the account page are
enough — there is no generic "what does platform X need" endpoint.

### Listings are server-side

Search, filters and paging for clients, accounts, transactions, contributions and transfers all
run in the API (`app/listing.py`), driven from the URL (`book/listing/query.ts`). A filter
applied to one page is a lie — it says "no client called Halloway" when it means "not on this
page" — and a filtered listing has to survive a reload and be shareable.

Two rules worth keeping: `total` is the count of the **filtered** set (what the pager needs, and
what makes "41 of 1,340" honest), and a facet's options are computed with its **own** filter
lifted. Without the second, picking an adviser leaves that adviser as the only option and the
filter becomes a one-way door.

`GET /crm/transactions` opts into the envelope with `paged=true` rather than by the presence of
`limit`, because `limit` there has always meant "how many to fetch from the lake"; the other
listings follow the existing rule that `limit` switches the shape.

## 9. Not built yet

- **`platform.reconciliation_breaks`.** Deliberately not built — §2 says why. The three-way
  compare is derived on read.
- **RAS batch tracking.** SEI reclaims in a monthly batch and we hold no batch identifier, so
  the relief monitor reports the per-member position and the account-level credit rather than
  inventing batch rows. `ras_applied`/`ras_received` are now driven by the Morgan Lloyd status
  refresh rather than only by `POST …/status`.
- **BACS Direct Debit** / SEI Payer Profiles, so regular contributions can be _collected_ rather
  than merely expected. A regular contribution still lodges one expectation, not a recurrence.
- **A scheduled matcher.** `POST /contributions/match` runs on demand and is written to be
  re-run safely (idempotent, ambiguity reported not guessed); nothing calls it on a timer yet.
- ~~**Morgan Lloyd transfer push.**~~ **Built** (Sept 2026) —
  `app/integrations/morgan_lloyd/transfers.py` plus `POST /transfers/{uid}/{record-to,
refresh-,cancel-at-}morgan-lloyd`. `platform.transfers`' columns were written to track
  `cam_createtransfer`'s required list and nothing had ever sent one.

  **`basis` and `extent` are columns** (`0148_transfer_basis`) — cash vs in-specie, full vs
  partial. The raise drawer writes them; the Morgan Lloyd adapter reads the column first, then
  `data` jsonb for rows raised before 0147, then `transfer_type` only where that CHECK holds
  `cash`/`in_specie`. They are REFUSED when absent, with the reason named: sending Cash for an
  in-specie transfer instructs the ceding scheme to liquidate the client's portfolio, and the
  vendor cannot object because both values are valid. `transfer_type` stays the **wrapper**
  (`pension`/`isa`/`gia`), still derived from the product.

  **`pension_type` (`cam_pensiontype`) is a column** (`0176_transfer_pension_facts`). The raise
  drawer asks it of every pension, with the vendor's 1–20 labels. Drawdown status is
  No Drawdown / Partial Crystallisation / Full Crystallisation (stored as uncrystallised /
  partial / crystallised) and the split amounts are derived from it; safeguarded
  rights are the s.48(8) question and are ours, not a vendor field. A Seccl transfer sends
  the client's address, starting from the one on the client record.

  Cancel is 409 once `transferstatus` reaches Funds received: the ceding scheme has already paid,
  so withdrawing is a return arranged with two counterparties rather than a cancellation. The gate
  reads the VENDOR's current status, not our mirror — `NotAnApplication`'s reasoning.

- **Account-level pension phase.** We do not store accumulation vs drawdown. The person's
  `flexi_access_taken` is the MPAA trigger and is not a substitute: one person can hold several
  pensions in different phases. The account detail must not infer a phase from it.
- **Paging is post-filter in Python, not SQL.** `app/listing.py` shapes the full RLS-visible set
  and then cuts, which is what `crm.py::_page` already did and is fine at a firm's book size. The
  seam is deliberate: the predicates are declared as data (`_ACCOUNT_FILTERS` &c.), so pushing
  them into the WHERE clause is a change behind one contract rather than a rewrite of five
  endpoints.

## 10. Client-raised money (Oct 2026)

A client who may move their own money — an execution-only account, or a book whose
classification carries `capabilities.money_movement`
([`client-classifications.md`](client-classifications.md)) — raises payments and withdrawals
from the portal at `/transfer/deposit` and `/transfer/withdraw`. Nothing new is stored: the
door is `/crm/money` (`apps/api/app/routers/client_money.py` over `app/client_money.py`), and
what it writes is an ordinary `platform.instructions` row through
`instructions.create_instruction`, so the firm sees it on the same workbench, under the same
funding gate, with the same pending transaction, as one a planner raised. The note starts
"Raised by the client in the portal." — that prefix is also what makes it the client's to
cancel (`POST /crm/money/instructions/{uid}/cancel`; a firm-raised one is listed but not theirs
to change).

**The three steps are three actions**, not client state. `preview` (`POST /crm/money/preview`)
answers the review screen without recording anything; `confirm` (`POST /crm/money/instructions`)
re-decides server-side from the account's position at that moment; a failure returns to the
amount step carrying the API's own words ("Only £870 can be withdrawn from this account just
now."). A reload, or a browser without JS, lands on the same screen. The declaration — "This is
your decision, not financial advice" — is a checkbox the server checks (`understood=on`); an
unticked one re-previews rather than raising.

**Deposits** become an `expected_in` with `due_date` = today + the expected-in window, dispatched
to the custodian the way a planner's would be. An ISA/JISA/LISA refuses an amount over the
remaining allowance with the figure that is left. The Done screen shows the instruction uid as
the payment reference; **no firm has pay-in bank details on the platform yet**, so it says the
firm will confirm where to send the money (see the note below).

**Withdrawals** are cash first: an account holding the money raises a cash `withdrawal` and
sells nothing; short of cash, an account run to a model raises a `model_withdrawal` for the
whole amount and the review says how much is sold; an unmodelled account is refused beyond its
cash with the cash figure. Availability is read from the SAME gate that decides the raise
(`instructions._fundable`, net of pending claims), and where that gate cannot tell — no provider
link, no lake — the preview refuses too, rather than building a review the last click would
refuse. A withdrawal that trips a **safety check** (any of: at or over £10,000; over 50% of
the account; over 10% of the client's whole book) is still raised `pending` but is NOT
dispatched: the note records the flags and "speak to the client before releasing it", a
high-priority task goes to the client's adviser (`source_kind='system'`,
`source_ref='client_money:<uid>'`), and the client is told the firm will call. Releasing it is
the firm's ordinary dispatch of a pending instruction.

**Firm-level configuration — not built yet.** Every number above is a constant on
`client_money.Policy` and is read only through `client_money.policy(tenant_id)`, so the move to
per-firm settings is one function: the three thresholds, the expected-in window (5 days), the
"lands by" working days (7), the "invested within" working days (3), and the firm's PAY-IN bank
details (`Policy.pay_in`, `None` today — nothing in the schema holds a firm's own collection
account; when it does, the Done screen prints it above the reference without any other change).
Where it should live: the tenant's branding/settings document alongside `client.support`, or a
small `platform.tenant_money_policy` row — decide when it is built; nothing reads the constants
directly.

Code map: `apps/api/app/client_money.py` (rules + `preview`/`raise_instruction`/`list_pending`/
`cancel`), `app/routers/client_money.py`, `tests/test_client_money.py`;
`apps/client/src/lib/money-movement.ts`, `routes/transfer/[kind]/+page.server.ts` (loader and
the three actions), `+page.svelte`, `routes/transfer/transfer.test.ts`.
