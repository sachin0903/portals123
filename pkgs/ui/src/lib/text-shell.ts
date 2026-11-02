/**
 * The one description of how long text behaves in a box that cannot grow.
 *
 * A client's name, a fund's name, a filename, an adviser's email — none of them has a length we
 * control, and every one of them lands somewhere with a fixed width: a table column, a 16rem
 * sidebar, a 360px aside, a 24rem dialog title, a 356px toast. The choice at each of those places
 * is the same one: the TEXT gives way, or the BOX does. This file is that choice, made once.
 *
 * It exists because `truncate` was applied 157 times across `pkgs/ui` and mostly did nothing. Three
 * ways it silently fails, all of them present in this codebase before this file:
 *
 *  1. NO WIDTH BOUND. `truncate` is `overflow:hidden` + `text-overflow:ellipsis` + `nowrap`. With no
 *     ceiling there is nothing to overflow, so the box grows instead and the class is inert. Every
 *     `<td>` already carries `whitespace-nowrap` and the table is `table-layout:auto`, so a column
 *     is exactly as wide as its longest value — one 89-character fund name made every row that wide.
 *
 *  2. `min-w-0` ON THE WRONG LEVEL. A flex item's automatic minimum size is its min-content width,
 *     which for a nowrap string is the WHOLE string — so the item refuses to shrink and either
 *     overflows or squashes its sibling. `flex-1` does NOT fix this: `flex: 1 1 0%` sets the basis,
 *     not the minimum. The `min-w-0` has to be on the item that truncates, not on its container.
 *
 *  3. A GENUINELY INLINE BOX. `overflow` does not apply to a non-replaced inline box, so
 *     `<span class="truncate">` inside a table cell is a no-op. (Inside a flex or grid container it
 *     works, because the item is blockified — which is why some of these looked fine and some did
 *     not, from identical-looking markup.)
 *
 * And a fourth shape truncation is simply the wrong answer for: an email, a filename or a mono id
 * has NO break opportunity (`_` is not one, nor is `@`), so it overflows a bounded box even where
 * wrapping would be welcome. That is what `WRAP_NAME` is for.
 *
 * THE RULE THAT MATTERS MOST: anything wearing a truncate preset also carries `title`. Clipping
 * without it does not shorten the value, it HIDES it — and a name you cannot even hover is worse
 * than a wide column. Only three files got this right before this one: `audit/event-row.svelte`,
 * `select-menu.svelte` and `multi-select.svelte`.
 *
 * Places that already solved this correctly, so they are not "fixed" back into the bug:
 *   - `select-menu.svelte` — the clearest existing write-up of shapes 2 and 3.
 *   - `multi-select.svelte` — `min-w-0 shrink` overriding Badge's own `shrink-0`, without which the
 *     chip keeps its full intrinsic width and the truncate inside never fires.
 *   - `document-preview-sheet.svelte` — a drawer title done right: `min-w-0` wrapper, `truncate`
 *     title, `shrink-0` on both flanking buttons.
 *   - `document-detail.svelte` — argues `break-all` over truncation for a document's uid, and is
 *     right to: half an id is not a reference anyone can act on.
 */

/* -------------------------------------------------------------------------------------------------
 * Containment — how the text yields
 * ---------------------------------------------------------------------------------------------- */

/** A name that is a FLEX OR GRID ITEM. The `min-w-0` is on the item, which is the whole point —
 *  see failure 2 above. Pair with `shrink-0` on the siblings that must keep their size. */
export const TRUNCATE_ROW = 'min-w-0 truncate';

/** A name with no flex parent to lean on — a table cell, a bare span. `block` is what makes
 *  `overflow` apply at all (failure 3); the caller supplies the ceiling. */
export const TRUNCATE_BLOCK = 'block truncate';

/** A person, firm or file name in a bounded box where CLIPPING would be wrong — a heading, a dialog
 *  title, a toast. `overflow-wrap: anywhere` breaks an unbreakable token AND lets a flex item shrink
 *  to fit, which `break-words` does not. Prefer this over `break-all` for anything a human reads:
 *  `break-all` shreds the readable part of an email. */
export const WRAP_NAME = 'wrap-anywhere';

/** A uid, reference or ISIN, where breaking mid-token is correct because there is no word to keep
 *  whole. Not for names — see `WRAP_NAME`. */
export const WRAP_TOKEN = 'break-all';

/* -------------------------------------------------------------------------------------------------
 * Affordance — how a name says it is a way out
 * ---------------------------------------------------------------------------------------------- */

/**
 * A name that is a LINK TO THAT RECORD — a client, an account, the person a thread is with.
 *
 * One definition because these names are the same kind of thing wherever they appear, and a reader
 * scanning a listing learns the affordance once. Written out per call site it had already drifted
 * into two looks inside a single change: `hover:text-brand` in the transfers and contributions
 * workbenches, `hover:text-foreground` in documents, look-through, tasks and the client record —
 * so the same client name invited a click differently depending on which screen you met it on.
 *
 * REST STATE IS DELIBERATELY ABSENT. These sit in dense tables where colouring every linked name
 * would leave a column of blue and bury the one value the reader is actually hunting for; the row
 * carries its own hover, and the brand colour plus the underline arrive together on hover to say
 * "this one, and it goes somewhere". So this class sets no colour of its own: the call site keeps
 * whatever it had (`text-muted-foreground` on a sub-line, nothing on a primary name) and this adds
 * only what changes under the cursor.
 *
 * Compose it with the containment and ceiling presets rather than instead of them — a linked name
 * in a table cell is `{CELL_NAME} {LINK_RECORD}`, and it still needs its `title`.
 */
export const LINK_RECORD = 'hover:text-brand underline-offset-2 hover:underline';

/* -------------------------------------------------------------------------------------------------
 * Ceilings — how much room the text gets first
 *
 * These are CEILINGS, NOT HAIRCUTS. Sized off the real book (measured over the committed Seccl
 * extract) so that for most of them the cap never fires on ordinary data and only catches the
 * outlier that would otherwise blow out the row. Longest real values at the time of writing:
 * fund name 89 chars, model name 50, account model 25, adviser 19. So only the instrument ceiling
 * is load-bearing day to day; the rest are guard rails.
 *
 * Named per KIND rather than per size, so two that share a number today can diverge without a
 * sweep, and so a call site says what it is showing rather than how wide it happens to be.
 * ---------------------------------------------------------------------------------------------- */

/** The identity column of a listing (Account, Client, Date). Takes the LEFTOVER width instead of a
 *  fixed ceiling, so it is the column that gives way when the table is squeezed. `max-w-0` looks
 *  wrong and is not: under `table-layout:auto` it is the hint that lets the cell shrink below its
 *  content, and `w-full` then claims whatever the fixed columns did not use. */
export const CELL_SPINE = 'w-full max-w-0';

/** A person, organisation or account name. */
export const CELL_NAME = 'max-w-[14rem] truncate';

/** Thirty characters in the transfer and contribution queues, then an ellipsis. The uncut
 *  value is what the tooltip shows. A width ceiling is not this: a narrow 32-character name
 *  fits in 30ch and would otherwise display whole. */
export function clipName(value: string, limit = 30): { text: string; full?: string } {
	if (value.length <= limit) return { text: value };
	return { text: `${value.slice(0, limit - 1)}…`, full: value };
}

/** An adviser name, or a joined list of them — the widest thing in several listings, because
 *  `advisers.join(', ')` has no upper bound. */
export const CELL_ADVISER = 'max-w-[12rem] truncate';

/** An investment model name. */
export const CELL_MODEL = 'max-w-[16rem] truncate';

/** An instrument, fund or asset name. The one ceiling that earns its keep on ordinary data. */
export const CELL_INSTRUMENT = 'max-w-[20rem] truncate';

/** Free text we did not author — a description, a provider name, an error string. */
export const CELL_TEXT = 'max-w-[18rem] truncate';

/** A raw value with no shape at all: a URL, a warehouse cell, a SQL result. Widest ceiling, because
 *  these are read rather than scanned, and they are the ones that overflow without bound. */
export const CELL_RAW = 'max-w-[22rem] truncate';
