# The legacy onboarding flow, in full

Read from `platform-main/` (the previous-generation Next.js app) in August 2026, function by function,
because the new engine's onboarding seed is a port of it and every argument about "who does what,
when" was being had from memory. This is the reference: what the old system actually did, with the
file it did it in. Where the new app deliberately differs, that is called out — a divergence is a
decision, and an undocumented one is indistinguishable from a bug.

`docs/flows.md` describes the new engine. This describes what it was modelled on.

## 1. The one-sentence version

**The adviser does everything; the client reviews it and consents twice.** In the whole client-facing
journey there are exactly two writes: an identity verification and a terms acceptance. Every
fact-find field is typed in by the adviser before the client is ever emailed.

## 2. The actors, and the handoff between them

| Phase                 | Who           | Where                                                                                                                                                                                             |
| --------------------- | ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Build the application | Adviser       | `components/applications/application-form.tsx` — a 5,555-line multi-step **Sheet**, not a page. Steps: `portfolio → organisations → applicants → accounts → identity → terms → complete` (`:267`) |
| **Declare**           | Adviser       | A single checkbox `#adviser-declaration` on the wizard's own terms step (`:5474-5490`)                                                                                                            |
| Review + consent      | Client        | `app/a/[applicationId]/[applicantId]/page.tsx` — ONE page, steps by `?step=` query param                                                                                                          |
| Adjudicate            | Adviser / ops | The applications table's row actions                                                                                                                                                              |

The declaration is the entire handoff. `updateAdviserDeclaration`
(`lib/services/application-actions.ts:44-66`) sets `applications.adviser_declaration/_on/_by`, and
that is the **sole** trigger for `draft → with_client`, which emits `application.ready`, which is the
email carrying the client's link. Nothing else opens the door.

### What the new app preserves

The new adviser fact-find opens with the applicable Applicant/Organisation details form. Once that
is submitted, every other applicable adviser form is ready together and the form page provides
Previous/Next navigation. A completed form can be amended until the handoff. “Declare and send to
the client” requires the adviser to attest that the information is correct, complete and not
misleading; only then are `adviser_declaration/_on/_by` stamped, the forms locked, and eligible
clients provisioned through a WorkOS first-access **invitation** (`invitation_token`), not a password
reset.

## 3. The state machine

`pkgs/db/src/application-state.ts`. Nine statuses on a plain `text` column (not an enum), stored and
never derived on read.

```
draft → with_client → identity_under_review → aml_under_review → offshore_under_review → processing → completed
                                                                                            ↕
                                                                                        sync_failed
(any non-terminal) → cancelled
```

`computeDerivedApplicationStatus` (`:334-367`) reads the facts in this precedence:

1. terminal (`completed`/`cancelled`) ⇒ unchanged.
2. `draft` + `adviser_declaration` ⇒ `with_client`.
3. **`if (termsGateSatisfied)` wraps everything below it** — nothing progresses until every
   KYC-relevant individual has agreed terms.
4. identity not verified ⇒ `identity_under_review`.
5. any AML `SUSPECTED`, or not all approved ⇒ `aml_under_review`.
6. offshore custody ⇒ `offshore_under_review`, else `processing`.
7. fallback: return the current status unchanged.

**The KYC cohort** is narrower than "the applicants": `isIndividualForClientGates` (`:122-126`) is
`type === 'individual'` AND role ∈ `owner | decision_maker | trustee`. An empty cohort makes every
gate vacuously true, so an application of organisations only jumps straight to `processing`.
Kickoff may also record `attorney` / `other` on `crm.account_contacts` (0109); those roles are
**not** in the invite/KYC cohort — attorneys are typically not invited for IDV.

`processing → completed`, `offshore_under_review → processing` and anything → `cancelled` are
**manual only** (`api/applications/[uid]/status/route.ts:72`, RBAC-gated). Derivation never produces
them.

### The order matters, and it is now the same order

Legacy is **terms-first**: the client consents, and that acceptance is what kicks off identity and
AML. The new engine ran the checks first for a while, because ordering resolved **within one case**:
identity and AML are participant-scoped, terms are account-scoped, and a prerequisite in another
case counted as satisfied — so wiring terms first compiled to no prerequisite at all and released
identity at kickoff.

`dependencies.unmet` now resolves across the execution's cases (a `foreign` set, consulted only for
keys the dependent's own case does not plan), so the seed wires the legacy order and the projection
follows it. One consequence worth knowing: because a key planned **nowhere** is still satisfied — the
rule that stops a conditioned-out step wedging a journey — an application with no accounts has no
account tier, so the participant-tier steps sequenced behind the terms lose that ordering and run
together. Harmless for a review-only journey, which has no terms to sign.

## 4. The client journey

`app/a/[applicationId]/[applicantId]/page.tsx` is the only file under `app/a/`. Steps are a query
param over one page, assembled per-applicant (`components/onboarding/onboarding-flow.tsx:29-46`):

```
overview → [organisations] → applicant → accounts → [identity] → terms → complete
```

- `organisations` exists iff some applicant's type is `organisation|corporate|charity|trust`.
- `identity` exists iff `digitalVerification && !isVerified` — and **disappears from the sequence**
  once verified.

| Step          | What the client does                                                                                    | Writes             |
| ------------- | ------------------------------------------------------------------------------------------------------- | ------------------ |
| overview      | Reads a dossier: adviser + firm, every applicant with terms/identity/AML ticks, every account with fees | —                  |
| organisations | Reads org cards                                                                                         | —                  |
| applicant     | **Read-only** review of adviser-entered detail (`DynamicFieldsDisplay`, empty values hidden)            | —                  |
| accounts      | **Read-only** review of accounts + fees                                                                 | —                  |
| identity      | iDenfy iframe                                                                                           | `PATCH …/identity` |
| terms         | Two checkboxes                                                                                          | `POST …/terms`     |
| complete      | Static "what happens next"                                                                              | —                  |

### What the new client page does instead

One page, no steps in the URL, no tabs. The order is: what you need to do next (the hero), **what
you're applying for** (every account, one labelled panel each — product, owners, custody, model,
fee packages, dividends, transfer, contributions, beneficiaries), your journey's stages, your
details, and the other people named on it; with the actions themselves pinned in a column
alongside.

The legacy sequence above was first ported literally, as a four-tab strip over the same content,
and that was the wrong reading of it. Legacy's steps are a WIZARD — the client is walked through
each panel and cannot miss one — whereas a tab strip is navigation: it presents the same panels as
optional. The one panel that is not optional is the accounts review, because it is what the terms
declarations are declarations ABOUT, and it was the tab a client had to know to click. Everything
here is read-only, so there is nothing to gain by revealing it a screen at a time.

`apps/client/src/routes/onboarding-mode.test.ts` pins both halves: the summary is present, and
neither `Segmented` nor a `?section=` writer comes back.

### There is no gating at all

The complete server-side guard is: the application uid exists and is not soft-deleted, the applicant
belongs to it, and `?step=` is one of seven literals (an unknown one silently falls back to
`overview`). `get-application-for-onboarding.ts:51` does not select on status. There is no
middleware — `proxy.ts:5-6` explicitly whitelists the subtree, commented
_"access controlled by application+applicant UID in URL"_.

So: **a client can type `?step=terms` against a `draft` application and agree the terms.** The POST
validates body shape only (`// Public endpoint - no authentication required for client access`). The
only access control in the entire journey is the secrecy of the URL.

The new app is already stricter — a request-backed step with no artifact cannot be fulfilled
(`orchestrator._assert_fulfilment_supported`). **Do not port the looseness.** The client portal's
`/onboarding` page has no step parameter at all: it is one continuous page (§4), and nothing in
its URL unlocks anything — an actionable step still requires `available` and the minted request
artifact.

## 5. The fact-find: four dynamic sections, two hard-coded forms

Field definitions are rows in `onboarding.fields` (`entityType`, `fieldKey` — dotted paths allowed —
`fieldType`, `section`, `required`, `conditions`, `validation`, `options`), resolved by a
six-clause scope union deduped on a specificity score of tenant 100 / product 10 / platform 1
(`pkgs/fields/src/service.ts:46-53, 178-268`).

| Section                | Entity    | Shown when                                      |
| ---------------------- | --------- | ----------------------------------------------- |
| Additional Information | applicant | every individual applicant                      |
| Account Activity       | account   | `custody == 'offshore'`, inside Account details |
| Pension Information    | applicant | `application.hasPensionProduct`                 |
| Employer Contributions | applicant | `application.hasSippProduct`                    |

Hard-coded (Zod, not in `fields`): the **core applicant** (names, DOB, nationality, tax residency,
national identifier, address, email, phone, bank account) and the **organisation** shape (entity
type, classification, LEI, charity/registration number, trustees and interested parties).

`hasPensionProduct` / `hasSippProduct` are computed client-side across **all** accounts
(`application-form.tsx:926-975`), so one SIPP on a joint application shows the pension and employer
sections to **every** applicant.

There is no section-level gate object: a section disappears because every field in it evaluated
invisible and the empty card is filtered out.

### The condition grammar

One evaluator for both whole-section and intra-form conditions
(`pkgs/fields/src/runtime/visibility.ts:65-167`):

- Leaf `{field, operator, value}`; operators `equals`/`===`, `notEquals`/`!==`, `in`, `notIn`,
  `contains`, `notContains`, `isEmpty`, `isNotEmpty`.
- Group `{conditional: {logic: 'AND'|'OR', conditions: [...]}}` — **one level, no nesting**.
- **Unknown operator ⇒ visible. Missing field or operator ⇒ visible.** It fails open.
- Paths: bare dotted against the form data; `application.*`, `applicant.*`, `account.*` against their
  own scopes.

Intra-form conditions are mechanically identical, just referencing a sibling value —
`occupation == 'Retired'` reveals `occupation_retired`; `pension.hasEmployerContributions == true`
reveals the four employer fields. Hiding a field unregisters it and clears its errors, so a stale
value cannot block a step.

### Validation

Required + `minLength`/`maxLength`/`pattern` only. The seed's `min`/`max` (retirement age 50–75) and
`allowedValues` are **not enforced** by the renderer. Dynamic requiredness is checked on Next
(`trigger()`), and **never re-checked server-side** — `isFieldVisible` has no server call site. "Save
draft" bypasses all of it with an `x-graphene-skip-validation: 1` header.

Answers merge into the entity's `data` jsonb; core fields are written back onto real columns.

## 6. The three compliance gates

**Identity (iDenfy).** Token minted lazily per applicant; the client completes an iframe. Manual
"verified in person" path exists for the adviser. It **does not block the client** — `canContinue` is
set true on every branch including `failed`, and the failure screen offers "Continue". It blocks only
the status machine. No cross-application reuse and **no expiry**.

**AML (Creditsafe KYC Protect).** Fully automatic and **inline in the request handler** — no job, no
cron. `autoTriggerCreditsafeAmlIfEligible` fires from the terms POST, the identity PATCH and the
declaration route. Searches at threshold 85 (individuals) over `PEP/SAN/INS/AM/POI/ENF`. The decision
**ignores Creditsafe's own status field** and keys on match level: only `notApplicable|veryLow|low`
pass. `CHECK_FAILED` retries up to 3. Invisible to the client — it runs after their journey ends.
An adviser can override with "Approve AML", which writes a real `aml_checks` row (the code notes that
bumping the status alone would be reverted by the next recompute).

**Terms.** Selected live per application: tenant-scoped, `audience='client'`, `enabled`, filtered by
platform/custody/product/model, plus a hard-coded `heading === 'transfer declaration' ⇒ hasTransfer`.
Presented as sanitised HTML + a PDF viewer, accepted by **two checkboxes** — no signature, no
per-document acknowledgement.

## 7. Known legacy defects — do not port these

1. **The invite link is wrong on joint applications.** `buildApplicationEventPayload` builds the URL
   from `ctx.applicants[0]` only, while the recipient token `{applicants[].email}` fans out to every
   applicant. Applicant 2 receives a link to applicant 1's journey and, following it, agrees terms and
   verifies identity **as applicant 1**.
2. **Terms acceptance used to record almost nothing.** A boolean and a timestamp on the applicant. No
   IP, no user agent, and **no terms version or document id** — the posted `id` is the applicant's.
   The new engine closes the version-pin gap: `terms.acceptances` pins an immutable
   `term_version_id` (HTML snapshot or the exact PDF bytes). IP / user-agent are still out of
   scope. See [`docs/terms.md`](terms.md).
3. **The iDenfy webhook never recomputes status.** It writes the verified flags and stops, so an
   application sits in `identity_under_review` until some unrelated write triggers a recompute. There
   is no reconciling job. The webhook is also unauthenticated with no signature verification.
4. **`sync_failed` has no producer.** It exists in the status list, transition map, event map and UI
   badges; nothing sets it. Reachable only by a manual `PATCH /status`.
5. **Global email templates never fire.** Only tenant-scoped enabled templates are queued, so a tenant
   that never ran "Generate Templates" silently sends **no invite at all** — the client is never told
   their journey exists, and nothing errors.
6. **Notification retries have no cap and no cron.** A failed send sets `nextRetryAt = now + 10min`
   and is retried only if an unrelated request re-enters the sender.
7. **No lock around `progressApplicationStatus`.** The AML trigger and its two recomputes run outside
   any shared transaction, so concurrent callers can each start a Creditsafe screening.
8. **The overview leaks the other applicants** — every applicant's name, email and compliance status
   is shown to whoever holds the link.
9. **A step outside the computed sequence still renders.** `indexOf` returns `-1`, `Math.max` clamps
   the progress index to 0, and `renderCurrentStep()` switches on the raw param regardless.
10. **Product-scoped fields resolve from `accounts[0]` only**, so a mixed-product application asks the
    wrong questions.

## 8. What the new engine keeps, and what it does not

Kept faithfully: the actor split (adviser fills everything; the client reviews and consents), the
conditional sections and their predicates, the KYC cohort rule, the phase order including
terms-before-checks, and the adviser declaration as the handoff — a `declaration` step
(application-scoped `task`) that every client step is wired behind.

Deliberately different:

- **Fulfilment is evidence-based** — a step cannot complete without its artifact, so the URL is not
  the access control.
- **Steps are gated by `depends_on`**, which the client surface must respect: a held step is not the
  client's to do yet, and must not be offered as though it were.
- **Client review dossier** shows co-applicants as name/type/role only — no compliance leak of the
  other parties' AML/identity position (legacy overview defect #8).
- **Invite links** are per applicant via portal membership, not a single application secret URL.

### Live parity matrix (Aug 2026)

| Area                                                                               | Status     | Notes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| ---------------------------------------------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Fact-find → CRM write-back (address, bank, nationality, tax, gender)               | **kept**   | Nested `address.*` / `bank_account.*` merge into jsonb columns. Legal gender on the individual form and on the column is Male and Female. An empty bank account name prefills from the person's name.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| Postcoder addresses                                                                | **better** | `GET /ref/postcoder/*` on the API; portals are thin BFFs; individual + org fact-find use the composite `address` field. The field carries the full searchable country list into Postcoder, uses country-aware international address lines, supports lookup/manual/edit modes, preserves line 2 for individuals and organisations, and validates the nested address at both browser and transition boundaries.                                                                                                                                                                                                                                                                                                                                                                                                                  |
| NI / CONCAT national identifier                                                    | **kept**   | DynamicForm `nationalIdentifier` + seed fields                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| Phone number                                                                       | **better** | The hard-coded `phone` question keeps its field key (in-flight answers survive), is labelled "Mobile number", and maps to `crm.contacts.mobile`. The optional `other_numbers` question is a single phone number labelled "Phone" and maps to `crm.contacts.phone`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| Multicurrency account details                                                      | **better** | Kickoff chooses the wrapper only; **Account details** sets currency before setup                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Model + fee packages                                                               | **better** | Assigned inside **Account details**; `access = display` packages auto-apply (visible, non-removable, with the actual charge) and `access = select` stays adviser-pickable. Hidden/custom packages stay off the form.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| Transfer intent (type/amount/provider/reference)                                   | **better** | Gathered by **Account details** before declaration, not during journey initialisation; non-null `accounts.transfer` lets the existing terms branch see `has_transfer`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| Regular / lump contribution intent                                                 | **better** | Gathered by **Account details** during the journey; writes `accounts.data.regular_contribution` and, for pension products, a real `platform.contributions` + expectation (`lodge=False` so the journey never dies on SEI)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| Investment capacity + dividend treatment                                           | **kept**   | Capacity is captured at kickoff; dividend treatment moves to **Account choices**. Both remain snake_case on `platform.accounts`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| JISA child                                                                         | **better** | Child is a real `crm.contacts` **owner**. Kickoff keeps the filled adult as `registered_contact` and reuses one child across extra JISAs. Product `max_age` (JISA 17) refuses an owner who is 18+ on the journey start date. The child gets a narrow Child details form with no NI number, contact details, address, bank details or adult Additional information; the parent gets Registered contact details (name, email, mobile and address) rather than the applicant forms.                                                                                                                                                                                                                                                                                                                                               |
| Pension beneficiaries                                                              | **kept**   | Expression of wish on `accounts.data.beneficiaries`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| Org owner + trustee / decision_maker / attorney / other / registered_contact roles | **kept**   | `crm.account_contacts.role` at kickoff; KYC invite is owner\|decision_maker\|trustee\|registered_contact except a JISA child owner                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| Flexi-access / crystallisation flag (SIPP)                                         | **kept**   | Kickoff stamps `crm.contacts.flexi_access_taken` on individuals only                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| Assigned adviser                                                                   | **better** | The kickoff choice writes both `crm.applications.adviser_id` (journey ownership) and an `adviser` `crm.client_advisers` row for every distinct party, which is the relationship the client record, adviser scope and client portal read. Choosing a new adviser moves the previous one to support, so a client never has two (0245).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| Declaration → portal invite                                                        | **kept**   | Best-effort; WorkOS failure does not fail the step                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| Client review dossier before terms                                                 | **better** | `GET /applications/me` → `review`; client `/onboarding` is ONE continuous page (§4), leading with what the client is applying for — not the four-tab strip that shape was first ported as — still name/type/role only for co-applicants (no AML leak)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| Milestone order terms-before-checks                                                | **kept**   | Stages: details → sign → verification → setup                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| ComplyCube AML in seed when configured                                             | **better** | `_aml_provider()` → complycube else manual; the step raises a Screen task for operations                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| Full 7-step adviser Sheet UX                                                       | **better** | Flow choice sits on the Journeys page's New journey menu. Kickoff is only **Accounts & parties → Review**: each account derives or asks its product-valid custodian/custody, and the review shows the records and IDV routes it is about to trigger.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| Digital IDV (ComplyCube)                                                           | **kept**   | Seed uses `provider: complycube` + client assignee when `COMPLYCUBE_API_KEY` is set; otherwise staff+manual. New people and existing people without a reusable pass get an inline per-person Auto/Manual toggle on their account-party card; one shared journey person has one choice across every account. The client photographs the document and their face in the portal (`GET /applications/me/identity-capture`, `POST …/identity-capture/submit`); the ComplyCube result releases the step.                                                                                                                                                                                                                                                                                                                             |
| DynamicFeeSection multicurrency polish                                             | **kept**   | Kickoff select shows `{name} · {summary}` (`formatFeePackageSummary`) plus muted description/meta; multi-package grouped by category×frequency; currency from `ref.currencies`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| Custodian account open (`setup_account`)                                           | **better** | Local book activation (`draft` → `active`) + `applications.synced` hand-off stamp + ops-visible `platform.accounts.data.custodian_open` marker (`status: pending_ops`, `local_activated: true`, `requested_at`). For a SIPP whose product names Morgan Lloyd as scheme administrator, the same step then creates or resumes the client/scheme/policy there (`account_enrol.sync_activated_account` → `sync_client`); a vendor refusal is stamped on `data.platform_sync` rather than failing the journey. The active account appears in the client portal immediately at its OLTP value; only holdings and provider value history wait for the later `platform.links` identity. **No** Seccl/SEI enrolment yet — the marker is the queue substitute until custodian enrolment lands; legacy mostly left the same gap to Excel. |

When this matrix drifts, update it in the same change that moves a row — do not leave §8 as aspiration.
