# Outbound communications — the email ledger

What we sent, to whom, why, and what the provider did with it. Schema `comms`
(`0116_comms_messages`), writer `app/comms/ledger.py`, surfaced by the platform portal's
**Activity** page.

## Why it exists

`app/email.py::send_email` has always been the single chokepoint every send funnels through,
and until now it recorded **nothing**: it returned a bare `bool`, discarded the id Resend
answers with, and wrote no row. So the only durable evidence a client had been emailed was a
`flows.outbox` row reading `delivered` — which means "Resend accepted it", not "it arrived" —
and a firm asking _did my client get the suitability report_ had nothing to read.

Note what that boolean collapses: not configured, malformed recipient, suppressed by the
stage guard, and a genuine failure are all `False`. Those are four different answers to a
firm, and three of them are not "the email failed".

## The two tables

**`comms.messages`** — one row per send attempt, whatever the outcome. It carries the
envelope (`to_address`, `to_domain`, `subject`), the rendered `html` (the Activity preview —
the author's original, because inlined `cid:` images only resolve in a mail client), the
`request` JSON that was posted (or would have been) — method, url, body, with attachment
bytes stripped — why it was sent (`template_code`, `event_type`, `rule_uid`, `category`),
and the current `status` with its stamps.

**`comms.message_events`** — the provider's callbacks, append-only, one row each, unique on
`(message_id, provider_event_id)`. That constraint is what makes Resend's at-least-once
redelivery a no-op, and what lets the detail pane count a repeated callback rather than
flattening it into one stamp.

The current status on the parent is denormalised and could be derived from the events. It
deliberately is not: the feed's hot path is "this firm's last 50 messages with their state",
and deriving that per row is a join and a sort per message for a value that changes at most
six times in its life.

## Status is monotonic

Ranked `sent < delayed < delivered`. `bounced`/`complained` win from any rank — a
deferred address that later hard-bounces is genuinely bounced, and that is the more important
fact than "we once delivered to it". Nothing walks a bounce back.
`unconfigured`/`suppressed`/`failed` are set at INSERT and never advance, because nothing was
posted for a provider to have an opinion about.

This is the `contributions.ml_status` rule, and it is not theoretical: Resend delivers at
least once **and out of order**, so a redelivered `email.sent` routinely arrives after
`email.delivered`. Without the rule, a delivered email would display as merely sent.

The rank lives once, in `ledger.STATUS_RANK`/`ledger.advance` — a CHECK constraint cannot
express "only forwards" without a trigger, and a trigger would be a second writer.

## What "delivered" does and does not mean

`delivered` means the receiving mail server accepted the message. It does not mean a human
saw it, and it is not a read receipt. It is the last thing we can honestly claim.

## No read receipts

There is no `opened` and no `clicked` — not demoted, **absent**. No status, no stamp column, no
stored event.

An open is a tracking pixel loading, and it answers neither question a firm actually has:
Apple Mail Privacy Protection prefetches images for every Apple Mail user, so an "open" is
recorded whether or not a human looked, and a client that blocks images produces no open
however carefully the mail was read. The signal is evidence in neither direction — and
collecting it means putting a tracking pixel in a client's email, which is the firm's decision
to make rather than ours, and not one worth making for a figure nobody can rely on.

So `ledger.NOT_TRACKED` refuses `email.opened`/`email.clicked` **at the door**, before the
message is even looked up: the provider's tracking payload never reaches
`comms.message_events`. The route answers `{handled: false, reason: "not_tracked"}` — a 200,
like every other outcome, because a retry cannot improve it. Resend emits these only when
open/click tracking is switched on in its dashboard, so with it off the set never fires at
all; the refusal is there for when it is on.

## The delivery fallback (`COMMS_POLL_SECONDS`)

The webhook is pushed, and **it cannot reach `localhost`**. So on a dev box nothing ever advances:
every message sits at `sent` for ever and the whole timeline reads as broken rather than as
un-notified. The same is true of any deployment whose endpoint is not registered yet.

`app/comms/poll.py` closes that: a worker loop asks **`GET /emails`** — one request answering for
Resend's most recent page — for messages still at `sent`/`delayed` (within 72 hours, 25 a pass,
**newest first**) and feeds each answer's `last_event` through **the same
`ledger.apply_provider_event`** the webhook uses — so the rank, the stamps, the read-receipt refusal
and the idempotency live in one place and cannot drift between the two paths. The synthetic
`provider_event_id` is `poll:{last_event}`, which makes a repeat poll a no-op on the same unique
index a redelivered callback hits.

Two honest limits. **A polled stamp records when we LEARNED, not when the event happened.** Resend
publishes no per-event timestamp anywhere outside the webhook payload — `GET /emails/{id}/events`
does not exist (405) — and the retrieval object's `created_at` is when the email RECORD was created,
so stamping a delivery with it put `delivered_at` before `sent_at` and every polled message rendered
"Delivered 11:10:55 -> Sent 11:10:56". The only lever on the gap is asking more often, which is what
one request per pass buys. And the poll learns the provider's CURRENT opinion, not the sequence: a
message that bounced after delivering shows `bounced`, with no second timeline entry — where the
webhook, arriving per event, has both.

**Why newest-first.** The provider's list is newest-first and cannot be filtered by id, so a message
older than the page window is simply absent from it — and absence therefore means "not on this
page", never "no news". Those rows fall through to a per-id `GET /emails/{id}`. The database scan
runs newest-first to match: scanning oldest-first handed the pass exactly the rows that page could
not answer for, so the whole batch fell through and a pass cost one MORE request than before the
list call existed.

**The webhook gets first refusal.** A message younger than `WEBHOOK_GRACE_SECONDS` (60, three passes
at the default cadence) is not polled at all — as long as `RESEND_WEBHOOK_SECRET` is set. This is
correctness, not politeness: the webhook carries the provider's real event time where a poll can
only record when it learned, and `apply_provider_event` stamps `COALESCE(stamp, :occ, now())`, so
**the first writer owns the timestamp for ever** and `advance` then refuses to move an
already-`delivered` row — the webhook's real time, arriving a second later, is discarded rather than
applied. That used to hold by luck: a minute's cadence scanning oldest-first reached a seconds-old
message long after it had settled. Asking every twenty seconds and scanning newest-first made this
loop the likely first speaker for exactly the freshest row, so the grace is what makes the rule true
on purpose. **With no secret the grace is zero**, because then there is nothing to wait for — the
route refuses every callback (`not_configured`), so the poll is not a net under a live webhook but
the only source of a delivery state at all. Both halves read the same setting so they cannot
disagree about whether the webhook exists.

The sweep carries a **cursor**, so the tail cannot starve. Without one it re-read the same newest 25
rows every pass: a message the provider never resolves (`last_event: "failed"`, an unknown state, a 404) stays pending for the whole horizon, so 25 such rows would consume every pass and nothing older
would ever be polled. Each pass takes the next 25 **downward** and starts from the newest again when
it runs out — the same fairness as before, the other way up. The cursor is in memory, not a column:
it is a fairness hint, not a fact about a message, and losing it on a restart costs one re-read of
the newest rows, which are the cheapest ones to re-read.

Selection is **one row per provider id, the newest**, because that is the row `apply_provider_event`
resolves to (0111 left the provider-id index non-unique on purpose). Asking about one row and
writing to another would leave the asked-about row unchanged and re-polled for ever. And the
decision to write is made first, with `ledger.advance` — an answer that cannot move the row earns no
transaction and no event row, where the webhook records those anyway because there the callback
itself is the evidence.

`COMMS_POLL_SECONDS` defaults to **20 and is ON**, alongside the webhook rather than instead of it:
the webhook is the half that stops silently (a deleted endpoint, a rotated secret, a deploy that
lost the variable) and a net that is off on the deployment that needed it catches nothing. Twenty
seconds is affordable because `messages_pending_idx` (0111) is partial on exactly the unresolved
set, so a pass is one index scan over a near-empty index — no sort, no provider call, and the same
cost whether the table holds a thousand rows or a million. `0` disables it, and then the loop
returns rather than idling. The free part is the HEALTHY case: an unresolved message is re-asked
about on every pass until it settles or ages out of the horizon, so a stuck one costs three requests
a minute for as long as it is stuck — bounded by the batch, and the price of noticing. It is in the
worker's `LOOP_STALL_SECONDS` **only when it is on** — `healthcheck` reads a missing heartbeat as
"never ran", so a deliberately-disabled loop would otherwise report every webhook-configured
deployment permanently unhealthy. Set it locally (20 is plenty).

## The delivery webhook

`POST /webhooks/resend` is how a message advances past `sent`. It carries no principal (the caller
is a vendor), so it sits on `actions.EXEMPT_MUTATING_ROUTES` and runs on
`system_transaction` — there are no claims for RLS to scope by, and the row is found by an id we
minted ourselves.

**`RESEND_WEBHOOK_SECRET` must be set or the endpoint does nothing.** Blank disables it outright
rather than accepting unsigned callbacks: this route can mark a message `bounced`, which is a claim
a firm will act on, and absence of configuration means "do nothing" here exactly as an explicit
blank `EMAIL_ALLOWLIST` suppresses every dev and staging send. Point Resend's webhook at the
deployment and paste its `whsec_...` signing secret in.

Signatures are Svix's: the signed payload is `{svix-id}.{svix-timestamp}.{body}`, keyed by the
**base64-decoded** secret past its `whsec_` prefix — signing with the displayed string builds a
verifier that rejects every genuine callback. The header carries space-separated `v1,<sig>` values
(Svix sends two during a rotation, and any match passes). There is a ±5 minute timestamp tolerance,
without which a captured callback is replayable for ever. The body is verified as **raw bytes
before anything parses it**, because re-serialising would check a different value than the one
signed.

Every outcome is a 200 — Resend retries a non-2xx, and an unparseable body, an unknown message id
or an unmodelled event type are all things a retry cannot improve. An unknown id in particular is
not an error: a callback for a pruned row and one for mail sent by another deployment sharing the
Resend account look identical.

## Recording rules

- **One INSERT, after the attempt** — not a queued row updated on completion. `flows.outbox`
  already _is_ the queued state for notification email. The honest cost: a process killed
  between the POST and the INSERT sends an email this ledger does not know about. That is the
  same best-effort floor `audit.actions` accepts.
- **Its own connection.** `record` opens a `system_transaction` rather than taking the
  caller's session — the mirror of `flows/ai_usage.py`. There the meter must survive the
  rollback of the work it measured; here the mail has _already gone_ by the time we write, so
  a row that rolled back with its caller would erase the record of a send that really
  happened. It also means `send_email` needs no session, which matters because half its
  callers are the worker's loops.
- **It never raises.** An email must not fail because the ledger did.
- **Every exit path records except one.** The malformed-recipient branch records nothing:
  `to_address` is NOT NULL and would have to hold the value, and that branch refuses
  _precisely because_ we could not decide what the value means. Pinned structurally by
  `tests/test_email_send_guard.py`.
- **`ctx` is optional.** An un-threaded call site degrades to an unattributed row (visible to
  Graphene operations) rather than to a hole in the ledger nobody can see.

`SendContext` is one value rather than eight loose keyword arguments, so adding a field is a
change in one place. The worker path is the exception to "the caller knows why": `_send` runs
in the worker with no request session and cannot resolve a rule, a template or a recipient, so
the producer (`notifications/email_consumer.py`) puts a `comms` block in the outbox payload and
`flows/notifications._send` reads it back — exactly as `sender`/`reply_to` already work. A row
queued before that key existed still dispatches, recording with the tenant alone.

## RLS

Scoped by `auth.tenant_lineage()`, and it **deliberately omits `auth.contact_scope()`**.

This is the load-bearing decision. An adviser's contact scope is their whole book, so adding
`contact_scope` "for symmetry" with `0026` would hand a **client-portal principal** the ledger
of every mail sent about them, `html` bodies included. A client has their own inbox; this is
the firm's operational record. `docs/aml.md` makes the same argument about screening.

`tests/test_comms_rls.py` guards it two ways — behaviourally, with a client principal whose
`contact_scope()` covers the exact recipient contact, and by asserting no `comms` policy
mentions `contact_scope` at all. The behavioural test alone would pass against a leaking
policy that happened to match no rows.

A `tenant_id IS NULL` row is a Graphene-side send and is readable under `auth.is_system()` only.
Nullable on purpose: a NOT NULL tenant would drop an unattributable send, and the gap it exists
to show would become invisible.

`authenticated` holds `SELECT` and nothing else. Reads are deliberately unrestricted (Activity
and the event registry both SELECT); what stays in one place is the **write**, because
`advance` is only monotonic while every UPDATE goes through it.

## The Activity surface

`/activity` in the platform portal (Firm settings → Communications), gated on `activity:read` —
the same permission as `/logs`, because both answer "what has this firm been doing" and a firm
administrator who may read one has no reason to be refused the other.

`GET /firm/comms/messages` serves the listing, `…/{uid}` the detail. Scope is hard-coded from the
session with no `?tenant=`, and that predicate is load-bearing: `firm_session` runs as
`service_role`, so RLS is bypassed and the policy above is a second line for other readers rather
than for this path.

**The same feed is a client-record section**, `/clients/{uid}/activity`, beside the Events tab —
where the question is not "what has this firm sent" but "did THIS client get the invite, and did it
arrive". `?contact=` narrows it, and it takes the client's **uid**, the same spelling as the action
stream's, so the record's two feeds narrow the same way. Both are resolved by
`routers/firm.py::_contact_filter_id`, and the one rule that matters there is that an
**unresolvable client narrows to nothing, never to the firm**: the streams read a `None` contact as
"no predicate at all", so returning it for a uid that matched no row does not narrow, it widens. The
Events tab shipped sending the serial (which matches no uid) and answered its first page with every
client's events, while its own "Load more" — which sent the uid — answered with that client's.

The UI is `@graphene/ui/comms` — a subpath export, not the package root, since `MessageRow` and
`statusMeta` are too generic for that namespace. `MessageStream` is master–detail; the timeline
derivation (`timelineFor`) is pure and separately tested, because "Sent 14:02 · Delivered 14:02 ·
Delivered 14:02" is the whole point of the page and a wrong ordering there looks like a timeline
either way.

Three rules the surface keeps:

- **`sent` is `info`, never `success`.** Handing a message to Resend is not delivery, and colouring
  it green is the exact overclaim the ledger exists to stop.
- **A suppressed send is `neutral`, not an error.** A dev box's entire feed reads "Not sent";
  tinting it red would say the system is broken. The endpoint returns `sending_disabled` alongside
  the rows so the page can say _why_ — carried on the feed's own response rather than read from
  `/health/ready`, which is infrastructure (out of the OpenAPI schema, 503s on unrelated outages,
  and no portal calls it).
- **The body renders in `EmailPreviewFrame`** — the product's one way of putting an email on
  screen, a `sandbox=""` iframe. The HTML is firm-authored template output that is deliberately not
  sanitised server-side, so `{@html}` would run injected script under the adviser's session. Pinned
  by a source scan over the component's markup.
- **Raw is the wire request, not a second dump of the preview HTML.** `html` is what the iframe
  shows (the author's original); `request` is the JSON that was posted (cid: body, attachment
  metadata, no bytes, no Authorization). Rows written before that column reconstruct the envelope.

## Growth

`html` is capped at `MAX_HTML_BYTES` (256KB) and stored **whole or not at all** — half an
email renders as a broken one, which reads as corruption rather than as "too big". `request`
uses the same cap (replacing the body's `html` with an elision marker first if the serialised
JSON is over — a missing key would read as "we sent an empty email").

**The body is stored ONCE.** It briefly went in twice — as the preview `html` and again inside
`request.body` — which for an email with no inlined images is byte-identical (measured on real
rows: 3,952 bytes each) and doubled the largest column on the table for nothing. Where the two
match, the request records `SAME_AS_PREVIEW` and the detail pane splices the stored body back in
for Raw; where inlining genuinely changed it, the wire copy is kept in full, because the `cid:`
references are the only place "did the logo go as an attachment" is answerable from.

So a 20–60KB email costs 20–60KB, and a firm sending 1,000 a month adds a few hundred MB a year.
There is still **no pruning job**. If volume demands it, the
follow-up is to move the body to the tenant's own bucket (the messaging-attachments pattern)
and keep a key on the row — deliberately not done now, because it would put a storage dependency on the send path, and a best-effort
failure there would mean previews silently going missing.

## The sending domain

`EMAIL_FROM` is the platform sender. A firm that should send as itself has one row in
`email.sender_domains`, registered by Graphene operations (admin → the firm's Settings → Sending)
on the shared Resend account. Resend returns the DKIM and SPF records; the firm publishes them at
its own DNS provider. Use a subdomain (`mail.example.co.uk`) rather than the firm's root domain.
The page also offers the optional DMARC record (`_dmarc`, `v=DMARC1; p=none;`). Resend does not
check it, and sending does not wait for it. **Download DNS records** is a CSV of those rows — the
required ones and DMARC — for whoever publishes the firm's DNS.

Mail uses that domain only when both of these are true: Resend's sending records are verified, and
operations has chosen an address on the domain (`notifications@mail.example.co.uk`). The firm's
display name is the From name. The support address from branding stays the Reply-To. Until then,
and if verification later fails, the platform sender is used exactly as before.

`POST /webhooks/resend` applies `domain.updated` and `domain.deleted` as well as delivery events.
The settings page can also start a check or refresh the stored status, which is what a local
deployment has to do — Resend cannot call `localhost`. **Send test email** mails the operator who
clicked it, and only them, using the sender the firm would use for a client at that moment. Open
and click tracking stay off, and the domain is sending-only. A non-production data reset keeps the
row: it is firm configuration, and the registration at Resend is not part of the book being cleared.
