# Transaction status mapping

How a Graphene-originated money movement is recorded as a `platform.transactions` row, how
that row is synced to SEI / Seccl / Morgan Lloyd, and how its status relates to the
instruction, contribution, transfer and expectation vocabularies.

The inbound half of the ledger is unchanged: the custodian feed is an immutable mirror keyed
on `(tenant_id, source_system, source_transaction_id)`, and matching attaches a landed credit to an
expectation. This document is the **outbound** exception — an adviser instructing money that
has not landed yet.

Authoritative code: `app/outbound.py`. Schema: migration `0197_outbound_transactions`.

---

## 1. What creates an outbound transaction

| Action                                              | Origin         | Kind               | `platform.transaction_type` | Typical destination                                                                                       |
| --------------------------------------------------- | -------------- | ------------------ | --------------------------- | --------------------------------------------------------------------------------------------------------- |
| Payment in (`platform.instructions`, `expected_in`) | `instruction`  | `expected_in`      | `deposit`                   | Seccl `Payment/Deposit/In` (and `Cash/Adviser Adhoc Fee/Out` in the same group when `adviser_fee` is set) |
| Cash withdrawal                                     | `instruction`  | `withdrawal`       | `withdrawal`                | Seccl `Payment/Withdrawal/Out`                                                                            |
| Ad hoc fee                                          | `instruction`  | `adhoc_fee`        | `fee`                       | Seccl `Cash/Adviser Adhoc Fee/Out`                                                                        |
| Model top-up                                        | `instruction`  | `model_topup`      | `deposit`                   | Seccl `Payment/Deposit/In` + `autoInvest`                                                                 |
| Model withdrawal                                    | `instruction`  | `model_withdrawal` | `withdrawal`                | Seccl `Payment/Withdrawal/Out` + `autoDisinvest`                                                          |
| Contribution (booked, path B)                       | `contribution` | category           | `deposit`                   | SEI Anticipated Cash Receipt; Seccl `Payment/Deposit/In` (or `Employer`)                                  |
| Transfer in                                         | `transfer`     | `transfer_in`      | `transfer_in`               | Morgan Lloyd `cam_createtransfer`                                                                         |

**Not an outbound transaction** (and why):

| Action                                                      | Why not                                                                                                       |
| ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| Path A classify (`POST /contributions/from-transaction`)    | The credit already exists in the feed. Inventing a sibling pending row would duplicate money that has landed. |
| SIPP Drawdown (`POST /crm/accounts/{uid}/drawdown-session`) | DCube / `cam_setmemberdetails`, not a custodian cash instruction.                                             |
| Recurring payments on a Seccl account                       | Standing `recurringPayments[]` on the account, not a one-off group.                                           |
| Natural-income payment runs                                 | Unbuilt (`docs/data-architecture.md` §4.3).                                                                   |
| HMRC relief credits                                         | Inbound from SEI (description codes 21122/21126), never instructed here.                                      |
| Admin console `create_transaction_group`                    | An operator sending a vendor body directly, not a Graphene domain record.                                     |

Contributions are payments **in** (the client pays the scheme). They are not withdrawals. The
cash-out on a SIPP is Drawdown, not `instructions.kind = withdrawal`.

---

## 2. Identity and idempotency

Outbound rows are keyed:

```
source_system         = 'graphene'
source_transaction_id = {uid}
```

`uid` is the instruction, contribution or transfer's public id. Which of those it is lives in
`data.origin` (`instruction` | `contribution` | `transfer`). `source_system = graphene` is what
keeps the custodian feed off this row, so the reference does not carry a prefix.

A payment in with an adviser fee is still **one instruction**. It writes two Graphene rows that
share `report_group` (the instruction uid): the deposit keyed on that uid, and a sibling fee
keyed on a second public id in `data.companion_uid`. Dispatch sends both as members of one
Seccl `POST /portfoliotransactiongroup`, so they share the vendor's order / `linkId`. SEI
sends one anticipated receipt and puts the fee on it as `feesAndCharges` (the firm's
real-time initial advice fee type, gross amount). The
listing prints `Deposit: Payment` and `Fee: Adviser fee`, not the origin kind (`expected_in`).
The unique constraint on `(tenant_id, source_system, source_transaction_id)` is the idempotency key:
`ensure_pending` upserts, a second book of the same origin returns the same row. A second firm
with the same origin uid keeps its own row.

**The Graphene key is never rewritten to a vendor id.** The book-sink upserts the feed on the
same unique pair; colliding with a Seccl group id would UPDATE this row and could wipe
`data.origin`. The vendor's id is `platform.links` (`entity_type = 'transaction'`,
`entity_id = transactions.id`, `provider` = `seccl` | `sei` | `morgan-lloyd`,
`link_type = 'primary'`). A send checks that link. `data.provider_references`
(`{ "sei": "857458", "morgan-lloyd": "<guid>" }`) is still written so a reader that has
the transaction, and a row sent before the link was the guard, still sees the id. New
code does not treat the copy as the source of truth.

A second success for the same provider overwrites the id (the vendor's current handle). A
second provider (SEI ACR and then Morgan Lloyd on the same contribution) is a second link
row: the unique key includes `provider`.

### Vendor-side idempotency

| Destination                                                      | What we send                                                   | What the vendor guarantees                                                                                                                                              |
| ---------------------------------------------------------------- | -------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Seccl** `POST /portfoliotransactiongroup`                      | `externalReference = origin uid`                               | **Nothing.** A retried group is a second instruction. We therefore refuse to fire a second group once a Seccl link exists, even if the outbound row is still `pending`. |
| **SEI** Anticipated Cash Receipt                                 | `externalSystemTxnId` = contribution uid (existing lodge path) | No idempotency key. `POST /contributions/{uid}/lodge-expectation` 409s once the expectation is `instructed`.                                                            |
| **Morgan Lloyd** `cam_createcontribution` / `cam_createtransfer` | Record endpoints 409 when `ml_*_id` is already set             | Creates have no delete. Retrying a create puts a second record on the member.                                                                                           |

Failed (never accepted) rows stay retryable. Submitted and posted rows do not walk back to
`failed`: a miss recording to Morgan Lloyd must not unwind a custodian accept (or the reverse).

---

## 3. Status vocabulary

Constrained **only** on `source_system = 'graphene'`. Inbound `status` remains the custodian's
own string.

| Status      | Meaning                                                                                                                                                                                                                                                                                       |
| ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pending`   | Recorded locally. No vendor has accepted an instruction for this money.                                                                                                                                                                                                                       |
| `submitted` | The account's custodian (or Morgan Lloyd, for a scheme-admin record) accepted it. The id is the transaction's `platform.links` row. For SEI, `data.sei` holds the activity's status and state from the lookup after the lodge, refreshed every fifteen minutes while the row stays submitted. |
| `posted`    | A landed inbound credit has been matched to the same expectation. Realised, not merely instructed.                                                                                                                                                                                            |
| `failed`    | The vendor refused or was unreachable. Local records stand; the lodge/dispatch can be retried.                                                                                                                                                                                                |
| `cancelled` | The origin was cancelled while the money had not posted.                                                                                                                                                                                                                                      |

`review_status` is a separate column, only on a Graphene row that went through an action
approval. It is not a sixth vendor status. The ledger badge shows it while it is the news:
`awaiting_approval` and `rejected` replace `status`; `approved` shows only while the row is
still `pending` (released, not yet sent). Once the custodian has the money, the badge is the
vendor status again.

| `review_status`     | Meaning                                                                             |
| ------------------- | ----------------------------------------------------------------------------------- |
| null                | This send has no approval. `status` is the whole story.                             |
| `awaiting_approval` | Booked, and held. Dispatch will not send it. The approval itself is the action run. |
| `approved`          | A second person released it. The send then moves `status`.                          |
| `rejected`          | Refused. An open row is cancelled in the same write, so it cannot be sent later.    |

```
pending ──► submitted ──► posted
   │
   └──► failed
   └──► cancelled
```

`posted` and `cancelled` are terminals against a further submit. Unlinking a match reverts
`posted` → `submitted` if a vendor id was recorded, otherwise → `pending`.

---

## 4. Mapping onto the other ledgers

These vocabularies are **not the same length** and are not forced to be. A contribution tracks
the HMRC/ML journey; an expectation tracks the forecast; an instruction tracks the cash-gate
claim; the outbound transaction tracks whether we have told a custodian and whether the money
has landed.

| Outbound tx | Instruction                                    | Contribution                                 | Transfer                       | Expectation  | Meaning            |
| ----------- | ---------------------------------------------- | -------------------------------------------- | ------------------------------ | ------------ | ------------------ |
| `pending`   | `pending`                                      | `awaiting_confirmation`                      | `requested`                    | `expected`   | Local only         |
| `submitted` | `pending` (still a cash claim)                 | `awaiting_confirmation` (or later ML stages) | `submitted`                    | `instructed` | Vendor accepted    |
| `posted`    | still `pending` until a settlement path exists | `confirmed` / matched                        | `matched` via `transaction_id` | `matched`    | Feed credit joined |
| `failed`    | `pending` (retry)                              | lodge failed; contribution kept              | —                              | `failed`     | Vendor refused     |
| `cancelled` | `cancelled`                                    | `cancelled`                                  | `cancelled`                    | `cancelled`  | Local cancel       |

Notes that look like details and are not:

- **Instructions stay `pending` after a successful Seccl group.** Nothing settles an
  instruction yet; a pending row is still a claim on the cash/invested leg. Cancelling one
  whose outbound transaction is `submitted` or `posted` is refused — that would release the
  gate while the custodian still has the order.
- **`contribution.transaction_id` / `transfer.transaction_id` always point at the inbound
  credit**, never at the Graphene pending row. Matching attaches the landed movement;
  `app.outbound` is a sibling.
- **Path A (classify a credit) never writes a Graphene row.** The feed row is the transaction.
- **Classifying a booked contribution (path B) as `not_a_contribution` / `transfer_in` cancels
  the Graphene row** (`outbound.cancel_origin`), the same as `POST /contributions/{uid}/cancel`.
  Classifying back out restores it to `pending`. `cancel()` cannot run after a disposal because
  the contribution is already cancelled.
- **SEI cash disbursement is not wrapped.** A withdrawal on an SEI-custodied account stays
  `pending` with a skipped dispatch until `createcashdisbursement` and a pay-to profile exist
  in the API.
- **Morgan Lloyd does not move money.** `cam_createcontribution` / `cam_createtransfer` are
  scheme-admin records. Their success is a `morgan-lloyd` link and, if the row was still
  `pending`, a move to `submitted`.

---

## 5. Dispatch matrix

One rail, taken from the account: `accounts.custodian`, else the primary custodian link.
Create writes the pending Graphene row only. `outbound.dispatch` (instructions, from the firm
router) or `lodge_expectation` (contributions) then talks to **that** custodian — never both.
Morgan Lloyd is the scheme administrator, not a cash rail; `record-to-morgan-lloyd` /
`on_ml_recorded` is a separate step.

Best-effort: a vendor miss does not roll back the domain record.

| Origin                                        | SEI (custodian)                                        | Seccl (custodian)                                                                    | Morgan Lloyd (administrator)                   |
| --------------------------------------------- | ------------------------------------------------------ | ------------------------------------------------------------------------------------ | ---------------------------------------------- |
| Contribution                                  | ACR via `lodge_expectation` → `submitted` + `sei` link | PTG Deposit/Employer → `submitted` + `seccl` link                                    | `record-to-morgan-lloyd` → `morgan-lloyd` link |
| Transfer in                                   | none (credit is matched when it lands)                 | none (Product Transfer is a different body, not built)                               | `record-to-morgan-lloyd` → `submitted` + link  |
| Instruction `expected_in`                     | anticipated receipt → `submitted` + `sei` link         | PTG Deposit (plus Adviser Adhoc Fee in the **same** group when `adviser_fee` is set) | —                                              |
| Instruction `withdrawal` / `model_withdrawal` | BACS disbursement → `submitted` + `sei` link           | PTG Withdrawal                                                                       | SIPP cash-out is Drawdown, not this path       |
| Instruction `adhoc_fee`                       | one-time direct charge → `submitted` + `sei` link      | PTG Adviser Adhoc Fee                                                                | —                                              |
| Instruction `model_topup`                     | —                                                      | PTG Deposit + `autoInvest`                                                           | —                                              |

Unconfigured credentials, a missing account external id, or no mapping are `skipped`, not
`failed`. `failed` is a vendor refusal or transport error on a call we actually made.
