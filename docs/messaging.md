# Client<->adviser messaging (prototype slice)

A single chat channel between a client and their firm, with any number of file attachments on
a message.
Schema `messaging`, service layer `apps/api/app/messaging.py`, router
`apps/api/app/routers/messaging.py`. Explicitly a first-pass prototype (see the module
docstrings) — not the full production design (multi-instance push, a reassignment ledger).

## Schema

- **`messaging.conversations`** — one row per `(tenant_id, contact_id)`. `adviser_user_id` is an
  optional **mutable pointer** to the client's adviser (the one `crm.client_advisers` row with
  `role = 'adviser'`, 0245), re-synced when an adviser is assigned
  — it is not a gate on who may open or send. Staff reach a thread with `messages:read` (view)
  and `messages:write` (send); being on that client's book is not required. There is no
  reassignment history table; `audit.actions` already carries "who reassigned this client's
  adviser, and when" via whatever endpoint writes `crm.client_advisers`.
  `client_last_read_at`/`staff_last_read_at` are the two read-receipt watermarks; `unread` is
  always **computed** from them, never a stored counter.
- **`messaging.messages`** — append-only (no UPDATE/DELETE grant, like `audit.actions`). `body`
  is nullable (0052) — a message may be attachment-only; "at least one of body/attachment" is
  enforced in `app/messaging.py`, not a CHECK constraint (a CHECK can't see a sibling table's
  row, since the attachment is a separate INSERT after the message's). `tenant_id`/`contact_id`
  are denormalised from the parent conversation so RLS is a plain column check, no subquery.
- **`messaging.attachments`** — one row per FILE on a message. `message_id` was `UNIQUE` (0081),
  capping a message at one attachment, and `0123_messaging_multi_attachments` drops that index for
  a plain `attachments_message_idx`. The cap was never a domain rule — it was the staged-upload UX
  the first pass shipped with — and it forced both composers to loop, sending three documents as
  three separate messages the reader had to piece back together. Also append-only, same RLS
  predicate as `messages` (denormalised `tenant_id`/`contact_id`).

RLS on all three: `tenant_id = ANY(auth.tenant_lineage()) OR contact_id = ANY(auth.contact_scope())`
— a client sees their own contact's rows, staff see their whole tenant's. Permission gating
(`messages:read`/`messages:write`) is the API layer's job; RLS only proves tenant isolation.

**Identity gotcha, pinned by tests:** a real client's WorkOS role is _never_ literally the
string `'client'` (it's a role slug like `org-advised-client`). The signal for "this caller is a
client" is `auth.portal() = 'client'`, not `auth.role()` and not "is `contact_scope()`
non-empty" — a staff adviser's own assigned clients are folded into their `contact_scope()` too.
See `app/messaging.py::_is_client_portal_session`.

## Attachments

Any number of files on ONE message, staged client-side (pick files, each sits as a removable chip
until Send). Allowed: PDF, Word (doc/docx), Excel (xls/xlsx), PowerPoint (ppt/pptx), plain text —
25 MiB max each (`storage.MAX_DOCUMENT_BYTES`, the same ceiling the documents feature uses).

**One request, one message, every file.** The composer appends a repeated `file` part and the
endpoint reads `list[UploadFile]`. Three things have to agree for that to hold, and each has been
wrong on its own: the composer must not loop (the client portal's did, so a client attaching three
documents produced three bubbles, and a failure part-way through left some sent and some staged);
the storage key must be per-ATTACHMENT, not per-message, or the second file overwrites the first;
and the three SvelteKit proxies in front of the API must rebuild the body with `getAll('file')` —
`get` returns the first part only, which uploaded one file and reported success. Each proxy has its
own test, because they are separate files that merely happen to agree.

**Storage: the tenant's own bucket, not the shared documents bucket.** Branding assets already
resolve a tenant's `t-{uid}` bucket (`app/tenant_storage.py`); attachments are exactly the same
per-tenant-sovereignty case, so they land there too, under a **private**
`files/{contact_uid}/messages/{attachment_uid}.{ext}` key — keyed on the CLIENT's own uid and on
the ATTACHMENT's (a message-keyed path made the second file on a message overwrite the first), and
deliberately _not_ under `files/tenant/`, which carries an anonymous-read bucket policy for
branding. If a tenant's bucket isn't provisioned yet, sending an attachment 503s
(`AttachmentStorageUnavailable`) rather than silently falling back to a shared bucket.

**Validation is magic-byte, never the client's declared Content-Type** (`app/storage.py`,
"Chat attachments" section — extends the same allowlist `sniff_document_type` uses for
documents):

- PDF: `%PDF-` header.
- docx/xlsx/pptx: all three share one outer zip signature: they're disambiguated by which
  top-level part the zip actually contains (`word/`, `xl/`, `ppt/`).
- Legacy doc/xls/ppt: all three share one OLE2 compound-file signature; disambiguated by the one
  top-level stream each format mandates (`WordDocument`, `Workbook`/`Book`,
  `PowerPoint Document` — via `olefile`, a real dependency, not hand-rolled binary parsing).
- Plain text has no magic bytes at all — accepted only by elimination, when the bytes decode as
  clean UTF-8 with no embedded NUL (so a binary blob renamed `.txt` still fails).

**Upload is a capped multipart route.** Starlette's multipart parser applies no size ceiling to
a file part (it spools to disk unbounded past 1 MiB) _before_ any endpoint code runs — the same
gotcha `routers/documents.py`'s `CappedBodyRoute` exists for. The two attachment-send routes
(`/messaging/thread/attachments`, `/messaging/conversations/{conversation_uid}/attachments`) opt into
`SingleUploadRoute` from that module rather than duplicating the ASGI-body-capping logic.

**Download is a proxied stream, fetched only on click** — `GET
/messaging/thread/attachments/{attachment_uid}` (client) / `GET
/messaging/conversations/{conversation_uid}/attachments/{attachment_uid}` (staff), gated the same
way as the parent conversation (RLS for the client; tenant + conversation join for staff — the
conversation in the staff URL is load-bearing, not decorative: an attachment from a _different_
conversation in the same tenant 404s). No presigned URL: the object store is
compose-internal in local dev, so a presigned URL wouldn't resolve from the browser — both
portals proxy the bytes through a same-origin `+server.ts` route instead, mirroring
`documents/[id]/content/+server.ts`. List responses (`GET .../thread`,
`GET .../conversations/{id}/messages`) carry only attachment _metadata_ (filename/content-type/
size) — never bytes, never a URL — so nothing downloads merely from loading chat history.

## One thread, one composer (`@graphene/ui/book/messages`)

Three staff surfaces show the same conversation — the platform inbox's direct messages, the inbox's
group feed, and the client record's Messages section — and they draw it with ONE implementation of
each part that is the same everywhere:

- **`MessageThread`** — the scrolling list: a day divider that is a RULE with its label sitting on
  it, RUNS (consecutive messages from one sender inside `RUN_GAP_MS` share one avatar, name, role
  and time), a bubble per message with the tail on the first of a run only, your own messages
  mirrored to the right, and the "New" bar.
- **`MessageComposer`** — the box you type into: the ask-for-a-document button in its top-right
  corner (absolutely positioned, so it costs no height), the staged-file chips — several files ride
  ONE message — and Ctrl+Enter to send.
- **`ConversationPanels`** — the pane's reserved header row (Chat and Files, and the search
  control) and the Files panel behind it.
- **`MessageRail`** — the context beside the thread (below).

Both surfaces are also the same SHAPE: the THREAD fills the content area and sits on the page's own
`--background`, with the rail beside it split off by a hairline rather than by a gap between boxes.
Both pages cancel the shell's padding so the conversation reaches the true edges — a frame around
one of them reads as a different application from the other showing the same conversation. The only
thing the inbox has that the record does not is the conversation list, because the record already
knows its client.

**THE CONVERSATION LIST IS THE ONE CARD, and it is the only one.** It is a register of clients to
pick FROM rather than part of the conversation, so it is the half that takes a frame and keeps its
own `bg-card` fill (`rounded-xl border shadow-xs`, inset by the shell's own rhythm). It used to be a
full-bleed white column running to the viewport edge, which read as a second page welded on rather
than as a panel beside the thread.

**The conversation is carded too, on BOTH surfaces.** The thread and the rail share one card —
never two boxes with a gap — and the client record's Messages tab wears the same one. It was left
on the page background for a while, on the reasoning that a record's section should match
Documents and Activity; what that produced was one conversation with two appearances depending on
which door it was opened through, which is a worse fault than a section that reads a shade apart
from its neighbours. The list keeps its own card, and the hairline between thread and rail is what
tells the two halves apart inside one.

What stays with the surface is what genuinely differs: the header, the empty state's words, and
what a send POSTS to (`submit`, with `draft`/`files` bound so the caller clears them once it
lands). Hrefs are injected — `download(conversationUid, attachmentUid)` — so the platform portal,
the admin book's read-only mount and the client record each build their own. One more prop exists
for a real difference: `subline`, the extra line inside a group feed's bubbles.

The composer is docked at the foot of the thread in BOTH states and the full width of the messages
it answers. A composer floating mid-pane on an empty conversation moves the moment the first
message arrives, and one at half the width reads as a different surface from the thread above it.

### The client's chair (`viewer="client"`, Oct 2026)

The client portal's `/messages` is the SAME `MessageThread` and `MessageComposer`, not a fourth
copy. It carried its own for a year — a face, a name and a time over every single message, no
bubbles, no runs, bodies floating right on the bare page canvas — so none of the improvements above
ever reached the people the product is for. What differs between the chairs is stated as props,
and the staff defaults are unchanged:

- **`viewer: 'staff' | 'client'`** on the thread. The client's own messages are the FILLED brand
  bubble (`bg-primary text-primary-foreground`) on the right with no avatar and no "You" over them
  — there is one of them, and a disc with their own initials beside every reply says nothing; the
  time stays, once per run. The firm's keep the muted bubble under the sender's face and name,
  with no role beside it: the page header already names the adviser, and "Administrator" next to
  the name told the client something about the firm's internal structure they do not need. "Client"
  is never printed as a role either, and an unattributed firm row reads "Your firm" rather than
  "Staff" (`staffSenderLabel(message, fallback)`). This is what every messaging app a client
  already uses draws — Monzo and Starling's in-app chat, Nutmeg's and Wealthsimple's adviser
  messaging, iMessage and WhatsApp — and it is the convention the staff register deliberately does
  NOT follow, because a staff thread can hold three colleagues and the client, and there a face and
  a role on every run is the point.
- **No read receipt.** `seenUpTo` still stamps "Seen" under the last of a STAFF reader's own
  messages the client has opened. The client page does not pass it: whether the firm has opened a
  message is not shown to the person who sent it, and the thread ignores the watermark anyway when
  `viewer` is `client`. Nothing is stamped "Sent".
- **`onFulfil`** — makes a PENDING upload request's card a button ("Tap to upload a document") that
  opens the page's fulfil dialog, the same dropzone-and-send as `/requests`. Without the handler
  the card is inert, as it is for staff, who cannot fulfil what they asked for. A fulfilled one
  reads "Sent" in the client's chair and "Received" in the firm's.
- **`enterSends`** on the composer — Enter sends and Shift+Enter breaks a line, but only on a
  device with a fine pointer (`matchMedia('(pointer: fine)')`); on glass the return key stays a
  newline and the Send button is right there. Staff keep Ctrl+Enter: an adviser's reply is often
  several paragraphs, and a stray Enter sending half of one is the costlier mistake.
- **`compact`** on the composer — one row, paperclip · box · Send, in a pill. The staff shape (box
  above, actions beneath, room for the ask-for-a-document button) took a third of a phone screen
  before a word was typed.

The page itself keeps what is genuinely its own: the "New" bar's position (`client_last_read_at`,
computed ONCE before the mark-read effect runs or it erases itself), `sent_by_me` derived from
`sender_kind` (the API sets the flag for a staff reader; a client is the only client on their
thread, and a live push omits it), the portal's proxy paths, and a HEADER naming who the thread is
with — the adviser's headshot or their `PersonAvatar` (the same disc the thread draws beside their
replies), name, job title and firm. It is not a link: there is no adviser page, and email and
phone live on the adviser block wherever a firm places one. A chat with no name at the top is a
list of your own words. **When nobody is assigned the recipient is the firm** (Oct 2026): the
loader substitutes `$lib/adviser::firmContact` — `kind: 'firm'`, the tenant's name, its brand icon
(contained on the brand wash, or a building glyph), "Your firm · Client support" under the name —
and the empty state and composer say "Message {firm}" by the firm's whole name rather than a
"first name". The thread itself is untouched: whichever member of staff replies is named on their
own messages, so a client always knows who they are talking to even when the firm has not named
one person as theirs. Below `lg` that header
IS the top bar: the page is `immersive` (ClientShell stands down — no bar, no column padding, no
scrollbar gutter; `docs/design-language.md`) and the header leads with the shell's own "‹ Home"
pill, so the conversation is the whole screen with one header, not two. The whole conversation
sits on ONE glass card: the full viewport on a phone (the composer pays the safe area itself),
the shell's full content column under the back row with a 1.5rem bottom gutter from `lg` — a chat
is not an article, so it takes no reading measure of its own; the bubbles' `max-w-[42rem]` bounds
the line. The client's
`$lib/messaging.ts` re-exports the shared message types and keeps only its own narrower
`ApiConversation`; its duplicated helpers and their tests are gone.

## The list IS the recipient picker

**Every client the reader may message is a row, whether or not anyone has written to them.** There
is no "New message" button and no picker dialog behind one.

`GET /messaging/conversations` answers "who have we talked to" — a client with no thread has no row
in `messaging.conversations` yet, so it can never answer "who can I talk to". The inbox merges it
with `GET /crm/contacts`, keyed by `contact_id`: live threads first in the order the server returned
them (recency is what makes an inbox an inbox), then everyone else by name. Selecting a client with
no thread POSTs `/messaging/conversations` and opens it, which is exactly what the client record's
Messages section already does on load.

**A row is current the moment anything happens on it.** An arrival patches its preview, its time
and its unread dot and re-sorts the list; so does a SEND, which used to patch nothing — the firm's
own reply left the row showing the client's last line at the client's last time until a reload.
Both go through one `previewOf`, so a send and an arrival cannot word the same message differently
in the same list. The TIME itself reads "Now" for the first five minutes (the window the thread
already uses to decide two messages are one turn) and is rendered against the app-wide ticking
clock in `notifications/now.svelte.ts`, so "Now" ages into a clock time on its own rather than
being frozen at whatever it said when the row first rendered.

**No count under the title, and a narrower list.** The heading used to carry "N conversations",
which named the threads rather than the clients on the list — and once the list became the whole
book, the number answered a question nobody asks of an inbox: you look for a person, not for how
many people exist. The rows say how many there are. The list itself is `lg:w-72`, down from
`360px`: a name, a one-line preview and a timestamp do not need that width, and what it was
spending came out of the thread.

Before this, the two surfaces PARTITIONED the book between them — the list showed the clients WITH a
channel and the dialog showed the ones WITHOUT, down to an empty state reading "everyone in the book
already has a channel" — so no surface ever showed the set you were actually choosing from.

**The contacts read is uncapped and searched server-side.** It used to send `limit=200` and filter
what came back in the browser, so a firm with more clients than that silently could not reach the
rest: no error, no "showing 200 of 1,340", just names that were not there. `routes.contacts` is a
query-building function for that reason — the same term the conversation search sends, sent to
`/crm/contacts` as well, so one box narrows both halves of the list.

Eligibility needs no endpoint of its own: who may be messaged is `messages:write` plus the contact
being in the caller's tenant, and RLS already scopes that listing to exactly those rows. Being the
client's assigned adviser is not required (see the `adviser_user_id` note above).

The GROUP picker keeps its own copy of the book, deliberately not the list's: that one tracks the
search box, and a group dialog offering only the clients matching whatever is typed behind it would
hold a different set every time it opened.

**The "New" bar needs a watermark captured ONCE, before the read receipt goes.** `firstUnreadId` is
the first CLIENT message newer than `staff_last_read_at` — the firm's own are read by definition,
and a watermark older than a reply you sent yourself would put "New" above your own words. It
cannot be `$derived`: opening a thread marks it read, so a reactive watermark erases itself at the
one moment it is useful, and holding it still also keeps a message that arrives while you are
watching below the bar where it belongs. The inbox computes it in `openConversation` before POSTing
the receipt, then advances that row's own `staff_last_read_at` — without which re-opening the same
conversation in one session raises the bar again over messages this very visit has just read. A
GROUP passes none: `ApiGroup` carries no `unread` (every group-tagged message is staff-sent), so
there is no point to mark.

## The conversation's rail (`MessageRail`)

Beside every staff thread: **who the conversation is with**, what they hold, the notes the firm
keeps on them, what they still owe (open document requests), the open tasks about them and who else
is on the conversation. Files are NOT here — a file belongs to the thread, so it is a tab on the
conversation pane. The client record and the platform inbox mount the same one, so the same
conversation carries the same context wherever it is read from.

**The rail LEADS with the client** — avatar, name, the address you would reply to and their number.
The NAME is the road to the record: a separate "View profile" beside it was two things to read for
one destination, and the name is what a reader reaches for anyway. The rail answered every question
about a thread except the first one anybody asks of an inbox, which is who is on the other end. It
costs no extra request: `GET /chats/rail/{contact_uid}` already reads the contact detail for the
advisers below and was discarding the rest of it.

Three cases legitimately pass no client and get no block. A **GROUP** is several clients, so one
name there would be a false statement about who is being written to. The **client record** leaves it
out because the client is the page — their name is on the header and on the thread already, and a
block naming the client you are standing on is furniture. And a reader without
`contacts:read` gets `null` rather than a card with three blanks in it. `clientHref` is a separate
prop for the same reason the rail's other links are injected: a mount with no `/clients` tree (the
admin book's read-only one) states the name without offering a road to it.

**The panel of humans is "People", not "Team".** The client record's own section is People, and the
rail calling the same set Team taught the reader two names for one idea across two screens showing
one client.

**Under it, the facts an adviser would otherwise open the record for**: reference, type, service,
date of birth and location, each already NAMED by the helper that owns its vocabulary
(`clientName`, `clientStatus`, `clientTypeLabel`, `serviceTypeLabel`, `providerReference`) so this
panel states them rather than spelling them. `crm.contacts.name` is the ORGANISATION's name and is
null for an individual — reading that column straight is what put "Unknown" at the head of the rail
for every human being in the book, and the same mistake listed half the directory as Unknown.
EVERY field is stated, with an em dash where there is no answer, exactly as the record states them:
a field that vanishes when it is empty is a field nobody knows is missing, and "we have no phone
number for them" is itself an answer an adviser needs mid-conversation.

**The client is PINNED; only the bands under them scroll.** The rail is a flex column with one
scroller in it, not one scroller: scrolling to a note or the fourth account must never scroll away
the name, email and phone of the person the whole panel is about.

**Four bands, in the order they are asked for: Accounts, Notes, Tasks, People.** Accounts and Notes
open themselves — they answer "who is this and what is going on with them", which is why the rail
is read at all. Tasks and People start CLOSED with their count on the band: they are checked
deliberately, and four open bands made the rail a page of its own to scroll. None of it is
persisted — a fold is a glance, not a setting. Each band is a `Collapsible.Root` wrapping its own
heading and body together, which is what the component requires.

**Accounts states three and offers the rest.** Each row is the account's own NAME (what an adviser
calls it), its value, and its growth tinted up or down — no provider reference, which is a filing
code for talking to a custodian rather than something read beside a conversation — and the row is
the road to that account — the next question after "what is this about" is always the wrapper
itself. The href comes from the injected `links.account` builder, like the owed rows', so a mount
with no `/accounts` tree states the row plainly instead of offering a dead link. There is NO
TOTAL: a rail states what a client holds, not a valuation, and the record is where a figure that
has to add up lives. A client with eight wrappers pushed Notes and everything under it off the
bottom, and the first three answer "roughly what do they hold" on their own.

**A note can be written from the rail.** "They said on the phone they are retiring in March" is
learned in a conversation, and a note that needs a trip to the record and back is the note that
never gets written. The composer POSTs to the SAME endpoint the record's own does
(`POST /firm/contacts/{uid}/notes`), through `/chats/notes/{contact_uid}` for the inbox, which is
client-side about which client is selected and so cannot use the record's form action. It is gated
on `contacts:write` — NOT on the `messages:write` that governs sending — and the platform page
withholds the route entirely from a reader without it, so the rail never offers a box whose submit
can only come back 403. The inbox puts the created row straight on the panel and into its rail
cache; the record invalidates instead, because the Notes card on the Overview tab beside it must
not disagree about what the client's notes are. The box and the empty state are never both shown:
"No notes yet" under a box whose placeholder already reads "Add a note about this client…" says the
same thing twice, so the empty state is what a reader who CANNOT write one gets instead.

**And withdrawn from the rail, by its author.** The rail is the record's notes list to the row —
the same `NoteComposer`, the body over its author line, a hairline between notes, and the delete
in the top-right corner on hover or focus only. It calls the record's own
`DELETE /firm/contacts/{uid}/notes/{note_uid}` through `/chats/notes/{contact_uid}/{note_uid}`,
gated on `contacts:write` like the composer; the API restricts the delete to the caller's own
notes and answers 404 otherwise, and the rail reports that rather than guessing authorship from
a display name. The rail's notes carry the row's `uid` for this — they used to carry the serial
`id`, which is not what the endpoint is keyed on.

**Search reads the message bodies, and the person's name.** `GET /messaging/conversations?q=`
matched `ct.name` only — the organisation column, null for every individual — so a search for a
client's surname found nothing and a search for a word they wrote found nothing. It now matches
the organisation name, the first name and surname together, and the body of any direct message
in the conversation. The in-thread filter (`MessageThread`) matches what a row SHOWS, not only
its body: a request's title and an attachment's filename as well, since a bodiless row is one
of those two.

**The two owed panels are two people's work and are named apart.** "Pending client tasks" is what
the CLIENT still has to do (open document requests); "Open tasks" is what the FIRM has to do about
them. Neither is "Waiting on the client" any more — a heading in the passive voice about somebody
who is not reading it, and the same phrase was the status on every outstanding request bubble in
the thread beside it. That bubble now reads "Pending", matching its own siblings "Received" and
"Cancelled", which are one word each.

**An empty panel names its OWN kind.** Files said "Nothing has been sent in this conversation yet"
— a sentence about the conversation, sitting beside a thread with plenty in it, so it read as a
claim that the whole thread was empty. It says "No files yet". Notes is ALWAYS offered, unlike the
other bands, and says "No notes yet" when there are none: that is a real and useful answer about a
client — the difference between nobody having written anything down and nobody having looked.

**What the rail names depends on what is selected.** A conversation names the client and their
team; a GROUP names its members and nothing owed — "pending client tasks" cannot mean one
thing across several clients. Every role stated is the relationship on THIS conversation
(`crm.client_advisers.role`: Adviser, Paraplanner, Administrator or Support), never a job title:
the record says one thing about a person and a second vocabulary here would disagree with it.

**The record gets the rail's three panels from its page load; the inbox cannot.** Which client is
on screen there changes client-side with the selection, so `GET /chats/rail/{contact_uid}` serves
advisers, open requests and open tasks in ONE round trip — one question, one request per click.
That endpoint is why a conversation publishes `contact_uid` at all: `GET /crm/contacts/{uid}` is
uid-addressed, so the serial alone could not read its own client's advisers. The panels are
CLEARED synchronously on every selection change, before either fetch lands — the previous client's
open requests left standing against this one is a false statement about who owes what.

**The rail does not collapse.** It used to fold away behind one toggle, which put the client's own
details one click away on every conversation. Only the breakpoint still varies: below `lg` there is
no room for two columns and it stacks under the thread.

**Open means open, not `status = 'open'`.** Both readers fetch the client's tasks unfiltered and
the rail narrows them with the shared `isOpen` from `@graphene/ui/book/tasks`, which is open OR in
progress. Filtering server-side on `task_status=open` would have quietly hidden exactly the tasks
somebody had started. Requests are narrowed to `pending`: a fulfilled one is history and belongs on
the Documents section, and a cancelled one is not owed at all.

**The thread takes the whole pane, and a thread being READ draws a skeleton.** A capped, centred
column left a channel of empty card down each side and floated the firm's own replies off the edge
they are anchored to; each bubble carries its own `max-w-[42rem]`, so a wide monitor gets more
conversation rather than longer lines. And while the messages are in flight the pane shows a run of
placeholder bubbles built from the shared `Skeleton`, not the "Send a message to start the
conversation" panel — that panel is a statement that this client has never written to the firm, and
it was being made about every thread for the half-second of its read.

## The conversation pane's header (`ConversationPanels`)

**Chat and Files are TABS, and the row is reserved.** Only two things here are views of the
CONVERSATION — what was said and what was sent — so only those two are tabs; everything about the
CLIENT is in the rail. The row is `shrink-0` above a thread that is the only thing scrolling, so a
long conversation can never slide under it: a header a conversation can push off the top is a
header you cannot use at the moment you want it.

**Search is a BUTTON that becomes a box.** A permanent input in that row was a field competing with
the two tabs for the reader's eye on every conversation, most of which nobody searches. It folds
back when the reader leaves it EMPTY; with a term typed it stays, because closing clears the term
and a collapsed box over a filtered thread is a thread missing messages for no reason the reader
can see.

**The Files panel is derived from the live message list, never fetched.** `messages` is `$state`
that both `send()` and the realtime push mutate, so a file sent or received while the tab is open
appears in it at once; a server-side files list would go stale on the first send and stay stale
until a reload. The MESSAGES are reversed and then flattened, not the flattened list — several
files ride one message, and reversing afterwards would put that message's own files backwards.

## What a failed panel does not do

Neither side panel counts towards the surface's `loadError` — this page is the CONVERSATION, and a
panel that failed must not put a "messages couldn't be loaded" banner over a thread that loaded
perfectly (the inbox's own rail read fails silently to empty panels for the same reason). Each is skipped rather than 403'd for a reader without its permission
(`documents:read` / `tasks:read`), so the panel is absent rather than permanently empty. Hrefs come
from an injected `links` builder and the rows render unlinked without one, so a read-only mount
cannot send an operator to a route their portal does not have.

## A message you are watching arrive raises no notification

A chat message notifies the other party — that is what the bell is for — but it used to notify them
even when they had that very thread open, so a toast announced a message you had just watched land
and the badge then claimed something was waiting. Both are false statements about what the reader
has seen.

**It is suppressed on the CLIENT, deliberately, and the notification is marked READ rather than
merely un-popped.** Not popping it would leave it on the badge, which is the same false claim in a
quieter form; the reader did see it.

Suppressing it server-side was rejected rather than skipped. The notifier runs in the worker; the
only signal for "is this person looking at that thread" is the API process's in-memory connection
registry, which is single-process by design (see above). A worker cannot ask it, so it would take
either a `LISTEN`/`NOTIFY` fan-out or a new "last seen on conversation X at T" column — real
plumbing for a cosmetic win, and a heartbeat that was ever wrong would silently swallow
notifications, which is far worse than one spurious toast.

Three pieces:

- **`arrivals()`** (`pkgs/ui`) takes an optional `showing` predicate and returns a third bucket,
  `silence`. It partitions BEFORE the toast cap: if the cap ran first, three louder arrivals could
  push the one you are reading out of `silence` and leave it unread. Marking read is a write, so
  the function stays pure and hands the list back rather than acting on it.
- **`NotificationBell`** calls its own `markRead` for each silenced item — the same path a toast's
  dismissal takes, so badge, list and server move together. Absent predicate ⇒ nothing suppressed,
  which is every portal that has not opted in.
- **`$lib/reading.svelte.ts`** (platform) is the seam: the thread registers the CONTACT it is
  showing, `isOnScreen` compares it against the notification's own `subject_id`. A contact rather
  than a conversation uid because the same thread is reachable from the inbox and the client
  record, and the contact is what both agree on — it is also what the notification already carries,
  so nothing has to be translated. Module-level rather than a context: the bell is mounted by the
  root layout and the thread several layers below it, so there is no boundary to pass one through.

Two traps the tests pin. `subject_id` arrives as a STRING and the register holds a number, so a
bare `===` is false for every notification ever sent — and the symptom is indistinguishable from
the feature not being wired at all. And the registration returns its cleanup from an `$effect`, so
navigating away cannot leave a claim standing and swallow that client's next notification from
every other page.

## Live push (`app/live_push.py`)

The browser opens a same-origin **Server-Sent Events** connection to its SvelteKit portal. The
portal authenticates that ordinary request from its sealed session, calls
`POST /messaging/ticket` over private `API_URL` with the server-held bearer, then opens
`GET /messaging/events` with the single-use ticket in `x-realtime-ticket` and pipes the response
back. The ticket therefore reaches neither browser code nor a URL/access log, and a public Client
or Platform browser never needs network reachability to FastAPI.

The API subscribes the redeemed key to a bounded queue before streaming. It flushes immediately,
sends a heartbeat comment every 15 seconds so Traefik and intermediaries keep an idle stream open,
and unregisters in the generator's `finally`. A slow reader cannot grow memory without bound:
overflow collapses its backlog to one `resync` event, which makes the browser refetch the source of
truth. Native `EventSource` reconnects automatically, and every reconnect also refetches so an
ephemeral channel never pretends to replay events it missed.

**The client portal's stream is keyed on a primitive, and a push re-reads one loader** (Sept
2026, `apps/client/src/routes/messages/+page.svelte`). The `$effect` that opens the `EventSource`
used to read `data.conversation`; every reload of `data` handed it a new object, it closed the
stream and opened another, and the new connection's `open` handler — "refetch on reconnect" —
reloaded again. Measured at 138 connects, 138 full `invalidateAll()` reloads and 276 mark-read
POSTs in fifteen seconds from one idle tab, each reload also re-running the layout's five slice
reads. The effect now depends on `streamContactId` (a `$derived` number, so unchanged values do not
re-fire it), a push or a send calls `invalidate('app:messages')` — the page's own `depends` key —
rather than the whole tree, and the first `open` is not treated as a reconnect (the loader that
rendered the page just read the thread). `messages.test.ts` pins all three. The bell in
`@graphene/ui` follows the same pattern for the same reason.

The old ticket-authenticated WebSocket endpoint (`/messaging/ws`, `/notifications/ws`) has been
removed: no portal ever constructed a browser WebSocket or read `PUBLIC_API_URL`, so it was dead
code from the browser's perspective. `push()` runs every payload through `jsonable_encoder` once
before fan-out (a message row contains a real `datetime`, which plain `json.dumps` cannot encode
raw).

The subscriber registry is a **plain in-process dict** — correct for exactly one API process.
Scaling messaging past one API instance still needs a Postgres `LISTEN`/`NOTIFY` fan-out
(notifications already have that bridge), so delivery works regardless of which instance holds the
stream or handled the POST.

**Read state follows what's on screen, not just which conversation is open** (the platform
portal's `MessagesInbox.svelte`): a push for the open conversation only auto-marks-read if the
thread was scrolled to the bottom when it arrived — scrolled up reading history, a new message
stays unread until you scroll down to it, same as Slack/Teams. Reconciling live pushes against
local state is done by patching in place from the payload, never a background list refetch — a
refetch racing the read-receipt POST is what put a cleared unread dot back in an earlier version
of this component.

### Graphene support in a firm's channel

Operations uses the same messaging API through
`/tenants/{uid}/operate/messages`, with every REST request carrying `tenant=<uid>`. Reads require
`tenant_portal:read`; sends, attachments, group changes and document requests require
`tenant_portal:write` at the admin proxy and again at the target session. The ticket endpoint uses
`target_tenant_read_session`: although ticket minting is a POST, it creates no tenant data, and the
redeemed key is `staff:{target_tenant_id}` rather than the operations tenant.

Migration `0127_messaging_support_sender` adds `messages.sender_is_support` and
`sender_display_name`. `_insert_message` sets the marker from `auth.portal() = 'operations'` while
preserving the real operator's `sender_user_id`, and snapshots their display name because a target
firm's client cannot read the cross-tenant `auth.users` row under RLS. List, immediate-send and
group-feed projections prefer that snapshot and return both attribution fields. Shared
message UI renders such a sender as **“Graphene support · {operator name}”** and never substitutes a
firm role. This is attribution, not impersonation: the client and firm can see who wrote the
message, action events retain actor plus target firm, and historic firm-authored messages keep the
default `false`.

## Not built

No adviser-reassignment ledger (mutable pointer only), no multi-instance push (single-process
registry), no per-tenant attachment storage quota, and no scroll-position read-tracking on the
client portal's single-conversation view (opening the thread marks it read, matching how the
client side has always worked — there's no "which conversation" ambiguity to track there).

## Addressing — conversations, groups and attachments carry a uid

`0112_messaging_uids` adds `uid` to `messaging.conversations`, `messaging.groups` and
`messaging.attachments`, and every path that names one of them — the API's own, and the portal
proxies in front of it (`/chats/{uid}`, `/chats/groups/{uid}`, `/chats/{uid}/attachments/{uid}`,
`/messages/attachments/{uid}`) — names the uid. This is the estate-wide rule in
`docs/api.md` → "Addressing"; messaging shipped before it and was the only feature still
publishing serials, which the rule's static scan (`apps/admin/src/route-addressing.test.ts`) is
what caught.

Three things worth knowing before changing it:

- **The API is uid-addressed here, unlike the rest of the surface.** These rows are reached only
  through the portals' own `+server.ts` proxies, which pass the segment straight through, so a
  serial in the API path was the serial in the browser's URL. A `by-uid` resolve in between would
  cost a second round trip on every read receipt. The endpoints resolve in the lookup that already
  had to prove the row belongs to the caller's tenant, so the change costs no extra query.
- **Only the boundary moved.** `get_conversation_for_tenant_by_uid` /
  `get_group_for_tenant_by_uid` return the row, and every internal call, FK and comparison keeps
  the serial `id` — including the realtime payload's `conversation_id`, which is an event field
  consumers compare against a conversation they already hold, never a path.
- **A message has no uid**, because nothing addresses one. What a message does carry is
  `conversation_uid`, so a frontend can build a link to the thread (or to an attachment inside it)
  from the message row alone rather than mapping ids to handles client-side. The header search
  does that as `/chats?conversation={uid}` (a group hit as `/chats?group={uid}`): `/chats/{uid}`
  is this proxy, and linking it opened the JSON instead of the inbox.
