# Domain actions

> **The engine is now baton** (`docs/baton.md`), not the table-queue described below. This page
> still explains the catalogue concept (a domain action, firm policy, `READS`), but the planner/
> steps model is historical. The money actions live in the `transaction.*` namespace, and
> `transaction.payment` is the one money-in action — a payment and an ISA/SIPP contribution are the
> same call (see "The money namespace" in `docs/baton.md`). The runs screens — the listing, the
> run detail as a tree, retry and cancel — are described under "The runs screens" there.

> 22 Oct 2026: the book rows are no longer listed in settings on their own. "Change model"
> (`model.attach`) and "Change fee packages" (`fee.attach`) hold the record save and push to the
> custodian; the instruction, regular-movements and beneficiary-nomination reviews were retired
> (`docs/reviews.md`, migration `0270_retire_book_reviews`).

**Status: the registry is in place.** Schema `actions` (migration `0215_actions`, approval roles in
`0216_action_approvers`), permissions
`actions:read` / `actions:write`, the catalogue in `app/actions/catalogue.py`, firm settings at
`/actions`, and a table-queue worker over `actions.steps`. Reviews, `audit.actions`, and
`audit.api_log` keep their writers. This schema is shaped so a later swap is a copy, not a
redesign.

A **domain action** is what an adviser asks for: `account.create`, `transaction.payment`,
`client.details`. The adviser does not pick a Seccl or SEI endpoint. A planner
(`app/actions/plan.py`) expands the action into ordered **steps**, each step one provider
operation. Which providers appear comes from the book: `platform.accounts.custodian`,
`platform.product_platforms` (custodian versus scheme administrator), and
`platform.tenant_platforms`. An account with no custodian has no provider steps.
Enabling SEI writes `role='custodian'` on GIA, ISA, JISA and SIPP so that choice
exists before any account is already at SEI; a mapping with a null role does not count.

A SIPP payment is one action. The planner emits the custodian's cash call and, when the product
names Morgan Lloyd as scheme administrator, the contribution record. A GIA on Seccl emits only
the Seccl step. A book change such as bank details is the same kind of action with **no steps**:
its apply handler mutates our row. That hook (`register_book_apply`) is how this later replaces
reviews. The reviews engine and its inbox are unchanged.

The Graphene row and the provider call stay two steps of one action, in that order. The client
record, the account, the journey, and the account-page payment write the row. The action reads
it and sends the provider call. It does not open a second form, and the settings page does not
ask for the amount, the address, or the bank details again — those are already on the row the
trigger names. `READS` in the catalogue is that sentence, shown on the action. A run starts
on the screen that already holds the client and the form. The actions page lists those runs.

The account page and the client portal book the instruction, then start the matching action
with that instruction uid (`transaction.payment`, `transaction.withdrawal`, or `transaction.fee`). The
run's idempotency key is the instruction uid, so a second click is the same run. That lodge
is the Seccl portfolio-transaction group when the custodian is Seccl. The step records that
call — the body posted, and the id Seccl returned — not the book's own status report.
`transaction.payment`
sends the pending instruction the run names. When the run does not name one, it sends the
payment only when exactly one is pending, and refuses when several are. It does not book a
second one, and it does not send the fee sibling of a payment as an ad hoc fee. An
instruction already lodged stays lodged: the sent-id is the transaction's `platform.links`
row, and a later run does not send it again. A missing policy still lets the door send, and
the door records the run. A policy with `enabled = false` refuses the send and the screen
hides that control. Approval on the policy holds the run, and that is the only hold on an
instruction — the `instruction.raised` / `instruction.cancelled` reviews were retired on 22 Oct
2026 (`docs/reviews.md`). Bank, model and fee packages stay on the reviews catalogue as the
keys their doors hold on. Money stays `manual`: a person asks for another try, and that try checks the
transaction link first. A pension payment stores the contribution category the
adviser chose — personal, employer, or third party — and that category selects the firm's
SEI treatment code. A payment with no category is not sent. On SEI the send is an
anticipated cash receipt. An ad hoc fee is a one-time direct charge on the account,
not a second receipt. The portfolio id is the account's principal portfolio. The firm's
description and treatment codes are required, and they are not filled from the reference list.
When Morgan Lloyd administers the scheme, that same run records the contribution or
transfer booked on the account. The workbench can still name that row by its uid.
A transfer already raised on the account is sent by `transaction.transfer`. Raising one
(`POST /transfers`) and the workbench buttons that place it at Seccl or lodge it at SEI
run that action; they do not call the custodian beside it. Seccl places the product
transfer. SEI cash is an anticipated receipt. SEI in-specie is a transfer-in. The status
written on `platform.transfers` is the status the transfers workbench shows, and a later
run does not place that instruction again. A SEI SIPP is recorded at Morgan Lloyd by
`transfer.record` on the same run. The SEI step waits until Morgan Lloyd has completed
the transfer. The workbench then runs `transaction.transfer` again, on its own key, and that
step lodges the cash receipt or — when the assets were already asked for — a free
receipt. It does not send a second transfer-in.

## Settings

Firm settings, People & Access. One row per action, including the book actions, created on
first save. A missing row means the action is on. Switching it off stops new runs. A book
action has no enable switch —
the door always exists. The bank saves are **Update client bank account** and **Update
account bank details**, not a second row each: their approvers hold the record even when the
custodian send is switched off.

The row is not per source. Seccl and SEI are not choices on this page. Enablement, who may run
it, and who may approve it belong to the action. The planner still decides the provider from the
account when the action runs. **IO** is Intelliflo, source `io`, nothing wired, and it is not a
member of `platform.provider_code`.

> 22 Oct 2026: a held provider action tells its approvers and its requester (it was silent), a
> rejection needs a reason, the Approvals tab is the runs workbench locked to the gate, and a
> gated child of a composite reads as awaiting approval on its root — "The kinds of hold" in
> `docs/reviews.md` and "The approvals seat" in `docs/baton.md`.

Approver roles are the four-eye switch. An empty list means a second person is not required.
`actions.policy_roles` with `kind = 'approve'` is who may say yes, and more than one role can.
`kind = 'invoke'` is who may start a provider action. An empty list means anyone may run
it. Book actions do not use it: the door keeps its own permission. A held run stores the
justification, the decision note, and the spoken case number `RV-{n}`. The proposer cannot
approve their own run. The roles are snapshotted onto `actions.run_approvers` when the run
is held.

Book actions keep their existing permission (`instructions:write` and the rest) until the swap.
The activity page lists recent runs. A plan lists the calls its children made —
the request and the response — on the plan itself. Money actions are still started from the
contribution or transfer.

## Tables

| Table                   | What it is                                                                                                                                                                                      |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `actions.policies`      | Sparse enablement. Unique `(tenant_id, action_key, provider)` with nulls not distinct. The firm page writes `provider` null: one policy per action.                                             |
| `actions.policy_roles`  | Who may invoke (`kind = 'invoke'`) and who may approve (`kind = 'approve'`). A foreign key to `auth.roles`.                                                                                     |
| `actions.run_approvers` | The approval roles snapshotted onto a held run.                                                                                                                                                 |
| `actions.runs`          | The job, and the held command when approval is on. Justification, decision note, subject and `RV-{n}` live on the row. A money row copies the stage onto `platform.transactions.review_status`. |
| `actions.subjects`      | The client, account, model, or instruction the run is about. Partial unique index while `blocking`.                                                                                             |
| `actions.steps`         | One intended provider call, and the queue. `SELECT … FOR UPDATE SKIP LOCKED`. Not PGMQ.                                                                                                         |
| `actions.http_requests` | One row per attempt. Append-only. Column-compatible with `audit.api_log` so the sync firehose can move later.                                                                                   |
| `actions.events`        | The thin "it happened" stream. Append-only, monotonic `id`. No request bodies and no bank digits.                                                                                               |

Payloads that can hold bank digits (`runs.payload`, `runs.before`) are not `audit.attach`'d.

`before` is the snapshot a later reverse, and conflict detection, will use. `applied_at` still
means the apply ran once. Catalogue flag `one_open_per_subject` is the reviews rule: one open
hold per action and subject, enforced by the partial unique index on `actions.subjects`.

Status moves `awaiting_approval` → `queued` → `running` → `succeeded`, or `rejected`,
`cancelled`, `conflicted`, `failed`, `needs_attention`. Approval off skips straight to `queued`,
or straight to apply when the action has no steps.

## Retry

The worker retries the **step**, not a blind HTTP replay. The class is the one already on the
provider operation:

- `safe` — repeat, up to three attempts.
- `guarded` — check `platform.links` first. A create that landed and lost its answer is not sent again.
- `manual` — one attempt, then `needs_attention`. Money is manual. A person can ask for another attempt.

A plan drains with best effort, so a step that cannot be sent is recorded and marked skipped.
When that skip carries the refusal, the activity page can send it again. A step that was never
wired has no refusal and stays skipped.

A success still drops the response body except where the body minted an id. Recording a failure
does not fail the caller by itself; a door re-raises the handler's exception so the existing
status codes (422, 502, 503) stay put. `client.sync` is the exception to the guarded skip: the
handler is itself the resume, and a link already on the contact must not stop the schemes that
are still missing.

## Onboarding

`client.setup` is a plan for the whole application, not one account. The parent lists every
person and the accounts they hold: a parent can own a GIA, own a joint GIA, and be the
registered contact on a child's JISA, while the other parent owns their own GIA and the same
joint GIA, and the child owns the JISA. A second account on that application joins the same
plan. A Seccl application creates each person once (`POST /client`, under the lead
adviser's Seccl node — the firm's only enabled node when the adviser has not chosen),
then each wrapper (`POST /account`) for a GIA, ISA, or JISA. A payment for a client
already at Seccl does not send a node. The model id goes on that create
(`wrapperDetail.assetAllocationId`); Seccl does not accept it on a later update. A Seccl
pension is sent when the product's `account_open_reference` names a scheme product, and
refused when it does not. A later update sends the email, vulnerability, and bank details
Seccl's client update accepts. A standing payment or withdrawal is `recurringPayments` on
the account, keeping the other direction. An adviser fee is `feeStructures.adviserFeeId`.
An illustration is `POST /illustration/product` for the client who already exists. A one-off
payment, withdrawal, transfer, or ad-hoc fee sends the row already booked on the account.
Drawdown is refused: the five advice fields Seccl requires are not on the book. A buy or
sell is refused until the book holds an order. A SEI application enrols every client and every
account in one `POST /accountservice/accounts`. Joint owners go on the joint account; the JISA's
owner is the child and the registered contact is named on that account, not as a second owner.
When the client's adviser has a DSR and that team is already the one on the firm's grant,
that team is the one sent. A team the book has never granted is not substituted, and a
bare team id is left off the call. The call waits until the
firm has configured its template, classification, and org, and a joint account also needs
the firm's joint ownership type. A servicing relationship is a type plus a team, sent when
the book has exactly one grant for that side. A firm missing from the newest drop is read
from its own latest link file. An empty list is left off the call.
The response is one process handle
(`businessKey`), stored on each account in the batch as `platform.links` link type `enrollment`.
It is not an account number, and it is not written as the primary link. When the
process then returns account numbers, each is read back and stored on the account of that
name — the identifier list is not in the order the request was sent. The status is read until the process settles: a process that comes back
`Validation Failed` is a refusal, not an opened account, and the party is not recorded
until that read has passed. That step finishes when the client and the account numbers
are stored. A minted number is not an account SEI will take a pay-to for: the status
is still Work in Progress, including WIP Submitted, and the pay-to is refused
(field 1193) until the account is Open. `account.pay_to` is the next step. It reads
the account again, every five seconds for about two minutes,
and creates one profile from the bank details already on file once the status is Open.
No bank details skips it. If the account is still work in progress when that wait ends,
the failure stays on this step. A retry polls the same account rather than opening
another. `account.currencies` runs after the pay-to. A SIPP uses the same account type as a GIA, 157 Investment Account. When
the firm has exactly one classification named Pension, that is the SIPP's classification, and
the one subclassification named Pension - HMRC Approved - SIPP is the SIPP's subclassification.
The enrolment template is the product's. Country of birth, citizenship and the National
Insurance number already on the client go on the new client. The teams already chosen are the
grants: each goes on the client under the client relationship and on the account under the
account relationship. A custody's teams are added to the firm's; they are not sent instead of
them. An account employee relationship, such as Investment Decision Maker, is the employee
the book already uses for it. The journey's adviser is that employee only when they already
hold the relationship. An employee of another SEI firm is not sent. A plus-address
is sent as the mailbox before the `+`, which is the address the
enrolment BPM accepts. Morgan Lloyd, when it administers the scheme, is the existing client sync. Payment,
model, and fee children appear only when the application captured them. `model.attach`
for SEI assigns the book's model to the principal portfolio once the account is Open,
using the variant for the model's published type. A model with no published tolerance
is not sent. An opening cash amount
on the application's payments is booked as an instruction and then sent; a transfer is not
created from the journey blob. Parent status is the aggregate. A failed child retries alone.
`setup_account` enqueues that plan and waits on the parent when the run is held or needs a
person. While that parent is awaiting approval the journey does not offer Record hand-off
again; someone who holds an approver role approves the plan from the step, and that one
decision releases every setup step that recorded the run. On the happy path it drains inline
and the journey completes. A step we cannot send is
skipped on that drain, so a seeded walkthrough is not blocked, and the attempt is still on the run.

## Fields that do not map onto a call

Each of these is a real action or a real field on the book. The call is refused, or only part of
the field is sent, and the attempt says why.

| What the book holds                                                                     | What can be sent                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | What to do                                                                                                                                                                                                                                                                                                   |
| --------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Client name, address, date of birth, nationality, NI number after a Seccl client exists | Seccl's client update accepts email, bank details, and vulnerability. The rest is create-time.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | Keep them on `POST /client`. A later address change needs a Seccl-confirmed update, not a guessed patch.                                                                                                                                                                                                     |
| Pension details, employment, source of wealth, tax relief                               | Seccl's individual takes employment, marital status, retirement age, and the employer. Flexi-access and primary or enhanced protection go on the pension client product (`PATCH /clientproduct`). A client has one client product per wrapper. Opening the account reads `wrapperDetail.clientProductId` and stores it on the client, and the Seccl book pipeline writes the same link from each synced account, updating it when the id changes. Source of wealth is a trust stakeholder field. Tax relief is not on the individual. Lump-sum allowance amounts are not on the book. Fixed and individual protection do not name the tax year, and certificate references are not held.                                                                                                                                                   | Send the client fields on create and on `client.circumstances`, and the client-product fields when a pension is open. Marital status on the book is Single, Married, Divorced or Widowed, which is Seccl's list. Employer bank details are not on the book, so the employer block goes without them.         |
| Bank account on the client, at Morgan Lloyd                                             | The mapped Dynamics contact has no bank block.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | Confirm the scheme's bank field before a patch. The Graphene account stays the record until then.                                                                                                                                                                                                            |
| Bank account on the account, at Seccl                                                   | The account update has no bank block. The bank Seccl takes is the client's.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | `client.bank` sends it. An account-level verification is a status this record does not hold.                                                                                                                                                                                                                 |
| Bank account on the client, at SEI                                                      | A pay-to profile on the account (`create_pay_to_recipient`), which returns a profile id.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | `account.pay_to` creates the profile from the client's bank details once the account is Open, and stores `provider_ref` as the returned profile id. The nightly PaidTo sync still copies profiles SEI already holds. Saving a bank account later does not create another one.                                |
| Account name after SEI enrolment                                                        | Set on the enrolment. No later rename is wrapped.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | Change it at enrolment, or leave the SEI name as enrolled.                                                                                                                                                                                                                                                   |
| SEI enrolment itself                                                                    | One call. The product's enrolment template is the account template. The client template is the one chosen for that client's type on the SEI platform connection. An individual and an organisation are separate; a firm with exactly one client template is still filled from that for both, and a single template saved before the split is used for both until the two are set. An organisation is client type 6, with its name and its subtype: company is Corporations (4), trust is Trust (14), charity is Charity (2). The Individual account classification and the servicing relationship are this firm's own catalogue values when the firm has exactly one of each. The account org is the onshore custody org on the SEI settings. A FATCA status of `w9`, `w8`, `w8_cure`, or `not_certified` is still required on the client. | Do not copy another firm's ids. `other_individual` and `other_entity` are ambiguous codes and are refused. A SIPP is account type 157, the same Investment Account as a GIA; the product's enrolment template is what distinguishes it.                                                                      |
| SEI principal portfolio                                                                 | SEI requires one. The book has no portfolio row at enrolment time.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | The call sends `{account name} Capital` and keeps that name on the attempt. Replace it when enrolment status returns the real portfolio id.                                                                                                                                                                  |
| SEI title, marital status, gender                                                       | Text on our side, codes on theirs.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | Profile update may send gender and marital status as text; SEI can accept the call and warn the field off. Enrolment sends the account's investment capacity as SEI's own codes: 3 Discretionary, 4 Advisory, 5 Execution Only. It does not guess a title id.                                                |
| Opening cash on the application                                                         | Seccl takes a portfolio transaction group for a booked instruction. The journey stores `data.initial_cash_deposit` (older accounts: `payments.initial_cash_deposit`) and does not book one.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | The one-off payment action books `expected_in` from that amount, then sends it. A second run finds the row and does not book another. The account must already be one that can take an instruction.                                                                                                          |
| Regular payment or withdrawal                                                           | Seccl: `recurringPayments`, a day of the month, replacing the whole array, so the other direction is kept. SEI: a recurring processing schedule, not wrapped here. Morgan Lloyd: a frequency on the contribution, and `platform.mandates` does not exist.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | Monthly Seccl standings are sent from the jsonb. Other frequencies are refused. Do not invent a mandate table for the other two.                                                                                                                                                                             |
| Transfer captured on the journey                                                        | Seccl's product transfer needs the ceding provider's address, the transfer basis, and, where they apply, safeguarded-rights and crystallisation answers. Account details records safeguarded rights and the FCA reference on the account transfer. Crystallisation and the ceding address are still collected by the raise drawer. SEI needs the same raised row: the ceding scheme, and the firm's transfer description and treatment codes.                                                                                                                                                                                                                                                                                                                                                                                              | The journey should raise the transfer the way the account drawer does (`POST /transfers`) before setup. Setup sends a transfer that was raised, through the same instruction the workbench uses, and the workbench status is that row. It does not invent one. A SEI SIPP is recorded at Morgan Lloyd first. |
| Drawdown                                                                                | Seccl crystallisation needs five advice fields the book does not hold.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | A crystallisation record, not invented flags.                                                                                                                                                                                                                                                                |
| Buy or sell                                                                             | No order row.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | An order on the account before `trade.place`.                                                                                                                                                                                                                                                                |
| Remove a model                                                                          | Would rebalance holdings, and there is no cash model to rebalance to.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | The refusal stays.                                                                                                                                                                                                                                                                                           |
| Illustration adviser                                                                    | Seccl can take `adviserInfo.FCANumber`. The firm does not hold one.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | Omit the adviser block until a firm FCA reference is in tenant config.                                                                                                                                                                                                                                       |
| AML and KYC ticks                                                                       | Not a custodian call.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | Stay read-only. They are not sent.                                                                                                                                                                                                                                                                           |

Morgan Lloyd scheme create is the same sync: adviser, contact, scheme (`cam_createscheme`), policy, and beneficiaries. The field-security refusal from 09 Aug 2026 was lifted on 05 Sep 2026; the live suite has created schemes with that call since. A scheme still needs the product's scheme type and SIPP type, and the contact still has to be attached to the adviser firm.

The Sync, lodge, and Morgan Lloyd record doors call the same executor. Prechecks (404, 409, 422)
stay on the route, before the run is created. Approval off — no policy row — behaves as those
doors did before.

## What stays where it is

- The Reviews inbox is the queue for a held run (`actions.runs` with a case number). Approving
  a book action applies it; approving a money action sends it. The ledger shows the same stage
  on the transaction (`review_status`). Configuration is the approver roles on Actions.
  `GET /reviews/policies` still answers `mode: off | four_eye`, plus `require_justification`,
  so a save form asks for a reason only when that switch is on. A payment, withdrawal or fee
  reads the same switch from `GET /firm/money-actions`, the read the send controls already use.
- The request middleware, automations, and notifications keep writing and reading `audit.actions`.
  `actions.events` does not share that id sequence. A terminal success writes the catalogue
  `event_type` (the name a notification rule already matches) plus `action.succeeded`.
- `audit.api_log` remains the sync firehose. `actions.http_requests` is the attempt log for a
  step. The columns match so the move is a copy.
- Flow `review` steps and AML adjudication stay out of this, as they are today.
- Persisted flow JSON keeps `kind: "action"`. Those are flow effects (`app/flows/effects.py`),
  not domain actions.

The word **action** in new code means a domain action. The audit stream lives in
`app/audit_actions.py`. The flow-effect executor lives in `app/flows/effects.py`. The reviews
apply registry lives in `app/reviews/apply.py`.
