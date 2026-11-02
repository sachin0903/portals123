# AML and identity — ComplyCube

ComplyCube is the one provider for AML screening and identity checks, for the portal's own
onboarding and for the partner (firm) API. There is one Graphene ComplyCube account; isolation is
`tenant_id` on every `compliance.*` row. Graphene operations review results in one console.

| Piece                | Where                                                                      |
| -------------------- | -------------------------------------------------------------------------- |
| Provider client      | `apps/api/app/complycube/client.py`                                        |
| Store                | schema `compliance` (`0186_compliance`, `0221`–`0228`, `0250`)             |
| Webhook in           | firm API process, `POST /internal/webhooks/screening`                      |
| Poller (main path)   | worker, `app/complycube/poller.py`                                         |
| Event consumer       | worker, `_complycube_events_loop` (`app/complycube/consumer.py`)           |
| Result routing       | `app/complycube/routing.py::route_result`                                  |
| Ops console API      | `app/routers/aml.py` (`/aml/*`)                                            |
| Ops console UI       | `apps/admin/src/routes/aml/`                                               |
| Portal flow adapters | `app/complycube/provider.py`, `app/idv/capture.py`                         |
| Client photo capture | `apps/client/.../onboarding/identity-verification.svelte`, `CameraCapture` |
| Ops photo upload     | `app/complycube/uploads.py` (`POST /aml/clients/{key}/identity-photos`)    |
| Firm (adviser) read  | `GET /crm/contacts/{uid}/screening`, platform `clients/[uid]/compliance`   |
| Firm identity window | `app/routers/client_identity.py` (`/crm/contacts/{uid}/identity*`)         |
| Partner API          | `apps/api/external/` ([partner-api.md](partner-api.md))                    |

The portal flow side (steps, retries, the capture endpoints) is in [flows.md](flows.md) under "The
ComplyCube identity check" and "The ComplyCube AML screen".

## 1. AML sits with Graphene, not the firm

AML and identity screening is Graphene's own regulatory responsibility as platform provider. A
firm can start a check (a journey step, or the partner API) and can see where it stands. Only
Graphene decides a result, identity and AML alike, in the AML console. The console is in the
operations surface, gated on `aml:read`, `aml:write` and `aml:adjudicate`. All three are
registered `portal='operations'`, so `rbac.OPERATIONS_ONLY` holds them and `assert_grantable`
refuses them to every firm role. There is deliberately no `screening:write`.

Who owns the AML decision in law — Graphene operations or each firm's MLRO — is still with
compliance/legal. The console is built so a firm sign-off step can be added after the Graphene
decision without changing the store.

## 2. One store

`compliance.*` holds everything: `clients`, `addresses`, `documents`, `live_photos`, `checks`,
`matches`, `events` (the webhook inbox), `webhooks` and `webhook_deliveries`. `crm.kyc_checks`
stays the thin, flow-facing summary the engine and the client record read. A portal check links
to its kyc row through `compliance.checks.kyc_check_id` and `origin = 'portal'`; a partner check
has `origin = 'partner'` and no kyc row.

The Creditsafe mirror (schema `aml`) is dropped by `0225_drop_aml_schema`. That migration
refuses to run, and changes nothing, while any `aml.*` table except `sync_state` holds a row:
export those to object storage (kept five years), empty them, then deploy again. The `audit.log`
rows the old `aml.*` triggers wrote stay, and the `aml.%` audit carve-out still keeps them from
firms.

## 3. How results arrive: polling now, webhooks later

**Polling is the main path for now.** Every 20 seconds the worker's ComplyCube pass
(`app/complycube/poller.py`) asks ComplyCube about each pending check that has waited 30 seconds
since its last change or question, and once a day about each monitored finished AML check, so a
monitoring alert is seen too. When ComplyCube's status differs from ours, the change is recorded
and routed exactly as a webhook would be. The time of each question is kept in
`compliance.check_polls`, not on the check, so polling writes no audit rows and moves no date a
firm sees. A check pending for 7 days is no longer asked about; the console marks its client as
needing attention, for a person.

The webhook path below is built and stays on. Once ComplyCube is set to call it, most polls find
nothing new, and the two never record the same change twice.

The webhook is only a trigger. The receiver checks `ComplyCube-Signature` (hex HMAC-SHA256 of the
raw body), stores the event in `compliance.events` and answers 200. It makes no provider call.
An event with no id is refused; the id is unique, so a replay is never queued twice.

The worker drains the inbox. For each event it fetches the check from ComplyCube (`get_check`) and
records what ComplyCube says now, not what the body said, so out-of-order and replayed events are
harmless. AML hits are written to `compliance.matches`. `record_event` returns the old and new
status, and `route_result` acts only when they differ.

A reviewer's decision stands against later events. Only a match ComplyCube had not reported
before, or a monitoring alert, reopens a decided check to `refer` and clears the decision.

## 4. What a status means

| `compliance.checks.status` | Meaning                                                                                                        | Firm sees (portal) | Partner API |
| -------------------------- | -------------------------------------------------------------------------------------------------------------- | ------------------ | ----------- |
| `pending`                  | running                                                                                                        | In progress        | `pending`   |
| `refer`                    | needs Graphene: undecided, or (portal) held on a confirmed match until Graphene allows the journey to continue | In progress        | `refer`     |
| `refer`, decided           | portal: allowed past a confirmed match, which stands on record; partner: a confirmed AML match                 | Passed             | `refer`     |
| `clear`                    | passed                                                                                                         | Passed             | `clear`     |
| `rejected`                 | failed                                                                                                         | Failed             | `rejected`  |
| `failed`                   | ComplyCube could not run it — NOT a result                                                                     | In progress        | `pending`   |

**A provider `refer` starts a review; it is not its result.** It never writes `referred` on a kyc
row. A confirmed match on a portal check holds the journey instead of failing it: the kyc row
stays pending and the step keeps waiting until an operator allows it to continue (§6, §7). A
confirmed match on a partner check is decided at once, and the partner firm's client is
referred.

**A technical failure (`failed`) is not a verdict.** The kyc row stays pending and the firm sees the
check in progress. The screen was started with monitoring asked for, but it never ran, and the
poller does not ask about it again, so neither surface says monitoring is on: the console omits
the Monitored badge and the re-screen line, and the firm reads `monitored` as off
(`outcomes.monitoring_covers`). A portal AML failure raises an ops task, "Screen for {client}
failed — run it again"; the console's **Run it again** (`POST /aml/checks/{uid}/rerun`) screens
the same ComplyCube client again, for portal and partner checks alike. A portal identity failure sends the client's
photos again by itself, once per try; a second failure raises an ops task. A failed partner
identity check can only be rejected, which tells the firm to send new images.

## 5. The tipping-off control

Telling a client, or anyone who might tell them, that they are under suspicion is tipping off
(POCA 2002 s.333A). Four things enforce it, in the database first:

- **No client read.** The `compliance.*` read policy scopes by `auth.tenant_lineage()` and
  deliberately omits `auth.contact_scope()`, unlike `crm.kyc_checks`. A client principal resolves
  zero rows. Do not add `contact_scope()` "for symmetry": that is a criminal-liability bug. A test
  proves it behaviourally and asserts no `compliance.*` policy mentions `contact_scope` at all.
- **Firms read status columns only.** `authenticated` has a column grant on `compliance.checks`
  (ids, kind, status, origin, attempt, `stale_at`, `decided_at`, timestamps) and no grant at all on
  `matches`, `events` or `webhook_deliveries`. The result, outcome, matches and decision comments
  cannot be selected by a firm session.
- **The firm read hides a review in progress.** `get_contact_screening` returns one status and one
  date per kind, and whether ongoing monitoring covers the AML screen (`monitored`: on or off, never
  an alert; off when the latest screen is a technical failure, null before any screen). A portal check is read from its kyc row only, which moves when Graphene decides. A
  partner check in `refer` reads as pending until it has a `decided_at`.
- **The firm's identity window shows the check and ComplyCube's analysis, never Graphene's
  decision.**
  `GET /crm/contacts/{uid}/identity` (`screening:read`) lists the client's identity checks with a
  status in the firm's own words — anything with Graphene, held for review or not, is
  `in_progress`; a clear is `passed`, a rejection `failed`, a try closed for newer photos
  `superseded` — with the document type, who uploaded the photos ("Graphene" for an operator) and
  the photos of the current check, or of an earlier one asked for (`?check=`; the compliance tab
  steps through the client's tries with ‹ ›), and that check's analysis: ComplyCube's scores and
  per-part outcomes, laid out as the AML console lays them out (`aml.identity_analysis`; Craig,
  12 Oct 2026). It reads `compliance.*` elevated, after RLS has found the client in the caller's
  book, and returns no decision comment and nothing of an AML screen beyond its status. Listing
  the photos writes `client.identity_images_viewed` to the firm's own trail. A firm never uploads
  identity photos.
- **A firm reads a confirmed match once Graphene let the journey continue past it.** Until then the
  screen is Graphene's to review and the firm reads nothing of it. Once an operator allows a
  portal screen past its confirmed matches (§7; `outcomes.allowed_past_match`),
  `GET /crm/contacts/{uid}/aml-matches` (`screening:read`) gives the confirmed matches of the
  latest screen as they were screened — name, kinds, score, date of birth, countries and lists —
  and never a dismissed match, Graphene's decision or its comment (Craig, 13 Oct 2026). The
  compliance tab shows them in a Confirmed AML matches card, the screen reads as passed, and its
  AML report is offered with those matches.
- **The client sees client-safe reasons only.** A verdict never reaches the client: a try
  ComplyCube does not clear goes to Graphene. When an operator asks for new photos, the client is
  shown the reason the operator chose from a fixed list
  (`app/complycube/identity.py::PHOTO_REQUEST_REASONS`: blurry, cropped, glare, face not visible,
  expired) or the generic "We could not verify your identity. Please try again." None of them
  describes a fraud signal or a face mismatch. The client portal's `aml-leak.test.ts` fails the
  build on screening or provider vocabulary.

Console activity is the `aml.*` event family, which `FIRM_EXCLUDED_EVENT_PREFIXES` keeps out of a
firm's audit view.

## 6. The console

The console is worked by client, and opens on the **workbench** (`/aml`, the **Workbench** tab): one
client at a time from the work queue (`GET /aml/work`), the longest wait first. It shows the
client's name, type, firm and how long they have waited (ageing after 2 working days, overdue
after 5), the five steps (Identity, Screened, Monitoring, Hits reviewed, Risk rating) and the
facts the decision needs. The open step expands in place with the same sections and acts as the
client page: decide a match (**True match** / **False positive**), allow a journey, decide an
identity check, ask for new photos, screen or run a screen again, turn on monitoring. Above the
profile, the queue is broken down by kind of work — identity checks to decide and clients with AML
hits to review, each a link to that filter. **Show** picks all profiles (the default) or one
reason (hits, identity, screens, monitoring alerts, held), each with its count; a firm filter
narrows it. ←/→ move through the queue, and past the last client comes the first again; S skips a client for the session. On a laptop screen and up an identity or risk step fits the window: only the step scrolls, inside its card. An AML screen runs down the page with its hits, one match card per row: the photo, name, score and flags, with the rest of ComplyCube's profile folded under **Details** (`AmlMatchProfile foldDetails`), then the decision. The queue is a free for
all: any operator works any client, and nothing is claimed or handed over. After a decision the
page reloads, and a client that left the queue gives way to the one that took its place.
**Open full profile ↗** opens the client page in a new tab. The queue loads once per filter; moving between clients does not reload it. The clients either side of the one on screen are read ahead, their identity images too (`GET /aml/clients/{key}?images=false`, then `GET /aml/checks/{uid}/images?record=false` and each image), so the next client shows at once. Reading ahead records nothing: the page records that a client's identity images were viewed when it shows them (`POST /aml/checks/{uid}/images/viewed`), so the record of who viewed them stays true.

`/aml/clients` (`GET /aml/clients`, the **Client list** tab) lists every client Graphene screens,
one row each: name, firm, origin (Portal or Partner API), a pill for the latest identity check and
the latest AML screen ("Awaiting screen" for a portal client due a screen with no check, "Stale"
beside it), ComplyCube's risk level (`compliance.client_risk`) and **Needs attention**. An old `/aml?q=…` (or `sort`, `dir`, `page`) link
moves to `/aml/clients` with the same query. A client needs
attention when an identity refer is undecided, an AML refer has an undecided match or is held on a
confirmed one, a failed check was not run again, a check is still pending after the poller gave up
on it (7 days), they are due a screen that did not start, or their latest screen is stale. A client
due a screen the worker already has queued (`aml.screen.run` on the flows outbox) reads **Screen
starting** and needs nobody: the client page shows it starting, with no Screen button, and asks
again every 4 seconds until the screen exists. The Screen button shows only when a client is due a
screen and none is queued. Those clients come first, then the most recently updated.
Search (client name), the firm filter, sorting and paging run in the API over the whole set.

The rule lives once, in `app/complycube/attention.py`, and the list, the client page and the work
queue all read it. `NEEDS_HUMAN` is the rule over one check: the OR of its named branches
(`IDENTITY_REFER`, `OPEN_HITS`, `HELD`, `FAILED`, `NO_ANSWER`). `client_rows` builds each client's
row and, for every reason in `REASONS`, its count, the time the oldest of them has waited since
and the check that has waited longest. The reasons are: identity to decide; an identity check or a
screen that failed or got no answer; a screen due; a screen whose details changed (stale); hits to
review; a check held on a confirmed match; and a monitoring alert, which only labels an open AML
refer (§8). The wait starts at the check's creation, the alert, the oldest undecided match, the
last match decision of a held check, the stale mark, or the screen falling due. Each reason opens one of five steps — Identity, Screened, Monitoring, Hits reviewed, Risk
rating (`steps_of`) — so a client needs attention exactly when one of its steps is open. A step
with nothing open is `waiting` (a check in flight, new photos asked for, a screen queued, no
rating from ComplyCube yet), `advisory` (monitoring off: it never puts a client in the queue),
`done` or `none`. A wait's age counts Monday–Friday days on London dates, with no bank holidays:
over 2 is ageing, over 5 overdue (`age_of`). The client page carries `reasons`, `steps`,
`waiting_since` and `age`.

`GET /aml/work` is the work queue: every client that needs a person, the longest wait first, each
with its reasons, steps and age, and its type (`individual` or `organisation`, from the contact,
else ComplyCube). It narrows by firm, name and `reason` — a closed set (`WORK_FILTERS`): `hits`,
`identity`, `screens`, `monitoring`, `held`. It answers at most 500 clients and says when it cut
the list (`truncated`); the console moves through what it holds. `GET /aml/work/summary` counts
the same clients: in all, under each filter, and overdue.

The Hits tab is gone: the workbench's **Hits to review** works the same matches client by
client, and an old `/aml/hits` link moves to `/aml?show=hits`.

A row's key is the client's CRM contact uid — the id the rest of admin uses for this client — or
the `compliance.clients` uid for a partner client with no contact (`attention.CLIENT_KEY`). A compliance
client uid of a client that also has a contact, and the legacy `contact-<contact uid>` form,
still resolve (`_resolve_client`); the page moves to the canonical contact key.
`/aml/clients/[key]` (`GET /aml/clients/{key}`) is the client page, built to fit one screen: a
strip of the client's facts and ComplyCube's risk factors (country, political exposure,
occupation and watchlist, with when ComplyCube last changed them; the overall level is in the
page header), then the latest identity check and the latest AML screen side by side (each in the
shape `GET /aml/checks/{uid}` returns), and every earlier check folded under them, each of which
opens in full on the same page (`?check=<uid>`). An earlier identity try opens in the identity card
itself, whose ‹ › step through the client's identity tries, newest first. While a check is pending it shows what has
happened and what comes next, and the page asks for itself again every 15 seconds (only while the
tab is in view and no dialog is open), so the result shows without a reload. The old one-check
address `/aml/[uid]` redirects to the check's client page through `GET /aml/checks/{uid}/client`.

The client facts strip (`_client_block`) always shows the client's date of birth, nationality and
country of residence — what a match is compared against — and each of the rest that
Graphene has on record: gender, occupation, email, phone, a one-line address, their title and
when they became a client. Beside them sit the firm (with its icon) and the client's team
(`crm.client_advisers`, the one adviser first, everyone else under their role) with their email,
phone, job title and FCA reference. A portal client's facts come from `crm.contacts` first, falling back to
`compliance.clients`/`compliance.addresses` for whatever it lacks; a partner client's come from
the compliance records first. No firm-facing router reads any of it.

Every firm's own icon sits beside its name — on the client list, the workbench, a check and this
page (`apps/admin/src/lib/compliance/firm-badge.svelte`: initials when the firm has set none). It
is the firm's PLATFORM-portal branding icon (`branding.parse_lenient(theme,
"platform").icon_url`, through `storage.public_asset_src`), read once per page for every firm
shown (`_tenant_icons`) and withheld, rather than repeated, past 64 KiB.

Each AML match is a card: the name, gender and countries, the match strength, its categories (the
match's `classifications` as words), addresses, and the lists it was found on with their dates,
plus ComplyCube's full profile (`match_profile`) — every photo, every name with its type, the
roles held (current ones first), birth places, the countries and dates on record and why, the
entity type, associates (each with a photo where ComplyCube sent one), the narrative notes, the
source links, the list types, and whether the profile is active or the person deceased. These are
read from `compliance.matches.raw`, tolerantly, in the field names of ComplyCube's AML Screening
Check reference. A source links out only when ComplyCube sends a URL for it; that reference shows
none. Photos and links point at ComplyCube's own third-party sources and open without a referrer
(`referrerpolicy="no-referrer"`) — a deployment that sets `CSP_POLICY` with an `img-src` directive
must allow https images or the match photos will not load, and `blob:` or the workbench's read-ahead identity photos will not show; the admin app sends no `img-src` by
default (`pkgs/auth/src/security-headers.ts`). Its decision box offers **True match**
(`confirmed`) or **False positive** (`dismissed`), each with an optional comment — sent to
ComplyCube with the validation (`dismissed` is sent as ComplyCube's `rejected`) — inline in the
card, with no dialog, on the client page and in the workbench (`match-card.svelte`, the shared form action `decideMatch`); a decided match shows
who decided it, when and why, and a quiet **Change** until the screen settles — or, on a portal
check, until an operator allows the journey past it (§7). There is no third choice: ComplyCube's
validate takes only `confirmed`, `rejected` and `attention`. The identity card shows the document
and selfie (a PDF side as a link), the result breakdown, the try number, and a decision box with
**Clear** / **Reject** / **Ask for new photos**. The AML card carries **Screen** / **Screen
again** for a client due a screen, a stale screen or one never screened (a client whose journey has
no AML step is screened here), **Run it again** for a failed screen, the
monitoring line (on, or **Turn on**; §8) and the history. Every decision is sent to ComplyCube as a validation and
recorded here under the operator. Clearing a portal identity check goes through
`decisions.clear_portal_identity`.

**Ask for new photos** (`POST /aml/checks/{uid}/request-photos`, `aml:adjudicate`) is offered
beside Clear / Reject on an undecided portal identity refer, on any try (there is no try limit:
every new try is an operator's choice), never on a partner check. The operator picks the photos (the document, both its sides; the
selfie; or both), the reason the client will read (`identity.PHOTO_REQUEST_REASONS`, mirrored in
`apps/admin/src/lib/aml.ts`, or none for the generic text) and an internal note, which is required.
The check is marked decided under the operator with the note, so it no longer needs attention; its
status stays `refer` and the kyc row stays pending. The client gets a new try at the next attempt
that reopens only the chosen photos (`routing.ask_for_photos`). Nothing goes to ComplyCube: it is
not a decision on the client. The act is the `aml.photos_requested` event (photos and reason code,
never the note). 409 when the check is not such a refer, or no try of the client's waits on it.

**Upload new photos** (`POST /aml/clients/{key}/identity-photos`, multipart, `aml:adjudicate`,
`app/complycube/uploads.py`) is for photos that reach Graphene another way: the client emailed a new
passport, their document or name changed, or they sent better photos of a try held for review. It
sits on the client page's identity card, and on the empty card of a client with no identity check,
for a portal client only, and not while a check runs or the client's own photos are being sent. The
operator uploads the document (its type, its front, and its back unless it is a passport; the
issuing country is the client's first nationality unless they choose another), the selfie, or both,
with an internal note, which is required. A photo left out is the one ComplyCube holds for the
client, from their latest identity check that names one; `GET /aml/clients/{key}` says what can be
kept, and whether a journey waits, as `identity_upload`. The photos go to ComplyCube in the request
and are never kept here. A photo ComplyCube refuses is answered at once, naming it, and nothing is
recorded. The check records who uploaded the photos and why (`compliance.checks.uploaded_by` and
`upload_comment`, 0250, granted to no firm), and its history says so.

When a journey step waits on the client's identity, the check answers it. An undecided held try is
closed under the operator with the note, as Ask for new photos closes it. The step's next try (the
same try while the client is still to send it) holds every photo already and is `submitted`, so the
client is asked for nothing, our held copies go, and a phone link for the step stops working. With
no step waiting, the check takes the kyc row of the client's undecided identity check, else a new
pending identity kyc row: a clear verifies the client again from today, and a rejection fails that
row. The act is the `aml.identity_photos_uploaded` event: which photos, never the note or an image.
An uploaded selfie was not taken live, so ComplyCube's liveness result on it is the operator's to
weigh.

**A firm never uploads them**: the client's compliance page reads a client's identity checks and
their photos (§5), and nothing on the firm's side starts one.

An AML check is never decided as a whole. It settles from its matches (`_settle_if_decided`):
when the last match is decided, all dismissed makes it `clear` (the kyc row passes); any
confirmed, on a portal check, HOLDS it instead of deciding it — it stays `refer` and undecided,
so the client still needs attention, the kyc row stays pending and the journey's AML step keeps
waiting; on a partner check it is decided at once (`refer`, decided), and the partner firm's
client is referred, unchanged from before. A match decision can be changed while the check is
undecided — on a portal check, that is for as long as it is held. A refusal from ComplyCube shows
its own message in the console.

**Held on a confirmed match** is the console's own heading for a portal AML screen waiting on an
operator once every match has a decision and at least one is confirmed. Its **Allow journey to
continue** (comment optional; `POST /aml/checks/{uid}/allow`, `aml:adjudicate`, event
`aml.journey_allowed`) decides the check under the operator — the confirmed match stands on
record, so the status stays `refer` — closes its tasks, passes the kyc row and completes the
journey's AML step, so the firm sees the screen as passed. Nothing is sent to ComplyCube: the
matches were already validated there. There is no "stop" action: the journey simply waits until
Graphene allows it. 409 once the check is no longer held.

`POST /aml/screen` is idempotent: an open AML check for the contact is returned instead of a new
paid screen, under a per-contact advisory lock. A client with no pending AML kyc row (screened here with no journey's AML
step, or again after their details changed) gets a new one, so the screen's result is their AML
record on the client page and the compliance tab, and a later journey reuses a pass
(`screen.run_portal_screen`).

Identity images are read from ComplyCube when an operator opens one
(`GET /aml/checks/{uid}/images/{part}`, `app/complycube/images.py`), never cached. Graphene keeps
its own encrypted copy of a portal client's photos — theirs, and any uploaded for them — until the
check they were sent for is decided (`app/idv/held.py`, migration 0252; [`flows.md`](flows.md)).
That copy is the backup: for any image ComplyCube cannot give, the console shows ours, marked
"Graphene's copy" (`decisions.identity_images`, `source`). When neither holds an image the card says
why (`images_missing`): none was sent, ComplyCube is not set up, ComplyCube does not hold them (a
check run under another key — live or test — reads like this), or ComplyCube did not answer. Every
response that lists a check's images writes an `aml.identity_images_viewed` audit event: viewing
biometric data is logged.

**Reports.** Graphene makes its own PDF reports, laid out like the application summary
(`app/complycube/reports.py`, rendered as `flows/application_pack.py` renders): made from
`compliance.*` as they are asked for, never stored. The client report gives the client's details
and addresses and every identity check and AML screen with its outcome
(`GET /aml/clients/{key}/report`); a check's report gives an identity check with its analysis, or
an AML screen with the lists it searched (`GET /aml/checks/{uid}/report`). Operations read
everything: the client's risk, each match, each decision and its comment. A firm downloads them
from the client's compliance page (`GET /crm/contacts/{uid}/compliance-reports[/{kind}]`, §5) with
each status in its own words (`outcomes.firm_status`, `outcomes.firm_aml_status`) and no match,
decision or comment: the client report once a check has run, an identity report once its check
has a result, and the AML report only once the latest screen is clear. That endpoint also lists
the client's applications, each with its application summary PDF.
Every download is recorded: `aml.report_downloaded` for operations, and
`client.compliance_report_downloaded` in the firm's own trail.

**Internal notes** sit under the two checks on the client page: free text an operator writes about
the client (why something was allowed, a call made, enhanced due diligence done), newest first,
each with its author and time. `POST /aml/clients/{key}/notes` (`aml:write`, 1–5000 characters)
keeps a note in `compliance.client_notes` (migration 0228) on the client's contact when it has one,
else on its compliance client; `GET /aml/clients/{key}` lists the notes of both, so a note written
before the compliance client existed still shows. Notes are append-only: nothing edits or deletes
one. They are Graphene only, like `compliance.client_risk`: RLS forced, no policy and no grant to
`authenticated`, and the `compliance.%` carve-out keeps their DML trail out of a firm's audit view
(§5). The act is the `aml.client_note_added` event, which captures no subject, so the text never
reaches the event row.

## 7. Portal journeys

- **AML:** the step starts once identity passes, and the screen runs by itself, with no ops
  action: the step queues `aml.screen.run` on the flows outbox and waits; the worker runs the
  screen (`complycube.screen`, `extensive_screening_check`, monitoring on). When the screen
  cannot run for good (ComplyCube not configured, a refusal, a client conflict) it raises the
  "Screen {client}" ops task with the reason instead, and ops screens from the console, which
  closes the task. A 429, 5xx or transport error is left to the outbox's retry. **A confirmed
  match holds the step** rather than failing it: the check stays `refer` and undecided, the kyc
  row stays pending and the step keeps waiting until an operator allows the journey to continue
  from the console (§6 "Held on a confirmed match"). The step reads **With Graphene** throughout,
  whether the screen is running or held, so a firm cannot tell the two apart (tipping off; see
  [`flows.md`](flows.md)).
- **Identity:** the client photographs their ID (front, and back for a licence or ID card) and
  their face in the portal, ticks "This is a true likeness of me" and submits. They can take the
  photos on their phone instead: the desktop shows a QR code of a one-time link, and the phone
  opens the photo page with no sign-in ([`flows.md`](flows.md), "Continuing the photos on a phone").
  A document step opens
  on two equal choices, **Take a photo** or **Upload a photo or PDF**; the selfie is camera-first
  and takes photos only (JPG or PNG), as a ComplyCube live photo must. Where the page can open a
  live camera, the selfie takes itself: a face finder in the browser (MediaPipe's BlazeFace,
  served from the portal, no frame leaves the device) watches the live view, tells the client how
  to move ("Move closer", "Hold still"), and takes the photo once one face sits in the oval and
  holds still for a second (`camera-capture/autocapture.ts`). The client still looks at it and
  chooses **Use photo**. **Take photo** stays, for when no face is found or the finder cannot
  load — an old browser, or a `CSP_POLICY` whose `script-src` does not allow
  `'wasm-unsafe-eval'`. On a plain http link a phone will not open that live view, so **Take a
  photo** opens the device's own camera instead — the same one **Upload** reaches through "Take
  photo". Each document photo is given a rough look before it is used
  (`camera-capture/document-check.ts`): the same face finder looks for the holder's photo (on a
  front, not on a back), and a text reader in the browser (tesseract.js, served from the portal)
  looks for the document's name and its machine-readable lines. On a phone only the face is
  looked for (`readsText`): reading the text is slow there (about 4 MB more to load, and many
  seconds a photo on a phone's processor). A doubt is a warning on the
  preview ("This looks like a driving licence, not a passport"). **Use photo** waits for the
  check, and a photo it doubts is used once the client confirms ("Use this photo anyway?"). It
  never refuses one, because both guesses miss on glare, wear and foreign layouts, and ComplyCube
  checks the document itself. On the document camera, **Back** returns to the two choices, where
  the upload already is. A selfie is saved portrait
  (3:4) and a document landscape (4:3), as the viewfinder frames them. Every photo is a JPEG between
  34 KB and 4 MB, ComplyCube's window: a small one is re-encoded at full quality and then enlarged,
  a large one at lower quality and then shrunk. Uploads are re-encoded to at most 2560 px, which
  also drops location metadata. A HEIC photo is decoded in the browser (libheif, `heic-to`, loaded
  only for a HEIC file), as only Safari decodes HEIC itself. A PDF scan must be one page (a PDF of
  more than one page is refused, naming the page wanted); inside the window it is sent as it is,
  over 4 MB as that page, a JPEG. A file may be dragged onto the upload box. A chosen file under
  34 KB is enlarged into the window like a photo, and the client judges it on the preview; one
  still too small once enlarged as far as it goes is refused, and so, before it is read, is an
  empty file or one over 20 MB; the box states the formats and the limit. The submit
  takes JPEG, PNG or PDF for a document side and JPEG or PNG for the selfie. **The photos are
  held encrypted until their check is decided**, then deleted ([`flows.md`](flows.md)).
  - **Every verdict that is not clear goes to ops, on any try.** A ComplyCube rejection or refer
    is held as an undecided `refer` that needs ops' attention; the client sees the "thank you, nothing to
    do" screen and the kyc row stays pending. Nothing is sent back to the client automatically.
    The operator clears it, rejects it, or asks for new photos (§6): a new try
    that reopens only the chosen photos, with the reason they chose. A selfie-only retake reuses
    the ComplyCube document the client already sent; a document retake makes a new one, because
    the old image was judged. There is no limit on tries.
  - **A refused upload is not a try.** When ComplyCube refuses a photo itself (a 400 or 422 on an
    upload), only that photo is asked for again — the selfie, or both sides of the document — and
    what ComplyCube accepted is reused, so no second document is made there. ComplyCube's message is
    kept for operations only (`identity_capture.last_provider_error`, shown on the admin journey
    page) and never reaches the client or the firm.
  - **While ops review**, the firm sees the step as "With Graphene for review", not as the client's.
  - The manual path stays on offer.
- **Re-screen:** any change to a detail the screen searched — name, date of birth or nationality —
  screens the client again (Craig, 13 Oct 2026). A trigger on `crm.contacts`
  (`compliance.mark_screen_stale`, 0223, widened by 0253) marks the latest portal AML check stale
  whatever it ended as, on every write path, and expires every passed AML row for that client —
  not only the one the latest check points at, because a journey reuses any passed AML row — so
  no journey reuses a pass of someone else's details (the firm reads the screen as not started
  until the new one). The
  outbox row it queues (`app/complycube/rescreen.py`) screens the client again by itself after a
  clear screen or one allowed past its matches; after a rejected screen or one under review it
  raises the Screen task instead, since a new clear screen must never undo Graphene's rejection
  or step round its review. The new screen
  searches the client's current details (next point).
- **ComplyCube holds the current details.** Before a screen, an identity check or the console's
  **Run it again** uses a client this database already has bound, `clients.refresh_bound_client`
  fetches it and sends only the fields that differ (name, date of birth, nationality, email);
  `personDetails` merges field by field, so nothing else is lost, and `type` is never sent
  (ComplyCube refuses it). What ComplyCube then holds is stored on `compliance.clients`, which is
  audited. Two things stop the check for a person, with nothing written and nothing screened: the
  client is not this contact's (ComplyCube's `externalId` is not the contact uid, or it carries
  another deployment's `graphene_env`), or ComplyCube refuses the new details (400/422, its message
  kept). The console answers 409, the journey's screen raises its Screen task, and an identity
  submit fails for good (reported) rather than asking the client for photos that cannot fix it. A
  client only found by `externalId` is never written: ComplyCube does not keep `externalId` unique,
  so on a shared sandbox it could be another environment's (see §9). Partner clients are not
  refreshed yet.
- **Old journeys:** published flow versions are immutable, so steps naming `creditsafe` or
  `idenfy` resolve to the ComplyCube adapters (`providers.RETIRED`). Preflight refuses `idenfy` on
  a new identity step.
- **Every AML step is automatic.** A flow's AML step has no provider setting and never waits for a
  person to start it; a `provider` saved on an older flow, `manual` included, is ignored.
- **No key, no AML pass.** With ComplyCube unconfigured nobody can screen: the worker raises the
  "Screen {client}" task and the journey's AML step waits. There is no bypass.

## 8. Monitoring

Ongoing monitoring is always asked for: every screen Graphene starts (the journey's, the console's
Screen and Run it again) asks ComplyCube for it (`screen.DEFAULT_MONITORING`). A screen that
finished shows "Ongoing monitoring is on". A screen ComplyCube could not run does not: the flag
was stored at the start, and the poller never asks about a failed check, so the console shows
neither the Monitored badge nor the re-screen line, and does not offer **Turn on** — **Run it
again** is the action. A finished screen that somehow has monitoring off shows **Turn on**
(`PUT /aml/checks/{uid}/monitoring`), and nothing in the console turns it off. A ComplyCube plan
without continuous monitoring (the trial plan) refuses a monitored screen with 403 "Continuous
monitoring is not available in your current plan": the screen is then started again without it
and recorded as unmonitored (`ScreeningClient.create_screening`, `monitoring_of`), so the client
is still screened and the console offers **Turn on**, which ComplyCube refuses until the plan
includes monitoring. A monitoring alert
reopens the check to `refer`, which marks its client as needing attention, and for a portal client
also raises an ops task. The client is not blocked: a passed kyc row stays passed while ops
review.

The consumer stamps `compliance.checks.alerted_at` when an alert lands (`sync.is_alert`): a
`check.monitoring.attention` event or a monitored check that was decided going to `refer`, or a
settled refer reopened by one. The last case keeps the status `refer`, so it is no status change
and raises no ops task; the stamp is how the console still knows. While the check has hits to
review or is held, its client carries the **Monitoring alert** reason and its Monitoring step is
open (§6); it waits from `alerted_at`. Firms cannot read the column. Migration 0254 filled it from
the monitoring events and alert tasks already held.

## 9. Configuration and plugging in the key

| Setting                     | Also read as                | Needed by                                           |
| --------------------------- | --------------------------- | --------------------------------------------------- |
| `SCREENING_API_KEY`         | `COMPLYCUBE_API_KEY`        | API, worker, firm API                               |
| `SCREENING_WEBHOOK_SECRET`  | `COMPLYCUBE_WEBHOOK_SECRET` | firm API (webhook in)                               |
| `COMPLYCUBE_BASE_URL`       |                             | default `https://api.complycube.com/v1`             |
| `COMPLYCUBE_ALLOW_LIVE_KEY` |                             | outside production, use a `live_` key (default off) |

`docker-compose.yaml` gives all three services the `x-complycube` block, which forwards only
`SCREENING_API_KEY` and `SCREENING_WEBHOOK_SECRET`. So on dev, staging and prod set those two
names: a `COMPLYCUBE_*` name set there never reaches a container, and `COMPLYCUBE_BASE_URL` and
`COMPLYCUBE_ALLOW_LIVE_KEY` are local only. A blank key makes the console answer 503 "ComplyCube is not configured.", leaves the worker
idle and fails a partner check closed. `SCREENING_WEBHOOK_SECRET` can stay blank while results
arrive by polling. Outside production a `live_` key is ignored, and the console says so, unless
`COMPLYCUBE_ALLOW_LIVE_KEY=true`: live checks are billed and hold real people's data.

**One account, many deployments.** ComplyCube keeps sandbox (`test_` key) and live (`live_` key)
apart: neither can see or change the other's clients, checks or webhooks. Every deployment on one
key shares one space, though, and the sandbox is shared by every dev box, dev and staging. Two
things then cross: every sandbox webhook receives every sandbox event, and `externalId` (the
contact uid) is not unique at ComplyCube, so a deployment holding the same uid (a copied database)
can find another's client. Two guards keep each deployment to its own:

- **The worker takes in only a client it holds** (`sync.record_event`): the event's ComplyCube
  client id must be one we hold for a contact, and ComplyCube's `externalId` for it must be that
  contact's uid. Anything else is marked processed and nothing of it is stored, whatever its
  metadata says. A check we start is stored in the transaction that starts it, and the poller asks
  about it, so an event that arrives first loses nothing. This needs no setting.
- **Every ComplyCube client says whose it is** (`client_metadata`): `graphene_env` (the
  deployment's `APP_STAGE`), `graphene_origin` (portal or partner, set once and never changed),
  `graphene_firm_uid`, `graphene_firm`, `graphene_contact_uid`, and for a partner client
  `graphene_client_uid` and the firm's own `graphene_firm_reference`. Every key starts `graphene_`, the prefix the firm API
  reserves in a firm's own metadata, which is never sent to ComplyCube. A client is found by
  `externalId` only with this deployment's `graphene_env`, and a bound client carrying another
  deployment's is never refreshed: the screen stops (409). A client made before this is stamped the
  next time it is refreshed.

Every dev box is `dev`, like the dev deployment. Dev boxes get no webhooks and poll only their own
checks, so the first guard covers them. The firm API container takes the same `APP_STAGE` as the
api in `docker-compose.yaml`: without it, it read itself as `production`, stamped a partner's
clients so on every deployment, and the console's Run it again 409'd on the mismatch.
ComplyCube metadata values are strings of at most 500 characters under keys of at
most 40, and an update replaces `metadata` whole, so a refresh sends what ComplyCube holds with ours
over it (sandbox, 7 October 2026).

**Local, with a sandbox key:**

1. Put `SCREENING_API_KEY=test_…` in `apps/api/.env.local`.
2. `moon run api:migrate` (this branch adds `0221`–`0228`).
3. Restart the API so it reads the key, and run the worker, which is what polls:
   `cd apps/api && RENDER_SERVICE_PORT=8011 uv run python -m app.worker`.
4. Answer the sandbox questions: `cd apps/api && uv run python -m scripts.complycube_spike`
   (add `--front`, `--selfie` and, for a licence, `--back` image paths for the identity part). It
   refuses any key that does not start with `test_`, writes nothing to the database, and saves every
   raw answer to `complycube-spike.json`. Copy the answers into §11. The surnames that choose an
   outcome are in §10.

**Staging:** set `SCREENING_API_KEY` on the API, the worker and the firm API. When webhooks are
wanted, register the firm API's `POST /internal/webhooks/screening` URL in ComplyCube, subscribe to
the `check.*` events and set `SCREENING_WEBHOOK_SECRET` on the firm API.

## 10. Testing in the sandbox

A `test_` key (§9) returns a scripted outcome, chosen from the client's name. The triggers are
[ComplyCube's testing data](https://docs.complycube.com/documentation/integration-resources/testing-data).
The strings are the lowercase words in the table. Ran against the sandbox on 15 October 2026, for
an AML screen and an identity check of each surname: a well-known name (`Putin`) came back `clear`
with no matches, so the scripted surnames are the triggers.

**Checks.** The surname is sent as ComplyCube's `lastName` (`client_payload`). The same surnames
apply to an identity check and an AML screen. On a portal contact, change the surname and run the
check again: a client already bound is updated first (`clients.refresh_bound_client`). On a
partner client, `last_name` is sent when the ComplyCube client is created, on the first check; a
later update of that client does not change it, so each scenario is its own client, with the name
set before the check. An identity photo has to be between 34KB and 4MB or ComplyCube refuses the
upload (`400`) before the check exists; a photo in that range is accepted and the surname still
decides the outcome.

| Surname       | ComplyCube                               | We store (`outcomes.map_status`) |
| ------------- | ---------------------------------------- | -------------------------------- |
| anything else | status `complete`, outcome `clear`       | `clear` — the firm reads passed  |
| `attention`   | status `complete`, outcome `attention`   | `refer`                          |
| `failed`      | status `failed`, outcome `not_processed` | `failed` — a technical failure   |

`attention` holds the check for a person. An identity check waits for Clear, Reject or Ask for new
photos. An AML screen with that surname also returns two synthetic matches, so the Hits queue has
something to decide: John Smith (PEP, score 100, US and GB) and Jane Smith (watchlist and adverse
media, score 95, GB). `failed` is ComplyCube's check status `failed`, which we store as a technical
failure (§4): the kyc row stays pending, the firm reads the check as in progress and monitoring as
off, and the console offers Run it again without saying ongoing monitoring is on.

**Risk.** The console's risk pill is ComplyCube's profile (`compliance.client_risk`), fetched after
a check changes. `low` is what a client gets once it has an address, a nationality, a birth country
or an incorporation country; with none of those the profile is `not_set`. `medium` and `high` need
one of those too, plus the middle name `medium-risk` or `high-risk` — both confirmed by creating
the ComplyCube client with that middle name. We send the first nationality, and an address when
line, city and country are all present, so a sandbox client with a nationality scores `low`. A
middle name stays on our contact: `client_payload` sends the first name, surname, date of birth and
the first nationality, so a portal or partner client cannot force `medium` or `high`.

**Elsewhere on that page.** A workflow session is `clear` unless the last name is `attention`, and
email or SMS OTP succeeds with `123456`. This platform calls neither. A live photo uploaded through
the API is accepted when the file is between 34KB and 4MB.

## 11. Still to confirm, and not built

**Confirm in the sandbox spike** (`scripts/complycube_spike.py`, §9; write the answers here):

- S1 — **answered (2 Oct 2026, a real identity check):** yes. An API-uploaded live photo gets a
  liveness result: `result.breakdown.authenticityAnalysis.livenessCheck` (`clear`) with
  `breakdown.livenessCheckScore` (83). Face match is separate:
  `result.breakdown.faceAnalysis.facialSimilarity` with `breakdown.facialSimilarityScore`. Our own
  capture is enough; ComplyCube's SDK is not needed for liveness.
- S2 — **answered (ComplyCube API reference):** `POST /checks/{id}/validate` takes `outcome`
  `confirmed`, `rejected` or `attention`, an optional `comment`, and a `matchId` that a screening
  check requires. Identity: `confirmed` makes the check `clear`, `rejected` makes it `rejected`
  (`aml.py::CHECK_OUTCOMES`). Screening match: `confirmed` makes the check `match_confirmed`,
  `rejected` dismisses the match (`aml.py::MATCH_OUTCOMES`). Monitoring is `POST /checks/{id}`
  with `enableMonitoring`, for screening checks only.
- S3 — **answered:** once every match is rejected, ComplyCube clears the screening check itself.
  The console sends no check-level validate (ComplyCube refuses one without a `matchId`).
- S4: whether a validate sends a `check.updated` webhook (routing tolerates either). Needs
  webhooks on.
- S5 — **not needed:** the client's retake reason is chosen by an operator from a fixed list
  (§5), not read from the breakdown.
- S6: the inbound signature header and algorithm. Needs webhooks on.

**Not built, by decision for now:** companies (KYB); one ComplyCube account per firm; a per-firm
monitoring flag; the retention job. The retention rule agreed is: delete our copies of ID and
selfie images 5 years after the relationship ends, the same as AML records, and ask ComplyCube
for their own retention setting.

**Not built in the work queue, by decision for now:** case management, four-eyes, MLRO / SAR /
EDD, an ops risk override (risk is ComplyCube's only), a "Discard" decision on a match (only
confirm and dismiss exist), claiming or handing over work (the queue is a free for all), bulk
decisions, and a bank-holiday calendar for ages. Two dead ends of today's rule stay as they are: a reopened portal screen with
every match dismissed reads as held but offers no **Allow journey**, and a partner screen
reopened by a monitoring alert with no new match needs nobody (it is stamped `alerted_at`, but
the alert only labels open hits or a hold).
