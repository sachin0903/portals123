# The email chrome

`apps/api/app/email/shell.py` no longer writes the chrome's markup. It comes from the three
components here, rendered by `better-svelte-email` into `apps/api/app/email/chrome_generated.py`:

| Component              | Emits                                                                                                |
| ---------------------- | ---------------------------------------------------------------------------------------------------- |
| `chrome.svelte`        | the card — outer table, accent bar, content cell, slots for masthead / heading / body / CTA / footer |
| `chrome-cta.svelte`    | the bulletproof button (the paste-the-link sibling stays in Python)                                  |
| `chrome-footer.svelte` | the footer's own table                                                                               |

`moon run ui:build-email-chrome` regenerates; `ui:check-email-chrome` fails when the committed
artefact is stale, the way the generated api-client's drift check does.

## Why a build step

The send path is Python and the kit is a Svelte renderer. Rendering at send time would put a Node
process between a firm and its client's inbox, so the markup is rendered once, here, and checked in.
`vite`'s `ssrLoadModule` is what compiles the components — plain node answers
`ERR_UNKNOWN_FILE_EXTENSION` on a `.svelte` file.

## What adoption cost, measured

Every Outlook-critical property survived: the button's pill on the `<td>` (Word ignores padding on
an inline `<a>`), the `cta` class the `MSO_CTA_INK` conditional hooks, `width="600"` beside
`max-width:600px`, the paste-the-link fallback, the footer hairline, Arial-first.

Three things did change, and they are what to look at in review:

1. **Six tables where the Python emitted three.** The kit has no primitive that renders a bare
   `<tr>`, so a table cannot carry sibling rows and every row becomes its own table. Word's table
   handling is why the Python kept the nesting shallow — this was the real cost, and it was checked
   in a real inbox (Sep 2026): it renders correctly. The rounded button corners in that render mean
   a browser-engine Outlook, so classic Outlook for Windows — which draws HTML with Word and ignores
   `border-radius` — is the one engine still unverified.
2. **`Email card row`, `Button row` and `Footer row` lost their editor names**, for the same reason:
   an attribute cannot be put on a `<tr>` the kit owns. `Card body` is new (the extra nesting's
   cell). An unnamed row falls back to the editor's own component vocabulary, so the layer tree
   still reads — 'Table row' rather than 'Accent bar row'.
3. **The footer carries its own table.** It used to be a bare `<tr>` spliced into the card's open
   `<tbody>`; with one table per row that `<tr>` landed inside a `<td>`, which renders only because
   an HTML parser hoists it — and Word's is the least likely to hoist it the same way.

`Row` and `Column` are used throughout and `Section`/`Container`/`Button` deliberately are not: those
three put padding on a `<table>` where Word honours it on the cell, merge their own `max-width:37.5em`
with ours so two conflicting declarations ship, and style the anchor rather than the cell. `Row` and
`Column` spread `...restProps`, which is what lets the widths, the class and `data-gjs-name` land
where they must. `chrome.svelte`'s header carries the full reasoning.
