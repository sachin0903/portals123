# Shared packages (`pkgs/*`)

Five TypeScript workspace packages, consumed by the apps via `workspace:*` deps and all
`private`, plus two Python ones resolved through the uv workspace: `pkgs/morgan-lloyd`
(the Dynamics CRM client — see [`morgan-lloyd/README.md`](morgan-lloyd/README.md)) and
`pkgs/seccl` (generated Seccl payload models, below).

## `@graphene/api-client` — generated TS API client

The typed client the SvelteKit apps use to call the API. **Generated, never hand-edited** — Python/Pydantic is the single source of truth (see [`stack-and-workflows.md`](stack-and-workflows.md) §11).

- Tooling: [Hey API](https://heyapi.dev) (`@hey-api/openapi-ts`) + the `@hey-api/client-fetch` plugin. Config in `openapi-ts.config.ts`.
- Input: `process.env.OPENAPI_URL` if set, else `http://localhost:8000/openapi.json`. A relative `OPENAPI_URL` is resolved against the repo root (openapi-ts runs with its cwd inside this package), so the dumped-schema command below works from anywhere. Output: `src/` (prettier-formatted).
- Regenerate:

  ```
  moon run api:dev                 # in one terminal — serves /openapi.json
  moon run api-client:generate     # in another — reads it, rewrites src/
  ```

  Or against a dumped schema without a running server:

  ```
  OPENAPI_URL=apps/api/openapi.json moon run api-client:generate
  ```

- The generated `src/` is committed — a barrel `index.ts` plus `types.gen.ts` / `sdk.gen.ts` / `client.gen.ts` — and currently covers the `/health` and `/me` endpoints. Never hand-edit it; rerun the generator instead.
- **CI guards drift**: the `client-drift` job (`.github/workflows/pr.yml`) regenerates the client and fails the PR if the committed `src/` differs from what the current schema produces. So regenerate and commit whenever you change an API route/model.

## `@graphene/auth` — framework-agnostic WorkOS auth helpers

The shared auth logic all three portals (`apps/client`, `apps/platform`, `apps/admin`) build on. **Deliberately framework-agnostic** — no SvelteKit `$env`/`$app` imports — so the logic is identical across portals, unit-testable with plain vitest, and reusable if another consumer appears. Anything environment-specific (the WorkOS client, the `dev`/`secure` flag, the client id and cookie password) is **injected** by the caller rather than read from the framework.

- `src/errors.ts` — WorkOS error classifiers (`emailVerificationRequired`, `mfaChallengeRequired`, `mfaEnrollmentRequired`, plus `signupErrorMessage`) + the `PendingVerification` / `MfaChallenge` / `MfaEnrollment` types.
- `src/cookies.ts` — cookie name constants (`SESSION_COOKIE`, `PENDING_COOKIE`, `MFA_COOKIE`, `ENROLL_COOKIE`) + `cookieOptions(dev)` / `pendingCookieOptions(dev)` factories (the `secure` flag is passed in, not read from `$app`).
- `src/auth.ts` — `passwordAuth(workos, { clientId, cookiePassword }, email, password)` (deps injected) + the `PasswordAuthResult` type.
- `src/validation.ts` — `validateEmail` / `validateNewPassword` / `validateSignup`.
- `src/environment.ts` — the portal→API service calls: `consumeRateLimit` (surfaces `login`,
  `forgot-password`, `mfa`), `requestPasswordReset` (forwards the client IP so the API's per-IP
  key is real), `safeClientAddress` (SvelteKit's `getClientAddress()` throws on some adapters;
  this returns `null` instead), `mfaChallengeHandle` (SHA-256 of the pending-auth token — the
  limiter's key for a challenge, so the token itself never reaches a log or a store).
- `src/permissions.ts` — `fetchMe` with a per-token cache (`PERMISSIONS_TTL_MS`, 20 seconds; a
  token is minted per refresh, so entries are keyed on it and expired entries are swept on every
  write). The hook awaits this before any HTML can go out, so on a warm session it sits on the
  critical path of every page; the window is sized to cover a run of navigations a few seconds
  apart while still bounding a revoke. It gates navigation only — every API call re-reads the
  grants from the DB. A failure is never cached.
- `src/dev-bypass.ts` — `devPrincipal` / `devBypassAllowed`: the local `dev-auth` cookie, allowed
  only under the API's own four guards (`DEMO_AUTH_BYPASS=1`, `APP_STAGE` in `dev|local|test`, a
  loopback peer, and no proxy headers or non-loopback Host). Shared so the portals cannot drift
  looser than the API they are bypassing into.
- `src/gates.ts` — the post-session gate the client hook applies to every request: `decideGate`
  (`allow | wrong_portal`). A session confirmed for another portal is refused; terms are not
  part of the decision — the login terms gate was removed on 13 Oct 2026, and terms are collected
  inside a journey. `isNavigationRequest` (`sec-fetch-dest` / `accept` / `isDataRequest` — a page
  is redirected, an endpoint gets `gateRefusal`'s 403 JSON), `refusePortal`.
- `src/csrf.ts` — `isCrossSiteMutation` / `crossSiteRefusal`: `Sec-Fetch-Site` is authoritative,
  `Origin` the fallback, neither present → not judged. Defence in depth behind `SameSite=Lax`
  cookies for the portals' `+server.ts` mutations.
- `src/relay.ts` — `relayBuffered` / `readBounded`: a proxied download is read chunk-wise and
  refused past `MAX_RELAY_BYTES` (26 MiB, one over the API's 25 MiB document ceiling) rather than
  buffered whole on trust; the response gets an explicit `content-length`, the upstream
  `content-type`/`content-disposition`, and `cache-control: private, no-store`.
- `src/index.ts` — barrel re-exporting all of the above.

> No portal has a self-service signup route — accounts are provisioned in WorkOS. The `signupErrorMessage`, `validateSignup` and `validateNewPassword` helpers are kept for a future invite / admin-create-user flow.

- Tests co-located, one per module (`errors.test.ts`, `auth.test.ts`, `validation.test.ts`, `gates.test.ts`, `csrf.test.ts`, `relay.test.ts`, `dev-bypass.test.ts`, …), plain vitest, no SvelteKit harness.

**Per-app glue pattern.** Each portal (client, platform, admin) keeps a thin `src/lib/server/` layer that adapts this package to SvelteKit: `workos.ts` imports `$app/environment` + `$env/dynamic/private`, constructs the WorkOS client lazily via `getWorkOS()`, defines the app's `CLIENT_ID` / `COOKIE_PASSWORD` / `cookieOptions` / `pendingCookieOptions`, and re-exports the shared names/classifiers; `auth.ts` wraps `passwordAuth` injecting the app's client + config; `validation.ts` re-exports the validators. The route files import only from `$lib/server/...`. See [`auth.md`](auth.md) for the full flow.

## `@graphene/rum` — browser page-view beacon

One trace per page view. `RumBeacon` (mounted once from each portal's root `+layout.svelte`) records the SvelteKit route id, how long that view's loads took to settle, and web vitals (LCP, INP, CLS, TTFB), then `navigator.sendBeacon`s them to the portal's own `/rum`. The navigation hooks live in that component: SvelteKit implements them with `onMount`, which throws outside a component and takes the hydrated page with it. The route id is the template, so an email or an account id in the URL never leaves the browser. `src/forward.ts` is what `apps/*/src/routes/rum/+server.ts` uses to hand the body to `POST /me/rum` with the session bearer. The browser does not call the API, and it does not call a third party. The page-view beacon is a separate record and stays.

LCP, INP and CLS are registered with web-vitals' `reportAllChanges`, so each candidate is stored as it happens rather than only when the metric is finalised. web-vitals 5 finalises those three from a capturing `visibilitychange` listener on `window` and does not listen for `pagehide`; `pagehide` can fire while the document is still visible, so the beacon waits until the document is hidden and that listener has run. `POST /me/rum` ends the `browser.view` span immediately, and an ended span cannot gain attributes, so every value has to be on that one beacon. LCP is once per hard navigation: the enter view carries `lcp_ms`. A soft navigation does not. If the enter view is left before any LCP candidate, its beacon waits until the page is hidden (the candidate still belongs to it) instead of being sent without one. `settle_ms` is unchanged: `performance.now()` at `afterNavigate` for the enter view, and the navigation's own duration after that.

## `@graphene/config` — shared lint/format/TS presets

One source for the toolchain configs, exported via the `package.json` `exports` map:

| Import                                | What                                                                                               |
| ------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `@graphene/config/eslint`             | flat ESLint config (JS + TS + Svelte, prettier-compatible)                                         |
| `@graphene/config/prettier`           | Prettier config — **tabs, single quotes, no trailing commas, width 100**, `prettier-plugin-svelte` |
| `@graphene/config/tsconfig.base.json` | base `tsconfig` the apps extend                                                                    |

Apps consume these in their own `eslint.config.js` / `.prettierrc.js` / `tsconfig.json`. Match this style in new code — the format gate (`moon run :format`) enforces it; generated `.svelte-kit/` and vite temp files are excluded via each app's `.prettierignore`.

## `@graphene/email` — **deleted (Aug 2026)**

There was a Svelte email package here: `svelte-email-tailwind` primitives, a `<Brand>`/`<Shell>` pair
and Svelte mirrors of three codes (`Invite`, `PasswordReset`, `RequestRaised`), rendered by
`renderEmail(Component, props) → { html, text }`. It was **never on the send path** — the shipped
defaults are composed in Python (`apps/api/app/email/transport.py`), a firm's override renders from the DB
(`app/email/render.py::render_or_default`), and authoring is the GrapesJS editor — and its only
consumer was a dev-only preview route at `apps/platform/src/routes/dev/emails`.

It is recorded here because the reason it went is a rule worth keeping: nothing pinned it against
either Python authoring, it covered three of the six codes, and a preview of templates that no longer
send is **worse than no preview** — a developer can open it and reasonably conclude that is what
clients receive. The replacement is stronger on its own terms: the editor's Preview dialog renders
through the API's `render_preview` (the same merge the override branch uses), and Send test
(`POST /email-templates/{uid}/send-test`) puts the live canvas in a real inbox. If email rendering
ever moves into Node, start from `app/email/transport.py` — which is the copy that actually sends — not from
git history here.

## `@graphene/ui` — shared components + design theme

The one component layer all three portals build on (shadcn-svelte style, Tailwind 4). Consumed as
**source** (like the other packages): `exports['.']` → `src/index.ts` barrel, plus `./theme.css`,
plus a set of **subpath** exports (`./book/*`, `./nav`, `./automations`, `./email`, `./email-editor`,
`./fixtures`, `./portal-slugs`). A subpath is not a tidiness choice: it keeps a heavy or
collision-prone surface off the root barrel, so an app that imports `@graphene/ui` never pays for a
dependency it doesn't mount, and names too generic to live in one flat namespace (`TaskRow`,
`ActivityRow`, `toRow`) stay behind their own door.

- **Theme — the single re-theme point.** `src/styles/theme.css` holds the design tokens (CSS variables
  for `:root`/`.dark`, the `@theme inline` mapping, base layer). "Private-wealth" identity: warm paper
  surfaces, ink-navy primary, gold + emerald accents, semantic `--positive`/`--negative`, deep-navy
  sidebar. Self-hosted fonts via Fontsource (`@fontsource-variable/fraunces` display serif +
  `@fontsource-variable/inter` body) `@import`ed at the top and mapped to `--font-serif`/`--font-sans`
  (use `font-serif` for display headings + hero figures). Change a token here and every portal updates.
- **Components** (`src/lib/components/ui/`, shadcn-svelte on `bits-ui`): primitives (button, input,
  label, card, alert, badge, avatar, separator, skeleton, switch, checkbox, textarea); overlays
  (dialog, sheet, dropdown-menu, popover, tooltip, command); nav/feedback (tabs, breadcrumb, sonner);
  and `table` primitives. The barrel exports the auth-page primitives **flat** (`Button`, `Card`,
  `CardHeader`, …) and compound components **namespaced** (`import { Dialog } from '@graphene/ui'` →
  `<Dialog.Root>`). `src/lib/utils.ts` — `cn()` + shadcn prop helpers.
- **Listings** — `DataTable` is the shared table for every book-sized listing (clients, accounts,
  transactions, transfers): column definitions in, sorted rows out, cells rendered by the caller's
  `cell` snippet. Sorting is client-side and lives in `data-table/sort.ts` (not the component) so it
  is unit-tested: blanks sink to the bottom in **both** directions (mirroring the queries' `NULLS
LAST`), collation is `numeric` so "Account 2" precedes "Account 10", and a stale sort key returns
  the rows untouched rather than blanking the table. Columns declare `sortable`, `align`,
  `defaultDir`, `hideBelow` and — whenever the cell renders something other than the raw field — an
  explicit `sortValue`, without which the visible order won't match the column. `Money` renders GBP
  with the pence smaller and muted (`splitGBP` in `src/lib/money.ts`, built on `formatToParts` so
  separators stay locale-correct); it sizes the pence at `0.85em` so the same component works in a
  dense row and a page-header stat. `CurrencyBadge` is the ISO-code pill beside it: the outline
  badge with the country's flag as a disc on the left. The flags are `circle-flags` SVGs, globbed
  as `?no-inline` asset URLs in `src/lib/currency-flags.ts` (ISO 4217's first two letters are the
  ISO 3166 country; `X` codes get no flag) — `no-inline` because at ~1 KB each every one is under
  Vite's inline threshold, and 265 data URIs in one chunk is not what a pill should cost.
- **Shells** (`src/lib/components/shell/`), all on one design language (liquid-glass surfaces, navy/teal,
  Inter, system-theme-following): `AuthShell` (split brand panel + glass form, pre-auth — **all** portals,
  with the shared `LoginForm`); `ClientShell` (top bar + nav + avatar user-menu + `search`/`actions` slots
  - mobile glass bottom-tab — the **client** consumer portal); `SaaSShell` (collapsible sidebar + glass
    topbar + `search` slot + `TenantSwitcher` + sidebar user-menu — the **platform/admin** workspaces, which
    need room for many menus). All authed shells share the avatar user-menu → `/account` settings area
    (Profile + Security) and the glass card/`glass-canvas` aesthetic. Prop-driven (nav config, `user`,
    `pathname` — framework-agnostic, so apps pass `$app/state` rather than the package importing SvelteKit
    virtuals). The old plain-centred `AppShell` is retained but unused.
- **Portal IA** (`./nav`, `src/lib/nav/`): the sidebar structure both staff portals project
  (`PLATFORM_NAV_STRUCTURE`, the capability inventory), the focused-record sidebar, and the client
  and account section builders. Platform and admin import `@graphene/ui/nav`. Href and permission
  gates stay in each app's `nav.ts`. The firm-book search catalogue (`BOOK_SEARCH_SCOPES`,
  `BOOK_ROUTE_KEYS`) lives on the root barrel next to the other search mechanics; each app still
  owns the href a hit opens.
- **Charts** (`src/lib/components/charts/`, themed SVG via `d3-shape`/`d3-scale`): `PerformanceChart`
  (area+line), `AllocationDonut`, `Sparkline`.
- **The overflow audit** (`src/lib/overflow-audit.ts` — `auditOverflow`, `markOverflow`,
  `reportOverflow`, `watchOverflow`): the development-time tripwire for content spilling out of
  its box or clipped behind an `overflow-hidden` one. Geometry-based (it asks the browser where the
  boxes landed, not what the classes say), framework-agnostic, and tested in jsdom with declared
  rects (`overflow-audit.dom.test.ts`). The rules it applies, and the waiver attribute
  `data-overflow-ok`, are in [`design-language.md`](design-language.md) → "Nothing spills, nothing
  is clipped".
- **The touch-target audit** (`src/lib/touch-audit.ts` — `auditTouchTargets`, `markTouchTargets`,
  `reportTouchTargets`, `hitBox`): the same kind of tripwire for a control a thumb cannot rely on —
  a hit box (border box plus any positioned `::before`/`::after` extension) under 44×44, or an
  extension reaching into a neighbour. Tested in jsdom with declared rects and declared pseudo
  styles (`touch-audit.dom.test.ts`); waiver `data-tap-ok`. The mechanisms it polices — the
  `tap-target` utility, the client's `[data-slot='button']` extension and field `min-height` —
  are in [`design-language.md`](design-language.md) → "Every control is a thumb's width".
- **`watchLayout`** (`src/lib/layout-watch.ts`): the shared scheduler — MutationObserver on the
  body and the `<head>` (so a CSS hot-reload re-runs), ResizeObserver, window resize, one
  debounced frame. The client portal's `+layout.svelte` runs both audits through it under `dev`;
  `watchOverflow` is the single-audit convenience on top of it.
- **Investment** (`src/lib/components/investment/` + `src/lib/money.ts`): the advised-investment client
  components — `PortfolioHeader` (hero + perf chart + deposit/withdraw dialogs), `WrapperCard`
  (GIA/ISA/SIPP/JISA), `HoldingsTable`, `ActivityList`, `AdviserCard`; money helpers `formatGBP` /
  `formatSignedGBP` / `formatMoney` / `formatSignedMoney` / `formatPct` + domain types (`Wrapper`, `Holding`, `Activity`, `Adviser`).
- **The email-template editor** (`./email-editor` — `EmailEditor`, `EmailTemplateEditorPage`,
  `PreviewDialog`, `VersionHistoryDialog`, `TemplateSettingsDialog`, `createPreviewFetcher`,
  `mergeFieldsFor`, and `createTestSender` + `testSendFeedback` — the inbox test and the copy for its
  three outcomes). The
  GrapesJS `grapesjs-preset-newsletter` canvas a firm composes its transactional emails in, mounted by
  **both** the platform firm surface (`/email-templates/[id]/edit`) and the admin per-tenant surface
  (`/tenants/{uid}/email-templates`, where Graphene curates the model preset or acts on a firm's
  behalf) — so there is one editor, not two that drift. On its own subpath because grapesjs is heavy
  and `apps/client` must never pull it. **App-agnostic by construction**, which is the whole
  constraint: the canvas emits content through `onChange` and the app owns save/publish/navigation;
  each dialog takes its endpoint as a prop (`previewUrl`, `versionsUrl`) rather than building one from
  an id; and anything needing SvelteKit's `$app/*` virtuals — which only exist inside an app — arrives
  injected, as the app's own `enhance` plus `onRestored`/`onSaved` callbacks. Same pattern as the book
  surfaces' injected `links`/`actions`. The API contract, the two stored style shapes and the
  load-order gotcha are in [`api.md`](api.md) → "Email".
  - **The LISTING that fronts it** (`./email-editor` → `EmailTemplatesListing`, plus the two pure rule
    sets it is built on — `emailTemplateState` and `codeState`/`takenNote`/`createBlocker`/`categoryFor`)
    was extracted the same way, later: the editor had been shared while the list stayed duplicated at
    528 lines in the platform route and 455 in the admin one, and the copies had drifted so that only
    the firm's had the honest status vocabulary (the admin's read the HEAD version's status, so a
    template published for months with one draft on top said "Draft"), the pre-submit duplicate-code
    check, a retryable `LoadErrorBanner` and a real `Segmented` toggle rather than a hand-rolled
    `bg-muted` pill group. One component now, with the two apps as thin mounts (57 + 116 lines).
    **Presence of `actions` IS write mode** — the component never learns a permission slug, so the
    platform's `email_templates:write` and the admin's `model:write`/`tenants:write` split (via
    `$lib/email-templates/scope`) both stay app-side — and `actions.loadDefaults` is the one real
    capability asymmetry: absent on the firm's own surface, so no button renders there. What genuinely
    differs otherwise arrives as props: `archiveConsequence` (a firm's archived template falls back to
    Graphene's default; the MODEL preset's means a new firm clones the preset without that email —
    collapsing the two would make one a lie), a `notices` snippet for the admin's acting-on-behalf
    banner and read-only notice, and `showCodes` for the operator who curates codes rather than uses
    them.
- **How apps consume it.** Each app's `src/app.css`:

  ```css
  @import "tailwindcss";
  @import "@graphene/ui/theme.css";
  @source '../../../pkgs/ui/src/**/*.{svelte,ts}'; /* Tailwind 4 ignores node_modules — required */
  ```

  and imports components: `import { Button, Card, ClientShell } from '@graphene/ui'`. Apps also depend on
  `@lucide/svelte` directly (nav icons are passed into the shells from app code).

- **Adding components.** Run the shadcn-svelte CLI against this package
  (`pnpm dlx shadcn-svelte@latest add <name> -c pkgs/ui -y`), then
  `node pkgs/ui/scripts/relativize-lib-imports.mjs` to rewrite the CLI's `$lib/...` imports to
  relative paths (required because the package is consumed as source — an app's `$lib` ≠ this
  package's). Export the new component from `src/index.ts`.
- **Tasks.** `moon run ui:check` (svelte-check, honours the `warningFilter`) and `ui:lint` (eslint, with
  a scoped `valid-compile` warnings override). No build step — source is consumed directly; each app's
  `:check`/`:build` type-checks and bundles the components it imports.

## `seccl` — generated Seccl payload models (Python)

Pydantic v2 models for Seccl's API payloads. **Generated, never hand-edited**, and generated from
a Postman collection rather than a schema — Seccl publishes no OpenAPI document, so
`pkgs/seccl/spec/seccl.postman_collection.json` (vendored byte-exact) is the source: 377 requests
carrying 205 example request bodies and 204 saved responses, and no schema anywhere.

That makes every type **inferred from examples**, which is why `services/seccl`'s hand-written
models (`seccl_sync.records`, re-analysed against the live API) stay authoritative where the two
disagree. What these buy is the write path: 205 documented request bodies make the create/update
shapes the best-evidenced part of the collection, and a rejected `POST /portfoliotransactiongroup`
costs a payment.

- **Two steps, both checked in.** `moon run seccl:build-schemas` merges the examples into one JSON
  Schema per resource (`src/seccl/schemas/`); `moon run seccl:generate-models` runs
  datamodel-code-generator over those (`src/seccl/models/`). `seccl:test` regenerates into a temp
  directory and fails if either output is stale.
- **The merge is the point.** Handing the examples straight to the generator produced
  `WrapperDetail`, `WrapperDetail1` and `WrapperDetail2` for one concept and marked fields required
  because one example had them. Merging first — unioning keys and types across every example,
  pooling each nested concept across all three shapes — gives 136 classes, none numbered. The
  judgement calls (required means present-in-all; no inferred enums; strict date detection; money
  stays `number`) are tabulated in `pkgs/seccl/README.md` and pinned by tests.
- **Shapes.** Up to three per resource: `Account` (as returned), `AccountCreate` (POST body),
  `AccountUpdate` (partial PATCH/PUT body with Seccl's `updateId` concurrency token). Fields are
  snake_case with camelCase aliases and `populate_by_name`; **dump with `by_alias=True`**, which is
  what Seccl reads. Nested types come from their resource module, not the package root — different
  resources give the same name to different objects.
- **Note the moon ids.** `pkgs/seccl` owns the bare `seccl` id, so the sync service's tasks are
  `seccl-sync:*` (`moon run seccl-sync:dev`), mirroring `morgan-lloyd` / `morgan-lloyd-sync`.

## `sei` — SEI Wealth Platform (SWP) API client (Python)

Async client for the custodian behind the SIPP book. Covers **only what Graphene calls** — today
the cash-receipt service used to lodge a payment expectation against a client's account
(`docs/contributions.md`).

- **Hand-written, not generated** — the opposite call from `seccl`, for the opposite reason. SEI
  documents SWP as **~200 separate Swagger files with no combined spec**, each describing one
  operation, with overlapping and sometimes contradicting component names. Generating from 200
  specs to reach three endpoints would produce far more surface than it removes, so the payloads
  are transcribed and **pinned by tests**: `tests/test_cash_receipts.py` validates every payload
  the models can build against the spec's own JSON Schema, and checks in both directions that no
  field was invented and no required field missed. The one spec depended on is vendored in
  `pkgs/sei/spec/` so CI never reaches for a sibling repo.
- **Three credentials stack on every call** and all three must be right or a misconfiguration
  looks like an outage: an **mTLS** PKCS#12 bundle (the machine identity — httpx can't load a
  `.pfx`, so it's unwrapped via `cryptography`), an **OAuth** password grant, and an **AppKey**
  gateway header sent on every request _including_ the token call. The token request is unusual
  enough to be pinned by a test: `AppKey` and `grant_type` are **headers**, the app pair is HTTP
  Basic, and only the user credentials go in the form body.
- **SEI allowlists by source IP**, so a correctly configured client still gets 401/403 from
  anywhere but the allowlisted host. That surfaces as `SeiAuthError` with an explanatory message
  rather than looking like a dead service — and it means the call cannot be exercised from a
  laptop.
- **Everything is a string on the wire**, including amounts and the numeric code sets; SEI rejects
  a JSON number. Use `sei.money()`, which quantizes rather than calling `Decimal.normalize()`
  (`Decimal("60").normalize()` renders as `6E+1` — the same trap the CTC XML builder hit).
- **`descriptionTypeCode` / `treatmentTypeCode` are firm-configured reference data**, not fixed
  codes, and no lookup endpoint exists in the published spec set. They are the pair that actually
  classifies a contribution at SEI's end, and they arrive as configuration — see
  `docs/contributions.md` §5.
- **Note the moon ids.** `pkgs/sei` owns the bare `sei` id, so the file-drop sync service's tasks
  are now `sei-sync:*` (`moon run sei-sync:pull`), mirroring `seccl` / `seccl-sync`.
