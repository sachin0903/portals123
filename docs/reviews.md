# Four-eye checks

**Status: folded into actions** (migration `0249_fold_reviews`). The `reviews` schema is gone.
A hold is an `actions.runs` row: justification, decision note, subject, and `RV-{n}` are columns
on that run. Approver roles on the action are the switch — an empty list means a second person
is not required. There is no separate Peer review settings page; that sentence sits on the
action in Firm settings → Actions. See [`docs/actions.md`](actions.md).

There is no `reviews:*` permission. Who may run an action, and who must approve it, is the
role list on that action. An empty invoke list means anyone may run it. An empty approver
list means no second person is required. The Reviews workqueue, the hold-then-apply
interceptor, notifications, and the dashboard action kind are otherwise unchanged.
`GET /reviews/policies` still returns `mode: off | four_eye` so a save form knows when to
ask for a justification. Later waves add a catalogue entry plus `apply` / `describe` /
`before` — no new engine.

A **four-eye check** is a firm-configurable hold in front of a designated staff action. The
first person still uses the same screen they use today. If the firm has four-eye on for that
action, the save asks for a **justification**, the change does **not** apply, and it lands in
the **Reviews** workqueue. Someone else who holds a role the firm picked for that action
must approve. Only then does the mutation run and the existing “it
happened” event fire — so automations, client notifications, and the event log all see the
change _after_ the check, not before.

This is **not** a task you mark done. Completing a task must never apply a bank-detail change.
Flow `review` steps (KYC/AML in a journey) and ops-only AML adjudication stay as they are.

## Why this is not “make `audit.actions` fat”

`audit.actions` is the wrong place to hold a change:

- It is an **audit of effects**, written **post-commit** and best-effort (`app/audit_actions.py`). A
  held action has not happened yet. A `202 Accepted` from a designated door is therefore **not**
  recorded as the door’s happened-event — `ActionEventsMiddleware` skips `202`.
- It is **thin on purpose**. `client.bank_account_updated` captures nothing before or after —
  bank digits are redact-class.
- Automations and notifications **tail it**. If the happened-event is written when the adviser
  clicks Save, client emails and rules fire before anyone has checked.

So: **intents are the fat, idempotent, rerunnable unit** (`actions.runs.payload` + a
registered `apply`). `audit.actions` stays the “it happened” stream and is emitted only after
apply. Reviewers see the full proposed values on the review **surface** (listing expand and
the pending card on the record). The public trail still does not store bank digits.
Each held run also has a spoken **case number** (`case_no`, shown as `RV-12`) — the
`uid` stays the route handle.

Reversibility is designed in (every command stores a `before` snapshot) but **undo is not wave
1**. Reject/cancel never applied anything. A later compensating apply can restore `before`.

## Data model (`actions` schema — migration `0249_fold_reviews`)

The hold is an `actions.runs` row. It is not `audit.attach`’d: the payload can hold bank
digits, and the DML trail must not store them. Lifecycle evidence is the thin `review.*`
action events plus who/when on the run. Policy is `actions.policies` plus
`actions.policy_roles` where `kind = 'approve'`. No approver role means off. The API still
speaks `mode: off | four_eye` and `reviewer_role_id` for the save forms.

### Policy

Per tenant, per action key, `provider` null. Missing approver roles = off.

| Column / row       | Meaning                                                           |
| ------------------ | ----------------------------------------------------------------- |
| `action_key`       | Catalogue key (`contact.bank_details_changed`, …)                 |
| `approval_enabled` | True when at least one approver role is selected                  |
| `approval_role_id` | The first approver role, snapshotted onto the run at propose time |
| `policy_roles`     | Every approver role (`kind = 'approve'`)                          |

### The held run

`actions.runs`, for a book action and for a money action waiting to be sent.

| Column                                        | Meaning                                                                                                                      |
| --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `case_no`                                     | Per-firm sequential number, displayed as `RV-{n}`                                                                            |
| `action_key`                                  | Catalogue key                                                                                                                |
| `event_type`                                  | The `audit.actions` name that fires **after** apply                                                                          |
| `subject_type` / `subject_id`                 | `contact` / `account` / `instruction` + that record’s public uid                                                             |
| `contact_id`                                  | Book-scoping handle (the client the change is about)                                                                         |
| `payload`                                     | Full apply-able command                                                                                                      |
| `before`                                      | Snapshot at propose time (conflict detection + future reverse)                                                               |
| `justification`                               | The proposer's reason, when that action requires one                                                                         |
| `approval_role_id`                            | First approver role, snapshotted                                                                                             |
| `requested_by` / `requested_at`               | Maker                                                                                                                        |
| `status`                                      | `awaiting_approval` → `succeeded` \| `rejected` \| `cancelled` \| `conflicted`. The API still says `pending` and `approved`. |
| `decided_by` / `decided_at` / `decision_note` | Checker                                                                                                                      |
| `idempotency_key`                             | Unique per tenant — propose twice = same row                                                                                 |
| `applied_at`                                  | Apply runs **once**; a second approve is a no-op                                                                             |

One open request per `(tenant_id, action_key, subject_type, subject_id)` — except
`client.details_changed`, whose one key covers five record cards: its holds are per field set
(`one_open=False` on the spec), so a pending pension-card edit does not stop the contact card
being edited. **A second edit while the case is still waiting UPDATES the case** (22 Oct 2026,
`reviews/baton.py::_amend`): the proposal is merged (a details hold keeps the fields it carried
and takes the new values over them; any other hold is replaced whole), the `before` snapshot is
taken again now, `review.requested` is recorded again with `amended: true` so the approvers hear
the case changed, and the door answers 202 with the same case number and `amended: true` (the
record's toast says "Review RV-3 updated"). The record's cards therefore stay editable while a
hold waits. Only a case already **approved and landing** refuses a further edit — a 409 that
**names the case** (`… already approved and being applied (RV-3). Let that land first.`) — and
the client record shows every pending hold on the card it will change, so the refusal is never a
mystery
(22 Oct 2026).

**RLS.** `contacts:read_all` sees the firm’s queue. Book-scoped staff see reviews on their book
plus their own proposals. Clients see nothing. Writes: authenticated may insert (propose) and
update their own pending row (cancel). Approve/reject run on the reviewer’s session — RLS
already lets firm staff touch the underlying book row; `instructions:write` is **not** required
of the checker (that is the point of the review door).

## Permissions

The queue is open to any staff member. A decision still requires the role named on the
action, and the proposer cannot approve their own. Configuring the role lists is
`actions:write` on the Actions page. Proposing still uses the action’s existing write
permission (`instructions:write` for bank details). The proposer always sees their own
pending request.

Hard rules, tested:

- Approver ≠ proposer, even if they hold the reviewer role.
- Approver must hold the policy’s role.
- Enabling four-eye **preflights**: at least one _other_ active platform member must hold
  that role, or the toggle is refused in plain language.
- One open request per `(action_key, subject)` — a second propose while it waits amends it;
  one while it is approved and landing is 409.

## Designated-action catalogue

Platform-owned, not firm-invented — same discipline as the automation trigger menu
(`app/flows/triggers.py`): a friendly key, a human label, a family, a wave. A firm never types
an event name. Declared in `app/reviews/catalogue.py`.

**Ineligible until split.** `client.updated` is one PATCH for five cards. Four-eye on “edit a
client” is not reviewable. If a firm later wants checks on name or address, those get their own
doors first (the bank-details PUT is the precedent).

**Never holdable.** Notes, messages, views/renders, builder config (flows, automations,
branding), task complete, AML (already ops-separated), drawdown SSO
(`account.drawdown_session_started` — a session, not a durable command). Role-permission and
membership-role changes stay out until we have a deadlock story (a check on “who can review”
can lock the firm out of reviewing).

**Wave 1 (wired).** Record saves whose send is one act with the save:

| Key                            | Happened event                  | Door                                    |
| ------------------------------ | ------------------------------- | --------------------------------------- |
| `contact.bank_details_changed` | `client.bank_account_updated`   | `PUT /crm/contacts/{uid}/bank-account`  |
| `account.bank_details_changed` | `account.bank_accounts_updated` | `PUT /crm/accounts/{uid}/bank-accounts` |
| `account.model_changed`        | `account.model_updated`         | `PUT /crm/accounts/{uid}/model`         |
| `account.fee_packages_changed` | `account.fee_packages_updated`  | `PUT /crm/accounts/{uid}/fee-packages`  |

None of these has a settings row of its own. Approvers on **Update client bank account**
(`client.bank`) hold the client bank-details save, approvers on **Update account bank details**
(`account.bank`) the account one, approvers on **Change model** (`model.attach`) the mandate,
and approvers on **Change fee packages** (`fee.attach`) the packages (`_SAME_ACT` in
`app/reviews/service.py` pairs each door key with its row). The doors still record the book key,
and a second person must approve before the record is written. For the model and the fee
packages the approval is also the custodian push: the door starts `model.attach` / `fee.attach`
after an immediate save, and after a held save the worker's terminal hook starts the same run
once the apply has landed (`GrapheneHooks._push_after_review`, started `approved` so the gate is
not raised twice). Clearing a model or every package is a local removal and pushes nothing.

**Retired (22 Oct 2026).** `instruction.raised`, `instruction.cancelled`,
`account.regular_movements_changed` and `account.beneficiaries_changed` left the catalogue
(migration `0270_retire_book_reviews` closes any open hold and drops their policies). A money
instruction is held at its **send** — approvers on `transaction.payment` / `withdrawal` / `fee`
hold the run that sends it — so a review of the booking made a second person approve the same
payment twice; a cancel is immediate; a standing schedule is sent through `payment.regular` /
`withdrawal.regular`; a nomination is sent through `beneficiary.replace`. Settled history still
carries the old keys, named from `RETIRED` in `app/reviews/catalogue.py`. Still catalogue only:
`contribution.created` / `cancelled` / `categorised` / `expectation_lodged`, `transfer.created`,
`account.beneficiary_added` / `removed`.

**Approve refusals.** The registered `apply` is the live door's own service call, so it may
refuse (`ValueError` → 409 "This change can no longer be applied: …", `LookupError` → 404). Those
used to escape as 500s on Approve — a payment held with Expected On = today and approved tomorrow
was the common case — with the reason lost. The request stays pending; the proposer resubmits.

**Wave 3 (catalogue only).** `client.archived` / `restored`, `client.adviser_assigned` /
`unassigned`, `client.portal_user_linked` / `unlinked` / `portal_invited`,
`client.vulnerability_updated`.

The settings page lists **wired** actions only. Each wired entry registers `apply`,
`capture_before`, `conflicts`, and `describe` in `app/reviews/apply.py`.

## Handler seam

Each designated endpoint opts in through `maybe_hold_for_review`:

- Policy off → call `apply`, return 2xx as today; middleware emits the happened event.
- Policy on, and **Require justification or reason** is on, with no justification → 422 on the
  `justification` field.
- Policy on → insert `actions.runs`, emit thin `review.requested`, return **202**
  `{ status: "pending_review", review_uid, case_no }`. A reason is stored when one was given.

`justification` is an optional body field on the existing write models (Pydantic default
ignores unknown keys, so old clients that omit it keep working). When four-eye is off, or the
reason switch is off, the field is ignored. Cancel-instruction accepts an optional JSON body
for the same reason. The switch is `actions.policies.require_justification`, one per action.
Approver roles hold the change; the switch is only the form's why box. It is off until a firm
turns it on.

Approve (same transaction as the decision): conflict-check `before` vs live row → `apply` →
stamp `applied_at` → emit `review.approved` (the approve route) **and** the action’s normal
event type with the **proposer** as actor, so automations such as
`contact.bank_details_changed` fire for the first time. A second approve is a no-op.

Reject requires a reason; cancel is the proposer’s. Neither applies. A stale `before` marks
the request `conflicted` (409) and writes `review.conflicted`.

## The kinds of hold (22 Oct 2026)

Four things can rest at an approval gate. They are one queue and one set of rules, and each
is listed here with how it is held, who is told, how it is decided and where it shows, because
until 22 Oct 2026 only the first kind told anyone anything.

| Kind                                                                                           | Held by                                                                                                                                   | Told                                                                                                                                                                                                                                    | Decided through                                                                                                                                               |
| ---------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A book review** — bank, model, fee packages, vulnerability, client details                   | The door, via `reviews/baton.hold` → `engine.start(approval=…)`, `meta.review`                                                            | `review.requested` to the approver roles at the hold; `review.approved` / `rejected` / `conflicted` to the proposer                                                                                                                     | `/reviews/{uid}/approve` · `reject` (its apply and lifecycle live there); the worker applies; `GrapheneHooks._settle_review` writes the happened-event        |
| **A held provider action** — a payment, withdrawal, fee or transfer an approver role holds     | The money door, via `service.start` → `engine.start(approval=…)`                                                                          | `GrapheneHooks.on_event` mirrors the engine's `run.held` to `review.requested` (every approver role, `reviewer_role_slugs`) and `run.approved` / `run.rejected` to `review.approved` / `rejected`. New: before this the hold was silent | `POST /actions/runs/{uid}/decision`; a rejection needs a note (`service.decide`); the worker sends; a refusal parks on Actions                                |
| **A gated child of a composite** — a firm that gates `account.create` holds onboarding's child | `GrapheneHooks.before_child` returning an `Approval`; the child gets its own case number and gate; the engine now calls `on_event` for it | As a provider action                                                                                                                                                                                                                    | The child's own uid on the decision route; its root reads `awaiting_approval` on Runs and names it (`approval.held_uid`); it lists in Reviews as its own case |
| **A manual action** — a policy marked `manual`                                                 | `service.start` with `meta.manual`; the same gate                                                                                         | As a provider action                                                                                                                                                                                                                    | Accepting completes the run by hand; never runs the body. Listed on Approvals only, not Reviews                                                               |

The record-holding rows (`client.bank`, `account.bank`, `model.attach`, `fee.attach`) are the
first kind: approvers on them hold the record save, and the custodian push follows the approval
`approved=True`, never gated twice; `before_child` skips gating those as children.

Common to all four: `eligible_approvers` — how many people other than the requester hold an
approver role — travels on every row and detail (`app/actions/approvals.py::eligible_sql`), so a
case nobody can decide is a state the screens say (a red role chip, a counter, "Nobody else
holds Compliance") rather than an absence of buttons; and `POST /actions/runs/{uid}/remind`
re-sends the approvers' notice (the requester, or anyone with `actions:read`; once an hour,
recorded as a `run.reminded` engine event the detail reads back).

## Surfaces

- **Actions** (`/actions`) — Firm settings → People & Access, gated `actions:write`.
  Book actions have no enable switch. The approver multi-select is the four-eye control,
  and the sentence under it is the old peer-review description. Leave empty if no approval
  flow is required. **Require justification or reason** is a separate switch on each action:
  on, the matching form asks why; off, it does not. `/peer-review` redirects here.
- **Reviews** (`/reviews`) — Work group, open to any staff member. The same listing frame as
  the rest of the book (glass card, `ListingToolbar`, scrolling `DataTable`). **It is the
  firm's whole approval queue** (22 Oct 2026): every run held for a second person has a case
  number and lands here — the book reviews below _and_ a provider action an approver role
  holds (a payment, a withdrawal, a transfer, an account opening). Nothing that did not need
  approval is listed; a run that went straight out has no case. Each wired book action has
  its own body — this is not one now-vs-proposed table — and a provider action, which has no
  before/after, shows the input its door recorded (`meta.input`: the instruction's kind and
  amount, every line of the transfer form) as facts under "Waiting for approval before this is
  sent." Approve/reject on a provider action post to the run's own decision route
  (`source: 'action'` on the row); a book review still goes through `/reviews/{uid}/approve`.
  The Action filter is built from the loaded queue, not a hard-coded list. The page is served
  waiting rows first (`GET /reviews?scope=all&limit=` up to 500, default 200), so a settled
  provider action never pushes a waiting book review off the end. A row is **pending** only
  while its run rests at its gate; once approved it reads approved whatever the run does next
  (a provider refusal is Actions' concern), and a book review whose apply failed reads
  conflicted. Approve/reject on a provider action post to the run's own decision route; the
  `/reviews/{uid}/approve` family is the book reviews' own and answers 404 for a run. Firm
  settings → Actions keeps its Approvals tab as the ops view of the same gates. Client is always
  named; account is named when the hold targets one. **Default is the queue**: reviews
  still waiting for a decision, and nothing else. Approved, rejected, cancelled and
  conflicted rows are settled and cannot be acted on here. The resting status row is
  named "Waiting for a decision"; each settled status and "All statuses"
  (`?status=all`) are explicit choices. FilterBar narrows by submitter, action or status. The first column
  is the case (`RV-12`). `/reviews/[uid]` redirects to `/reviews?open=`, and an opened
  case is shown even when the filters would hide it, so an approval notification's link
  still lands on its row. The dashboard still reads `GET /reviews?scope=inbox` for
  “waiting for me”.

  | Action                                                          | What the reviewer sees                                                                                                                                                                          |
  | --------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
  | `contact.bank_details_changed` / `account.bank_details_changed` | Field table: Now vs Proposed (digits in full)                                                                                                                                                   |
  | `client.details_changed`                                        | Field table: Now vs Proposed for the fields the card sent, labelled and formatted as the record prints them (dates, countries by name, closed vocabularies, one-line addresses, money, W-9/W-8) |
  | `client.vulnerability_updated`                                  | The card's rows — Vulnerable, Drivers, Characteristics, Duration, Notes — now vs proposed                                                                                                       |
  | `account.model_changed`                                         | One-row Model diff (current mandate → proposed)                                                                                                                                                 |
  | `account.fee_packages_changed`                                  | One-row Fee packages diff, by name (the snapshot carries `names`; an older hold counts)                                                                                                         |
  | any other book key                                              | One-row Change diff from the describer's two sentences                                                                                                                                          |
  | `transaction.*`, `account.*`, … (a held provider action)        | Facts from the recorded input: what was asked, no now/proposed                                                                                                                                  |

  The Change column names the kind and what moved (`Client details · First name, Email`,
  `Vulnerability · Not vulnerable → Vulnerable`, `Fee packages · Platform fee → Platform fee,
Adviser fee`, `Cautious → Balanced`), and the expanded row's rail says who submitted it and
  when, why, the approver role, and — once settled — who decided it, when, and their note.

  **The seats (22 Oct 2026).** The reader takes a seat on the queue — For me (pending cases the
  reader may decide) · Submitted by me · All waiting · Settled — with counts, in the URL as
  `?seat=`; with none, the queue opens on For me when it has anything, else on All waiting. The
  columns are Case · Change (what moved, then where and why on a second line) · Client ·
  Submitted by · Waiting (amber past a day, red past three, as an inset on the row) · Decides
  ("You", the roles, or "Nobody can"); Settled swaps the last two for Decided and Outcome, and
  the Outcome filter lives only there. The expanded row is the decision: the body with its
  unchanged rows folded ("Show 5 unchanged"), the client named by the row and not again, the
  rail Submitted · Why · Decides, then Reject (asks why) · Approve, whose dialog says what
  approving does for THAT kind of hold (`reviewApproveCopy`: a payment is sent, a bank change
  moves future withdrawals). After a decision the next case for the reader opens
  (`nextReviewFor`); J/K move, A/R decide. A case nobody can decide says so and what to do; the
  submitter of a waiting case can nudge its approvers (Remind) or withdraw it. Rows carry
  `approver_roles` (every role) and `eligible_approvers`; a gated child of a composite lists as
  its own case.

- **Client / account record** — a pending hold on that record is said ON the card it will
  change (22 Oct 2026): `ReviewHoldBanner` under the card's title names the case, what it
  touches, who raised it and how long ago, and whose move it is; the register still says what
  the record holds now; the proposal follows it, now vs proposed. A details hold is placed by
  its fields (`DETAIL_CARD_FIELDS` → contact · personal · pension · employer · compliance); one on
  a field no card owns keeps the older block above the cards. The vulnerability card used to
  change nothing until the change applied and the record turned red: the same banner now sits
  in the hero as well as on the card, so the record reads as modified while it waits. The hold
  body is the same action-specific body (case, who, why, then the change), not a tinted
  sentence over empty dashes. The
  proposer can cancel; anyone can open the case. The matching save asks for a justification
  when that action’s reason switch is on: the account’s own bank card and Change model, and
  the money drawer for a cash instruction (the reason switch on the money action that sends
  it). Those acts sit in the account heading’s Actions menu with Payment / Withdraw / Ad hoc
  fee / Transfer. A regular schedule, a cancel and a beneficiary nomination never ask why —
  they are not four-eye doors any more. The Withdraw drawer's in-place bank editor goes
  through `account.bank_details_changed` like the overview pencil.
- **Dashboard** — the "Needs you" rail carries a review awaiting the signed-in reviewer as its
  own row: a stamp icon, the case and the change (`RV-1042 · Bank details`), and who it concerns.
  It sits with the reviewer's own onboarding journeys, which wear a route icon; a small dot on the
  right — an unread marker, not a chip — says the next move is theirs (every review has one; a
  journey has one while a staff step is open, none while it is with the client). The rail used to
  merge document requests in too; those are gone from it, since the requests queue sits behind
  `documents:show` and a request row could only be chased. Below the list, the reader's own notes
  checklist (`platform.staff_notes`, `/me/notes` — stored centrally, read by the owner alone).
- **Notifications** — category `reviews`. `review.requested` → members of every approver role
  (`reviewer_role_slugs`, each person once, never the proposer; older events carry one
  `reviewer_role_slug`), for a book review and a held provider action alike, and again as a
  reminder; `review.approved` / `rejected` / `conflicted` → proposer. The notice names a
  provider action from the actions catalogue ("Payment"). The "Needs you" rail says how long
  each case has waited.

- **Firm settings → Actions → Approvals** — the ops seat on the same gates: the runs workbench
  locked to `awaiting_approval` (see "The approvals seat" in `docs/baton.md`).

## Event contract

| Property           | Where it lives                                                                                                  |
| ------------------ | --------------------------------------------------------------------------------------------------------------- |
| Fat / rerunnable   | `actions.runs.payload` + registered `apply`                                                                     |
| Idempotent propose | unique `idempotency_key`                                                                                        |
| Idempotent approve | `applied_at` once                                                                                               |
| Granular           | catalogue keys only; never `client.updated`                                                                     |
| Reversible later   | `before` snapshot                                                                                               |
| Happened-stream    | existing thin `audit.actions` **after** apply                                                                   |
| Review trail       | `review.requested` / `approved` / `rejected` / `cancelled` / `conflicted` — who/when/action/subject, no secrets |

Do not add bank fields to `SUBJECT_FIELDS` / `PREVIOUS_FIELDS`.

## Key files

| Path                                            | Role                                                   |
| ----------------------------------------------- | ------------------------------------------------------ |
| `apps/api/app/reviews/catalogue.py`             | Designated-action menu                                 |
| `apps/api/app/reviews/apply.py`                 | Wave-1 apply / before / describe                       |
| `apps/api/app/reviews/service.py`               | Policy, propose, decide                                |
| `apps/api/app/reviews/hold.py`                  | Endpoint seam                                          |
| `apps/api/app/routers/reviews.py`               | REST                                                   |
| `apps/api/migrations/sql/0249_fold_reviews.sql` | Moves the hold onto `actions.runs` and drops `reviews` |
| `pkgs/ui/src/lib/components/book/reviews/`      | Inbox + detail                                         |
| `apps/platform/src/routes/reviews/`             | Workqueue                                              |
| `apps/platform/src/routes/actions/`             | Approver roles                                         |
