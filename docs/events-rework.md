# Events, the audit log, and the merge-field schema (rework spec)

Branch `feat/events-rework` (off main). Promotes the `audit.actions` stream to a first-class
**Events** surface in both portals with a master–detail UI, folds document activity into it, gives
the DML audit log its own page, and closes the merge-field/variable gaps.

## Context

Three surfaces read `audit.actions` today and none is called "Events": the admin `/audit` page (a
cramped "Actions" tab sharing space with the "Record changes" DML trail), the platform `/activity`
page (the firm's view of the same stream), and a _separate_ document log (`documents.audit_log`) on
its own `/documents/audit` screen. Meanwhile the notification rule builder advertises only 3–6 merge
variables per trigger from a hand-written vocabulary of seven, `context.build_context` resolves
values it never offers, and the email editor keeps a separate, duplicated variable list.

## Decisions

| Question                  | Decision                                                                                                                                                                                                                                                                                                                                    |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Events UI                 | Master–detail (WorkOS-style): list left, detail pane right, selection in the URL (`?event=<id>`)                                                                                                                                                                                                                                            |
| Admin IA                  | Own nav item `/events`; `/audit` keeps only record-changes and loses its tab strip                                                                                                                                                                                                                                                          |
| Admin per-tenant view     | **Both** (Craig): keep the estate-wide `/events` + `/audit` for cross-firm oversight, AND nest per-firm `/tenants/[uid]/{events,audit}` (an "Activity" nav group, `audit:read`) — the same surfaces scoped to the firm you are inside, matching the per-tenant **book** pattern                                                             |
| Audit-log firm filter     | `list_audit` returns `{items, tenants}` (a self-feeding firm lookup, mirroring `list_audit_actions`) → a `FilterSelect` on `/audit`; the per-tenant audit view drops the firm column + filter (one firm)                                                                                                                                    |
| Platform route            | `git mv` to `/events` + redirect from `/activity`; permission stays `activity:read`. **Superseded:** renamed again to `/event-logs`, and the `/activity` stub deleted — `/activity` is the outbound-email feed and the platform `/events` route is gone entirely, the event registry having stayed at `/notification-rules` (docs/comms.md) |
| Document activity         | 100% into the stream via **explicit writers** into `audit.actions`; `documents.audit_log` kept as the module's product-state store (**dual-write, not retire**)                                                                                                                                                                             |
| `/documents/audit` page   | Deleted with its header button                                                                                                                                                                                                                                                                                                              |
| Recent-activity column    | Kept on the documents overview                                                                                                                                                                                                                                                                                                              |
| Per-document Activity tab | Kept on the document record                                                                                                                                                                                                                                                                                                                 |
| Cursor tailers            | **Unchanged** (D0 records why + what would justify revisiting)                                                                                                                                                                                                                                                                              |
| Covering note (`message`) | Carried in the event                                                                                                                                                                                                                                                                                                                        |
| `share.accessed` IP / UA  | Carried, behind the Raw disclosure only                                                                                                                                                                                                                                                                                                     |

## A — Admin: Events becomes its own page

New `apps/admin/src/routes/events/{+page.server.ts,+page.svelte}`, lifting the _actions_ half of
`audit/+page.server.ts` verbatim (the `ActionLogFilters` mapping, `ACTIONS_PAGE_SIZE` — now the
shared `PAGE_SIZE` from `@graphene/ui/book/listing`, 25, rather than its own 50 — the
`until → T23:59:59.999Z` stamp, the `before` cursor, the `{items,has_more,tenants}` of
`GET /admin/audit/actions`). Gate `audit:read`. Keep `actionEntityHref` (`target_tenant_uid ??
tenant_uid` into `/tenants/{uid}/book/…`, uid never serial id) + "Back to newest".

`/audit` shrinks to the record-change trail only: delete the `Tabs.Root`/`switchView`/actions
branch; the changes filter card + table + keyset paging fill the page; Export CSV moves out of the
`view === 'changes'` guard into the header unconditionally. **Redirect rule** at the loader top:
`view=actions` (or an actions-only param like `event_type`/`proxied`) → `/events` carrying its
query; **everything else stays** — note that bare `/audit` (previously the Actions default) now
lands on the audit log; accepted.

Nav (`apps/admin/src/routes/+layout.svelte`): **two** entries, both `audit:read` —
`{ label:'Events', href:'/events', icon:ActivityIcon }` then
`{ label:'Audit log', href:'/audit', icon:ScrollTextIcon }`. (Phase-0 renamed the single `/audit`
entry to "Events"; reconcile to these two.) `audit/export/*` untouched.

## A2 — Admin: per-tenant Events + Audit (nested)

Graphene ops mostly work _inside_ a firm (the `/tenants/[uid]/…` section, where the per-tenant
**book** already lives), so the firm's activity is nested there too rather than only reachable from
the estate-wide pages + a filter:

- `apps/admin/src/routes/tenants/[uid]/events/{+page.server.ts,+page.svelte}` — the firm's slice of
  the action stream. Loader forces `?tenant=<uid>` (the section uid; never widenable from the URL),
  reads the other filters, gates `audit:read`. Page mounts `ActionLog` with
  `showTenantFilter={false}` and `attribution='operations'`; entities link into **this** firm's
  book; gotos stay under `/tenants/[uid]/events`.
- `apps/admin/src/routes/tenants/[uid]/audit/{+page.server.ts,+page.svelte}` — the firm's slice of
  the record-change trail. Loader forces `tenant_id=<numeric>` from the layout's resolved tenant
  (`await parent()`), gates `audit:read`. Export CSV points at the top-level `/audit/export` with
  `tenant_id` set (that route is outside the section).
- Nav: an **Activity** group in the tenant branch (`Events` + `Audit log`), gated `audit:read`.
- Both loaders pinned (`events.test.ts`, `audit.test.ts`): the gate, the forced firm scoping, the
  filter/cursor mapping, the failure modes.

## A3 — Audit-log firm filter (the record-change trail)

The `/events` firm dropdown is fed by `list_audit_actions`' bundled `tenants` lookup; the DML
`GET /admin/audit` returned a **bare array**, so `/audit` had no firm options. `list_audit` now
returns `{items, tenants}` (`[{id, name}]`, filtered by numeric `tenant_id`), mirroring the actions
endpoint's self-feeding pattern (works under `audit:read` without `tenants:read`). The DML types
moved to `$lib/audit/types.ts`; the CSV export walker + the global loader read `.items`; the table
is a shared admin-local `$lib/audit/audit-table.svelte` (`showFirm` flag — dropped on the scoped
view). `/audit` gains a `FilterSelect` firm control. **Client regen required** (response shape
changed); `test_admin.py::test_list_audit_returns_recent_events` updated to the `{items, tenants}`
shape.

## B — Platform: `/activity` becomes `/events`

`git mv apps/platform/src/routes/activity apps/platform/src/routes/events` (+ `activity.test.ts →
events.test.ts`). Re-create `routes/activity/+page.server.ts` as a redirect-only `load` →
`redirect(301, /events${url.search})`. `nav.ts` → `{ label:'Events', href:'/events' }`, still
`activity:read`; update `nav.test.ts`. Loader posture unchanged: `tenant:''`,
`showTenantFilter={false}`, `attribution="firm"`, and **`FIRM_EXCLUDED_EVENT_PREFIXES =
("action.aml-",)` stays exactly as is** (tipping-off control, pinned by
`tests/test_firm_audit_actions.py` — do not touch).

## C — The Events UI (master–detail)

Split `pkgs/ui/src/lib/components/audit/action-log.svelte` (785 lines) into: `event-stream.svelte`
(component `EventStream`, was `ActionLog` — shell + toolbar + panes + selection + paging),
`event-row.svelte` (one compact row), `event-detail.svelte` (right pane, content lifted unchanged),
and `event-detail.ts` (pure helpers, unit-tested: `changeRows`, `permissionsDiffOf`,
`legacyPermissionsCount`, `displayValue`, `fieldLabel`, `previousIds`, `currentIds`,
`residualPathParams`, `coveredIds`, `metadataRecord`, `initialsOf`).

Layout (as built — aligned to the Documents-audit surface, the visual reference): a `bg-card`
master-detail framed once (`rounded-xl border shadow-xs`). The MASTER is a `Table` — the actor-led
sentence split into columns (a category glyph in the accent cell · **Who** the actor + initials
disc · the **Action** with the raw `event_type` chip + firm chip · **When** the exact time +
status), `table-fixed` so the Action cell truncates (`…`) rather than widening the table. The DETAIL
pane reads CHANGES-first → a **Metadata** definition list. The whole surface is locked to the
viewport (`h-[calc(100svh-var(--topbar-h,4rem))]`, the shell's editor pattern + `overflow-hidden`):
the PAGE never scrolls — only the two panes do — and the master never scrolls sideways.

**The pane states the event, not the record.** The detail used to end in a **Details** block
carrying the full event JSON, line-numbered and syntax-highlighted. It is gone. A wall of JSON is
not what an adviser or a compliance reader opens an event for, and everything in it worth reading is
stated above in English. Removing it also closed a leak: the acting Graphene tenant's real name rode
in that JSON on a proxied row, past the abstraction the humanised view makes of it
(`Graphene support`) — `event-stream.ssr.test.ts` now asserts the name never appears.

**The transport does not surface as vocabulary.** `methodLabel` turns POST/PUT/PATCH/DELETE into
**Created / Replaced / Updated / Deleted** and `statusLabel` turns the HTTP status into
**Succeeded / Refused / Not found / Conflicted / Rejected / Failed** — in the row chip, the Metadata
Operation row and the method filter segments. The filter still SENDS the verb, and both the raw verb
and the exact status code stay in the element's `title`, so nothing is lost to whoever wants it.
`methodRoute` survives for the Metadata row, where the route is the point.

Selection is **client-side, no URL param** (the documents-audit treatment): `EventStream` holds a
`selectedId` in `$state`; the pane shows it, else falls back to the newest row (never empty), and a
stale id after a filter change falls back too. **Load-more grows the list in place** — no page
navigation, no prev/next: as built, it appends the next KEYSET page (`?before=<id>` via
`/events/more`, fetched by the injected `onLoadMore` and appended client-side — no growing `?limit`,
no re-fetch of what's on screen), with a CLIENT-side `MAX_EVENTS` cap in the component past which the
answer is to narrow the filters (see the as-built refinement note below). The `fetch_actions`
**`event` id filter (C2, committed)** stays available for a future off-page deep-link, not wired.

Naming (as built): the COMPONENT is renamed `ActionLog → EventStream` (the audit `index.ts` + the
root `pkgs/ui/src/index.ts` + all three call sites); the filter/tenant TYPES stay
`ActionLogFilters`/`ActionLogTenant`/`EMPTY_ACTION_LOG_FILTERS` (a cosmetic `→ EventFilters` rename
is deferred to keep this change contained — svelte-check would catch any drift). `event-detail.ts`
also exports `entitiesOf`, `headline`, `correlationId`, `queryParams` beyond the spec's list.

**Port the two SSR tests FIRST** (`event-stream.ssr.test.ts`, `event-stream.firm.ssr.test.ts`) —
every posture assertion must survive (proxied tint + "Acting on {target}" in operations posture;
"Graphene support · {actor}" with the acting-tenant chip suppressed in firm posture; entity hrefs;
firm icon present/absent). Add `event-detail.test.ts` for the extracted helpers.

> **FIX #4 (added in review):** the `?event=` id filter must be **ANDed with `scope_sql` AND
> `exclude_event_prefixes`**, never a keyhole around them — a firm deep-linking an `action.aml-…`
> id must still be denied by `FIRM_EXCLUDED_EVENT_PREFIXES`. Pin this with a test.

## D — Document activity into the stream

Document activity that the request-path `ActionEventsMiddleware` **cannot see** gains an
`audit.actions` row via a **new explicit writer `record_document_action`** (in `app/audit_actions.py`,
beside `record_automation_action`, reusing its INSERT + `redact()` + `current_correlation_id()`).
`documents.audit_log` keeps every writer (dual-write; it stays authoritative product state).

> **AS-BUILT SCOPE CORRECTION — fold three events, not six.** The plan's gap table below assumed
> six document events lived _only_ in `documents.audit_log`. In fact the middleware already emits an
> `audit.actions` row for every 2xx **mutating** document route — send (`documents-send-document`,
> with `contact_ids` in the subject, names resolved by the read-time entities resolver), fulfil
> (`documents-fulfil-request-item`) and reassign (`documents-reassign-request-item`, which carries
> the supersession). Folding those would **double-count** them in the stream. So D folds only the
> three genuine gaps — the ones that are a GET, an anonymous request, or not a request at all:

| Event                | Site                                                | As-built writer                                                                                                                                                                                                                                                                                                                                      |
| -------------------- | --------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `document.viewed`    | GET `/{uid}/url` + `/{uid}/content` (authenticated) | `event_type="document.viewed"`, `principal=…` (resolves tenant + actor via `auth.resolve_claims`); emitted via a **`BackgroundTask`** so the best-effort write is off the view's critical path (its own pooled connection is never held beside the request session)                                                                                  |
| `document.generated` | worker render loop (service_role)                   | `event_type="document.rendered"` (a DISTINCT id from the queue's `documents-generate-document`, so no collision/double-count), `actor_id=generated_by` (reads in the queuer's name, as `documents.audit_log` does), `actor_sub="system:worker"`, `session_maker=maker` (writes to the worker's own DB), emitted only when `_persist` claimed the job |
| `share.accessed`     | anonymous GET `/share/{token}` (service_role)       | `event_type="document_share.accessed"`, `actor_id=NULL` + `actor_sub="anonymous:share"` (no verified subject), IP + UA in `metadata` via `redact()`, written after the `system_transaction` commits                                                                                                                                                  |

> **FIX #1 (as-built) — service_role, one uniform shape.** `audit.actions` is `service_role`-only
> (FORCE RLS, no `authenticated` grant). `record_document_action` therefore **always** opens its
> **own `service_role` transaction, best-effort** (the `ActionEventsMiddleware._write_event` model,
> NOT `record_automation_action`'s caller's-session model) — the view path (authenticated) and share
> path (anonymous) cannot write on their own session, and using the same shape for the worker keeps
> a render failure from a rolled-back event write and vice-versa. The worker passes `session_maker`
> so its event lands in its own DB (and, in tests, the testcontainer). The event is not atomic with
> the act on any path — exactly as the request-path floor already is.

> **FIX #2 (as-built) — NOT NEEDED.** `document.sent` is a mutating route the middleware already
> covers, capturing `contact_ids` and resolving names at read time. No new work; not folded.

> **FIX #3 (as-built) — `describe.ts` vocabulary.** `VERBS` gained `view`/`access`/`render` so the
> three new types read "Viewed a document" / "Accessed a document share" / "Rendered a document".
> No `ENRICHED` builder is needed (the generic verb+noun path suffices), and `eventCategory` already
> routes the `documents`/`shares` tags to the documents glyph. The three types are deliberately kept
> OUT of `SUBJECT_BEARING_EVENT_TYPES` (they carry no `SUBJECT_FIELDS` entry, so listing them would
> make `recordedBeforeEnrichment` fire on every such row for ever). Both that list and the hint were
> removed in October 2026 — see `docs/api.md` — so this is now a note about a trap that is gone.

> **TEARDOWN DEFERRED.** The plan removed the `/documents/audit` page + the "Audit log" header
> button + the `links.audit`/`canViewAudit` plumbing. That is **deliberately not done** (Craig's
> call). Beyond "not yet", it is the safer default: `/documents/audit` is **book-scoped** (an adviser
> sees only their own clients' document events), whereas `/events` is **firm-wide behind
> `activity:read`** — deleting the page would _widen_ who sees document activity. The page, the
> button, `GET /documents/audit`, the Recent-activity column and the per-document Activity tab all
> stay; the fold and the page coexist (dual-write) until this is revisited.

> **As-built EventStream refinement (load-more).** Slice C's "Load more" grew a `?limit=` in the URL
> and re-fetched the whole longer list. Reworked to a true **client-side append**: the loader serves
> a fixed first page, and `EventStream.onLoadMore(before)` fetches the next keyset page from a
> same-origin proxy (`/events/more`, `/tenants/{uid}/events/more`) and appends it in place — no
> navigation, no URL change, no re-fetch of what is on screen. A cap (`MAX_EVENTS`) stops a runaway
> pull; past it the footer says to narrow the filters. When the whole trail is on screen the footer
> shows a quiet **"End of events"** marker, and both the button and the marker live INSIDE the scroll
> (where the next row would be), reached by scrolling to the bottom.

### D0 — cursor tailers left alone (considered, rejected)

The three `audit.actions` consumers fetch every row (`id > :pos` PK range scan, `LIMIT 25`) and
filter in Python; the new read events are fetched-and-discarded. At any plausible volume this is
milliseconds (a 200-recipient view burst = 8 back-to-back 25-row PK scans). **Do not** narrow the
tailers in this change — a filtered tailer must still advance past what it skipped, and a bare
`max(id)` jump loses stragglers committing above the cursor (why `LAG_SECONDS` exists). That's
delivery-critical work for its own change with its own tests, not a passenger here.

## E — Does the event carry enough?

Widen `SUBJECT_FIELDS` (the lever that also feeds `changeRows`, the entities resolver, and the merge
context — one change pays three times): add `item_titles` + `due_date` to the request-raise event
and `recipients` (ids; see FIX #2) + `message` (the covering note, client-facing free text, through
`redact()`, deliberately surfaced in the detail pane + a firm's `/events`) to the send event. Fix
`email_consumer._template_context` so `portal_url` resolves the recipient's own portal base
**independently of `cta_url`** (today a staff-audience document-template rule renders no button).

> **AS-BUILT (E).** The three surfaces are fed by DIFFERENT channels, so this is not one lever:
>
> - **`SUBJECT_FIELDS`** — added `message` to `documents-send-document` (a DELIBERATE free-text
>   exception, doctrine comment amended; `redact()` masks keys only, so the note lands verbatim by
>   design) and `due_date` to `documents-create-document-request`. Both now show in the `/events`
>   detail pane (`changeRows` reads `metadata.subject`). `recipients` was **moot** — `contact_ids`
>   is already captured and names resolve at read time (FIX #2, no work).
> - **`item_titles` is NOT captured** — `CreateRequest.items` is a nested list, not a top-level
>   field, so `extract_subject` can't reach it and the tripwire would fail. It is resolved at read
>   time; exposing it to the in-app notice + picker (alongside read-time `message`/`due_date`) is
>   **deferred to F2** (each new `Param` ships with its `build_context` resolution). The in-app
>   consumer does not resolve these today (only the email consumer does), so that dedup rides F1.
> - **`portal_url`** — fixed as `cta_url or portal_base` (call site passes the recipient's own base:
>   platform for a user, client for a contact), NOT the literal "independent of cta_url". Making it
>   the base ALONE would send the client's document email button to the portal home instead of the
>   document (a regression the existing `test_document_share_notice…` guards); `cta_url or base`
>   keeps the deep-link and only FALLS BACK to the base when the CTA is empty — which is what makes
>   the staff-audience document-template button render at all. (`cta_url` was renamed to
>   `button_url` in Aug 2026; the names above are the ones this decision was taken against.)

## F — The merge-field / variable system

- **F1** — extend `catalogue.Param` with `email_token: str | None`; make
  `email_consumer._template_context` build itself from `params_for(trigger)` instead of a hardcoded
  dict, so the notification (`{dotted.key}`) and email (`{{ jinja }}`) pickers agree by construction.
- **F2** — widen the advertised set (each new `Param` ships with its `build_context` resolution + a
  test): the already-resolved-but-unadvertised ids (`contact.id`/`account.id`/`task.uid`/
  `document.uid` — a **Links & identifiers** group); new cheap columns (`client.first_name/surname/
email`, `adviser.name`, `account.product`, `task.due_date/description`, `document.type`,
  `firm.support_email`, `actor.email`, `event.when`). **Never** AML/screening-shaped; keep
  `test_catalogue_params_match_subject_kind` passing (`document.name` genuinely unresolvable on
  generate / form-submit).
- **F3** — rebuild `notification-rules.svelte`'s picker: collapsible namespace groups + a search
  box + a "used" tick + an inline **unknown-token warning** (client-side only; don't make `_validate`
  refuse unknown tokens — would break stored rules). Keep click-to-copy + `previewOf`.
- **F3 (email)** — wire the dead `fields` prop (`email-editor.svelte:62-64`): pass the rule's
  trigger variables (in `email_token` spelling) when the editor opens from a rule; add search + show
  the raw token.
- **F4** — fix under-advertising (`_CODE_KEYS['notification']` + `merge-fields.ts` notification group
  vs what `_template_context` actually supplies) and over-advertising (`document_sent`/`message`,
  `request_raised`/`item_titles`,`due_date` — closed by E at the source).

## Migrations

**None.** `SUBJECT_FIELDS` is Python; the new writers use existing `audit.actions` columns
(`actor_sub` stays NOT NULL — synthetic subs are values); `documents.audit_log` untouched;
`activity:read` deliberately not renamed. Next free number would be `0085` if one proves needed.

## Build order

**A+B** (routes/nav/redirects) → **C** (SSR tests ported first, then the split) → **D** (writers +
`SUBJECT_FIELDS`, with FIX #1's service_role) → **E** → **F**. Regen the API client the CI way
(`OPENAPI_URL=apps/api/openapi.json`) right after the `fetch_actions` id filter lands. Verify each
slice's gates before the next; adversarial-verify C (SSR posture rails) and D (security rails).

## Out of scope

Retiring `documents.audit_log`; any change to the three cursor tailers (D0); renaming
`activity:read`; `document.viewed` as an automation trigger; the client portal (no events surface).
