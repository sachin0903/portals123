# Design language

The product's UI law. Every screen in every portal (client, platform, admin) composes
`@graphene/ui` (`packages/ui`) — a themed shadcn-svelte system driven by the white-label tokens in
`packages/ui/src/styles/theme.css`. **Never a native control, never a browser popup, never a
hand-rolled copy of a component that already exists.** When a primitive is missing, add it to
`packages/ui` and export it — don't improvise at the call site.

## The rules

| Concern                                            | The one way to do it                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | Never                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| -------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Dropdowns                                          | `SelectMenu` (simple; `name` keeps form POSTs working; grouped items supported) or compound `Select.*` for custom item markup                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | `<select>`, local `selectCls`/`fieldClass` consts                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| Checkboxes / toggles                               | `Checkbox` (forms), `Switch` (settings that apply immediately)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | `<input type="checkbox">`                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| Text areas                                         | `Textarea`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | `<textarea>`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| Confirmation                                       | `ConfirmDialog` (`destructive` for irreversible; async `onconfirm` gets a busy state)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | `window.confirm()`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Alerts/notices from code                           | `toast.error(...)`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | `window.alert()`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| Action feedback (archive, save, complete, invite…) | `toast.success(...)` / `toast.error(...)` — the `Toaster` is mounted in every app layout                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | Silent `invalidateAll()`, bare coloured `<p>` texts, emerald/positive inline "Saved."                                                                                                                                                                                                                                                                                                                                                                                                                 |
| Persistent panel/page state                        | `Alert` `variant="destructive" \| "success" \| "warning"`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | Bespoke bordered divs (12 variants existed; all retired)                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| Load failures                                      | `LoadErrorBanner` (with `onRetry`) — and loaders must return a `loadError` flag, **never** swallow a failure into an empty array                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | Rendering "you have nothing" on an outage                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| Field validation                                   | Inline `<p class="text-destructive text-xs">` + `aria-invalid` on the control                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | Round-trip-only errors with no field messages                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Phone and email entry                              | `PhoneNumberInput` / `EmailInput` (exported from `@graphene/ui`; `name` posts with a plain form). Each names a bad value under the box as it is typed, not only on save                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | A bare `<Input type="tel">` or `type="email"` — it took letters and `1111111`, and said nothing until the API refused it on save                                                                                                                                                                                                                                                                                                                                                                      |
| Editing a record's panel                           | View first — a definition list plus a pencil in `SectionHeading`'s `actions` slot; the form appears on request and closes on save                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | A permanent form on a record that is mostly read (every value in an input reads as unsaved work); an "Edit" button in the action bar that only scrolls                                                                                                                                                                                                                                                                                                                                                |
| The look of a field (any box you type/choose in)   | Compose `FIELD_CLASS` / `FIELD_WRAPPER_CLASS` from `pkgs/ui/src/lib/field-shell.ts`; override only the one property that differs                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | A local copy of the border/bg/height/radius/ring string — that is how the combobox drifted lighter and taller than the Inputs beside it                                                                                                                                                                                                                                                                                                                                                               |
| Colour entry                                       | `ColorInput` — a swatch that opens a picker, with the value as editable text beside it; `swatches` offers the firm's brand palette                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | A native `<input type="color">` (an OS popup), a third-party picker restyled with `!important`                                                                                                                                                                                                                                                                                                                                                                                                        |
| A date range                                       | `DateRangeFilter` in a listing toolbar — a trigger opening a `RangeCalendar`, with presets above the grid in three bands (last 24 hours, 7 days, 30 days, 3 months, 12 months; then month, quarter and year to date; then last month, last quarter and last year); the chosen span is named on the trigger and not repeated in the panel; `./date-range.ts` converts to and from the `YYYY-MM-DD` the URL stores                                                                                                                                                                                                                                                                                                                                                                                                                                                     | A pair of `<input type="date">` boxes (a browser popup, and one range asked as two unrelated questions)                                                                                                                                                                                                                                                                                                                                                                                               |
| A length (a number with a unit)                    | `UnitInput` — the unit sits INSIDE the box, and is plain text rather than a menu when only one is offered                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | An `Input` next to a `SelectMenu`, `type="number"` (the scroll wheel silently edits it)                                                                                                                                                                                                                                                                                                                                                                                                               |
| Padding / margin / corner radius                   | `BoxSides` — one linked value or one per part, the mode DERIVED from the values; parts-generic, so corners work as well as sides                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | One field claiming to be the whole box (its next edit overwrites the sides nobody was shown), or four fields always                                                                                                                                                                                                                                                                                                                                                                                   |
| A row in an inspector or properties panel          | `PropertyRow` — label, control, a brand-lit label when set, and a clear                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | A re-decided label width and gap per panel; no way to unset a property (`0` and "not set" are different answers)                                                                                                                                                                                                                                                                                                                                                                                      |
| A collapsible band in an inspector rail            | `InspectorSector` — caret, sentence-case bold label, a dot when the band carries a value                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | A hand-repeated trigger class string per band (the email style rail had five, and their dots already meant two different things)                                                                                                                                                                                                                                                                                                                                                                      |
| A right-click menu                                 | `ContextMenu` — its own Root/Trigger/Content; every other part re-exported from `DropdownMenu`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | A second menu family, so an item is padded one way on left-click and another on right-click                                                                                                                                                                                                                                                                                                                                                                                                           |
| Money entry                                        | `MoneyInput` — symbol inside the box, grouped **as you type**, value bound as a number in major units, left-aligned                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | `<Input inputmode="decimal">`, `type="number"` (the scroll wheel silently edits the amount), a second money mask                                                                                                                                                                                                                                                                                                                                                                                      |
| A currency code (the ISO column beside a figure)   | `CurrencyBadge code={…}` — the outline pill with the country's flag as a 12px disc on the left (`circle-flags`, via `currency-flags.ts`); the code is always printed, the flag is `alt=""` decoration; an `X` code (XCD, XAU) keeps a neutral disc so the column lines up                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | `<Badge variant="outline">{currency}</Badge>` (six copies existed), emoji flags (Windows prints the letters), a flag with no code                                                                                                                                                                                                                                                                                                                                                                     |
| Empty states                                       | `EmptyState` (`icon`, `title`, `description`, `action` snippet; `compact` inside cards)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | The 7+ bespoke dashed/solid/bare-`<p>` treatments                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| Page titles (staff listings)                       | None — the topbar crumb is the title. The primary create act is a named `Button` (or named dropdown when choosing a kind) at the table’s top-right; `ListingOverflow` is only for secondary or infrequent acts                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | `PageHeader` restating the nav; hiding “New client” / “New journey” behind an unlabelled kebab                                                                                                                                                                                                                                                                                                                                                                                                        |
| Page titles (staff records / settings / wizards)   | `PageHeader` (`title`, `description`, `back`, `actions` snippet)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | Hand-rolled `<h1>`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Page titles (client)                               | `<h1 class="text-3xl font-semibold tracking-tight">` + muted `<p>`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | Mixed `text-2xl`/`text-xl` sizes                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| Panels                                             | `Card` (+`glass` where siblings use it)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | `bg-card rounded-* border p-*` divs                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Side drawers                                       | `Sheet` — full width below `sm`; from `sm`, `w-[58vw]` (illustration / consoles). Form drawers (payment, withdraw, ad hoc fee, contribution, transfer, vulnerability) pass `sm:max-w-xl` (the previous wide). A peek opts out with `sm:!w-*` (mobile nav; the transaction drawer is `sm:!w-[26rem]`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | `w-3/4`, `sm:max-w-sm` (the old one-field column), a bare `!w-[55vw]` (55% of a phone)                                                                                                                                                                                                                                                                                                                                                                                                                |
| Status chips                                       | `StatusBadge` with a semantic tone; labels Title-case, normalised at the render boundary                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | Hand-rolled `rounded-full` chips, `Badge variant="secondary"` meaning "good", raw API casing                                                                                                                                                                                                                                                                                                                                                                                                          |
| Graded ratings (risk 1–10 and the like)            | `RiskBadge` — the interpolated green→amber→red ramp (`risk.ts`), NOT three discrete tones                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | Bucketing a 10-point scale into success/warning/danger, so 4 and 5 look identical; any palette colour                                                                                                                                                                                                                                                                                                                                                                                                 |
| Mode/view toggles                                  | `Segmented` (or `Tabs.*` unstyled for view switching)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | Hand-rolled `bg-muted` pill groups (22+ variants existed)                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| Action buttons                                     | `Button` variants (`ghost`/`outline`/`link`/`secondary`/`destructive`, `size="icon"`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | Raw `<button>` that re-implements Button's look                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| Destructive actions                                | `variant="destructive"` styling **and** a `ConfirmDialog` when irreversible                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | Ghost-styled deletes with no guard                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Submits                                            | `disabled` while pending + a pending label, via `use:enhance`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | Double-submittable buttons, full-page-reload POSTs                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Colours                                            | Theme tokens (`success`, `warning`, `info`, `destructive`, `brand`, `positive`, `ring-background`…)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | Raw palette (`emerald-600`, `rose-700`, hex literals, `ring-white`)                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Telling cards apart (accounts, covers, charts)     | A card finish (`categorical.ts::finish`) or a brand lightness step (`series`), never a rotated hue                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Errors pages                                       | Each app ships `+error.svelte`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | SvelteKit's unstyled default                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| Narrowing a listing                                | `ListingToolbar` with `filterDefs` / `filterValue` / `onfilters` — a ghost, borderless funnel immediately right of search (pointer cursor; muted at rest, the current foreground once a filter is applied, and highlighted on hover or while the bar is open), badged with the applied count and named `Show filters` / `Hide filters`; the funnel shows and hides the bar, which lists every filterable field, and Clear sits in that bar and leaves with it; a set field names its value and carries an × that clears it, an unset field is only its label; each tag opens that field's values in place: a tick and the row count, a search box from eight options; the adviser list is the firm's directory, loaded in full and narrowed in the browser; currency types to search; never a second dropdown and never a checkbox. A filtered arrival opens the bar | Printing “Filter” beside a universally understood funnel; tens or hundreds of checkboxes in the filter panel; a filter that opens a dropdown which then opens another; a field-picker menu in front of the values; an × on a field that has no value; a Clear button left behind after the bar closes; a row of dropdowns carried whether or not anyone set one; a count with nothing to say WHAT is applied; hiding the adviser directory until a keystroke; the whole currency list before a search |
| Primary listing controls                           | One shared `ListingToolbar`: 36px search at left with the ghost funnel beside it; named primary action at the right; all triggers normalised to 36px. Put view/scope `Segmented` controls on its `stacked` second row. The loading skeleton mirrors the same order and dimensions.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | Actions detached in `PageHeader`; hand-built search/filter rows; a kebab hiding the primary action; mismatched 32/36px controls; skeleton controls in a different order                                                                                                                                                                                                                                                                                                                               |
| Global platform search                             | A field-shaped topbar trigger opens the `Command.Dialog` Spotlight palette: window-centred, its top derived from one max-height knob so results fold down into a centred panel and never past the fold, the theme's `glass` material with a scoped `--glass-*` override (the one glass exception in a `sharp.css` portal) and no scrim at all; at rest just the bar plus a Categories / Quick actions pair, with lists appearing only on `>`, `/` or those buttons; plain-language labels, `/`, `>` and `clients:` as secondary shortcuts; pages come from the permission-filtered sidebar nav. See [`search.md`](search.md)                                                                                                                                                                                                                                         | Technical “command”/“scope” language in the interface, listing everything before the user has asked for anything, hand-rolled `backdrop-blur` instead of the glass tokens, a palette centred on the content canvas or vertically centred (so the bar jumps as results arrive), an inline result popover competing with the page, duplicated nav permissions, actions and category filters mixed into one list, or OS/browser-reserved modifier shortcuts                                              |

## The visual system — "institutional depth"

Component discipline (above) stops screens looking _different_. This section is what stops them
looking _cheap_. Direction: layered depth, editorial typography, data-forward density — a premium
financial terminal, not a bootstrap admin. **Platform and admin are one product** and must be
indistinguishable in chrome; only their content differs.

### Depth

Three elevation steps, defined once as `--elev-1/2/3` (ink-tinted, y-offset dominant — "raised
sheet", never "glow"). Consume via `Card`'s `elevation` prop (`flat | raised | floating`) or
`shadow-[var(--elev-2)]`. The one glow in the product is `LookThroughButton`'s brand halo, and it
is a call to action being lit, not a surface being raised (`docs/lookthrough.md`). Dark mode swaps to darker, deeper shadows because a light-tinted shadow
on a dark canvas reads as haze.

Staff portals (`chrome.css`) drop the 1px box: widgets and the chrome L are **white sheets on a
chroma-0 canvas a notch behind them**. Fill does the edge — a drop shadow on a page you sit in
all day either reads as dirt or vanishes. `--elev-2/3` stay for popovers and the one card that
must dominate. Dark is the same split inverted, with a wider notch: near-black paper
(`oklch(0.12 0 0)`), sheets at `0.19`, chroma-0 ink — no navy wash. A 0.06 mid-charcoal
pair reads as one slab. Radius is 0.5rem (the theme's 0.75rem turns a listing card into a pill). `sharp.css` still squares corners and draws hairlines — that is the
boxed toggle (`document.documentElement.dataset.chrome = 'carded'`), not the default. Hand-rolled
`bg-card border` panels follow the same treatment so they cannot reintroduce a ruled box. Table
row rules and field outlines stay; those are not a widget outline. The table
wrapper's scroll-edge fades go: they are `--background` strips on `--card`, which
the fill split turns into a shade down each side.

Surfaces, from back to front: `glass-canvas` (the ambient page backdrop) → `glass-panel` / `Card`
→ `hero-surface` (the one dark anchor) → popovers/dialogs.

**The client header's controls are all one liquid-glass round** — the avatar, search, the bell,
the message button and the way-back pill wear the same `glass` circle as the floating tab bar,
40px on a phone and 36px on the desktop bar. The string lives once, in
`@graphene/ui`'s `CLIENT_HEADER_BUTTON_CLASS` (`shell/client-chrome.ts`), with
`CLIENT_HEADER_ICON_CLASS` for the glyph and `CLIENT_HEADER_BUTTON_ACTIVE_CLASS` — the tab bar's
lit pill — for the control whose page is open. A new header control takes those, not a `ghost`
`Button`: the staff tools' flat square next to three glass rounds is what this rule exists to
stop. The shared `NotificationBell` keeps its flat chrome for the staff portals and takes the glass
through its `triggerClass` / `iconClass` props.

Theme is account preference, not page furniture. On the staff shells, Light / Dark / System lives
in a **Theme** submenu under the logged-in user at the bottom-left. The topbar’s trailing utility
cluster holds a compact palette launcher beside notifications and page actions: global search is
not an inline field, so it must not grow across or centre itself in the bar. The client shell keeps
the same theme choice in its account chrome.

### Motion

Motion has one vocabulary in `theme.css`. A component chooses the **meaning** of its movement; it
does not choose a fresh duration/easing pair. The web cannot ask CSS for Apple’s perceptual-spring
`duration`/`bounce` API directly, so our spring curves are sampled `linear()` responses:

| Behaviour                    | Use                 | Contract                                                                    |
| ---------------------------- | ------------------- | --------------------------------------------------------------------------- |
| Menu or overlay reveal       | `motion-menu`       | 220ms bouncy spring, ~12.6% overshoot; 150ms ease-out exit                  |
| A region folding open        | `motion-reveal`     | The same 220ms bouncy spring, so container and contents move as one gesture |
| Structural expansion         | `motion-expand`     | 500ms smooth/critically damped spring, zero overshoot                       |
| Status/opacity/colour        | `motion-fade`       | 250ms ease-in-out; no spring physics                                        |
| Toggle/switch                | `motion-toggle`     | 200ms ease-out; immediate, tactile, no bounce                               |
| Newly mounted result/content | `motion-item-enter` | Reveal-duration fade + 4px rise; do not stagger long lists                  |
| A side panel beside a list   | `panelSlide`        | Expand duration on the smooth spring, 40px; opacity on the reveal; exit     |

The tokens are `--motion-duration-*`, `--motion-spring-smooth`,
`--motion-spring-bouncy`, `--motion-ease-out` and `--motion-ease-in-out`. Rules:

- **New UI must use the semantic utilities.** Do not add `duration-300`,
  `ease-[cubic-bezier(...)]`, or component-local keyframes when one of the behaviours above fits.
  Existing ad-hoc motion migrates when its component is touched; do not churn it blindly.
- A spring moves **geometry**. Opacity and colour use `motion-fade`; a mechanical overshoot on a
  tint or status change is visual noise.
- **Bounce is the house feel for anything transient** — an overlay, a menu, a region folding out of
  one. It has to be felt: at 2.5%, and then at 8%, the overshoot read as a rendering artefact rather
  than a response, so the curve now peaks ~12.6% past target and rebounds once before settling.
  The bounce lives in the tail — the target is reached by ~23% of the duration — which is why 220ms
  still feels elastic instead of clipped. A container and the region it unfolds share one spring and
  one duration, or a quick panel ends up wrapped around a slow list.
- Bounce stops at surfaces large enough for overshoot to read as instability: a drawer, a record
  section, a card growing in place. Those take `motion-expand`. Never bounce a page-sized surface.
- A true physical spring (mass/stiffness/damping/initial velocity) is reserved for a gesture whose
  release velocity must carry into the animation. Use Svelte’s motion primitives and preserve the
  pointer velocity; do not approximate a drag with CSS. We currently have no such application
  interaction.
- Motion must be interruptible: animate the rendered property toward the new state, never wait on
  a timeout before accepting the next input.
- Every motion utility owns its reduced-motion fallback. Under
  `prefers-reduced-motion: reduce`, semantic animations stop and transitions settle in 1ms; content
  and state changes remain complete.

A side panel pushed in beside a listing (the transactions ledger's detail, the contributions
classify panel, the transfers detail) takes `panelSlide` from `panel-motion.ts`. It reads the
tokens above at run time rather than copying them, and moves only `transform` and `opacity`:
the listing changes width once, at the click, and the panel does the moving. Animating the
panel's width instead re-lays the table out on every frame, measured at 50–117ms a frame
against 8–17ms for the slide. On the way out the panel is lifted out of the layout where it
stands, so the listing takes its room back at once. Picking another row while it is open
brings in only its contents, with `motion-item-enter`.

Spotlight is the first consumer: `motion-menu` pops the glass panel in, `motion-reveal` folds the
result region downward on the same spring, and `motion-item-enter` brings in result groups. The
panel’s fixed top means the bounce never moves the search field after it has been read — the
overshoot is spent on the bottom edge, which is the part the eye is following.

### The hero band

`HeroPanel` is the dark ink band that anchors a screen's headline figure — the treatment
`PortfolioHeader` proved, now generalised. Rules:

- **At most one per screen**, directly under the `PageHeader`.
- Only where the screen has a genuine headline figure. **An empty or redundant hero is worse than
  no hero.**
- **Never state a number twice on one screen.** If the hero shows it, the tiles below must not.
  (The admin dashboard's first draft repeated firms/users/awaiting in both — that was the bug.)
- In-hero text uses `--hero-foreground` / `--hero-muted` / `--hero-positive` / `--hero-negative`,
  never `text-white` or a raw palette value, so a re-themed hero stays legible.
- The hero has its **own ink base** (`--hero-base`), deliberately not derived from `--primary`,
  which inverts to a light teal in dark mode; the tenant's **accent** drives the specular hairline
  and in-hero marks via `--hero-accent` (`var(--brand)`). The band itself is the product's ink —
  a firm colour does not retint the slab. A firm that wants its brand on a dark card picks the
  `brand` or `deep` card finish instead (`portal-customisation.md` §4).

### Type scale

One scale, owned by components so no screen can invent a bigger title:

| Role           | Treatment                                                                                                                         |
| -------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Page title     | `PageHeader` — `text-[1.75rem]/1.15 font-semibold tracking-[-0.015em]`. Staff listings have none — the topbar crumb is the title. |
| Page eyebrow   | `PageHeader eyebrow` — `text-xs font-medium`, muted, sentence case                                                                |
| Hero figure    | `text-3xl sm:text-4xl font-semibold tabular-nums`                                                                                 |
| Section band   | `SectionHeading` — `0.8125rem font-semibold`, sentence case, optional muted count                                                 |
| KPI figure     | `Stat` — `text-2xl font-semibold tabular-nums` + `text-xs font-medium` muted label                                                |
| Inspector band | `InspectorSector` — `text-xs font-bold`, sentence case, foreground ink, NO divider                                                |

**Every figure is `tabular-nums`** — money and counts must not jitter as they update or misalign
down a column.

**A VALUE IS NAMED WHERE IT IS PRODUCED, AND NAMED ONCE.** The sentence-case rule below is about
headings; this one is about the strings beside them, and it is the half that kept being broken. A
status, role or kind arrives as a `snake_case` key, and three habits grew up around printing one:
render it raw (a lowercase `draft` badge beside a `roleLabel`'d "Owner" in the same row), reach for
`class="capitalize"`, or write a label map per surface. The middle one is the worst of the three
because it looks like a fix: Tailwind's `capitalize` is `text-transform: capitalize`, i.e. TITLE
case — it uppercases every word and cannot touch the separator, so `not_a_contribution` renders
"Not_a_contribution" and an already-correct "Fixed income" renders "Fixed Income". **It is banned in
all three portals and `pkgs/ui`, enforced by a static scan** (`apps/admin/src/display-case.ts`,
scanning the sibling trees the way `route-addressing.ts` does — nothing else can see this defect:
it type-checks, it lints, and the DOM in an SSR test holds the raw key, so the Title-Casing happens
only in the reader's browser). `lowercase`/`uppercase` stay allowed: those are registers a look opts
into wholesale (the ledger finish, the ink look's `· pending`) over a label that is already right.

Where a value's words differ from its key, the map goes **beside the thing that already maps it**:
`stateLabel` sits next to `stateTone` in `book/journeys/applications.ts` because a tone and a name
for one vocabulary that live apart will drift — `stateTone` shipped without a sibling, and every
caller coloured the badge correctly and then printed the raw key into it. Where the words ARE the
key, `humanise` / `sentenceCase` from `@graphene/ui` (`pkgs/ui/src/lib/sentence-case.ts`) is the one
implementation; `humanise` adds the book's em dash for an absent value, `sentenceCase` returns the
string. Only the first character is touched, so `GIA` and `SIPP` survive.

**BOTH HABITS ARE BANNED, AND THE SECOND ONE IS THE ONE THAT CAME BACK.** Banning `capitalize`
stopped the Title-Casing and said nothing about rendering the API's key straight into a pill — so
the same vocabulary kept appearing lowercase in a list beside its named self in a header
("completed" under "Completed"). `apps/admin/src/display-case.ts` now refuses both: `capitalize`
anywhere, and a badge whose entire content is a bare status-ish expression. The second check keys on
the PROPERTY NAME (`status`/`state`/`stage`/`phase`/`outcome`/`decision`/`priority`/`severity`/
`trigger`), because a badge holding a wrapper, an ISIN, an id or a person's name is content rather
than a code. Four raw renders are allowlisted by name with their reason — two HTTP status CODES in
the audit log, Seccl's deliberately-unparsed `ceding_status`, and a branding preview over fixtures
already written as words — and a companion test fails if an allowlisted file stops existing or stops
rendering that expression, so the list cannot quietly outlive what it excuses.

A vocabulary's namer lives ONCE, next to whatever else already interprets it (`stateLabel` beside
`stateTone`). Five hand-rolled `charAt(0).toUpperCase() + slice(1)` copies had grown up around the
status vocabularies — two for flow status alone, under two different names (`statusLabel`,
`titleCase`) — and none of them touched the separator, so `in_progress` read "In_progress".

**Headings and labels are SENTENCE CASE.** The staff chrome used to set every small heading as
tracked small caps — section bands, page eyebrows, table column headers, sidebar group labels, KPI
labels, a record's field labels, the journey builder's node types. Uppercasing a heading of real
words destroys the word shapes that make it scannable, and it buys only a "this is a label, not
body copy" cue that weight and ink buy more cheaply: a section band is `--foreground` semibold
against muted body, a data label is muted against `--foreground` values. At the sizes these labels
live at (10–11px in the denser panels) the caps were costing legibility twice over, so anything
that dropped the caps and sat below 11px went up to 11px rather than staying tiny.

**A field has TWO tones, and both live in `field-shell.ts`.** The default is OUTLINED — a border on a
transparent box, which is what says "type here" on a form card with nothing else on it. An inspector
rail is the opposite problem: six sections of label-and-control in 264px, where an outline per row
draws a grid of boxes and the values inside it are the last thing the eye finds. So a rail sets
`data-field-tone="filled"` on the panel and every field inside it becomes a filled box —
`FIELD_TONE_FILLED`, composed into `FIELD_SHELL`, so it reaches `Input`, `UnitInput`, `ColorInput` and
anything else that composes the shell with no prop threaded through them. It is `in-data-[…]` (the
idiom `Button` uses for `in-data-[slot=button-group]`) because Tailwind emits that as
`:where([attr]) .utility` and `:where()` costs no specificity — so a caller's own `class` override still
wins, which is the contract the shell rests on. Do NOT hand-write the tone on a control, and do not
reach for a `tone` prop: adding one is how the combobox drifted from the Input in the first place.

Two labels keep their weights, and the weight is still the meaning — don't "fix" one into the other:

- **Page band** → `SectionHeading` (`0.8125rem`, `font-semibold`, foreground). Separates bands of a
  screen.
- **Data label** → `text-xs font-medium` muted, inside a card (a record's field labels, a kanban
  column header, a canvas node's type). Labels a value, not a region.

Anything else wearing that style (a dialog option row, a modal shell, a popover) is neither, and
keeps its own treatment — `Card`'s radius is for page cards, not for every bordered box.

Three deliberate exceptions, all of them a register rather than a heading: the client portal's
**`finish: ledger`** axis (`--card-title-case: uppercase` and the `investment/*` ledger looks —
small caps IS the private-bank aesthetic a firm opts into, and `pkgs/auth`'s branding tests pin
it); the **template and email editors**' panel chrome, which matches the GrapesJS widget it wraps;
and the `Logo` wordmark fallback, which is a lockup.

### A column means the same thing on every table

- **Alignment follows what the cell renders.** `right`: a figure you compare down the column (money,
  size, units, a share). `center`: a switch, a checkbox or a short count, never `right`. No `align`
  (left): everything else, **pills, badges and dates included**.
- **`defaultDir`**: `desc` for dates and figures, none for text. A queue's due date sorts soonest first.
- **`sortValue` is required** when the cell renders anything but its raw field (dates on raw ISO,
  status and priority on a label or rank).
- Headers may differ by context; columns holding the same kind of figure behave the same.

### Density (pro/dense)

Staff live in these tools all day and scan long lists, so list/table screens are packed:
`size="sm"` controls, page bands at `space-y-5`. Record, form and editor screens keep
their breathing room — reading and data entry are not scanning. Never shrink text below `text-xs`.

**Table rows are the exception:** one row height everywhere; `DataTable`'s `dense` defaults off.

On a phone the same listings must not squash. `table-fixed` percentage columns plus nowrap cells
overlap below `md`; the table keeps auto layout, grows with its content, and the wrapper scrolls
sideways. The page is the vertical scroller — do not trap rows inside a viewport-tall table pane
on mobile. Desktop listings still fill the inset and scroll their own rows so the toolbar and
pager stay put.

**Client-portal tiles on a phone are near-bleed** (`PortalPage`, below `sm`). The composed grid
takes most of the shell's `px-4` gutter back (`max-sm:-mx-2`, leaving 8px a side) and tightens to
`gap-2`, so the tiles run almost edge to edge with their own padding as the real inset — a phone
cannot spend a full gutter _and_ the card's padding on every edge without the content reading
smaller than it is — but a hairline of ground stays either side: a sheet touching the glass
edge-on read as a table, not a tile. The tiles keep their radius. The studio's phone preview
renders the same rule.

### Scrollbars

**Quiet by default, everywhere** — `theme.css` applies it to `*`, because CSS has no selector for
"an element that scrolls" and this product is mostly scroll containers. A container's bar is
invisible until the pointer is over it (`:hover` matches ancestors, so pointing at a row reveals its
panel's bar) or something inside it has focus, then it fades up as a slim themed thumb on a
transparent track. It is a REVEAL, not a removal: the OS bar auto-hides only on a trackpad, so on a
mouse — or with macOS set to "Show scroll bars: Always" — the UA was painting a permanent
high-contrast bar down the edge of every listing, drawer, notes panel and feed at once.

Two opt-outs, both explicit:

- **`scrollbar-slim`** — always visible, for a container where the thumb is telling you something
  (the PDF viewer's page column: how far through a 40-page report you are). Pair it with
  `[scrollbar-gutter:stable]` so the thing being read does not reflow.
- **`no-scrollbar`** — no bar at all, for a container whose scrolling is self-evident and whose bar
  would sit on the design (the sidebar nav, the ⌘K result list).

Never add `::-webkit-scrollbar` rules to the global default. The pseudo-elements force a classic
non-overlay bar in every WebKit engine, which spends a permanent gutter of content width to hide a
bar that was already auto-hiding there. `pkgs/ui/src/styles/scrollbars.test.ts` pins that, plus the
two bugs this replaced: a `no-scrollbar` class two components asked for and nothing defined, and a
`scrollbar-slim` defined twice where the first was dead.

### "Done" states

Strikethrough means **"you ticked this off"**, not "this is complete":

- A **to-do** the user completes (tasks inbox, a journey's raised tasks) → `line-through` + muted.
  That's the understood convention for a checked-off list item.
- A **process step or checklist stage** that the system completed (firm onboarding steps, flow
  steps) → muted text + a tick icon, **never** `line-through`, which reads as "cancelled / no
  longer applies" when the tick already carries completion.

### Navigation feedback

A click on a nav row has to be acknowledged **on the click**. SvelteKit renders the page you are
_leaving_ for the whole of the destination's `load`, so on any surface that reads the API and the lake
`page.url` — and everything derived from it — is the old page for the best part of a second, and a
sidebar click read as a dropped click.

One value carries all of it: the app's `navigating.to?.url.pathname`, passed into the shell as
`pendingHref` (each root layout derives it; `pkgs/ui` cannot import `$app/state`). From it:

- **`NavProgress`** — the top-edge bar (`pkgs/ui/.../shell/nav-progress.svelte`), mounted for EVERY
  navigation, indeterminate and asymptotic. Reduced motion keeps the bar and drops the movement: it
  is the answer to "did my click land", so it is never suppressed entirely.
- **The sidebar highlight** follows the DESTINATION, not the current path, and the row it lights
  wears a spinner until its page arrives — a row can be active before its page exists, and without
  the spinner a slow load reads as a page that rendered nothing.
- **The breadcrumb and the tab title** are resolved from the pending path when — and only when — it
  is a nav row in its own right. Anything deeper keeps the trail it has, because naming an unloaded
  record would be a guess rather than a preview.
- **The canvas dims** (delayed 80ms, so a fast navigation never visibly dims) only when the pathname
  actually changes. A listing's search box and its pager navigate to the SAME path; dimming on every
  debounced keystroke reads as a fault.

None of it renders in the branding preview — a picture of the app never navigates, the same rule as
the theme toggle and the rail cookie. Pinned by `shell/chrome.test.ts`.

### Streamed surfaces and skeletons

Acknowledging the click is half the answer; the other half is that the destination has to appear.
Every daily-work surface on the platform portal — Dashboard, Investments, Pensions, Work, and the
client/account records they open — **streams**, and so does firm settings (Studio, Actions,
Forms, Roles, Data sources, Automations, Peer review). Those were left blocking at first because
they are visited rarely; measured, each one's first byte was simply its API reads, 600–770 ms
behind the daily surfaces, and a rarely-visited page is still a page somebody clicked. The loader
resolves the cheap things (the query out of the URL, the permissions out of the session, and every
`redirect`/`error`, which must be thrown before the response starts) and returns the fetches as
ONE promise property. The page renders immediately, with its toolbar already showing the filters
that are in the URL, and the rows arrive behind it. The one await a page keeps is the thing its
chrome cannot be drawn without: the client record's layout awaits the contact SHELL — the
sidebar's context header and section strip — and nothing else, because a shell-less first paint
that then jumps is a layout shift, not a win. (The client-book pin that used to follow the shell
as a second awaited stretch now rides inside the streamed record of the two sections that draw it.)

The dashboard streams TWO promises, not one: `overview` (the KPI tiles) apart from `dash` (the
book and the queue). Folded into one `Promise.all`, the "£7.8m" hero — the page's largest paint —
waited for the slowest of four reads (the paged, faceted book), and the page's LCP moved from its
first paint to its last. On its own stream it lands when the one summary query answers.

- **Every bar is a `Skeleton`**, which fills from `--skeleton` and sweeps left to right. The token
  exists because a placeholder sits on `--card`, and `--muted` is a 0.035 lightness step off white —
  visible in a swatch, invisible as a page, which is how the dashboard came to read as empty rather
  than loading. Do not hand-roll a bar: `bg-muted/40 animate-pulse` is what that dashboard had, and
  a pulse takes an already-faint block further towards the surface at the bottom of its cycle.
- **`ListingSkeleton`** is the placeholder for a book listing: the listing's own box — the same
  viewport-tall card, `h-9` search, ghost filter funnel beside it, row height and pager the real table occupies —
  not a spinner and not a short stack of bars. A grey table in the right proportions says "the
  thing you asked for is arriving here", and the content that replaces it does not jump.
- **A workbench that is not a listing gets its own skeleton**, next to the real component, because
  a generic table in those proportions is the wrong box. Transfers is the saga strip plus the
  listing (`TransfersSkeleton`). Contributions is six KPI tiles, the queue, and the 420px classify
  panel (`ContributionsSkeleton`). Messages is the 72-wide directory, the thread and the 80-wide
  rail (`MessagesInboxSkeleton`). Documents is the library's two-line `py-3` rows and the Recent
  activity rail (`DocumentsLibrarySkeleton`). Each one copies the destination's frame and sizes so
  the content that replaces it does not jump.
- **`RecordSkeleton`** is the same idea for a record: a header, optional KPI tiles, and glass
  cards of row-height bars, so an overview or a task does not jump when the body lands.
- **`Streamed`** renders either placeholder, and is the reason this is not a bare `{#await}`. These
  listings are server-driven, so every filter keystroke and every page cut is a fresh navigation
  with a fresh promise — and the two want opposite things. A **page cut** (or a sort, or a filter
  pick) asked for different rows, so the placeholder comes back until they land: a table that sits
  unchanged for two seconds after the click reads as a pager that does not work. A **keystroke** must
  not do that — swapping the listing out takes the search box, and the caret in it, with it — so
  while a text field has focus the last answer is HELD and the rows are replaced in place when the
  next answer lands (the progress bar is what says work is happening). Focus is the only thing that
  tells the two navigations apart: a pager click has moved it to the link (or the body, in Safari);
  the debounced search `goto` runs with `keepFocus`.
- **`keep`** makes the hold unconditional, for a surface where most of the content survives the
  query change — the dashboard's KPI tiles and action queue around its paged clients table, and
  every settings editor whose form actions re-run the load (a saved role, a flipped peer-review
  switch, an armed automation must not drop the editor back to bars). The
  content snippet's second argument, `stale`, is true while a held answer is being replaced, and the
  table shows it through `DataTable`'s `loading`: skeleton rows in the body, one per row it had, with
  the header, toolbar and pager left where they are. `stale` is false during a typing hold, so a
  search does not flicker.
- A rejection is reported only when there is nothing to show instead; a loader's own `loadError`
  flag remains the normal way a failed fetch is told.
- **The pager's current page is the URL's offset** — `pageOf(query)` from `book/listing`, the one
  offset↔page translation. The dashboard table once kept its own `page = 1` that nothing wrote to in
  server mode, so its strip read "26–50 of 798" while page 1 stayed highlighted.

Pinned by `ui/streamed/streamed.ssr.test.ts` (which branch the server emits), `streamed.dom.test.ts`
(swap on a page cut, hold while typing, `keep` + `stale` — none of which an SSR render can observe),
`ui/data-table/loading.ssr.test.ts` and `book/dashboard/clients-table.ssr.test.ts`.

### A surface a client is SUMMONED to states the action first

Anything a client is sent to by an email, a nav badge or a home-page card owes them an answer to
"what do you want from me" before it describes anything. The client's `/onboarding` page is the
worked example, and it is worth reading as one because its failure mode is not ugliness — it is a
page that is working correctly and reads as broken.

- **`OnboardingNextAction`** is the hero: the next step NAMED, with one button whose label says what
  the step is ("Review and agree", "Verify my identity") rather than "Continue" — a client about to
  photograph their passport should know that before pressing anything.
- **An active onboarding is a temporary portal mode, not one tab among six.** The stable post-login
  destination (`/`) redirects to `/onboarding`, and the global nav shows Onboarding alone until the
  journey completes. Sending a newly invited client to a £0 dashboard full of unavailable Accounts,
  Holdings and Activity makes a working invitation read as an empty product.
- **Show the journey as a vertical narrative.** Each milestone says what happens, whether it is
  complete/current/upcoming, and whether the current stage needs the client. The adjacent action
  card lists only steps they can perform now; held and completed client steps remain visible as
  context. A horizontal five-dot strip names phases but does not guide somebody through them.
- **The waiting state is the point.** In the seeded journey a client's own work is the terms
  declarations and the identity check, and both sit behind their adviser's fact-find, so "nothing
  for you to do" is the NORMAL state for much of a real application. A page that only lists tasks
  is empty exactly when the journey is healthiest. So the hero names the milestone the application
  has reached instead: "Currently at Getting your details."
- **Group a checklist by whose move it is** — Ready for you / Coming up / Completed — and let a held
  step say why ("Opens once the earlier steps are finished"). The bare "Not yet" badge it replaces
  read as a refusal, and sat in the same undifferentiated list as the things they could act on.
- **Count only what they can DO.** The home card's call to action reads the API's `available`, never
  `total - done`: most of the latter is the adviser's work, and counting it summons a client to work
  that cannot be started.
- **"All set" is a claim about the APPLICATION, not about the rows on screen.** A client's step list
  GROWS as the journey advances (identity is released once the terms are signed), so `done >= total`
  would announce completion in the gap and then produce another task. The hero defers to the
  milestone rollup; the nav entry follows the same rule, having previously vanished the moment a
  client finished their terms — removing their only route back to the page that explains the wait.
- **A repeated confirmation is one seat, not four.** The terms declarations chain inside one
  dialog ("Document 2 of 4", "Agree and continue"), because closing after each made a client open,
  read, agree and close once per document with a list refresh between them.

### Micro-craft

Interactive surfaces earn their affordances: linked KPI tiles lift (`hover:-translate-y-0.5`) and
show a chevron; every focusable thing has a `focus-visible` ring; hover states on rows; colour
carries meaning only via tokens.

## Layout constants

- `SaaSShell` pads the canvas (`p-4 md:p-6`) — pages add `mx-auto max-w-*` **without** their own
  `p-6`.
- The client shell pads the bottom for the floating tab bar — pages never add their own `pb-24`,
  and full-bleed layouts must not cancel the shell's bottom padding (the tab bar will overlay
  content).
- One heading scale per app; section headings inside a page use the muted uppercase
  `text-xs font-semibold tracking-wide` style.

### Nothing spills, nothing is clipped — and the portal checks (Oct 2026)

The rule: content never runs out of its box, and a box with `overflow-hidden` never cuts off
anything a client would miss. No CSS discipline can promise that — a 12-column grid cut five ways
gave a Docs tile 165px and "Correspondence" beside its mark needed 166, so the label ran out of
the tile — so the client portal **checks the laid-out page** in development. `@graphene/ui`
`overflow-audit.ts` (`auditOverflow`, `watchOverflow`) walks the DOM after every navigation and
layout change and reports two findings, geometry only:

- **spill** — a child's border box, or a run of text, past its parent's padding box while the
  parent's overflow is visible. A negative margin is read as a declared bleed (`-mr-1.5` on a
  "View all", the phone grid's `-mx-2`, the deck's `-mt-[3.25rem]`) and allowed up to that much;
  positioned children (`absolute`/`fixed`) are not spills; an inline child is judged horizontally
  only (a tall line box over `leading-none` is typography); text must disagree with the box AND
  show up in `scrollWidth` (a `pre-wrap` trailing space hangs past the box by design, and a
  `::before` hit-area counts toward `scrollWidth` but is nothing a reader sees).
- **clip** — an `overflow-hidden`/`clip` ancestor cutting off content: an element with its own
  text, an image, or a control. Decoration is not content (`aria-hidden`, `pointer-events-none`,
  `visibility:hidden` — sheens, bleeding sparklines, a collapsed period row's hidden pills);
  `text-overflow: ellipsis` and `-webkit-line-clamp` are intentional and skip; scroll containers
  are not clippers and are not descended into.

An offender gets `data-overflow="spill|clip"` — a red (spill) or amber (clip) dashed outline from
`apps/client/src/app.css` — and a collapsed console group with the element attached, per run.
`data-overflow-ok` on an element waives it for the rare bleed the rules cannot read; use it for a
reason you can write down, not to quiet a finding. `+layout.svelte` runs it under `dev` only: the
walk reads every element's computed style. The baseline (Home, Portfolio, Activity, Docs,
Insights, Messages, an account record, a document, an insight; 390, 768, 943, 1048 and 1422px)
is clean. The fixes that got it there are the pattern for the next one: size tracks by what the
tiles need (`repeat(auto-fill, minmax(12rem, 1fr))`), not by a column count; `min-w-0` AND
`max-w-full` on a flex child that must shrink; `truncate` with a `title` as the last resort for a
label that is one word.

### Every control is a thumb's width — and the portal checks that too (Oct 2026)

The rule: in the client portal every interactive element's **hit area** is at least 44×44 CSS px
(Apple HIG; WCAG 2.5.5). Its visible size does not have to be — a 24px "View all ›" and a 36px
round icon are right at their size, and a portal where every quiet link is a chunky row is the
wrong portal — so the hit area is grown invisibly. The mechanisms, in the order to reach for:

- **`tap-target`** (`theme.css`) — an absolutely positioned `::before` with
  `inset: min(0px, calc(50% - 22px))`, which pads each axis out to 44 only where it falls short
  (a 120px-wide button gets taller, not wider). On a link, a round icon button, a legend row.
  Every `Button` in the client portal carries the same extension through
  `[data-slot='button']::before` in `apps/client/src/app.css`, so `sm` stays 28px to look at and
  44 to tap; the shared theme does NOT do this, because the platform's dense tables put icon
  buttons 4px apart on purpose. The switch and the checkbox size their own `after:` box to 44.
- **`min-h-11`** where the whole row is the control (a documents list row, a profile tab, the
  bell popover's "Mark all read" row), and wherever an ancestor scrolls or clips — an
  `overflow-x-auto` tab strip clips an extension at its edge, so its tabs size themselves
  (`Segmented size="lg"` is this for the theme switcher).
- **A real `min-height`** for a field: `<input>`, `<select>`, `<textarea>` have no pseudo-elements
  to extend, so the client's `app.css` floors them at `2.75rem` (which `min-height` wins over the
  field shell's `h-8`). Menu, palette and select rows get the same floor.
- **`pointer-coarse:hidden`** for a pointer-only affordance: the hover-revealed ✓/× on a
  notification row have no business on a phone, where the row itself is the tap.

Two extended controls closer together than their shortfalls overlap, and the later one in the DOM
wins the shared strip — so a tap on the edge of the first fires the second. Lay small controls out
with a gap at least the sum of their halves: two 36px icons need `gap-2`, the allocation legend's
40px rows `space-y-1`. Icon-only actions carry an `aria-label` naming the verb and the person
("Email Andy Gaunt"), never just the verb.

`@graphene/ui` `touch-audit.ts` (`auditTouchTargets`) runs alongside the overflow audit in
development (`watchLayout` schedules both off one observer set) and reports two findings, from
geometry alone — the element's rect plus the computed insets of its positioned pseudo-elements:

- **small** — a hit box under 44 on either axis. Skipped: anything not rendered, disabled,
  `aria-hidden` or `tabindex="-1"`; screen-reader-only boxes; a link INLINE in a sentence (the
  WCAG inline exception — the sentence sizes it); a control credited its wrapping `<label>`'s
  box; and anything under `data-tap-ok`.
- **overlap** — an extension reaching into another control's visible box (nested controls and
  extensions that merely touch are not).

Findings are a collapsed `[touch]` console group per run (no on-screen outline: one was tried,
and a dashed ring around a button that was already fine read as the button being broken). The
baseline (every page above, plus Profile and its three
sections, Notifications with the filter bar open, Requests, Onboarding, the bell, the account menu
and the ⌘K palette; 390, 768 and 1422px) is clean.

## Copy

Staff read these screens dozens of times a day. Copy that explains the screen to them is noise, and
it reads as machine-written.

| Rule                                                                                                                                             | Instead of                                                                                                                                                                            |
| ------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `PageHeader description` is OPTIONAL. Omit it unless it says something the title cannot.                                                         | "Your firm's position, what needs you today, and your whole book — on one screen."                                                                                                    |
| Never narrate the screen. No "at a glance", "on one screen", "in one place", "Everything you…".                                                  | "Your book at a glance."                                                                                                                                                              |
| No triadic em-dash lists. Two clauses maximum, and usually one.                                                                                  | "colours, logo, login screen and support details"                                                                                                                                     |
| Provenance and metadata are a compact stamp, not prose.                                                                                          | "A batch snapshot of the accounts that can carry value — draft and closed accounts are excluded. No live valuation feed is connected…" → `seccl · as at 15 Jul 2026`                  |
| `ConfirmDialog description`: ONE sentence, the consequence. It earns its words because the action is irreversible — but one sentence, not three. | "They leave your book, your AUM and every picker. Documents, notes and audit history are kept, and you can restore them… A client with pending instructions can't be archived until…" |
| An empty state says what is missing and the one action that fixes it. It is not an explanation of the feature.                                   |                                                                                                                                                                                       |
| Don't apologise, don't hedge, don't say "please". State it.                                                                                      |                                                                                                                                                                                       |

A caveat that is not a licence: where the UI would otherwise mislead — a figure whose source or
as-at date matters, a control that is deliberately disabled — say so plainly and briefly. The fix for
verbose provenance is a shorter stamp, not silence about where a number came from.

**A client-portal widget has ONE heading row, and no description** (`investment/widget-header.svelte`,
Oct 2026). Every carded block used to open with a `text-lg` title over a `Card.Description` that
paraphrased it — "Allocation / Asset-class split across your portfolio", "Documents / Statements,
illustrations and agreements" — a sentence per card that told the client nothing the title and the
content had not, and on a phone cost five lines a screen. The rule the staff screens already had
applies: the heading is the `SectionHeading` register (sentence case, 13px semibold), the way out
("View all ›", only when the firm's page set has a destination) sits on the SAME row, and the only
second thing allowed is DATA — the valuation stamp, a count and total — because data changes and so
earns the ink. Statement cards whose heading IS content ("3 things need your attention", "Continue
your onboarding · 3 of 5 steps") keep it; the chrome-less looks keep their own registers (the
ledger finish still reaches `WidgetHeader` through the `--card-title-*` tokens). Empty states are
where a sentence still does work, and they are unchanged.

**The client portal has three tiers, and a page knows which one it is** (Oct 2026). _Home_ is the
short answer: the figure, what needs you, your accounts, what has changed, what has arrived, who
to talk to, the firm's note — no block that is a teaser for a tab one tap away, and the activity
(the one thing new since last visit) above the fold on a phone. The adviser card is on home because
an advised portal that never shows the adviser is a brokerage: the one block that is a person, not
a number, with the face, the name and one way to reach them (Message) — beside the recent
documents, after the money, not above it. The _tab pages_ (Portfolio, Activity, Docs, Insights) are the
full answer on one subject: Portfolio is the whole book — how it is spread and the funds under each
asset class — because a page of the accounts home already lists is a page with nothing of its own.
A client-facing holdings list is friendly rows (mark, name, units · price, value, return), never a
sortable table with a search box: that is a staff surface. The same rule shapes Docs (one row per
document — mark, name, "Statements · 15 Jul", chevron — in month runs, no size, no format badge)
and Insights (the newest item as a lead, the rest one row each); a search box appears on a client
listing only once it outgrows a glance (eight rows), and pills only with two things to choose
between — a control over three documents reads as a tool for a library the client does not have.
A _record_
(one account) is the same blocks scoped to one thing — the portfolio header wearing the account's
own series, the shared holdings and activity lists under it, a round chevron back — so that a client
tapping an account card arrives in the same product. The bar is five tabs at most and is the firm's page set and nothing else — Insights is a page in
it, not a route appended in code (`apps/client/src/lib/nav.ts::TAB_BAR_CAP`) — the adviser has the
home card, the top-bar message icon and the conversation rather than a tab, and the colour-theme
control lives on `/profile` (Appearance), not in every screen's top bar. The default page set in
`block-contract.json` is this shape; `docs/portal-customisation.md` §1.1–1.2 has the composition.

**The way back is the shell's, and a sub page has no bar** (Oct 2026). Every client route is
either a _tab page_ (home and the firm's other pages — every one a slug in the page set) or a _sub
page_ one level beneath one: a record (one account, one document, one fund's look-through), a
reader (one insight), or a utility reached from the top bar (profile, messages, notifications,
requests, a transfer). `ClientShell` takes a `back` target and draws the one way back
for all of them, so no page writes its own: on a phone the top bar's left slot becomes a glass
pill "‹ Home" / "‹ Docs" in place of the avatar and the floating tab bar is **hidden** — the way
the banking apps push a detail screen, and what both platform guidelines mean by the bar being for
top-level destinations only; on desktop the bar keeps its logo and nav and the same pill leads the
content. The label is the parent's name as the FIRM labels it (a renamed Docs reads "‹ Papers"),
and the destination is the parent, never `history.back()` — a deep link from a notification must
still have a way up. `$lib/nav.ts::backFor` holds the route-family → parent table (records return
to their tab page, utilities to home; a page whose tab was removed returns to home); a route whose
parent only its load knows (the look-through, which returns to the account it was opened from)
returns `back` in its page data and that wins. It was: six sub pages, five different back
affordances (a text arrow, a round glass chevron, a floating viewer button, an "←" glyph, none) and
a tab bar still glowing under a document viewer.

**One sub page is the screen: the conversation** (`immersive`, Oct 2026). Messages returns
`immersive: true` in its page data and `ClientShell` stands down below `lg` — no top bar, no column
padding, no reserved scrollbar gutter — so the conversation is the whole viewport and its own header
is the top bar: the same "‹ Home" pill the shell would have drawn, then the adviser's face, name and
title. That is how every messaging app a client uses draws a conversation (the contact IS the
title), and a global bar above it was two headers stacked, with search and the bell for a page you
are already inside. It is the ONLY route that does this, and the rule for the next one is the same:
only a page whose own header is unambiguously the better top bar, and only below `lg` — desktop
keeps the nav bar and the page sits in the content column with everything else.

## Judgement calls this codifies

- **`SelectMenu` exists because** 76 native selects each carried a copy-pasted style string (10
  copies of the same const). It renders a hidden input when given `name`, so `use:enhance` form
  actions keep working — there is no excuse left for `<select>`.
- **Toasts are for outcomes, Alerts are for states.** A toast says "that worked/failed just now";
  an Alert says "this panel is currently in this condition". Field errors live next to the field.
- **An arrival is a third thing, and it has its own card.** A notification landing while you work is
  not the outcome of anything you did, so it does not use the outcome vocabulary (no tick, no
  colour, no `toast.success`). `NotificationBell` raises `NotificationToast` via `toast.custom` —
  the category icon, the title, the body, an X, and the whole card an `<a>` to wherever the
  notification points. It is pinned **top-right** whatever the app's own `Toaster` position is
  (sonner renders a stack per position, so the client portal's `top-center` action toasts keep
  theirs), because that corner is where every desktop client has put arrivals for twenty years.
  It rides sonner rather than a second stack of our own: stacking, the dwell timer, pause-on-hover
  and the motion are already solved there, and two toast systems on one page cannot be made to
  agree about which is on top. Note a `component` toast gets **none** of sonner's card styling and
  **loses** its close button — the card states its own surface and draws its own X.
- **A client is a glyph, not initials, and a child has its own.** `ClientAvatar` is one muted
  disc whose icon says what KIND of client this is: `user` for an adult, `building-2` for an
  organisation, `users` for a group, and `baby` for a child — the one child glyph for the whole
  product (Lucide has nothing closer; `baby` reads as "minor" at 16px where `user`/`smile` do
  not). Initials were dropped everywhere clients are listed (clients listing, client hero, journey
  applicants): three "Hobbis" rows all read "H", and initials told you nothing about who the
  person was. Decide the kind with `clientKind` (`components/data/client-kind.ts`), never in a
  component: `crm.contact_type` is only `individual | organisation`, so a child is an individual
  under 18 by `dob` — the same rule the API's kickoff uses (`catalogue.completed_age`). A card
  that names a client prints `birthLine(dob)` ("Born 3 Mar 2014 (12)") beside the glyph, so the
  age is stated, not inferred.
- **A client ROW is the name with one line under it, and the two book tables are one table.** The
  /clients listing and the dashboard's book both print `clientSubline` under the name: the date of
  birth as the record prints it (`11 Dec 1952`), else — an organisation — the town, postcode and
  country (`shortLocation`: the record header's address line without its street), else nothing.
  Not the email, not the reference. Accounts is `AccountTags` ("GIA · ISA", then `+N`) on both,
  never a bare count; the tags wrap, because the wrapper vocabulary is open and the row is already
  two lines tall. Adviser is `AdviserStack`: a badge-height face (`PersonAvatar` `xs`, level with
  the pills) then the first name and last initial — "Gio M.", `shortName` — with the full name in
  the tooltip and an `sr-only` span; the full name truncated to "Gio …" on almost every row of a
  narrow table and said less. Columns of a KNOWN size are sized in rem, not shares: Currency (one
  flag pill under its head), Status (one badge, "Suspended" at its widest), Accounts (three tags
  and a count: 7rem wrapping, 10rem on one line from `4xl`), Adviser (7.5rem for a face and a
  short name, 10rem for three faces and a count from `4xl`) and the three money columns (a figure
  of at most eight digits — `£12,345,678.90` is ~120px, so Value and Growth take 8.5rem, Cash
  8rem). A share clipped the badge to "Onboardir" on a narrow pane, let a seven-figure value
  spill into the Currency pill beside it, and wasted width on a wide one. Columns drop by the
  TABLE's width (`hideBelowWidth`: Adviser below `2xl`, Cash below `4xl`, Growth below `5xl`), not
  the viewport — the listing is also mounted beside rails and in admin's operate-as-firm pane, where
  the viewport says nothing about the room the table has. When the Growth column drops out, its
  figure is drawn under the value (`visibleWhenHidden` — the inverse of that column's own hide
  class, so the two are never both on screen) rather than disappearing. Client declares no width
  and, being the only auto column under `table-fixed`, takes what the rest leave. `BOOK_COLUMNS`
  and `clients-listing.svelte` carry the same widths and rungs on purpose.
- **Unsaved-changes guards** use the `ConfirmDialog` + `beforeNavigate` recipe (cancel the nav,
  open the dialog, re-`goto` on confirm) — see `flows/[id]` / `forms/[id]/edit`.
- **CSS mistakes escape the gates.** `svelte-check` never compiles Tailwind, so an invalid
  `@utility` name (e.g. `hero-toggle-off:hover` — utility names must be alphanumeric; nest `&:hover`
  inside instead) passes every gate and then 500s SSR in all three apps. After touching
  `theme.css`, hit a running dev server or run the app builds — the type gates cannot see it.
- **The portal label stacks under the wordmark** in the sidebar rather than sitting beside it: a
  long label ("Internal Operations") truncated to "INTERNAL…" inline, and the two-line lockup keeps
  every portal's header identical.
- **A long configuration group is `solo`, not just pinned.** The platform's Firm settings group is
  fourteen rows: open alongside the three work groups it pushed them off the top of anything shorter
  than a tall desktop, so reaching Clients meant scrolling a sidebar. Marked `solo` (`NavGroup`), it
  holds the other collapsible groups shut while it is open and they come back **as the user left
  them** when it closes — the suppression is derived over the persisted map (`isGroupOpen`), never
  written into it, which is why closing it restores rather than expanding everything. The two
  corollaries live in `applyGroupOpen` and are the parts that go wrong if reimplemented: opening a
  normal group closes the solo one (otherwise its trigger is a control that does nothing), and a
  `close` reported for an already-suppressed group is ignored (that can only be the primitive
  syncing, and honouring it overwrites the state being restored). Never in the icon rail — there is
  no label there to click, so a suppressed group would be unreachable.
- **Admin's per-tenant sidebar mirrors the platform IA.** Group order, labels, and Firm settings
  sections come from `@graphene/ui/nav` (`PLATFORM_NAV_STRUCTURE`); admin projects that structure
  through `TENANT_CAPABILITY_MANIFEST` (admin permissions + operate/configure hrefs) and then
  appends a Graphene-only Configure group. Do not invent a second tenant IA in
  `apps/admin/src/lib/nav.ts`.
  In-firm header search is the same rule: under `/tenants/[uid]/…` the palette is the platform
  entity set routed onto operate paths (`?tenant=`), not a third catalogue. Global admin search
  stays firms-only.
- **Operate-as-firm pages are the platform's pages, mounted — never re-drawn.** A client or account
  opened under `/tenants/[uid]/operate` publishes `sidebarFocus` from `@graphene/ui/nav`'s section
  builders (`clientSidebarFocus`, `accountSidebarFocus`, which take a `prefix`), re-exported via
  `apps/admin/src/lib/record-focus.ts`; the admin root layout renders it with the same module's
  `parseFocus` / `focusedNavWithPinned`. So there is no row of section buttons across the top of a
  record. The dashboard is `@graphene/ui/book/dashboard`'s `FirmDashboard` in both portals, and each listing
  passes the same props (streamed book, `listing`/`matchCount`/`facets`/`pageHref`, links) the
  platform page does. When a platform page body is inline, extract it into `@graphene/ui` first and
  mount it in both; an admin-only copy is how the two drifted. Admin adds only its permission
  statement in the top bar and its Configure group.
- **The firm is named by the sidebar's scope header, and Configure holds three things.** Inside
  `/tenants/[uid]` the sidebar header is the firm — its own platform-portal icon (a building when
  none is set), "Firm", the name, and "All tenants" as the way back — built by `firmContext` in
  `apps/admin/src/lib/record-focus.ts`, the same `NavContext` band the platform uses for a focused
  client; a focused record keeps its own header with the firm in the eyebrow. The Configure group
  below Firm settings is **Settings** (one page — General, Sign-in, Platforms, Storage as a side
  sub-nav under `settings/`, gated by `settingsSections`), the **Catalogue**, and **Oversight**
  (Deleted documents, Audit log — `audit.log`, a different trail from the firm's Event logs).
  Nothing the firm already reaches from Firm settings is repeated: notification rules are
  configured from Event logs, as on the platform. A new Graphene-only control is a section of
  Settings before it is a sidebar row.
- **The Tabs active state** carries a visible border (`data-[state=active]:border-border`) because
  this theme's muted/background contrast is too low for the classic shadcn pill — fixed in the
  component, so no call site may override active-tab styling again.
- **`field-shell.ts` exists because** the same fix as `SelectMenu`'s was still outstanding one level
  down: `Input`, `Combobox`, `MultiSelect` and the dynamic form's combobox each carried their own
  copy of the border/background/height/radius/ring string, and they had drifted — the combobox was
  `h-9 rounded-md bg-background focus-visible:ring-2` against the Input's `h-8 rounded-lg
bg-transparent dark:bg-input/30`, which is why one field in the transfer drawer read as a lighter,
  taller box than its neighbours. Composed, not copied, and `field-shell.test.ts` scans the
  `cn(FIELD_…, …)` call in each control so a re-declared height or background fails a gate.
- **A third-party editor's chrome is not exempt from this document.** The email template editor's
  block palette and style rail were GrapesJS's own HTML, made to look like ours with ~156 lines of
  `!important` CSS. Underneath the paint they were native selects, a native colour input and
  Spectrum's popover — and the panel could never actually match the product, because the markup was
  not ours. Both managers now run `custom: true`, which suppresses GrapesJS's VIEW and nothing else,
  so every email-safety rule in its configuration still applies while the controls are the four
  primitives above. The CSS went with them. Two things this also fixed: the two editors were
  injecting five _identically-named_ stylesheets whose contents had drifted, so whichever mounted
  first in a session styled the other; and there had been no way to UNSET a style property, because
  GrapesJS's own clear affordance was one of the things the CSS hid.
- **A CONTROL THAT SHOWS A VALUE MUST SHOW THE VALUE IT HAS.** `ColorInput`'s swatch tested `isHex` to
  decide whether to paint, so `black`, `rgb(0 0 0)` and `oklch(…)` — all fixed colours a browser draws
  perfectly — rendered the hatched "not set" pattern beside a field that plainly read `black`. Every
  border colour in the email rail defaults to `black`, so most of the panel contradicted itself. It is
  two layers now: a hatch, and over it a pane whose `background-color` is the author's own string
  handed straight to the browser — a colour it can paint covers the hatch, one it cannot (a
  `{{ brand.accent }}` token) is a dropped declaration and the hatch shows through. No list of
  notations to maintain, and a colour syntax nobody has heard of yet works. The "set" badge is for a
  value that names a colour INDIRECTLY (`isIndirectColour`: a merge token, `inherit`, `currentColor`)
  and nothing else.
- **A HEADING IS A QUESTION THE READER HAS TO ANSWER BEFORE THEY CAN SEE WHAT IS INSIDE.** So a
  composite property group in the email style rail opens only when it CARRIES a value — offering a
  per-side border and expanding it before anyone asks are different claims. The mode is seeded from the
  values and then owned by the author, and it only ever forces itself OPEN, so setting something inside
  a group cannot collapse it under your hands (the `BoxSides` rule).
- **AND A HEADING PER CSS PROPERTY IS NOT A LAYOUT.** Collapsing a group is not a licence to have five
  of them for one idea. Borders are one section of three rows — a `BoxSides` four-up for the widths, one
  Style, one Colour — over five GrapesJS composites (the all-sides `border` plus four per-edge ones,
  three sub-controls each) that are left untouched underneath it
  (`email-editor/style-panel/borders.ts`), because they are what makes the exported CSS a per-edge
  shorthand Outlook honours. **Collapse the presentation, not the model.** State the cost where it is
  paid: here it is one style and one colour for the whole box, which every shipped block and default
  template on the email path lives within.
- **NAME A SECTOR FOR WHAT AN AUTHOR IS DOING, NOT FOR THE CSS FAMILY IT HOLDS.** `Dimension` and
  `Typography` are a stylesheet's words; the author is asking about "the spacing" and "the text".
  Renaming a GrapesJS sector means overriding the LOCALE, not the model — `Sector.getName()` reads
  `i18n['styleManager.sectors.<id>']` first, so a `set('name', …)` alone succeeds, reports the new
  string from `get('name')`, and still renders the old one. Same trap as its `-sub` property labels.
- **Money is right-aligned in a COLUMN and left-aligned in a FORM.** A shared decimal point is what
  makes a column of figures comparable; a form field is read left-to-right with its label, and
  right-aligning it pushes the digits away from the `£` that qualifies them. `MoneyInput` defaults
  to left and takes `align="right"` for a table cell. `Money`/`splitGBP` (display) stay as they are.
- **An amount carries the symbol from `ref.currencies`; the ISO code is a column.** `$` beside the
  figure, `USD` in the currency column — as a `CurrencyBadge`, which puts the country's flag disc
  before the code. Intl's `en-GB` currency style prints `US$` / `A$` / `CA$`,
  which is not the symbol the reference table holds. `formatMoney` / `Money` replace that glyph
  with the seeded one. A code the seed does not list falls back to a narrow symbol, which still
  does not say `US$`. Account and client figures are in that row's base currency, not always sterling.
  A transaction booked in another currency keeps that figure, and adds a muted second line:
  the same amount at the custodian's own rate, in the account currency, with the pence set
  back as on every other figure. The line is absent when the currencies match, the amount is
  zero, or the custodian gave no rate.
- **Grouping while typing needs caret arithmetic, which is why it was skipped the first time.**
  Inserting a separator moves every character after it, so a digit typed mid-figure lands in the
  wrong place unless the caret is re-anchored on the count of significant characters before it
  (digits and the decimal point — separators move, so they cannot be the anchor). That logic is
  extracted to `money-input/format.ts` and unit-tested, like `data-table/sort.ts`. It is worth the
  work: an unseparated `1234500` is misread by a factor of ten, and it is misread _while it is being
  typed_, not after.

## Flagged, deliberately not changed (product decisions)

- `$lib/demo` fixture data shown on real client surfaces (client search, transfer wrappers,
  adviser page, portfolio fallback-on-outage, admin/platform search bars) — needs a
  product call on demo mode vs production behaviour.
- Client `share/[token]` hard-codes a "FCA regulated" footer line.
- Tag editing UI exists but has no backend; the dialog is now honest about it.

## Listing pagination

- Every user-facing row listing pages at **25 rows**. `@graphene/ui/book/listing::PAGE_SIZE`
  is the frontend source of truth and `app.listing.DEFAULT_LIMIT` is its API twin.
- Compact surfaces do not invent smaller pages. Their row body owns vertical scrolling
  (`overflow-y-auto` / `DataTable scroll`) while the toolbar, filters and pager stay fixed.
- Every listing footer uses `PaginationStrip` (or the query-shaped `Pagination` wrapper): range
  left, page controls right, export last. Local arrays and URL-backed pages share that component;
  they do not hand-roll Previous/Next buttons or centre the pager.
- Export walkers, provider synchronisation batches, SQL consoles and search suggestion caps are
  processing limits rather than visible row pagination and do not use this value.

## Sortable table headers

A sortable column **always shows its affordance**: the active column a solid `ArrowUp`/`ArrowDown`,
every other sortable column a dimmed `ChevronsUpDown` (`opacity-30`, `group-hover:opacity-70`).
Non-sortable columns render plain text and no `aria-sort`.

The chevron used to be `opacity-0` until the header was hovered, so a sortable column and a fixed
one looked identical and "what can I sort by" was a question you answered by sweeping the header
row — on a touch screen, not at all. A table's DEFAULT sort already rendered its arrow on first
paint, because `DataTable`'s `active` test is only `sort?.key === column.key`; the gap was never the
default, it was the columns beside it. `sort-indication.ssr.test.ts` pins both halves.

## Listing search boxes

A search box is **`@graphene/ui/book/listing::SEARCH_BOX_CLASS`** — full width where the row
stacks, 24rem from `sm` up. `ListingToolbar` applies it, and a search box that sits outside the
toolbar applies it too.

It is one constant because the cap kept being decided per call site, and the boxes outside the
toolbar never got it: `flex-1` on a page the shell does not cap runs the box the whole width of a
wide monitor, so a one-word query sits in a metre of empty input and the controls beside it are
pushed to the far edge. A search box is not a form field — it does not want the row it is in.
