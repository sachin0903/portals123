# Client-portal customisation

The client portal is composable: a firm defines its portal as a set of **pages**, each page a grid
of **blocks**, each block carrying its own **settings** — all no-code, edited in the branding
studio and stored as part of the tenant's branding. This document is the definitive reference for
that system, in two passes: the concepts a firm-facing reader needs first (§1–§4), then the
engineer's rulebook (§5–§8). The machine-readable source of truth is
`pkgs/ui/src/lib/components/branding/block-contract.json` — the shared Python/TS contract that
`apps/api/app/branding.py`, the pkgs/ui registry and both sides' pin tests
(`apps/api/tests/test_branding.py`, `pkgs/ui/.../registry.test.ts`) all assert against. **Every
enumeration in this document is a hand-written rendering of that JSON; when they disagree, the JSON
and its pin tests win.** Related reading: `docs/api.md` → "White-label branding" (endpoints,
serving, storage), `docs/design-language.md` (the UI law the blocks obey),
[`docs/content.md`](content.md) (the news/article/guide library behind the news block).

## 1. The model

Branding lives in `auth.tenants.theme` (jsonb), one object per tenant:

```
Theme                        one per tenant (v2: per-portal)
└─ client / platform         each a Branding — client is the shopfront; platform is identity + accents
   └─ templates[]            client only; named design sets selected by client classification
      ├─ group_slugs[]       one template may serve several groups; one slug resolves once
      ├─ classification_slugs[]  one template may serve several books; one slug resolves once
      ├─ role_slugs[]        leftover fallback for firms still assigning by auth-profile role
      ├─ colours + finish    each template owns its light/dark palette and visual treatment
      └─ pages: PageDef[]    null ⇒ the built-in default page set
         └─ layouts          per-audience block lists (copy-on-write)
         ├─ default          always present
         ├─ individual?      override for individual clients
         └─ organisation?    override for trusts & companies
            └─ Block         { id, type, visible, w, h, settings }
```

- **`Theme { v, client, platform }`** — the client and adviser portals are branded independently.
  Only the client `Branding` carries `pages` (and the legacy `layout` mirror, §8); the platform
  portal is a **fixed surface** (§4): logos, icon, favicon, login/support copy, and two accent
  colours (primary + brand). Paper, type, density, radius, glass and finish stay the product look.
- **Client portal design sets** — `Branding.templates` holds named visual/page definitions.
  `group_slugs` assigns one design to any number of the firm's [client
  groups](client-groups.md) (a business stream the firm tags onto people); `classification_slugs`
  does the same for [client books](client-classifications.md). Assigning a slug to another design
  removes it from the first. A signed-in client's groups are tried first, in catalogue order; the
  first group that has a design wins, otherwise the book, otherwise the role.
  `role_slugs` remain as a fallback for firms that have not moved off auth-profile roles.
  `default_template_id` serves unmatched clients. A template owns colours, fonts, mode, density,
  finish, glass and pages, while the firm's logo, favicon, login copy and support details remain
  shared. An absent/empty template list preserves the legacy single-design behaviour. The
  authenticated client's classification (then role) selects the design in the client hook before
  SSR, so the palette, navigation and content composition change atomically.
- **`PageDef { id, slug, label, icon, nav, layouts }`** — `slug` is the URL segment (`/{slug}`;
  `home` serves `/`), `id` is the stable handle a rename or slug change keeps (drag state and
  audience overrides key off it), `label` feeds the nav tab (≤24 chars), `icon` names one of the
  curated lucide icons, `nav` puts the page in the tab bar (absent counts as shown). Page order is
  nav order.
- **Audience resolution** — a signed-in client is classified from `GET /crm/context`'s contact
  `type`: `organisation` for trusts/companies, everything else (including an unlinked or degraded
  context) reads as `individual`. The audience's override layout wins when set — **including an
  empty array**, which is an explicit "show this audience nothing" and is honoured; only
  null/absent falls back to `layouts.default` (`resolveAudienceLayout`, mirrored by the wire type
  in `@graphene/auth`). In the studio an override starts life as a deep copy of the default
  (copy-on-write, `page-audience.ts`) and can be reverted to following it.
- **`Block { id, type, visible, w, h, settings }`** — `type` names one of the eleven block types
  (§2); `w`/`h` are the column/row span on the 12-column grid (§5); `visible: false` keeps a block
  in the layout but off the page; `settings` is the per-instance configuration validated by the
  type's settings model. `id` is unique per page — the API re-mints missing/invalid/duplicate ids
  deterministically as `{type}`, `{type}-2`, … The same type can appear on a page more than once.
- **Defaults** — a tenant whose `pages` is null gets the built-in default set (§1.1), defined twice
  on purpose: `defaultPages()` in `pkgs/ui/.../portal-pages.ts` and
  `app/branding.py::default_pages()`. The API container ships without pkgs/ui, so the Python literal
  cannot read the contract at runtime — the contract's `defaultPages` entry and the pin tests keep
  the two byte-equivalent. Change all three together. A contract entry's `blocks` is the page's
  `default` layout; optional `individual`/`organisation` arrays alongside it are audience overrides,
  and a block's `id` is spelled out only where a layout holds two blocks of one type.

### 1.1 The shipped default page set

Five pages, all five slugs shadowing built-in routes, all five in the nav — exactly the phone
bar's cap (§1.2). Insights is a page like the others, not a route the client app appends: a firm
places, renames or drops it in the studio.

| Page                            | Default layout                                                                                                                                                                                                                                                                                                 | `organisation` override                                                                                                                                                                                            |
| ------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Home** `house`                | onboardingProgress 12×2 _milestones_ · actionItems 12×1 _banner_ · portfolioHeader 12×1 _hero_ · accounts 6×1 _stack/card_, by wrapper · adviser 6×1 _card_ · activity 6×2 _list/card_ 4 · documents 6×2 _recent_ 4 · message 12×2 _note_ · **hidden:** allocation 5×2 _donut_, holdings 6×2 _top_ 5, news 4×2 | accounts 12×2 _table_ **by owner** · adviser 12×1 _strip_ · activity 12×2 _table_ 8 · documents 12×2 _recent_ 4 · message 12×2 _note_ · **hidden:** allocation 6×2 _bars/spectrum_, holdings 6×2 _top_ 8, news 4×2 |
| **Portfolio** `pie-chart`       | portfolioHeader 12×1 _compact_, no chart · allocation 12×2 _donut_ · holdings 12×4 _grouped_ (by asset class)                                                                                                                                                                                                  | a 12×2 _summary_ roll-up · accounts 12×4 _table_, by wrapper · allocation 12×2 _bars/spectrum_ · holdings 12×4 _table_, toolbar                                                                                    |
| **Activity** `arrow-left-right` | activity 12×4 _list/card_, toolbar (the home card's list, uncapped — a toolbar'd block shows every row)                                                                                                                                                                                                        | —                                                                                                                                                                                                                  |
| **Docs** `file-text`            | documents 12×4 _grouped/timeline_ (recency groups, browse bar), toolbar, requests banner                                                                                                                                                                                                                       | —                                                                                                                                                                                                                  |
| **Insights** `newspaper`        | news 12×4 _library_ (the whole published library: newest item as the lead, the rest as rows)                                                                                                                                                                                                                   | —                                                                                                                                                                                                                  |

The decisions behind it, so a later edit knows what it is undoing:

- **Home is short; Portfolio holds the analysis; the listing pages are tools.** Home used
  to be every other page in miniature — eight widgets, each a teaser for a tab one tap away,
  roughly 2,500px of scroll on a phone before the activity list, with the activity (the one thing
  that is new since the last visit) last. Now it is what needs the client (onboarding, then
  actions), the position (hero), their accounts, what has changed (activity), what has arrived
  (documents) beside who to talk to (the adviser card — face, name, Message; a strip for an
  organisation, whose relationship is with the firm more than one face), and the firm's note. News
  is Insights. Allocation, holdings and news ship **hidden** on home rather than absent, so a firm
  that wants one back toggles it in the studio instead of re-adding and re-sizing it.
- **Portfolio, not Accounts.** The default set used to carry an Accounts page: the account
  cards, a donut and the whole-book holdings table. For the one-to-three-account client who is
  most of a book, the cards were home's deck again and the table was the account record again,
  so the tab owned one donut. Portfolio is the whole-book lens neither home nor a record gives —
  the figure and its return (compact header), how it is spread (the donut), and what it is made of:
  the `grouped` holdings variant lists every fund under the asset class the donut draws it in,
  in the donut's colours, one row per fund with its mark, units · price, value and return. The API
  names each holding's class (`/crm/holdings` → `asset_class`, `crm.py::_asset_class`, the donut's
  own bucket), so a row and its slice cannot disagree; the loader reads the whole book for this
  variant (500 rows, no page), because a class subtotal of one page is a page's. The account
  record uses the same list, scoped to the account. The organisation layout keeps the accounts
  register (summary + table) — a trust's accounts are its subject. `portfolio` is not a
  shadowable slug: it is served by the composed `[slug]` route, and `/accounts` still renders the
  legacy listing for a firm that links to it. **There is no Holdings page** either, for the same
  reason. (Oct 2026; before that the set was five pages and home carried eleven blocks.)
- **The account record is made of the same blocks.** `/accounts/[uid]` wears the canvas header
  scoped to the account (`GET /crm/accounts/{uid}` carries the account's own `performance`
  series and `performance_period`), then `WidgetHeader` + the shared holdings table and activity
  feed through the same projections the composed loaders use. It used to be hand-built — a plain
  card with a `text-3xl` figure and two desktop `DataTable`s — so a client tapping an account card
  arrived in a different product. **Holdings and Activity are one band with a `Segmented` switch**
  (`lg`, labelled with a count each, Holdings first): a real account's holdings run to two screens
  on a phone and the Activity card stacked under them was where nobody scrolled to. The chosen view
  is shallow-routed — `page.state.view` plus `?view=activity` in the URL for a reload or a shared
  link; `replaceState` does not update `page.url`, which is why both exist. Under the header, the
  **facts row** is never one tile: every account gets Cash · Invested (value less cash, with the
  count of investments) · Gain/loss (the lake's unrealised `growth`, "—" when absent or when it
  is at or above the valuation, which is a bad basis), then the wrapper's own — the ISA/JISA
  allowance and a pension's crystallised/uncrystallised split (`$lib/account-facts`). The
  allowance tile is **always present on an ISA/JISA/LISA**: the amount used ("ISA allowance
  used · £5,000 · £15,000 left · 2026/27") when stated, otherwise the statutory annual limit for
  the current tax year with "amount used not yet reported" (`allowanceFact` in
  `book/accounts/money-facts.ts`). The statutory figure is never used as the client's LIMIT —
  a flexible ISA's is not the statutory one (`docs/sei-data-sources.md` §8.5a) — only as the
  public annual limit, which is true of every ISA. An odd set of tiles lets the first (Cash) span
  the phone's two columns rather than leave a hole beside the last.
- **The `organisation` override is a register.** A trust or company has several signatories and no
  single personal relationship, so tables replace the personal stack, accounts group by **owner**,
  activity becomes a ledger rather than a story, and the adviser is a line rather than a portrait.
  Individuals keep the default composition — `individual` is deliberately never set, or `default`
  would mean nothing. The three listing pages carry no override: a full table already suits both.
- **Docs and Insights read like a client's, not a filing clerk's.** The Docs page used to be a
  shelf of cards with a size and a format badge on each, under a heading and a count; it is now a
  place with **no heading of its own** — the nav named the page the reader just tapped, and a
  count over a list the reader can see is a number they did not ask for. The categories are the
  **browse bar's tabs** (below), one friendly row per document (category mark, name,
  "Statements · 15 Jul", chevron; a new one in the brand tint with a brand dot and a heavier
  name), and a closing line that offers the adviser for what is not there. The rows file in
  **recency groups** after the Files-app convention — "This month", "Last month", "Earlier in
  2026", then one group per earlier year, the row's stamp carrying the year only once the group
  no longer says it (`documents-timeline.svelte`, `now` injectable for tests). They were month
  runs, which looked right at a dozen documents and fell apart at thirty — twenty headings over
  twenty-six rows, most over a single document — where the recency groups stay at four or five
  however long the history grows. The bar's search appears only once the list outgrows a glance
  (`filtered-listing.svelte::SEARCH_MIN_ROWS`, eight rows; a search in force or a paginated
  slice keeps it, a category tap does not summon it); the tabs only with two categories to
  choose between. Insights was a CMS grid of cards with a gradient placeholder where a hero was
  missing, over a search box and three filter rows for a library of a dozen items; it is now the
  `news` block's `library` variant — the newest item as a lead (its hero when it has one, words
  alone when it has not), the rest one row each (kind · date, title), with the same bar.
- **One browse bar for every library (`browse-bar.svelte`, Oct 2026).** The Docs page and the
  Insights library used to filter differently — category tiles over a floating search box on
  one, a pill cloud and a kind switch on the other — and neither survived a phone. Both now
  wear one bar, after Material 3's primary tabs and Apple News's section bar rather than a
  stack of controls: the **tabs** are how the reader browses (document categories; topics), full
  labels on a 44px target with a 3px brand indicator under the current one and a hairline under
  the row, scrolling sideways under a fade on a phone rather than wrapping; an optional
  **pull-down** beside them for a secondary narrowing used rarely (the kind of piece; the account
  on a listing that has one — a label and chevron, an icon alone on a phone); and **search as a
  button that opens the portal's one palette** on this page's category (`openSearchPalette`,
  `docs/search.md`) — never a second search box on the page, so searching from the Docs page
  gives the same results in the same place as searching from the header, already narrowed to
  documents. The bar is presentational — tab and menu state belong to the caller.
  `WidgetToolbar`'s `tabs` category style is the bar behind `FilteredListing` (the Docs timeline
  look uses it, with `searchScope="document"`; `pills` and `tiles` remain for the carded looks);
  `news-library.svelte` drives it directly, topics as tabs with the kind in the pull-down, or
  kinds as tabs while a library has no topics to browse, its search scoped to insights.
- **Insights is where a firm's identity has the most room — so `news` took the look axis (Oct
  2026).** It is the one surface made of the firm's own words and pictures rather than the
  custodian's numbers, and it had one design. Three now, on both the library and the home feed
  (`news-library.svelte` owns the filters and empty states; the body is the look's): **`card`** is
  the reading list above; **`magazine`** (`news-magazine.svelte`) is a cover story — the newest
  piece full-bleed under an ink wash, headline in the display face — over a grid of cover cards;
  **`journal`** (`news-journal.svelte`) is type-led and chrome-less — a brand rule, the lead set
  large in the display face, the rest a dated column with the topic as a small-caps kicker in the
  brand ink — and is what `auto` resolves to under the ledger finish. A piece **without a hero**
  gets a cover in the firm's own palette rather than a grey box: `insight-cover.svelte` paints the
  topic's card finish (`categorical.ts::topicFinish`; the busiest topic takes the brand finish
  itself — `insights.ts::topicSlots`) with the topic's initial
  set large in the display face, so a firm with no photography still gets a page in its colours.
  The **default Insights page pins `magazine`** (contract, TS and Python mirrors). The **reader**
  (`/insights/{uid}`) wears the same levers: a brand rule over a topic kicker in the brand ink
  (kind · date · "4 min read", `insights.ts::readingTime` — null for a PDF guide, never "1 min
  read" over forty pages), the headline in the display face, pull quotes ruled in `--brand` and
  set in the display face (`!important`, because publish inlines the editor's `--primary` rule
  and the portal owns the look), and a **Keep reading** rail of three covers — same topic first,
  then the rest of the library (`insights.ts::relatedInsights`, from a 12-item window read beside
  the piece; a failure there costs the rail, never the article) — with "All insights" pointing at
  whichever page hosts the library, the same target as the shell's back link.
- **Two blocks ship quiet rather than absent.** `news` is hidden by default on home, but a firm
  that deliberately enables it gets a calm, client-facing “You’re up to date” state until eligible
  content is published; an enabled widget never collapses merely because its data is empty. (The
  Insights page's `library` variant owns its own two empty states — "nothing to read yet" and
  "nothing matches", because a client must be able to tell a miss from an empty library.)
  `message` is placed with **no copy**, which the renderer treats as blank and gives no grid cell
  at all (`portal-page.svelte::isBlank`), so a client sees nothing and the firm has a positioned
  slot to write into. Platform-authored copy in a firm's voice would be a fabrication.
- **Looks are used where they say something, not to demonstrate the axis.** `float` lifts the home
  account stack off the page ground; `timeline` reads recent activity as a sequence; `spectrum`
  scales where a donut of many lines does not; `shelf` suits a browsed document library; the
  adviser is a `portrait` for an individual and a `strip` for an organisation. `ink`, `sheet`,
  `index`, `orbit`, `bars` (holdings) and the `canvas`/`ledger` headers are the **presets'**
  material (§7) — the shipped default is one composition, not a gallery.

### 1.2 The tab bar

The client app derives its nav from the page set (`derivePortalNav`: nav pages in stored order,
home → `/`), then **caps the bar at five** (`apps/client/src/lib/nav.ts::TAB_BAR_CAP`). Nothing
is appended in code: the bar is the firm's pages and only those (Insights used to be bolted on
here while a slot was free; it is a page now). Five is where both platform guidelines stop and
where the labels stop fitting at 390px. The studio's own cap (`PAGE_CAPS.maxNavPages`, 6) is
deliberately looser and unchanged — tightening it would invalidate page sets firms have already
saved — so a firm with six nav pages sees its first five on the bar and reaches the sixth by
link: the cards' "View all" links are computed from the page set (`listing-links.ts`, `news`
included, so the home news card leads to whichever page hosts a `library` block), and every page
still answers at its slug. The bar is for the tab pages only: a sub page (an account record, a
document, an insight, profile, messages…) hides it and shows the shell's "‹ Parent" pill in its
place — `docs/design-language.md`, "The way back is the shell's".

## 2. Block reference

Eleven block types. `variants` are the **data shapes** a block can take; `looks` are its **visual
designs** — orthogonal axes (a holdings _table_ can render as a card or as the chrome-less
_sheet_). Both lists lead with `auto` (§3). portfolioHeader alone deliberately carries **no look
axis**: its variants _are_ its looks.

| Block                | Data sources      | Default span | Min w | Variants                              | Looks                                | Settings                                          |
| -------------------- | ----------------- | ------------ | ----- | ------------------------------------- | ------------------------------------ | ------------------------------------------------- |
| `portfolioHeader`    | portfolio         | 12×1         | 4     | `auto, hero, canvas, ledger, compact` | — (variants are its looks)           | `variant, show_chart, actions, default_period`    |
| `accounts`           | portfolio         | 6×1          | 6     | `auto, stack, cards, table, summary`  | `auto, card, float, ink`             | `variant, look, limit, group_by, show_sparklines` |
| `allocation`         | portfolio         | 6×1          | 4     | `auto, donut, bars`                   | `auto, card, orbit, spectrum`        | `variant, look`                                   |
| `holdings`           | holdings          | 8×2          | 6     | `auto, top, table`                    | `auto, card, sheet, bars`            | `variant, look, limit, toolbar`                   |
| `activity`           | activity          | 6×2          | 6     | `auto, list, table`                   | `auto, card, timeline`               | `variant, look, limit, toolbar`                   |
| `adviser`            | context           | 4×1          | 3     | `auto`                                | `auto, card, portrait, strip`        | `variant, look`                                   |
| `news`               | content           | 4×2          | 2     | `auto, feed, library`                 | `auto, card, magazine, journal`      | `variant, look, limit`                            |
| `documents`          | documents         | 12×2         | 6     | `auto, recent, grouped`               | `auto, card, shelf, index, timeline` | `variant, look, limit, toolbar, show_requests`    |
| `actionItems`        | tasks             | 12×1         | 2     | `auto`                                | `auto, card, banner`                 | `variant, look, limit, hide_when_empty`           |
| `onboardingProgress` | applications      | 12×2         | 2     | `auto`                                | `auto, banner, slim, milestones`     | `variant, look`                                   |
| `message`            | — (settings only) | 12×1         | 2     | `auto`                                | `auto, card, note, banner`           | `variant, look, title, body`                      |

Data sources drive the composed page's fetch: the loader fetches only the union of the visible
blocks' sources (`page-data.ts::sourceUnion`). `portfolio`/`accounts`/`holdings`/`activity`/
`adviser` all ride the one `GET /crm/home` payload; `documents`, `tasks` (`GET /tasks/me`),
`applications` (`GET /applications/me`) and `context` (`GET /crm/context`, always fetched) are
their own reads. `news` reads `GET /crm/content`. Default span and minimum width are registry metadata (`client-blocks.ts`), not
contract entries — the minimum is a **builder-plane floor** only: the studio refuses to author a
narrower block, but the renderer still draws whatever span a stored layout carries.

Per-setting semantics (bounds from the contract's `settingLimits`; defaults from the API's
settings models):

| Setting           | Applies to                                                                                    | Type / bounds                              | Default            | Meaning                                                                                                                                 |
| ----------------- | --------------------------------------------------------------------------------------------- | ------------------------------------------ | ------------------ | --------------------------------------------------------------------------------------------------------------------------------------- |
| `variant`         | every type                                                                                    | member of the type's variant list          | `auto`             | The data shape; `auto` resolves per §3                                                                                                  |
| `look`            | every looks-bearing type                                                                      | member of the type's looks list            | `auto`             | The visual design; `auto` follows the portal finish (§3)                                                                                |
| `show_chart`      | portfolioHeader                                                                               | bool                                       | `true`             | The performance chart under the headline total                                                                                          |
| `actions`         | portfolioHeader                                                                               | `deposit_withdraw \| contact \| none`      | `deposit_withdraw` | The buttons beside the total                                                                                                            |
| `default_period`  | portfolioHeader                                                                               | `1M \| 3M \| 6M \| 1Y`                     | `6M`               | The performance window clients land on                                                                                                  |
| `limit`           | accounts 1–50 · holdings 1–25 · activity 1–50 · documents 1–12 · actionItems 1–10 · news 1–12 | int, clamped to the range                  | unset              | Rows shown; unset ⇒ the block's own default slice                                                                                       |
| `group_by`        | accounts                                                                                      | `none \| wrapper \| owner`                 | `none`             | Sectioning axis — `owner` is the family view (per-owner sections, subtotals, "For {child}" JISA badges)                                 |
| `show_sparklines` | accounts                                                                                      | bool                                       | `true`             | The trend line on stack cards                                                                                                           |
| `toolbar`         | holdings, activity, documents                                                                 | bool                                       | `false`            | Search box + filter row above the listing (the default full-listing pages turn it on)                                                   |
| `show_requests`   | documents                                                                                     | bool                                       | `false`            | The outstanding-document-requests banner (the default documents page turns it on); no fabricated counts — absent data renders no banner |
| `hide_when_empty` | actionItems                                                                                   | bool                                       | `true`             | Collapse the block when the client has nothing to action                                                                                |
| `title` / `body`  | message                                                                                       | plain text, ≤80 / ≤1000 chars, C0-stripped | `''`               | Firm-authored copy, shown verbatim (escaped at render — never markup)                                                                   |

**A card's "View all" is decided from the firm's page set** (`@graphene/ui`
`branding/listing-links.ts::listingLinks`, Oct 2026). The accounts, holdings, activity and
documents cards used to link to the built-in slug of the same name unconditionally, so a firm that
deleted its Holdings page — or renamed it, or folded holdings into a "Portfolio" page — shipped a
home card whose button led to a 404 dressed as "Something went wrong". A destination now exists
only when some other page in the effective set (not home, which is the overview the links lead
away from) shows a visible block of that source for the client's audience; the built-in slug wins
when it qualifies, else the first such page in order; otherwise the card draws no link at all. The
loader puts the answer on `ComposedPage.links`, `PortalPage` reads it, and the studio preview
computes the same from the draft pages — so a firm deleting a page watches the link leave the card.
_Where_ the affordance sits on the card is a design matter and unchanged here.

**The activity `list` grows columns with its container** (Oct 2026). The two-line row (title,
kind + account chip, amount) is the whole of it inside a six-column home block. On a block wide
enough — `@2xl` of the list's OWN width, the full-width activity page — the account chip moves to
its own column and the custodian's words (`detail`: "Platform Fee", "Employer", "Accumulation
Dividend" — never a bare provider code, never an echo of the kind) and the ISIN take another;
from `@3xl` a trade's units and price (`416 units @ £2.9675`) take a third. A column is drawn only
when some row fills it, so a feed of cash movements never shows a column of blanks. Both
`/crm/home`'s slice and the portal loader's `/crm/transactions` projection carry the same four
keys (`detail`, `quantity`, `price`, `isin`). The kind vocabulary (`ACTIVITY_LABELS` /
`_ACTIVITY_LABELS`) is now total over `platform.transaction_type` — it was a six-kind subset with
`buy` as the fallback, which is how an interest payment printed as "+£0.01 Buy".

**The adviser block when nobody is assigned shows the firm** (Oct 2026). `GET /crm/home` and
`/crm/context` return `adviser: null` when `crm.client_advisers` has no row for the active contact —
the direct and execution-only books, typically. The block used to render an empty note ("Your
adviser will appear here"), which told a D2C client to wait for something that was never coming.
The client loader (`$lib/adviser::firmContact`, applied in `page-data.ts` once the context read has
succeeded — a _failed_ read stays a gap, not a client with no adviser) now substitutes the FIRM as
the contact: an `Adviser` with `kind: 'firm'`, `name` = the tenant's name (`locals.appName`),
`photoUrl` = the brand's `icon_url`, and `email`/`phone` = the firm's **support** contacts from
Branding (`support.email` / `support.phone` — the same details the email footers carry). All three
looks read `kind`: the "Your adviser" heading is dropped (the firm's logo and name introduce it;
a "Your firm" line over them said it twice), the disc is the firm's icon (contained, not
cropped into a face) or a building glyph, "Client support" stands where the job title and firm line
would go (a firm is not its own employer), and an Email or Call action is drawn only for a contact
the firm has actually set — `''` draws nothing rather than a `mailto:` to nowhere. Message always
remains: the thread works without an adviser, and `/messages` heads the conversation with the firm
the same way ([`messaging.md`](messaging.md)).

Two of those settings are read through a client-side check before they reach the block (Sept 2026):

- `actions: deposit_withdraw` becomes `contact` for a client whose book does not carry
  `capabilities.money_movement` — the firm's choice stands in the studio, the client never sees a
  button to a route that 404s for them ([`client-classifications.md`](client-classifications.md)).
- `default_period` is a window over the series the custodian actually sent, anchored on that
  series' **last** point rather than on today (`@graphene/ui` `investment/periods.ts`). A book whose
  feed stopped a year ago still has a last year of data to show; anchored on the clock it fell
  outside every window and the hero printed `£0.00 (0.0%)` over a blank chart — a silent zero that
  reads as a fact. The header labels the window's real basis, so a stale feed is visible as stale
  rather than as worthless.

## 3. Auto behaviour

Firms set `auto` (the default) and the portal picks a presentation from the client's **own**
numbers, so one layout serves a two-account individual and a three-hundred-account trust. An
explicit, contract-valid value pins the choice; junk (a stale value from an old build, a
hand-edited setting) degrades back through the auto path rather than to a blank block.

**Variants** (`variants.ts::resolveVariant`, thresholds in `VARIANT_THRESHOLDS`):

| Block             | Auto rule                                                                                                                                                                            |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `portfolioHeader` | `hero` on the page whose slug is `home`, `compact` anywhere else. `canvas` and `ledger` are **explicit choices only** — auto never picks a statement look for a firm that didn't ask |
| `accounts`        | `stack` up to 6 accounts, `cards` up to 24, `summary` above. `table` is explicit-only (a power-user look, never a default)                                                           |
| `holdings`        | `table` from 8 holdings, **or always for an organisation** (trusts read positions, not highlights); `top` otherwise                                                                  |
| `activity`        | `table` from 12 entries, `list` below                                                                                                                                                |
| `documents`       | `grouped` from 8 documents, `recent` below                                                                                                                                           |
| `allocation`      | `donut`; `bars` is explicit-only                                                                                                                                                     |

Counts size against the **book**, not the slice: a known `*_total` outranks the fetched page's
length, so 25 paginated rows of a 400-position book still resolve `table`.

**Looks** (`pkgs/ui/.../investment/looks.ts::resolveLook`): `auto` follows the portal `finish`
axis. Under the **card** finish (or none) every type keeps its carded design — except
`onboardingProgress`, whose looks list carries no `card` at all (the banner _is_ its carded
design). Under the **ledger** finish, `auto` resolves each type to its chrome-less signature:

| Type        | accounts | allocation | holdings | activity   | documents | adviser | message | actionItems | onboardingProgress | news      |
| ----------- | -------- | ---------- | -------- | ---------- | --------- | ------- | ------- | ----------- | ------------------ | --------- |
| ledger auto | `ink`    | `orbit`    | `sheet`  | `timeline` | `index`   | `strip` | `note`  | `card`¹     | `banner`           | `journal` |

¹ actionItems deliberately stays carded — its alert card re-skins through the finish tokens and a
task list needs its edge. An explicit look always beats the finish, in both directions: a firm can
put one ledger-style block on a carded portal, or one card on a register. Chrome-less looks render
with **no Card wrapper** — each owns its whole treatment on the page ground.

`onboardingProgress`'s `slim`/`milestones` are explicit-only, like the statement hero looks — `auto`
never picks either. `milestones` is the numbered-circle stepper across the whole journey (details →
verification → sign → setup → complete), sourced from `GET /applications/me`'s additive `stages`
field: the client's own steps roll up alongside the staff-only tiers they wait on (fact-find,
ID&V, AML), never naming the staff step itself — only which bucket it falls in and whether that
bucket has cleared, which keeps it clear of the AML tipping-off rule (`docs/aml.md`). `banner`/
`slim` fall back to the flat `done`/`total` count they always have; `milestones` degrades to the
same flat bar if `stages` is absent (an older cached response).

## 4. Theme axes

The **platform portal is not a second shopfront.** A `surface: 'fixed'` inject (`brandingHead` /
`brandingPreviewStyle`) emits only `--primary`, `--primary-foreground`, `--brand` and
`--brand-foreground` — buttons, the active nav mark, focus rings (`--ring` aliases `--brand`),
in-hero hairline.
Paper, cards, sidebar fill, muted wells, status hues, type, density, radius and glass stay the
product look. Extra keys stored from an older studio save are ignored at inject and dropped on the
next platform save (`staffPortalBranding`). Future customisations should be added as named,
intentional axes — not by mapping a seed colour onto every token.

**Colour is a basic palette of fourteen tokens** per scheme — `background`, `foreground`, `card`,
`muted`, `muted_foreground`, `border`, `primary`(+`_foreground`), `brand`(+`_foreground`),
`positive`, `negative`, `warning`, `info` (`COLOUR_VARS` in `app/branding.py` and
`pkgs/auth/src/branding.ts`, pinned at fourteen by tests on both sides). Every other colour the
components speak is an **alias** declared once in `theme.css`: `--popover` is `--card`, every
`*-foreground` on a surface is `--foreground`, `--secondary` is `--muted`, `--accent` and
`--skeleton` are lightness steps off `--muted`, `--input` is `--border`, `--ring` is `--brand`,
`--success`/`--destructive` are `--positive`/`--negative`, and a solid status fill's ink is picked
from its base's own lightness (white below L 0.6, a dark ink of the hue above it). The studio
generates the fourteen from two seeds and a style (`theme-generator.ts`). The neutrals are a
fixed cool paper: they used to take the primary's hue, and the page wash used to be a strong mix
of the brand colours, so the colour a firm had just set read as a similar colour already in the
background. A seed keeps its hue. Its lightness moves only when it would otherwise disappear into
the page ground or fail to carry readable ink, and the walk goes away from the ground. There is
no per-token override. The older per-token
keys (`sidebar_primary`, `popover`, the status foregrounds…) are still accepted on read and dropped
— pydantic ignores them and the inject only walks the fourteen — so a theme saved before the
collapse loads unchanged apart from those overrides.

**Card designs** (`card_designs`, client portal, per design set). Product type → how its account
card is painted, read by every surface that paints an account (the deck, the chip cluster, the
account cards) through a Svelte context the client layout provides (`provideCardDesigns`; the
studio preview provides the design being edited). A design is a **finish** — a recipe built off
the firm's tokens, never a stored colour (`categorical.ts::finish`):

| Finish  | Paint                                                       | Ink      |
| ------- | ----------------------------------------------------------- | -------- |
| `ink`   | the hero ink ramp                                           | white    |
| `brand` | the brand hue pinned to L 0.52→0.44 (`--finish-brand-*`)    | white    |
| `deep`  | the brand hue pinned darker and quieter (`--finish-deep-*`) | white    |
| `tint`  | a pale wash of the brand hue, hairline                      | page ink |
| `paper` | the card surface, hairline                                  | page ink |

The lightness pins are numbers in `theme.css` that flip under `.dark`. A pale finish re-points the
hero ink tokens on the card itself, so the deck's markup reads on any finish unchanged. A design
may also set `color` — a hex (any plain CSS colour the branding allowlist accepts) that **replaces**
the finish. The card is painted that colour, with a slight light-to-dark ramp so it keeps some
depth, and the ink flips with the colour's own lightness (page ink at L 0.62 and above, white
below), so a pale hex stays readable. Clearing it returns the card to its finish. Artwork still
covers a colour. Defaults
(`DEFAULT_CARD_DESIGNS`): ISA brand, SIPP ink, GIA tint, JISA paper; any other type wears ink.
The studio lists the product types the firm has **enabled** on its shelf (`GET /catalog/products`,
`cardProductTypes`), not a fixed set of wrappers — a firm with no Junior ISA has no JISA row, and
a third-party bond it does offer does. Or a design is **artwork** — an uploaded image (`kind=card-art`, same allowlist as the
logos; rejected, not clamped, because it lands in an inline `url()`) with `ink: light | dark`; the
finish stays underneath as the fill. The map clamps like the builder fields (junk finish ⇒ unset,
at most 24 types). This replaced a categorical hue scale that rotated the brand hue per product
type, which told the cards apart by leaving the firm's palette. Insight covers wear the finishes
too (`topicFinish`), chart series are a tonal ramp of the brand then the neutral (`series`), and
staff avatars are four lightness steps of the brand hue (`person-tone.ts`).

Four `Branding` axes differentiate the **client** portal beyond colours; they compose with the
per-block looks above. All are validated as closed token sets — the emitted CSS comes from
hardcoded declaration tables (`pkgs/auth/src/branding.ts`), never from the stored string, so none
of them is an injection surface.

- **`finish: card | ledger`** (client portal only). `card`/null is today's carded look and emits
  nothing. `ledger` is the private-bank register: one token set (~9 declarations) re-skins every
  widget coherently — cards become open, transparent sections with a bottom hairline rule and no
  shadow (`--surface-card*`), corners go square (**ledger owns `--radius`** — the firm's radius
  slider is not emitted with it), card titles become small-caps eyebrows (`--card-title-*`),
  numerals take the display face (`--money-display: var(--font-display)`; under card finish that
  token stays unset so figures inherit their context's face), and the frosted-glass material is
  suppressed entirely (a ledger has no glass — the firm's glass sliders are moot under it). Finish
  is **material**, so a fixed surface (the platform portal) never takes it.
- **`density: comfortable | standard | compact`** (client portal only). One root `font-size`
  declaration rescales the whole rem-based system — type, spacing, touch targets: `comfortable` =
  106.25% (17px root, the 65+ mode), `compact` = 93.75% (15px, the data-dense professional mode),
  `standard`/null emits nothing (16px). A `--density` scale factor (1.0625 | 1 | 0.9375) is
  exposed for finer control. Density is a shopfront axis: a fixed surface (the platform portal)
  keeps the product scale.
- **`font_display`**. Drives `--font-display` (large numerals: portfolio totals, statement
  figures — the private-bank lever). The editor offers a curated serif set (`Playfair Display`,
  `Cormorant Garamond`, `Fraunces`, `Libre Baskerville`); any plain family name passing the font
  regex validates. Unset ⇒ `--font-display` keeps its theme.css default, `var(--font-sans)`, so
  display surfaces simply follow the body face.
- **Fonts outside the lists.** All three font pickers (`font`, `font_mono`, `font_display`) end in
  **Other…**, which opens a text box for a Google Fonts family the curated list does not carry —
  typed as Google lists it (`Figtree`, `Space Grotesk`). `FontPicker` (`pkgs/ui/.../branding/`)
  validates against `isFontFamily` from `@graphene/auth` — the same `[A-Za-z0-9 -]{1,48}` rule the
  API and `brandingHead` apply — so a name the save would refuse is refused in the box with the
  reason, and a valid one is written to the draft as the firm types. The portal fetches whatever
  passes from Google Fonts (`googleFontHref`); a family Google does not serve is a dead `<link>`
  and the stack's fallback shows, so self-hosted faces are not supported by this path.
- **Hero colour and feel** (`hero_color`, `hero_feel`; client portal only, per design set). The
  band behind the portfolio total. Unset, `--hero-base` is a deep shade of `--primary` (lightness
  pinned at 0.26, so a light brand stays a dark band and a dark brand stays deeper than the
  buttons). `hero_color` replaces that. `hero_feel`: `gradient` (null — the ink ramp), `solid`
  (flat), `luminous` (the brand pulled through the band). The whole ramp (`--hero-from`/`via`/`to`,
  foreground, shadow) is re-declared with the base: those tokens are resolved on `:root` and
  inherited as colours, so overriding the base alone leaves the stock navy ramp in place.
- **Hero looks** (portfolioHeader variants, not a Branding key): `hero` is the dark ink band,
  `canvas` a brand-derived animated field, `ledger` the typographic register header, `compact` the
  slim off-home treatment. The statement looks (`canvas`, `ledger`) are explicit-only (§3). Colour
  and feel above paint every `hero-surface`; the looks choose the header's layout.

Composition in practice: the "engraved register" portal is `finish: ledger` + a display font +
auto looks (which the finish then resolves chrome-less), optionally `density: compact`. The page
presets (§7) compose the block half of this; the finish and fonts stay Design-view theme choices.

## 5. Structure rules

| Rule              | Value                                                                                                                                    |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| Pages             | ≤ **12** (`maxPages`)                                                                                                                    |
| Blocks per layout | ≤ **24** (`maxBlocksPerPage`, per audience list)                                                                                         |
| Blocks total      | ≤ **200** across every page and audience override (`maxTotalBlocks`; the budget runs front-to-back, so trailing pages lose blocks first) |
| Nav pages         | ≤ **6** shown (`maxNavPages`), and **≥ 1** — the portal always has somewhere to land                                                     |
| Slug              | `^[a-z0-9-]{1,32}$`                                                                                                                      |
| Label             | ≤ 24 chars, C0 control characters stripped                                                                                               |
| Block id          | `^[a-z0-9_-]{1,40}$`, unique per page (re-minted otherwise)                                                                              |
| Icons             | 20 curated lucide names; unknown clamps to `layout-grid`                                                                                 |

**Reserved vs shadowable slugs.** A page may never take a reserved slug — they are (or shadow)
real portal routes: `onboarding, requests, account, adviser, login, logout, mfa, set-password,
forgot-password, verify, share, search, transfer, files, api, p, i, look-through, terms,
messages, notifications, active-contact, page-view, rum, view-as`. A page carrying one is
**dropped on read**, never a 422. The six built-in slugs — `home, accounts, holdings, activity,
documents, insights` — are _shadowable_: a firm page carrying one replaces the built-in page
(`accounts` and `holdings` stay shadowable for the page sets saved while the default set carried
them). `/insights/{uid}` — the reader — is the built-in route under the Insights page, as
`/documents/{uid}` is under Docs. The client
app's `[slug=portalPage]` matcher 404s reserved slugs at the router, and
`route-consistency.test.ts` keeps the reserved list honest — **any new top-level client route must
be added to `reservedSlugs`** in the contract, or a firm page could hijack it.

**Spans and the masonry.** The grid is 12 columns; `w` clamps to 1–12 and `h` to 1–6. Rows are
**fluid masonry**: the grid runs on 8px micro-row tracks with `row-gap: 0` and sparse
`grid-auto-flow: row`, and each cell spans as many micro-rows as its own measured content needs
(`--rs`, set by `portal-page.svelte`'s `masonryCell`). A card takes its natural height without
client-specific content pulling later blocks backwards into different columns. `h` is a genuine
**floor** on that span — 6rem per unit, the same figure the
no-JS fallback uses as a `min-height` — never a fixed size: content taller than the floor wins.
The rendered widget stretches through that full authored floor, rather than leaving a naturally
short card at the top of space the grid has already reserved. Sparse lists remain top-aligned with
intentional breathing room; empty states centre within the available body. The measured inner box
owns this independent minimum — it never takes a percentage height from the grid cell — preserving
the feedback-loop protection described below. All of that arithmetic — measured span, authored
floor, the builder's scale — lives in one pure module, `investment/masonry.ts`, because two grids
computing it separately is exactly how the studio stopped being representative.

**The studio's builder is a scaled miniature, not a diagram.** The layout builder used to position
tiles by `h` over 4rem tracks while the portal positioned them by measured content over micro-rows;
with `dense` back-filling holes from client-specific content, a block landed on a different row
for different clients. The live grid now uses sparse flow: authored order and widths determine
columns while measured content still determines heights. `PortalPage` reports that grid (`onmeasure` →
`PortalGridMetrics`: per-block spans, grid width, row unit, gap, and whether the grid is in its
12-column mode), the studio holds the last **spanned** reading — a phone-width preview is ignored
rather than throwing the builder back to authored spans — and `layout-builder.svelte` draws its
tiles in the portal's own unit under the same sparse flow, scaled uniformly by its column's
share of the grid width. A block the preview has not reported (one just dragged in, or a hidden
block the portal never renders) falls back to the authored floor. Measurements are attributed by
`blockKeys` — the single identity function the renderer keys its cells by and the builder keys its
rows by; the renderer also stamps it on each cell as `data-block-id`.

Two rules in that path must not be softened. The measured box (`.client-cell-inner`) stays
`height: fit-content`, or a card with `h-full` resolves against the cell height we are about to
compute from it and grows a gap per frame (observed at 147,000px). And `--cw`/`--ch` are set with
`style:` directives, never a `style=""` string — the string form is rewritten wholesale on update
and silently wipes the `--rs` the measurement just set.

Every widget then fits its **span**, not the viewport: each grid cell is a CSS size container, and
the components adapt with container queries (DataTable columns collapse by priority via
`hideBelowWidth`). The per-type minimum width (§2) is where that adaptation stops being honest —
the builder refuses to author below it.

## 6. Data & truth rules

These are behavioural laws, each pinned by a test. Do not "fix" any of them for symmetry.

- **The leak rule** (`slices.ts::resolveSlices`, pinned per-slice by `slices.test.ts`): demo
  fixtures render **only** for `data === null` — the studio preview. A signed-in client's data
  object is always non-null (`page-data.ts` guarantees it even when every fetch failed), so a
  missing slice renders the block's **empty state** — never fixtures. Weakening this shows a real
  client fabricated holdings the moment the API hiccups.
- **News is opt-in.** Eligible firm-authored content comes from `GET /crm/content`. The default
  pages keep the block hidden; when a firm enables it, an empty eligible set renders the client
  “You’re up to date” state rather than removing the allocated widget.
- **Blank blocks claim no cell.** A `message` with no copy, an empty `actionItems` with
  `hide_when_empty`, and a finished `onboardingProgress` are dropped from the grid entirely
  (`portal-page.svelte::isBlank`) rather than rendering an empty card in a reserved span. This is
  what lets the default page set place a message slot the firm has not written yet.
- **Provenance stamps.** `/crm/home` and `/crm/context` carry `as_of`, per-wrapper `value_as_of`
  and `performance_period`; the widgets stamp "as at …" so a figure is never fresher than its
  source. They ride the payload spread — don't strip unknown keys in loaders.
- **Sort disables on partial slices.** Client-side table sort turns off when the slice is one page
  of a bigger book (`*_total` > rows) — sorting one page of 300 would lie about the extremes. The
  pager appears only when the server declared a total that outruns the slice
  (`slices.ts::tablePagination`); no total ⇒ the slice _is_ the data ⇒ no pager.
- **Summary honesty.** The accounts summary derives no book-wide figures from a partial slice: the
  awaiting-valuation caption is skipped (null) when the book was summarised or the total outruns
  the slice, rather than miscounted.
- **Per-source empty states.** Slice fetches are independently best-effort — one failing slice
  empties its own blocks, not its neighbours', and a failed dedicated listing fetch leaves the home
  payload's compact slice standing (a degraded page beats an empty one).

## 7. The studio

The branding editor (`pkgs/ui` `BrandingEditor`, mounted at admin `/tenants/{id}/branding` and the
platform portal's `/branding`) has a Design view (theme) and a **Pages view** (composition). The
Pages view is client-only. On the adviser portal, Design is **identity + two accents** (and the
default colour scheme) — not a generated palette, not Material, not card designs. On the client
portal colour is the seeds, the style and the presets, plus a **Card designs** panel (one finish picker per product the firm has enabled, with artwork
upload); there is no per-token colour drawer.

- **Design sets** — the client portal may carry up to 12 named templates. The selector changes the
  active Design and Pages workspace together; creating the first design copies the current portal,
  and Duplicate deep-copies pages/settings/colours without copying book or role assignments. The
  **Client books** card owns assignment: each book's Portal design picker points at one design set
  and moves its slug away from the old design when changed. There is deliberately no client-role
  dropdown on a design set — auth-profile roles are not book segmentation. One design is the
  default for unmatched clients. Firm identity remains shared. The Design preview renders the
  selected template's real page set rather than the legacy flat `layout` mirror.
- **Pages rail** — add/rename/slug/icon/nav/reorder/delete, every mutation through the pure
  `page-ops.ts`, which enforces the same caps and refusals the API re-enforces on save, so the
  builder never produces a payload the API would silently mangle; refusals surface as inline copy.
- **Presets** — the rail header's "Start from a preset…" picker offers four curated whole-portal
  page sets (`page-presets.ts`: Classic — today's portal; Private bank — the engraved register;
  Family office — owner-grouped, household-first; Trust & company — data-forward tables). Presets
  are built programmatically from `defaultPages()` transforms so they cannot drift from the base,
  and applying one replaces the working page set behind a ConfirmDialog. A preset's tweaks reach
  **every** audience layout the base set carries, not just the default — otherwise a firm's trusts
  would stay on the platform composition while its individuals moved.
- **Audience tabs** — Default | Individuals | Trusts & companies, copy-on-write (§1) with a
  Customised badge and revert.
- **Block inspector** — rendered declaratively from the registry's `SettingDescriptor[]`; the
  variant control captions its live auto resolution for the previewed persona.
- **Personas** — four deterministic preview clients (`fixtures/personas.ts`), chosen to sit either
  side of every variant threshold: **Individual · 2** (under them all), **Family · 6** (crosses
  holdings/activity/documents), **Individual · 12** (the accounts `cards` band), **Trust · 300**
  (crosses everything → summary). No `Math.random`/`Date.now` — the preview renders identically on
  every load.
- **Device preview** — the live frame is the real portal shell in a simulated viewport (Desktop
  1920, iPad 834, iPhone 390 — chosen around the 768px chrome-swap breakpoint), scaled to fit,
  never upscaled.
- **Save semantics** — the working page set materialises once, into local state, when the Pages
  view opens; `Branding.pages` is written only by an actual edit. **Saving an untouched studio
  keeps `pages` null**, so a tenant that never customised keeps tracking the platform defaults
  instead of having today's shape frozen into its record. Save then stamps that dirty working
  set onto the PUT payload (`withWorkingPages`) rather than trusting `$state.snapshot(drafts)`
  alone — snapshotting the draft used to toast Saved while the body still carried the previous
  composition, so a refresh restored the old pages and the client portal never moved.

Gotcha: the pure page helpers deep-copy with `structuredClone`, which **throws** on Svelte
`$state` proxies — pass plain data (`$state.snapshot` at the component boundary), as
branding-editor does. The save path JSON-roundtrips the snapshot so the POST is the wire
shape, not a proxy.

A second gotcha sits on the **read** path: `resolveEnvironment` caches `/internal/env-config`
for 60s (WorkOS secrets on static-asset requests). HTML and SvelteKit data loads **bypass**
that cache, otherwise a saved studio is invisible on the client portal until the TTL expires.

## 8. The wire

Two validation regimes coexist deliberately in `app/branding.py`:

- **Strict allowlists** for anything injection-shaped (colours, fonts, URLs, radius) — these end
  up inside SSR `<style>`/`<head>` markup, so a bad value is an attack and is rejected.
- **Clamp/degrade, never reject** for everything the no-code builder produces: spans clamp,
  unknown variants/looks fall to `auto`, junk limits fall to unset, labels truncate, unknown block
  types and reserved/invalid-slug pages are **dropped on read**, garbage `pages` entries are
  skipped. A stale value from an old build must never 422 a save and strand the firm's edits.
  Unknown _settings keys_ survive round-trips: the canonical settings dump is merged **over** the
  raw dict, so a newer portal build's keys pass through an older API intact.

Serving: `environments.env_config_by_host` returns the matched portal's branding (+ `app_name`) on
the hot path; each portal's `hooks.server.ts` injects `%tenant.head%` before `%sveltekit.head%`
via `@graphene/auth` `brandingHead` (SSR, no flash), which re-validates and escapes every value
again (defence in depth). Editing endpoints: Graphene admin `GET/PUT
/admin/tenants/{id}/branding?portal=…` (`tenants:read`/`tenants:write`), firm self-service
`GET/PUT /firm/branding?portal=…` (`branding:read`/`branding:write`). **Portal composition is
gated separately**: on both PUTs a change to `Branding.pages` additionally requires
`pages:write` (split out of `branding:write` in migration `0042` — colours/logos are cosmetic,
but the page set decides which surfaces a client sees). The check is a canonical diff
(`app/branding.py::pages_changed`), so a cosmetic save that echoes the stored pages untouched —
the studio always submits the whole branding object — never demands it. The studio mirrors the
gate: both host loaders (platform `/branding`, admin `/tenants/{id}/branding`) pass
`canEditPages` (`pages:write`) into `BrandingEditor`, and without it the Pages view renders
read-only (rail/builder/inspector disabled, presets/customise/revert hidden, a quiet notice) —
the tab stays visible because the preview is still useful, and a read-only view can commit no
pages change for the echo rule to trip on. The Design tabs stay on `canManage` alone.

The legacy **`layout` mirror**: the flat pre-pages home layout migrates into `pages` on read, and
the home page's default layout is mirrored back into `layout` on every parse — a one-release
deploy-skew bridge for portal builds that read only `layout`. New consumers read `pages`; the
mirror (and `layout` itself) goes one release after every portal does.
