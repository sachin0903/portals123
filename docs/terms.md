# Terms

Legal terms are a versioned catalogue with durable acceptances. Graphene owns
platform-wide copy (privacy policy, service T&Cs). Each firm owns the
declarations and PDFs its clients must accept. A journey checks whether the
**current published version** is accepted, rather than storing one-off HTML
on a document request.

This is not `l10n.terms` (UI vocabulary) and not the unversioned
`platform.terms` sketch in `docs/proposed-schema.sql`.

## Model (`terms` schema — migration `0184_terms`)

- `terms.terms` — header. `owner` is `graphene` (`tenant_id` NULL) or `tenant`.
  `code` is unique per owner/tenant. Draft body lives here (`body_md` or a PDF
  file). `required_at` is `onboarding` and/or `login` (portals enforce
  `onboarding` inside a journey; `login` is unused at navigation — see Login
  gates). `audience` is
  `client` | `staff` | `all`. `applicability` is jsonb: `always`, `products[]`,
  `has_transfer`.
- `terms.term_versions` — insert-only snapshots. Acceptance pins **this row**.
  A trigger rejects UPDATE/DELETE.
- `terms.acceptances` — the register. A client row is unique on
  `(contact_id, term_version_id)`. A staff Graphene acceptance is unique on
  `(user_id, term_version_id)` where `contact_id` is NULL. Never updated; a new
  version is a new row.

`crm.kyc_checks.kind = 'terms'` stays as the derived Compliance-card tick:
passed when the contact has no outstanding onboarding terms.

## Body kinds

A version is exclusive: HTML **or** PDF, not both.

- **Written copy.** Draft markdown or a pasted HTML snippet on the header.
  Publish runs `declaration_html()` then `sanitise_html`. The version stores
  only the sanitised HTML.
- **PDF.** Draft file on the header (tenant bucket, `terms/{uid}/…`). Publish
  pins that file. Replacing the file is a new version.

The accept UI does not care which kind it is. It pins `term_version_id`.

## Outstanding

`app/terms.py::outstanding_terms` returns published terms whose audience and
applicability match and that have no acceptance for
`current_published_version_id`. Publishing v2 makes the same person outstanding
again; v1 remains on file.

Account opening / `terms_ok` is empty outstanding onboarding terms for each
terms-visible participant, **and** every `accept_terms` step fulfilled. In-flight
journeys that still pin only `content` / `document_uid` keep the step half.

The gate's refusal **names the terms** still to accept (`onboarding_outstanding`
returns the rows; `onboarding_outstanding_empty` is its boolean). Found on
staging 14 Oct 2026: a firm authored two catalogue terms after its journey was
seeded, no step pinned them, the client portal did not list them (below), and
the adviser reached account setup with "every owner's required terms" and
nothing to say which — or where the client could accept them.

Two scoping rules make that gate answerable, and both are enforced in
`onboarding_outstanding` rather than left to the caller:

- **The firm's own terms only** (`owner='tenant'`). Graphene's platform-wide
  terms used to be collected at the portal door by the login gate (removed
  13 Oct 2026 — see Login gates), and no `accept_terms`
  step pins one — so counting them refused every account opening for ever. The
  migration's own privacy-policy stub is `required_at` onboarding _and_ login,
  which is what made this unmissable: an adviser completing a journey with the
  client in the room had no step through which to clear it, and a client who
  never signs in never would. `crm.kyc_checks.kind='terms'` is scoped the same
  way, being the same question asked once for Compliance.
- **An explicit `tenant_id`.** `list_terms` leans on RLS, and the flow engine
  reads it on a `service_role` session where RLS is bypassed. Without the
  explicit scope the gate counted _other firms'_ terms as outstanding against
  this firm's clients. Pass `tenant_id` from any elevated caller; leave it unset
  only where a request session's RLS is doing the scoping.

Refreshing the Compliance tick elevates to `service_role` for that write alone
(`_as_service_role`) and restores the previous role. `crm.kyc_checks` grants
`authenticated` SELECT and nothing more — a KYC verdict is the system's to write —
but the tick has to land on the client's _own_ acceptance through `book_session`.

## Onboarding

The seeded spine (`terms_general` / `terms_isa` / `terms_transfer` /
`terms_platform`) is unchanged. Steps prefer `term_uid`. If the participant
already accepted the current version, `AcceptTermsHandler` completes immediately.
Fulfilment writes `terms.acceptances` then refreshes the KYC tick.

The transfer declaration is not planned until the account has a transfer. Account
details records that during the journey; the walk then creates the step. Opening
the account stays refused until that declaration is accepted. A journey that
already walked past the missing step grows it the next time it is opened.

**In person** — `POST /applications/{exec}/acknowledge` — writes `channel='in_person'`,
stamps `attested_by`, and **requires the signed copy**. The route is multipart
(`case_uid`, `step_key`, `participant_contact_id`, `attested`, `note`, `file`;
`SingleUploadRoute` caps the body): the adviser is recording another person's
agreement, so the record is the scan or photograph the client signed, not the
adviser's word for it. `_signed_copy_only` accepts a PDF or an image and
refuses anything else (422). The file is stored first through
`documents.store_contact_upload` as `document_type='signed_terms'` on the
client's own record (`document_contacts` owner), audited `document.uploaded`
with `signed_copy_of` naming the term; then the acknowledgement is filed with
`signed_copy` in its detail, the request item is fulfilled against that document
(`fulfilled_document_id`), the step's `data.document_id` is set so the journey
row links it as **Signed copy**, and `AcceptTermsHandler` writes
`terms.acceptances.evidence_document_id` (migration `0207`, FK to
`documents.documents`, `ON DELETE SET NULL`). `AcceptanceOut.evidence_document_uid`
surfaces it and the client record shows a **Signed copy** link beside an
in-person acceptance (or "signed copy on file" where the mount has no document
route). A portal acceptance carries no evidence document: the client's own
acknowledgement is the evidence.

Catalogue steps are planned once per `(term_uid, contact)` across all accounts in
an execution. The acceptance is legally attached to the client and published
version, not to each account that made the term applicable. For journeys started
before that planner rule, accepting one row settles the equivalent in-flight
rows and closes their request items; each settled step still receives a domain
event. Inline and library-document steps are not collapsed because their copy
may be account-specific.

**What the client is shown** is a `documents.documents` row of type `terms`,
filed when the step instantiates, so the request item has a target the client
dialog can resolve (`/…/items/{item}/terms` for HTML, `/target` +
`/target/content` for a file). Written copy is filed as `metadata.content`
(`_store_inline_terms`). A PDF version is filed as a `documents.files` row naming
the version's **own object** in the tenant bucket (`_store_terms_pdf`) — no copy;
one file row per object, so the second client asked to accept the same version
reuses it without a round trip to the store. Before the PDF half existed the item
had no target at all and the dialog reported "Couldn't load the document" for a
term the firm could read in its own editor. If the firm's store cannot be
resolved the step holds `BLOCKED` with `term_file_unavailable` rather than
raising an acknowledgement of a document nobody can open. A seeded term whose published
HTML is empty is republished from the seed the next time onboarding is seeded — the ISA
declaration is in that seed — and a request item whose snapshot has no HTML falls back to
that published body, so the client is not left with a tick and nothing to read.

Standalone `content` / `document_uid` remain for custom steps (XOR with
`term_uid`).

Client onboarding lists **every** outstanding onboarding term
(`/terms/outstanding?required_at=onboarding`) that no step of the client's
journey pins, as steps of its own in the same dialog chain; those POST
`/terms/{uid}/accept`. "Pins" means the step's filed target document carries the
`term_uid` — `/applications/me` exposes it per step, and
`$lib/onboarding.ts::catalogueTerms` filters on it. It used to list Graphene's
terms alone and drop any term whose _title_ matched a step's, which hid the
firm's own terms behind a same-named seeded step that pinned a library document
— the gate above could then never clear from the client's side. A PDF term is
read through the same-origin proxy `apps/client/src/routes/api/terms/[uid]/file`
(the API's `GET /terms/{uid}/file`), for the reasons the request-item content
proxy exists.

Still open: a term with no step cannot be collected in person. Staff clear it
only by adding an `accept_terms` step that pins the term (the in-person
acknowledge needs a step), or by the client accepting it in the portal.

## Login gates

Removed on 13 October 2026. Terms are collected inside a journey only
(`required_at = onboarding`): the onboarding page and the journey dialog list
what is outstanding and POST `/terms/{uid}/accept`. The platform, admin and
client portals used to call `GET /terms/outstanding?required_at=login` before
the HTML of every non-exempt page and redirect to `/terms/required`. That was
the one API call still in front of the first byte on every portal page, so the
check was removed rather than cached. No portal fetches `/terms/outstanding`
on navigation.

`required_at` may still include `login`. The API accepts that value and still
serves `/terms/outstanding`; no portal enforces it at navigation. The
`/terms/required` pages existed only to collect that login acceptance, and were
removed with the gate.

The client hook still decides a foreign session (`decideGate` in
`@graphene/auth` returns `allow` or `wrong_portal`, never `terms_required`) and
answers a non-navigation request with 403 JSON `{ error: 'wrong_portal' }`.
The layout turns a page's `wrong_portal` into the login redirect. Platform and
admin keep that refusal in the root layout, and stream the bell (and, on
platform, the Messages dot) behind the shell.

The swallowed-`redirect()` trap that hid the old gate is still recorded in
`docs/gotchas.md`. The gate itself is gone.

## Authoring

Not GrapesJS. The editor picks a body kind, then a markdown/HTML textarea or a
PDF upload, then Publish.

The platform editor is the two-column shape `/content`'s is: the document in the
growing column, and Details / timing / applicability / versions in the rail. It
carries no in-page back button — `/terms` is in `SECTION_LABELS`, so the shell
breadcrumb is the trail, and the page names its own crumb (`crumb` from load,
because `item` is not one of the loader keys the shell knows).

An attached PDF is rendered in that document column with the shared pdf.js
viewer. The platform's same-origin `/terms/{uid}/file` proxy asks the API for
`draft=true`, which requires catalogue read permission and returns the mutable
attachment an author is about to publish. The ordinary API file route remains
pinned to the published snapshot, so uploading a replacement cannot change what
clients read before the replacement is published.

**The product types on offer are the firm's own shelf**, from
`GET /catalog/products` through `productChoices` — deduplicated by
`product_type`, which is what `applies()` compares `crm.accounts.product`
against. A fixed ISA/JISA/GIA/SIPP list offered scopes that could never fire and
hid the ones that could. A product type already saved on a term stays on offer
after it leaves the shelf (marked "off shelf"): the accounts holding it are live,
and dropping the checkbox would silently widen the term on the next save.
`termDraftFromForm` refuses a conditional scope with no conditions rather than
letting it fall through to the API's "empty means always" rule.

- Platform `/terms` — firm library. Firm settings → Communications, after
  Content, gated `terms:read`. Firms author **client** terms in v1.
- Admin `/operations/terms` — Graphene catalogue, `platform_terms:*`.
- Admin operate `/tenants/{uid}/operate/terms` — the firm library via the
  target-tenant proxy.
- Client record Compliance card — the tick plus the acceptance list from
  `GET /contacts/{uid}/term-acceptances`.

## Permissions

`terms:read` / `terms:write` (platform). `platform_terms:read` /
`platform_terms:write` (operations). Graphene writes go through
`system_transaction`.

## Out of scope

IP / user-agent on accept; e-signature; consent withdrawal UI (a new version is
the supersession); firm-required **staff** terms; restoring the removed DFM
seed; rewriting in-flight journeys that pinned an old flow version.
