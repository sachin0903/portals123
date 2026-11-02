# Client portal content

Firm-published news, articles and PDF guides for the client portal. Placement (which page a widget sits on) stays in branding; **bodies live here**, not in `auth.tenants.theme`.

## Model

Schema `content` (`0125_content`):

- **`content.categories`** — firm taxonomy (empty until staff create rows). Distinct from `documents.document_types`.
- **`content.items`** — one row per piece. `kind` is `news` | `article` | `guide`. Status matches forms/templates: `draft` | `published` | `archived`. Public id is `uid`.
- **`content.item_versions`** — immutable snapshots written on publish (GrapesJS `schema` plus sanitised `body_html`). The live draft stays on `items.schema`.

`eligibility` jsonb is reserved for a later ConditionBuilder subset of `contact.*` / `account.*` / `contact.tags`. A CHECK forces it **NULL** in v1.

## Targeting (v1)

Evaluated in the API (`app/content.py::eligible`) against the signed-in `GET /crm/context` contact. Unpublished, expired, future-dated, and mismatched rows never leave the server.

| Axis              | Client signal                                                                | Empty / `all`              |
| ----------------- | ---------------------------------------------------------------------------- | -------------------------- |
| `audience`        | `contacts.type` (`individual` / `organisation`; unknown reads as individual) | `all`                      |
| `service_types[]` | `service_type`                                                               | empty = every service type |
| `sub_types[]`     | `sub_type` (organisations only; individuals ignore the axis)                 | empty = every org sub-type |
| `category`        | topic pill on the Insights page's library (shown once it passes eight items) | not a hide rule            |

## Who can see the tables

Staff: `tenant_lineage()` plus `content:read` / `content:write`. **No `contact_scope()`** on the body tables — broadcast HTML must not leak via “my book” (same posture as `comms.messages`).

Clients never use the staff routes. They hit `GET /crm/content` on `book_session`. RLS still has to let `authenticated` SELECT published rows for `auth.tenant_id()`, because a client principal has an empty lineage; writes stay lineage-only.

## Authoring (platform)

`/content` listing and `/content/{uid}/edit`, gated on `content:read` / `content:write`.

- **News and articles** — a labelled, responsive GrapesJS web canvas (`@graphene/ui/content-editor`), not the raw GrapesJS panel set or the 600px newsletter preset. Its small block palette covers headings, text, lists, quotes, callouts, dividers and tenant-hosted images.
- **Smart Word import** — `.docx` is converted in the browser with Mammoth. It preserves semantic structure (headings, lists, links, tables, quotes and images), discards Word's fonts/margins/positioning, uploads embedded images through the item's tenant-scoped image endpoint, and applies the standard portal editorial stylesheet. Import replaces the unsaved canvas only; staff review and explicitly save or publish it.
- **Guides** — PDF in the tenant bucket under `content/{uid}/guide.pdf` (private prefix). The client page is the product: summary + inline `DocumentViewer`, with download allowed.
- **Images** — heroes and article-body images live in the tenant bucket but are never exposed as
  object-store URLs. Uploads return an opaque same-origin `/_assets/…` source; each portal relays
  that source to the API over the internal network. Legacy S3 URLs in saved schemas and published
  HTML are translated on read.
- **Library folders** — the platform content library uses categories as folders in a familiar file-tree view; uncategorised items have their own folder, and articles, news and PDF guides retain distinct file icons. “PDF guide” is an explicit creation type: create it, then upload the finished PDF on its editor screen. Current and archived libraries remain separate tabs.

Graphene Operations uses the same library/editor at
`/tenants/{uid}/operate/content`. Loads, image/hero/guide uploads and mutations all carry the route
tenant through the target helper; uploads are same-origin proxies and never expose object-store
coordinates. `tenant_portal:read` renders the published/draft state without creation, save,
publish, archive or upload controls. Those actions require `tenant_portal:write` before their
request body is forwarded, then run through the unchanged content permission and tenant/RLS rules.

**The canvas needs GrapesJS's own stylesheet, and it is a `<link>` to a static asset** (`apps/{platform,admin}/static/grapesjs.css`, injected as `#gjs-css` by `create.ts::ensureArticleEditorStyles` — the same element id the email editor uses, so two editors in one session share one link). It cannot be imported: LightningCSS rejects GrapesJS's IE `filter` syntax. Missing, it does not degrade to plain chrome — the component toolbar's SVGs render at their intrinsic size as two huge black arrows over the page and the canvas, absolutely positioned and sized off `--gjs-*-width`, collapses into a narrow clipped column, so the whole editor reads as a broken product rather than as a 404. The rest of the chrome is scoped to `.article-gjs` (so it cannot reach the email canvas) and written in brand tokens, GrapesJS's default blue being otherwise the one colour in the product no firm's branding can move. **Anything the canvas alone needs goes in `canvasCss`, never the article stylesheet** — publish inlines the stylesheet onto the article, so a canvas drop-shadow would follow it into every client portal. Article images are `width: auto`, not `100%`: a Word document's images are mostly small graphics, and blowing each one up to the full column is much of what made an imported document look wrong.

**Two GrapesJS traps cost a shipped-blank editor between them; both were measured in Chromium, not deduced, and both are pinned by `chrome.test.ts`.** First, **the canvas height must be an absolute length** (`create.ts::CANVAS_HEIGHT`, and the host takes its height from the same constant): GrapesJS writes `config.height` as an INLINE style on the host it is given, so `'100%'` resolves against whatever the app wrapped it in — a padded `min-height` box, i.e. nothing definite — which resolves to `auto`, and the only thing inside is an absolutely-positioned canvas that contributes no height, so **every box in the editor came out zero tall** with the article correctly laid out at 760×720 inside an iframe nobody could see. Loading the core stylesheet is what turned that from "wrong but visible" into "blank", because without it the canvas is statically positioned and stumbles into a height; the two bugs therefore presented as one regression. Second, **the article stylesheet goes into `canvasCss` verbatim and never through `editor.setStyle`**: GrapesJS re-serialises a parsed rule through the CSSOM, which expands shorthands into longhands, and a shorthand carrying `var()` cannot be expanded — so it is dropped silently. `background: var(--background)` computed to transparent and `border-left: 3px solid var(--primary)` to `0px`, i.e. no sheet background, no pull-quote rule and no callout border, in the editor **and in the published article**, since publish inlines the same mangled CSS. That is the right home for it anyway: the stylesheet is the design system's, not the item's — nothing in this editor can author CSS (`styleManager.sectors` is empty) — so `getCss()` reports the constant, which is also what corrects an item saved before the fix on its next save. A rejected init is now surfaced in the panel rather than left as a skeleton for ever.

Publish inlines the article stylesheet then sanitises with `nh3` (including semantic table markup). Clients receive only `body_html`, never the GrapesJS schema or Word source.

## Client consumption

- The `news` block (DataSource `content`) has two shapes. The home card (`auto`/`feed`) reads `GET /crm/content?kind=news&limit=12` — the dozen newest news items — and its "View all" is computed from the firm's page set (`listing-links.ts`): it points at whichever page hosts a `library` block, or nowhere. The `library` variant is the whole published library as a page — `GET /crm/content?limit=50`, every kind — filtered client-side in the portal's one browse bar (`browse-bar.svelte`, shared with the Docs page — `docs/portal-customisation.md`; `news-library.svelte` drives it): once the library passes eight items its **topics are the tabs** (text tabs, a brand indicator under the current one, scrolling sideways on a phone), the **kind** is a pull-down beside them and **search** is a toolbar button that opens the portal's one search palette already scoped to insights (`docs/search.md`) — Material's primary tabs, not pills over a floating box, and no second search box on the page; a smaller library tabs by kind instead. Both shapes take a **look** — `card` (the reading list / headline list), `magazine` (cover story + cover cards; a piece with no hero gets a cover in the firm's palette, `insight-cover.svelte`) or `journal` (type-led, chrome-less; the ledger finish's auto) — see `docs/portal-customisation.md`. The default Insights page pins `magazine`. Signed-in `data !== null` never falls back to demo fixtures (`resolveSlices`). Default home ships the card **hidden**; the firm opts in from the studio.
- The reader (`/insights/{uid}`) also reads `GET /crm/content?limit=12` for its **Keep reading** rail (same topic first — `relatedInsights`), shows a reading time computed from `body_html` (`readingTime`), and rules pull quotes in `--brand`.
- Insights is a **page in the default set** (slug `insights`, label Insights, icon `newspaper`, one `library` news block) — the studio places, renames or removes it like any tab; it used to be a built-in route the client app appended to the bar in code. `/insights` shadows the composed page exactly as `/documents` does (`loadPortalPage('insights', …)`, 404 when a custom set omits the slug); `/insights/{uid}` and `/insights/{uid}/file` — the reader — stay built-in routes. `insights` is a _shadowable_ slug, no longer reserved. Ineligible or unpublished uids 404.

## Not this

Guides are not `documents.documents` (that requires `document_contacts` and is personal delivery). A guide file can live in tenant object storage without becoming a client document.
