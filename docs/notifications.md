# Notifications & notification email (design of record)

**Everything is event-driven.** A thing happens once and becomes an **event**; independent
**subscribers** turn events into an in-app notification and/or an email. Nothing sends a
notification by calling a "notify" function inline — the only things that create notifications are
the subscribers, and they only ever react to events.

## The bus — `audit.actions`

The event stream is `audit.actions` (migration 0041) — the append-only, cursor-tailable action-event
log already rendered in the **Activity** surface and already tailed by the Flows v3 automation
consumer. We do **not** stand up a second bus. Its vocabulary is `event_type = 'action.' +
<operation id>` (e.g. `task.created`), a closed, machine-routable set.

The events are the ones already written by `ActionEventsMiddleware` (`app/audit_actions.py`) — every
authenticated mutating 2xx request, post-commit — plus the automation actor's rows
(`record_automation_action`). The subscribers key off these **real** events; the rich
`metadata.subject` allowlist (`SUBJECT_FIELDS`) lets a rule resolve recipients without re-querying
(a document send carries `contact_ids`; a task creation carries the assignee).

**We do not manufacture events to make something notify.** If a moment matters, it already shows up
on the stream (a task creation is `task.created`; a flow that needs a human raises a
task, which is itself a task-creation event). Inventing a bespoke `notify`-only event would be the
opposite of event-driven — so there is no worker-side "emit an event just so it notifies" path. The
subscribers are pure consumers of what genuinely happens.

## Subscribers (independent, each its own cursor)

Both are worker loops (`app/worker.py`), each a cursor tailer of `audit.actions` (the shape of
`app/flows/automation.py::consume_once` — claim cursor `FOR UPDATE SKIP LOCKED`, read `id > pos AND
created_at < now - LAG`, react, advance cursor, one transaction; cursor seeded at the head on first
run so history is never replayed).

- **Notifications** (`app/notifications/consumer.py`, cursor `notifications.cursor`) — maps an event
  to in-app rows via `registry.RULES` and writes them through the single sink `service.notify`. The
  consumer is the **only** caller of `service.notify`.
- **Email** (`app/notifications/email_consumer.py`, cursor `notifications.cursor` consumer `email`)
  — the same rules and the same recipients, but the sink is a branded notification email
  (`email.shell` with the rule's title/body + a portal CTA for clients) enqueued to `flows.outbox`,
  so the dispatcher sends it and the non-production send guard + retry/dead-letter apply. It handles
  **notification** email only; transactional email (invite / reset / verify) stays inline. Gated by
  `settings.email_notifications_enabled` and, per recipient, the `email` channel of the 3-layer
  resolution — which defaults **off**, so nothing sends until a category's email is turned on. No
  dedup ledger: enqueue + cursor advance commit in one transaction, so a committed batch never
  re-enqueues and a crash replays cleanly (the in-app consumer's guarantee).

They share nothing but the stream and the preference store. Email is **not** a channel of a
notification — it is a peer subscriber.

**Document email is the subscriber's, not inline (the overlap is retired).** `documents/service.py`
used to email `document_sent`/`request_raised` inline, AND the `document.shared`/`document.request`
rules email the same templates — a double-send once the rule email is on. That inline send is now
**gone**: `send`/`create_request` grant portal visibility + persist their content (the covering note
on the `document.sent` event; the request + items), and the notification subscriber is the one email.
`email_consumer._document_extras` recovers what the event doesn't carry directly — the covering note
(from the `document.sent` audit event), the request's item list + note + due date (from the request
the action created, found by the subject contact) — so the subscriber email is as rich as the old
inline one. `send`'s `notified` count is now "how many recipients CAN be emailed" (have an address),
not a synchronous send result. Tasks still have their own notifier path (assignment + near-due
reminders), a separate concern from the `task.assigned` rule.

## Preference resolution — three layers

A subscriber decides, per (recipient, category), whether to act on each channel by resolving three
layers, most-specific wins:

1. **Registry default** (code, `registry.CATEGORIES`) — the shipped baseline.
2. **Tenant configuration** (`notifications.tenant_config`, admin-editable) — a firm-level default
   per category, plus an **enabled** flag. If a firm disables a category, it produces **nothing**
   for anyone in that firm, whatever a user's own setting says (the firm-wide off-switch). Absence
   ⇒ fall through to the registry default.
3. **Per-user override** (`notifications.preferences`, migration 0063) — the individual's own
   `in_app`/`email` choice, only within categories the tenant leaves enabled.

Effective channel = `user.channel ?? tenant.channel ?? registry.channel`, gated by
`tenant.enabled`. The notifications consumer reads the `in_app` result; the email consumer reads the
`email` result. Both tables live under the `notifications` schema (a later rename to a neutral
`comms` schema is optional and deferred).

## Admin configuration (per tenant)

Graphene operations configure a firm's notifications the same way they configure its branding,
emails and settings — under the admin tenant section (`/tenants/{uid}/notifications`), gated on
`tenants:write`. The surface is the categories × { enabled, in-app default, email default } matrix,
reusing the shared preferences component in a tenant-config posture (an `enabled` column + "default"
framing instead of a personal on/off). Endpoints: `GET/PUT /admin/tenants/{id}/notification-config`
(`app/notifications` service, `admin_session`). Firm self-service (the platform `/firm` side) can
mirror this later with a `notifications:configure` permission; admin is first.

## The registries (the human-named layer)

The raw `action.*` vocabulary is never shown to users. Each subscriber has a registry mapping event
types to a **category** (the preference group — Tasks, Documents, Journeys, Compliance,
Contributions), recipients, and copy/template. Recipients resolve from the event's `metadata.subject`

- book tenant (`target_tenant_id or tenant_id`), reusing the task-notifier joins (role → active
  members, contact → advisers). Pinned by a test, the automations/permissions idiom.

## Dynamic message copy (merge fields)

A rule's title/body may carry `{dotted.key}` placeholders that render against a per-event **merge
context** — so authored copy reads `"{client.name}'s bank details changed"` rather than a fixed
line. Three small pieces, all additive (an un-templated string renders to itself, so nothing shipped
changed behaviour):

- **`catalogue.params_for(trigger)`** declares the placeholders a trigger's copy may use, DERIVED
  from its `subject_kind` (never hand-listed per trigger): every trigger exposes `{actor.name}` +
  `{firm.name}`; a contact/account subject adds `{client.name}` (an account also `{account.name}` /
  `{account.number}`); a task adds `{task.title}`. A trigger with an extra resolvable entity declares
  it via `extra_params`: the document send offers `{document.name}`, resolved from the document id on
  the route **path** (`POST /documents/{uid}/send` → `audit.actions.path_params`), NOT from
  `metadata.subject` (which `extract_subject` fills from the body/query only). Only params we can
  **actually resolve** are offered — e.g. `document.generated` gets no `{document.name}`, since the
  new document's id is in the response, which the action stream never captures. The catalogue JSON
  serves `params` (key/label/example) so the builder can offer insertable chips + a live preview.
- **`context.build_context(session, action, tenant_id)`** resolves the event's subject + actor into
  a nested `{firm, actor, client, account, task}` dict, ONCE per action (the subject is the same for
  every rule) and only when some rule carries a `{`. Every lookup is defensive — an unresolvable
  name is simply absent, never an error that could stall the tail.
- **`render.render(template, context)`** does the substitution: it is **not** a template engine —
  only `{a.b.c}` tokens are recognised (a plain dotted lookup, no expressions/filters/attribute
  access), so authored copy can neither execute anything nor read outside the context. An unresolved
  token is **dropped** and the whitespace it leaves is tidied (`"A document {document.name} arrived"`
  → `"A document arrived"`), never left as a raw `{token}`. Pinned by `tests/test_notifications_render.py`
  and, end to end through the tail, `test_consumer_renders_merge_fields_in_authored_copy`.

The same context feeds the (deferred) email channel and is the shared foundation for manual email
templates, so a placeholder means the same thing wherever copy is authored.

## Categories → the events that drive them

Only categories that have a live rule off a **real** event exist — nothing aspirational:

| Category    | Driven by (real event types on the bus)                                                                                                                                                                    |
| ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Tasks       | `task.created` — a task assignment. (Flow/AML/journey "needs attention" reaches the bus when the flow raises a task, i.e. as this same event; nothing bespoke.)                                            |
| Documents   | `document.sent`, `document_request.created`                                                                                                                                                                |
| Messages    | `message.sent`(+`-attachment`) → the client's **adviser**; `message.sent` / `-group-send` (+ attachments) → the **client(s)**. Two single-audience triggers so each direction only pairs its own resolver. |
| Peer review | `review.requested` → members of the reviewer role; `review.approved` / `rejected` / `conflicted` → the proposer. Category `reviews`. See [`docs/reviews.md`](reviews.md).                                  |

**Chat messages notify the OTHER party** (the point of the messaging bell): a client's message
notifies their book adviser (resolved from the sender's login → `crm.contact_users` →
`crm.client_advisers`), and an adviser's message notifies the client (the conversation's contact, or
every group member). The message **body is never captured or shown** — there is deliberately no
`SUBJECT_FIELDS` entry for these events; the copy carries only the sender's name (`{actor.name}`). It
is additive to the live SSE push (`live_push`), which stays the ephemeral in-thread delivery.

Deliberately **not** categories: Journeys / Compliance(AML) / Contributions do not get bespoke
events — their attention moments arrive as tasks (or, for contributions, will map to a real
classify/booking action when that rule is added and verified). The category list = the live rules,
and it is what the admin config surfaces.

## Guardrails

- **AML tipping-off** — both registries use neutral copy for the compliance category (no hit
  detail), operations-recipients only, never the firm. A firm sees screening outcomes via
  `screening:read`, never via a notification or email.
- **Transactional email stays inline** — invite and password-reset are sent on their existing inline
  paths, not through the email subscriber. They are not notification-class and must not depend on a
  best-effort post-commit event; both also hand their caller the send's boolean, which is the only
  signal that the mail actually went out and is a thing no queue can answer. Document-sent and
  document-request are NOT in that set: they are notification-class and the subscriber renders and
  enqueues them like every other rule-driven notice.

## Status

- **Built** — the bus (0041), the notifications consumer + `service.notify` + `registry` (0063),
  the worker loop, the API (feed / unread-count / read / dismiss / preferences, 0064), and the
  in-app UI across all three portals. Live rules: documents + manual tasks.
- **Realtime delivery** — the bell no longer depends on polling alone. The worker ANNOUNCES each
  new in-app row on a Postgres `LISTEN`/`NOTIFY` channel (`notifications_realtime`, from the
  consumer's own transaction so it fires on commit — a rolled-back batch announces nothing); the API
  process holds a listener (`realtime.listen_forever`, started in the lifespan) that pushes
  `{"type":"notification"}` to the recipient's transport-neutral subscriber, reusing
  `live_push` with `ntf:`-prefixed keys. The browser opens same-origin EventSource; its portal
  mints the single-use ticket over private `API_URL`, opens `/notifications/events` internally and
  pipes the SSE response back. On a push the bell REFETCHES the unread count — never trusts the
  payload. The **poll stays as the fallback** for a dropped stream; the cross-process bridge is the
  piece messaging didn't need (its producer shares the API process).
- **The seed is streamed, and the stream's first connect does not refetch it** (Oct 2026). The
  platform and admin root layouts used to await `GET /notifications?limit=8` (and the platform's
  `GET /messaging/unread`) before returning, which put both on the critical path to the first byte
  of every page; they are returned as promises now and SvelteKit streams them behind the shell.
  The bell cannot simply be handed a promise — it records the rows it MOUNTS with as already
  announced, so mounted empty and handed the rows a moment later it would pop the whole backlog as
  cards — so `StreamedNotificationBell` (`@graphene/ui`) holds a same-sized placeholder until the
  seed lands and then mounts the live bell with it; plain values render at once, so the client
  portal's layout is unchanged. The bell and the platform's Messages dot then skip the refetch on
  the EventSource's INITIAL `open` (the seed is the server's answer from a moment ago) and still
  refetch on every reconnect, which is what closes the gap a dropped connection leaves.
  `arrival-toast.dom.test.ts` and `streamed-notification-bell.*.test.ts` pin both halves.
  `app/notifications/realtime.py`, gated on `notifications_enabled`; tests in
  `test_notifications.py` (`key_for` / `announce` dedup / an end-to-end LISTEN receiving a delivery).
- **Done** —
  - The consumer + `service.notify` + registry (documents + tasks, off real events), the worker
    loop, the API (feed / unread-count / read / dismiss / preferences), and the in-app UI across all
    three portals.
  - **Tenant configuration** — `notifications.tenant_config` (0065), 3-layer resolution, `GET/PUT
 /admin/tenants/{id}/notification-config`, the admin "Notifications" page. Surfaces the live
    categories only.
  - **RLS scope fix** — the personal feed is scoped to its recipient; a staff adviser does not see a
    client's notifications (0066).
  - **Reconciled to pure event-driven** — no manufactured events (the `emit_event` helper, the
    flow-task-raised emit and the journeys emit were removed); notifications play off real events
    only.
  - **Email subscriber** — a peer consumer off the same bus (`email_consumer.py` + worker loop),
    rendering branded notification emails to `flows.outbox`, gated by `email_notifications_enabled`
    - the `email` preference (default off, so inert out of the box).
  - **Model-tenant config** — the shared per-tenant page, linked from `/model`, and cloned to new
    firms on creation.
- **Next** —
  1. **Contributions** category — deferred until there's a real "needs attention" event (a task
     raised for unclassified credits), not the classify/booking action.
  2. Retire the inline document email in favour of the subscriber, when event-driven document email
     is wanted (removes the default-off overlap).
- **Deferred by decision** — the email channel ships disabled from the user's side until (3) lands;
  the `notifications` → `comms` schema rename.

---

# The event registry — the builder, presented event-first (Sep 2026)

The builder stays at `/notification-rules` — the route says what it configures. The rules,
the storage and the write
endpoints are unchanged; what changed is that an author picks an EVENT and sees what is attached to
it, rather than picking a rule off a list grouped by audience.

**It has no nav item, deliberately.** The registry is configuration FOR the event surfaces, so
it is reached by a "Configure notifications" button on Event logs and on Activity rather than
by a menu entry of its own — a bell in the sidebar said "notifications", which is the framing
this rename moved away from. The button is gated on the REGISTRY's `notifications:read`, not on
the host page's `activity:read`: gating it on the page it sits on would hand a reader a button
the route refuses, which is the nav's own "hide it rather than dangle a 403 trap" rule applied
to a button. `/notification-rules` still 301s here, so old links keep working.

It is **one component, not two**. `NotificationRules` takes `groupBy: 'audience' | 'event'` —
`audience` is the default and is exactly what the admin surface still mounts, so ops is unaffected.
`displayRows` already walked the catalogue to build the list; the registry is that same list
grouped by category with the built-ins made visible. Extracting the editor into a shared dialog was
considered and rejected: it would have moved the whole editor out from under three source-scanning
tests to gain nothing the flag does not.

**What an event needs to notify anyone is a SUBJECT SOURCE, and that is a narrower thing than it
looks.** `automation._SUBJECTS` (read publicly via `events_with_subject_source`) is what every
generic audience resolver funnels through — it reads `path_params` first, then captured metadata,
then a SQL fallback. An event absent from it resolves to nobody, however much its route appears to
name something. This is NOT the same set as `event_schema.REGISTRY`'s subject-bearing events: that
records what an event CAPTURES, and a capture with no source declared is unreachable. The registry
reports the difference as its own state, because closing it is a declaration rather than new
capture. An earlier version of the registry classified against `REGISTRY` and so reported the wrong
thing.

The four states, with today's counts of 207 declared events:

| State        | Count | Needs                                                                                                   |
| ------------ | ----- | ------------------------------------------------------------------------------------------------------- |
| `assignable` | 21    | nothing — authorable now                                                                                |
| `ready`      | 0     | a five-tuple in `catalogue._OFFERED`. Empty, and pinned so: every declared source already has a trigger |
| `declarable` | 14    | a `_SUBJECTS` entry. The cheapest real work here                                                        |
| `actor_only` | 172   | an actor or role audience — a client or adviser cannot be resolved                                      |

`actor_resolvable` is true on every row: `_resolve_actor` reads `actor_id` and needs no subject at
all. That is what makes `actor_only` a state rather than a dead end, and it is where the security
events sit (`authentication.failed`, `authentication.mfa_removed`) — those arguably want the ACCOUNT
HOLDER rather than the actor, which is a real gap and not a labelling one.

**The `actor_only` rows are HIDDEN by default, and the surface says so.** 172 of 207 put a wall
between a reader and the 35 they can act on. Hidden rather than dropped: the note above the table
gives the count and offers them, so the list never quietly reads as every event the platform
records. Filtering explicitly TO that state shows them regardless — a filter that selects a state
and then hides every row of it is a dead control.

**Built-ins are rows here, and that is the point.** `resolve.effective_rules` emits a catalogue
default when nothing is stored, so a firm whose clients receive the shipped `document.shared`
notice has no row for it — and the builder, which lists stored rules only, showed that firm
"nothing configured" while its clients were being emailed. The registry renders those pairs marked
_Default_, with no History or Delete (there are no versions of something never authored, and
nothing to delete), and materialises one through the ordinary save when it is first edited.

`GET /firm/event-registry` folds three things into one call. The two counts —
enabled automations, and sends in the last 30 days from `comms.messages` — are joined on the raw
`event_type`, **not** the friendly key: `flows.triggers` calls the event `document.sent` and the
notification catalogue calls it `document.shared`, so a join on either key alone counts nothing.
`tests/test_firm_event_registry.py` pins that bridge.

The registry deliberately lists only the ~17 notifiable events, not the ~200 `actions.EVENT_TYPES`
declares: a notification can only be authored against the catalogue, and 183 rows nothing can be
done with is a trap. The page says so and links to `/logs`.

# Authorable notification rules (the builder) — built, Aug 2026

Turns the hardcoded `registry.RULES` into **firm-configurable rules** authored by selecting an
event, mirroring the automations engine (curated triggers over `audit.actions`) and the
documents wiring for client scoping + permissions. **Built end to end** (branch
`feat/notifications`): the curated catalogue + stored rules drive both consumers, the admin and
firm builders author rules, and clients set their own contact-keyed preferences. The catalogue
built-ins reproduce the v1 shipped behaviour exactly, so a tenant with no stored rule notifies as
before; a stored rule overrides or adds to it.

## The model — one recipient per rule (decided)

`notifications.rules` (migration `0068`), one row per authored notification:

- `tenant_id`, `uid`, `trigger_key` (from the curated catalogue), `label`, `enabled`.
- `audience` — the single recipient kind (client / adviser / role / actor); the catalogue entry
  declares which audiences are valid for that trigger. (To notify BOTH an adviser and a client,
  author two rules — the deliberately simpler model over a multi-target row.)
- `title`, `body`, `link` — the copy for that audience. **`link` is where a click goes.** The
  builder offers a destination picker, not a raw path box: **named targets that resolve to the
  SPECIFIC item** ("The document" → `/documents/{document.uid}`, "The account", "The client", "The
  task"), a fixed page, or a custom path. A target is stored as a TOKEN path the consumers render
  per-notification (`catalogue.link_targets_for` declares the targets + a `client` flag; the id
  reaches the merge context in `notifications.context`). A target is offered to a CLIENT audience
  only where the client portal has that route (`/documents/{id}`, `/accounts/{id}`; not `/clients` or
  the task view). A blank `link` still deep-links a staff notice to its subject record via
  `consumer.py::_click_link` (the pre-picker fallback).
- **A rule SELECTS an email template — it does not carry one.** `notifications.rules.email_code`
  (0083) points at one of the shared, firm-editable email templates (`email.templates`, by `code`, the
  same templates the Emails page manages). `NULL` means the general **`notification` wrapper** — a
  single branded layout whose slots are filled with the notification's OWN `title`/`body` and a CTA to
  its `link` — which is the default for almost every rule, so a firm designs the notification email
  ONCE and every rule follows it. The **two document notices ship pointing at their own templates**
  (`document.shared` → `document_sent`, `document.request` → `request_raised`, set on the catalogue
  `Audience.email_code`), so turning their email on delivers the rich document email rather than the
  bare wrapper; every other rule can be pointed at a template the same way via the builder's **Email
  template** dropdown (`rules_service.email_templates_json()` → `/…/notification-email-templates`; the
  selectable set is every notification-category code, `notification` included; the category is the
  whole rule, since the one code that used to be excluded by name — the retired external
  secure-link share — left the catalogue with its feature). `email_consumer.py` renders the notification's copy + link
  for THIS recipient (merge fields, absolute CTA), then `render_or_default(rule.email_code or
'notification', ctx)` produces the email (the firm's published template, else the shipped default —
  fail-safe). The context is a superset: the wrapper's `title`/`body`/`cta_*` PLUS flat aliases
  (`first_name`/`document_name`/`sent_by`/…) derived from the notification's merge context, so a
  custom template resolves too — with the caveat that a value the EVENT does not carry (an adviser's
  covering note, a request's item list) renders blank on this path (the "enrich the event" follow-up).
  This is why the notifications consumer is exempt from `test_email_send_parity`'s per-code literal
  pins (`DYNAMIC_TEMPLATE_SITES`) and its wrapper context is pinned separately
  (`test_notifications.test_wrapper_context_fills_the_slots`). Transactional/auth email
  (reset-password, invite) stays a separate System code, only sharing the branded shell. _(Historical:
  an earlier iteration folded per-rule email copy, then a per-rule GrapesJS design, onto each rule —
  `notifications.rules.email_subject/email_body/email_html/email_css` from 0081/0082. Both were dropped
  in 0083 for this template-selection model.)_
- `default_in_app`, `default_email` — the **opt-in vs opt-out** default (on = opt-out).
  **Four built-in audiences ship email ON:** the two client document ones (`document.shared`,
  `document.request`) and the two task ones (`task.assigned`, `task.due_soon`, audience
  `assignee`). Task email being ON reverses the posture of the bespoke sender the catalogue
  replaced, which shipped it opt-in — a task you have been assigned is what email is for, and an
  in-app-only notice is one nobody sees until they next open the portal. It is a real change for
  existing firms and no migration writes the old default: `resolve.effective_rules` falls back to
  the audience default whenever a tenant holds no `notifications.rules` row, and `task.due_soon` is
  a NEW trigger key, so no stored row can shield a firm from it. A firm that wants it off turns it
  off per category in `notifications.tenant_config` — the same row the old sender read.
- `mandatory` — the recipient **cannot disable** it (forced to the default; no override honoured).
- `client_can_disable` — for a client-audience rule, whether the client may turn it off.
- `firm_editable` — whether the FIRM may edit this rule (the ops guardrail).
- `authored_by` — `graphene` | `firm`.

## Curated event catalogue (`app/notifications/catalogue.py`)

Human-named triggers over the `audit.actions` vocabulary — the builder's menu, never raw
`event_type` strings (the automations-triggers pattern). Each entry: key, label, description, the
`event_type`(s), the valid `audience`s + how each resolves recipients, and default copy. The
existing `registry.RULES` become the seeded/built-in defaults expressed as catalogue entries.

## Two-tier authoring + RBAC (modelled on documents/firm)

- **Graphene ops** — admin, per tenant + the model tenant (clones to new firms on creation). Authors
  rules and sets the guardrails: `mandatory`, `firm_editable`, `client_can_disable`. `admin_session`
  - `tenants:write` (or a dedicated ops perm).
- **The firm** — the platform `/notification-rules` page, `firm_session` + the
  **`notifications:read`/`notifications:write`** permissions (registered in `app/permissions.py`;
  `NOTIFICATIONS_MANAGE` is the `write` constant, so the slug is `notifications:write`). A firm's own
  rules are always `firm_editable` (not its guard to set); it may also edit — but never delete — an
  ops rule the admin marked `firm_editable`, and cannot reach another tenant's rules (404, not 403).
  Endpoints: `GET/POST/PATCH/DELETE /firm/notification-rules` + `GET /firm/notification-catalogue`
  (`_firm_rule` is the guard; pinned by `tests/test_firm_notification_rules.py`).
- **The client** — the client portal Account → Notifications, scoped by **contact** (the documents
  pattern: `contact_id`/`auth.contact_scope()`). `GET/PUT /notifications/client-preferences` returns
  the client-audience categories merged with the shipped defaults and upserts one preference row per
  contact in the caller's scope; RLS's `WITH CHECK` confines the write to the caller's own contacts.
  A `mandatory` category is still shown, but the resolver forces its default regardless, so toggling
  it is a no-op by design (the firm/admin guardrail wins). Pinned by
  `test_client_preferences_roundtrip_is_contact_keyed`.

## Resolution (extends the current 3 layers)

Per recipient of a rule's audience: **if `mandatory`, forced to the rule default** (no override);
else `override ?? rule default`, where the override is the recipient's own preference
(`notifications.preferences`, user- or contact-keyed) within tenant bounds. `tenant_config` folds
into the rule defaults. The client's contact-keyed preference is what the client portal writes.

## Consumer change

The tail resolves the book tenant, then reads **built-in defaults + that tenant's stored rules**
matching the event, and applies the resolution above. Recipient resolution generalises to the
catalogue's per-audience resolvers (client-contact / adviser / role / actor).

## Surfaces

- **Admin builder** (`/tenants/{uid}/notifications`, + `/model`): "Add notification" → pick a
  trigger (catalogue) → pick the audience → copy → channels + the guardrail flags; list/edit/delete.
- **Firm builder** (platform, `notifications:manage`): the same builder restricted to `firm_editable`
  rules.
- **Client preferences** (client portal): toggle the optional rules addressed to them.

## Also in scope

- **Client-portal notifications UI polish** beyond the `0067` leak fix.
- The `notifications:manage` permission + its grant into roles (RBAC), and the WorkOS role push.

## Phases (all shipped)

1. ✅ Migration `0068` (`notifications.rules` + RLS mirroring documents; clone-on-creation via
   `provisioning.clone_model_notification_config`) + the catalogue + built-in defaults as entries.
2. ✅ Both consumers (in-app + email) read stored rules via `resolve.effective_rules`, with
   generalised recipient resolution; resolution honours mandatory/defaults.
3. ✅ Admin CRUD API (rules + catalogue endpoint) + `notifications:read`/`notifications:write`
   perms; api-client + permissions regen.
4. ✅ Admin "Add notification" builder UI (`/tenants/{uid}/notifications` + `/model`).
5. ✅ Firm builder (platform `/notification-rules`, `notifications:write`) + client preferences
   (client portal Account → Notifications, contact-scoped).
6. ✅ Tests (`test_firm_notification_rules.py`, the client-prefs round-trip, the rules-service CRUD
   - email-subscriber cases in `test_notifications.py`) + docs (this section).
