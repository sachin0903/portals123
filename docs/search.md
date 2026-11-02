# Spotlight search

The platform portal’s global search is a command palette opened from the compact search button in
the topbar’s trailing utility cluster (beside notifications) or with `Cmd/Ctrl+K`. The topbar
control is deliberately not field-shaped: typing happens in the palette, so an inert wide bar must
not spend the centre of every page. It still prints its shortcut because this is a primary tool, but
only the shortcut for the current keyboard — **⌘ K** on Apple devices, **Ctrl K** elsewhere — as
resolved after hydration by `searchShortcutLabel`. It searches the work a staff member is allowed
to see, provides permission-filtered page destinations, and exposes a small set of safe create
actions. It is not a settings catalogue.

## Interaction

Commands and scoping are deliberately separate, because they answer different questions: a command
_runs_ something and leaves the palette, while a scope only decides _where_ a search looks. They had
one shared list at first, and “Create client” sitting beside “Search clients” made a mutation and a
filter read identically.

- `/` — **Quick actions.** Only the safe create actions below, filtered as you type (`/journey`).
- `>` — **Choose a category.** The search-category list, filtered by label or key
  (`>rev` → Reviews).
- `clients: ashworth` — the same scopes as an inline token, for someone who already knows the one
  they want. Unknown keys stay part of the query, so `ref: 12/A` searches for that text.

At rest the palette is only the bar, holding a large light field, the category the search is pointed
at, and two buttons — **Categories** and **Quick actions** — that do exactly what typing `>` or `/`
does, and hand focus back to the field so the arrow keys keep working. Nothing is listed until the
user has asked for something, which is why the lists are not both on show the moment it opens.
Technical terms such as “scope” and “command” stay out of the interface; the prefix hints are
secondary shortcuts, not instructions the user must learn.

When an unscoped query returns more than one category, that row changes into a contextual category
strip: **All** plus only the categories actually present in the result. Choosing one uses the same
server-side scope as `>` rather than merely hiding the other preview groups in the browser, so the
API can return a full result set for that category. Message records and group threads collapse into
one **Messages** choice, and categories keep the application’s stable order rather than jumping
around with result rank. A single-category result does not spend a row repeating its heading.

Visible occurrences of the query are highlighted in each result’s title, subtitle and metadata.
`highlightParts` matches case- and accent-insensitively but returns plain-text segments, never HTML,
so a record name cannot become an injection path. It deliberately does not invent highlights for a
typo-tolerant result or for a match against a non-displayed identifier: without an API-provided
match explanation, marking a merely similar visible word would be misleading.

The records themselves render as one **Best matches** stream, not one block per category. Every row
keeps its icon and always-visible type badge, so a client, account and transaction remain
distinguishable without making type decide position. The response still carries groups as a
transport shape because the contextual category strip needs to know which kinds were returned.

### The panel

The panel's material, geometry and phone behaviour are shared constants in `@graphene/ui`'s
`search.ts` — `SPOTLIGHT_PANEL_CLASS`, `SPOTLIGHT_PANEL_STYLE`, `SPOTLIGHT_OVERLAY_CLASS`, plus
`SPOTLIGHT_PHONE_CLOSE_CLASS` and `SPOTLIGHT_CHIP_CLASS` — so the platform's palette and the
client's cannot drift. Everything below describes the floating bar, from `sm` up.

**On a phone (below `sm`) it is a full-screen search view**, modelled on Monzo's (the App Store
and Material's SearchView do the same). A floating box with the keyboard under it leaves a slot of
results squeezed between two chromes, so the search takes the whole screen: a filled pill field
pinned at the top, a round close button beside it, the category pills underneath with no rule
between them, and the results filling the rest. The geometry knob becomes `100dvh` (the knob is a
class, not inline style, precisely so this override can win), the corners and glass go (a sheet
over the whole page has nothing to refract, so it sits on the solid background), and the dialog's
own X hides in favour of the round close the palette draws (`data-slot="spotlight-cancel"`). The
platform's `?` guide button hides there too.

It is centred on the **window**, not on the content canvas — `left-1/2` cancels the
`--content-inset-left` offset every other dialog takes, because a dialog about one screen belongs to
that screen while search is opened from anywhere.

Vertically there is one knob, `--spotlight-max-h` (70dvh), and the top is **derived** from it:
`top: calc((100dvh - var(--spotlight-max-h)) / 2)`. The panel grows downward from that fixed top, so
the resting bar sits high, a result list folds **down** rather than the bar climbing away from where
it was just read, the fully-unfolded panel is exactly centred, and it can never fold past the bottom
of the view. The panel owns the cap and the `Command.List` is the part that gives way: its open
max-height is the panel height minus the fixed search chrome, and overflow scrolls inside it. Two
independent numbers — one for the offset, one for the cap — would drift apart the first time either
changed.

The movement uses the application motion vocabulary rather than search-specific timings:

- `motion-menu` pops the panel in on the shared 220ms bouncy spring.
- `motion-reveal` transitions the list’s max-height, opacity, padding and border on that _same_
  spring and duration, so the panel and the results it unfolds read as one gesture. The top never
  moves, so the ~12.6% overshoot is spent on the bottom edge rather than on the field being read.
  It is deliberately not `motion-expand`, which is the zero-bounce spring for surfaces too large to
  bounce.
- `motion-item-enter` gives newly-mounted result groups a reveal-duration fade and 4px rise.

All three collapse through the design system’s `prefers-reduced-motion` rule. See
[`design-language.md`](design-language.md#motion); adding a duration or easing in this component
would be a second motion system.

The material is the theme’s own **`glass`**, not a hand-rolled blur, with a scoped `--glass-*`
override for tint, blur and sheen — theme.css re-derives the fills from an inline `--glass-*` style
(`[style*='--glass']`), per colour scheme, so a tenant’s branding still owns them. Two points are
worth knowing because each one cost real time:

- The platform imports `sharp.css`, which exists to **turn glass off** in the staff tools (a surface
  an adviser reads all day must not sit on a moving backdrop). That is why the palette looked flat
  no matter how much blur it asked for. It is a deliberate exception: a palette is open for seconds.
- The edge and drop shadow live in `.spotlight-glass` in the shared `theme.css` (both portals’
  palettes wear it), because those two tokens have to differ by scheme (an ink-tinted shadow
  disappears on a dark canvas), and the drop is re-declared as `shadow-(--glass-shadow)` — the
  dialog’s own `ring` utility emits Tailwind’s composed `box-shadow` chain, which silently
  replaced the shadow the material had set.

The scrim goes entirely, tint and blur, via the `overlayClass` prop added to `Dialog.Content`: a
dimmed, blurred page leaves glass nothing to refract. The overlay still closes on an outside click
and focus is still trapped. Left unset, every other dialog keeps the shared scrim exactly as it was.
The first time each signed-in user opens search in a browser, a three-point guide replaces the
results list, followed by a shortcut summary built from `PALETTE_SHORTCUTS` — the same prefix
constants the palette answers to, so a hint cannot advertise a key that no longer works. Its dismissal is recorded in local storage under a versioned, user-specific key; the
labelled `?` button beside the close control shows it again without changing that first-run state.
Typing immediately dismisses the guide, so it never blocks someone who already knows what they need.
Both prefixes are bare printable keys, so
`/` and `>` open the palette only from the page itself and never while the caller is typing into a
field — and neither needs a modifier (the first design used `Option+0–8`, which on a Mac types `º`,
`¡`, `™`… instead). Backspace or Escape on an empty scoped search returns to All categories, clearing
an inline token as well as a pinned scope; Escape again closes the palette. The command component
owns arrow-key navigation, selection and focus restoration.

Typing also filters page destinations built from the same `buildNav()` result as the sidebar, so a
page cannot appear in Spotlight unless it passed the sidebar’s permission gate. Common actions use
their own write permissions:

- Create client — `contacts:create`
- Start journey — `cases:write`
- Upload document — `documents:write`

The destination pages own their existing forms and dialogs. Spotlight only navigates to a URL
intent; it does not duplicate mutations or form logic.

## API and isolation

`GET /search` keeps client-portal search unchanged. The API retains task search for internal and
future consumers, but the platform palette has no task route or scope, so task groups are discarded
before rendering. The optional `scope` query parameter asks the API to execute one branch only.
Groups the caller lacks permission for are omitted rather than turning the whole request into a 403.

Every branch reads the existing source tables under the request’s normal PostgreSQL RLS claims.
There is deliberately no denormalised search table: copying tenant data into a second catalogue
would create another isolation policy and another synchronisation path. The platform proxy forwards
the signed-in user’s bearer token and accepts only the closed scope vocabulary.

## Matching and ranking

Migration `0182_spotlight_search` enables `pg_trgm` and adds the immutable
`search.normalise(VARIADIC text[])` expression. Migration `0211_search_column` gives every
searched table a stored generated `_search` column — its names, titles and identifiers folded by
`search.normalise` — with a trigram GIN index on it. The fuzzy predicates in
`app/routers/search.py` read `_search`, so each table’s searchable field list lives in one place
and the predicate always matches its index. Longer task, message and review prose keeps its
full-text index with the `simple` configuration.

The list pages' search boxes (clients, accounts, tasks, flows, message groups and the admin firm
list) match through the same column with `listing.search_sql`: a row matches when a `_search`
contains the term, or is word-similar to it from four characters with a threshold of 0.5 (`listing.fuzzy_allowed`, `listing.FUZZY_THRESHOLD_SQL`). The list rule is stricter than the bar's because a list is sorted by column, not by relevance, so a loose match is not pushed below the real ones: at the bar's 0.3, `sipp` put every account of “Sizeable Trust” into a search for SIPPs. Each page keeps its own sort
order, and fields outside `_search` — a task's description, an account's product type, model and
adviser — are still matched by containment. The documents library is the exception: its box has
always matched the document name only, so a search for “tax” does not return every tax
document, and it stays that way.

| Table                 | `_search` fields                                                                              |
| --------------------- | --------------------------------------------------------------------------------------------- |
| `crm.contacts`        | uid, name, first/middle/surname, email, phone numbers (not their labels), national identifier |
| `platform.accounts`   | uid, name, platform identifier, currency                                                      |
| `platform.products`   | uid, code, name                                                                               |
| `documents.documents` | uid, name, document type, source                                                              |
| `tasks.tasks`         | uid, title, category, subject, source reference                                               |
| `flows.executions`    | uid, subject                                                                                  |
| `flows.flows`         | uid, code, name, description                                                                  |
| `messaging.groups`    | uid, name                                                                                     |
| `actions.runs`        | uid, case number, action, event, subject (held runs only)                                     |
| `auth.tenants`        | uid, name, alias, website                                                                     |

Why trigrams and not full text or BM25 for these fields: people type fragments of surnames, phone
numbers and account ids while the query is still incomplete, and make one-letter typos. Trigrams
match all three; word-based indexes (`tsvector`, BM25 via `pg_textsearch`) match whole tokens or
token prefixes only. A trigram score also compares the query with one row, so other firms’ rows
cannot move it; BM25’s corpus statistics span the whole table regardless of RLS.

Transactions are the deliberate exception. The staff branch searches only an exact or prefix
provider `source_transaction_id`; it does not fuzzy-search transaction attributes, instruments or
the owning account on every global query. Migration `0183_txn_reference_search` replaces that
table’s broad trigram index with `lower(source_transaction_id) text_pattern_ops`, because the
existing unique key starts with `source_system` and cannot efficiently answer reference-only
lookups. The reference is included in the result subtitle, so its highlighted match is visible.
Client-portal transaction search remains unchanged and scoped to the active person.

Two-character queries are restricted to exact and prefix matching. Fuzzy trigram matching begins
at three characters, with a word-similarity threshold of 0.3. The database first keeps the per-type cap as a performance backstop, then
assigns each retained staff hit one cross-entity `rank` using:

1. exact identifiers, email addresses and normalised phone numbers;
2. prefixes;
3. trigram similarity for names and titles;
4. full-text prose matches;
5. recency as a tie-breaker.

The API’s groups remain available for category discovery, but the platform flattens their hits by
that rank. Search is sequence-guarded and debounced in the browser, and a changed query or scope
cancels the previous request so a slow response cannot repaint newer results.

## The client portal’s palette (`SpotlightSearch`, Oct 2026)

The client portal used to have its own, older palette — a centred card with a scope grid, a
grouped result list and a keycap footer. It now wears the same Spotlight bar as the platform,
through a shared `@graphene/ui` component, **`SpotlightSearch`**, which is this palette with the
operator’s tools removed: no quick actions, no `/` or `>` prefixes or inline `clients:` tokens, no
first-run guide, no keycaps and no keyboard shortcut — a client opens it from a button, types, and
taps a result. What it keeps is the surface and the state machine above: the glass panel and its
derived geometry and motion, the field, the category row, one relevance-ordered **Best
matches** stream with the matched text marked by `searchHighlightParts`, and the honest searching /
nothing / failed-with-retry states. Backspace or Escape on an empty scoped field lifts the
category before anything closes.

Unlike the platform's contextual strip, **the client's category row is always on show**: every
category the portal searches, so a client sees what search covers before typing and narrows with
one tap. Categories the current result does not contain stay offered but read quiet. Tapping the
pressed category again lifts it; the floating bar also has an explicit "All" first, which a phone
hides, as Monzo does, because the toggle is the way back there. An open on a category (below)
scrolls that chip into view.

**Action items are not searched.** The API's `task` group has no route in the client's `ROUTES`
and no entry in `SEARCH_SCOPES`, so any task hits are dropped as unroutable: action items are
worked from home, a handful at a time, and do not need finding.

The component owns none of the portal’s **policy**. The app supplies its categories (`scopes`), the
mark and label per hit type, where a hit routes (`routes` — the client’s `ROUTES`, which never
routes a staff-only type), a `SearchClient` with its own URL builder (the client appends
`contact_id` so a multi-client login searches the person it is viewing), and `onnavigate`. The
client’s `SearchBar.svelte` is now that policy plus the round header button. The platform’s bar
still carries its own markup for the operator features; the pieces both share — relevance order
(`relevanceOrderedSearchHits`), the category strip (`searchCategoriesForGroups`) and highlighting —
live in `@graphene/ui`’s `search.ts` and the platform’s `$lib/search.ts` aliases them.

A pinned category narrows what is **shown**: the client branch of `GET /search` has no `scope`
parameter (it is the platform’s), and the API already answers over everything the reader may see,
a handful per group, so the cut is made in the browser rather than with a second round trip. If a
client’s scoped search ever needs more than one page of a category, the honest next step is to
teach the client branch the `scope` parameter, not to raise the group limit for every keystroke.

**Opening it from a page.** The browse bar on the Docs and Insights pages
(`docs/portal-customisation.md`) has a search button, and it does not grow a second search box: it
calls `openSearchPalette('document')` / `openSearchPalette('insight')`, which dispatches a
`graphene:search-open` window event that `SpotlightSearch` listens for and opens on that category.
A window event rather than shared state because the two ends have no common ancestor — the palette
lives in the app shell, the button several components down inside a `@graphene/ui` page block — and
because it is SSR-safe: with no window there is nobody to tell, and the call is a no-op.
