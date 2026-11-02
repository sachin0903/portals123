/**
 * The masonry grid's span arithmetic — the ONE place the client portal and the branding studio's
 * layout builder agree on how tall a block is.
 *
 * The portal lays its blocks out over 8px micro-rows and gives each cell a span equal to its own
 * measured content height (portal-page.svelte). The builder used to lay the same blocks out over
 * 4rem tracks using the AUTHORED `h`, a number the portal discarded — so with `grid-auto-flow: row
 * dense` back-filling holes on both sides from different heights, the two pictures disagreed about
 * which row a block lands on. That is the "the customiser isn't representative" defect.
 *
 * Both sides now speak micro-rows: the portal reports what it measured (PortalGridMetrics) and the
 * builder draws a scaled miniature from the same numbers, falling back to the authored floor for
 * any block the preview has not reported (one just dragged in, or a preview that hasn't rendered).
 */

/** The portal's micro-row track height. Small enough that a natural height rounds up to within
 *  8px of itself, large enough that a page of blocks stays well under the grid's track budget. */
export const MASONRY_ROW_PX = 8;

/** One unit of authored `h`, in px — the same 6rem the non-masonry fallback uses as its
 *  min-height, so the authored floor means the same thing whether or not JS is running. */
export const AUTHORED_UNIT_PX = 96;

/** Micro-rows per unit of authored `h`. */
export const ROWS_PER_UNIT = AUTHORED_UNIT_PX / MASONRY_ROW_PX;

/** The grid's column gap at the default density, used when no live value can be read. */
export const DEFAULT_GAP_PX = 24;

/**
 * What the portal reports back about the grid it just laid out. Everything is in the PORTAL's own
 * pixels; a consumer drawing a miniature scales by `gridWidth`.
 */
export interface PortalGridMetrics {
	/** Micro-row span per block key (see blockKeys) — exactly the `--rs` applied to each cell. */
	spans: Record<string, number>;
	/** The grid's content width in px. Zero when unknown. */
	gridWidth: number;
	/** The micro-row track height the spans are expressed in. */
	rowPx: number;
	/** The grid's column gap — the row rhythm baked into every span (row-gap is zero). */
	gapPx: number;
	/**
	 * False when the grid is in its single-column (below `md`) mode. Spans measured there are
	 * full-width heights and say nothing about a 12-column arrangement, so a consumer must not
	 * draw a miniature from them.
	 */
	spanned: boolean;
}

/** The stand-in before any preview has reported: authored heights, no measurements. */
export const NO_METRICS: PortalGridMetrics = {
	spans: {},
	gridWidth: 0,
	rowPx: MASONRY_ROW_PX,
	gapPx: DEFAULT_GAP_PX,
	spanned: false
};

function positive(n: unknown, fallback: number): number {
	return typeof n === 'number' && Number.isFinite(n) && n > 0 ? n : fallback;
}

/** Zero is a legitimate gap (a firm on the tightest density), so it must not read as "unknown". */
function gapOr(n: unknown, fallback: number): number {
	return typeof n === 'number' && Number.isFinite(n) && n >= 0 ? n : fallback;
}

/**
 * The micro-rows an authored `h` claims. This is the FLOOR: an author who sets a block two units
 * high gets at least that much, and content taller than it still wins (see cellSpan). The gap is
 * included because row-gap is zero on the masonry grid — each cell carries its own rhythm.
 */
export function authoredSpan(
	h: number,
	gapPx: number = DEFAULT_GAP_PX,
	rowPx: number = MASONRY_ROW_PX
): number {
	const units = positive(h, 1);
	const gap = gapOr(gapPx, DEFAULT_GAP_PX);
	const row = positive(rowPx, MASONRY_ROW_PX);
	return Math.max(1, Math.ceil((units * AUTHORED_UNIT_PX + gap) / row));
}

/** The micro-rows a measured content height claims, rhythm included. */
export function contentSpan(
	contentPx: number,
	gapPx: number = DEFAULT_GAP_PX,
	rowPx: number = MASONRY_ROW_PX
): number {
	const content =
		typeof contentPx === 'number' && Number.isFinite(contentPx) && contentPx > 0 ? contentPx : 0;
	const gap = gapOr(gapPx, DEFAULT_GAP_PX);
	const row = positive(rowPx, MASONRY_ROW_PX);
	return Math.max(1, Math.ceil((content + gap) / row));
}

/**
 * Is this reading the grid's own last answer coming back, rather than the content's height?
 *
 * The span is computed FROM the measured box and then applied TO the cell that contains it, so any
 * route by which the cell's height reaches the box closes a loop: span = ceil((inner + gap) / row)
 * makes the cell `inner + gap` tall, a box that fills the cell reports `inner + gap` next time, and
 * the block gains one GAP per frame — 24px a frame, past 147,000px, which is what "the preview
 * scrolls for ever" looks like. The CSS in portal-page.svelte closes the routes we know of; this is
 * the backstop for the ones we do not, because the routes are engine-specific (`height: fit-content`
 * held the box content-sized in Chromium and did not in Firefox, where `fit-content` carries a
 * `stretch` term that resolves against the very height we are about to set).
 *
 * A box that has stretched fills its cell EXACTLY, so exact equality is the signal — not `>=`,
 * which is the ordinary case of content taller than the span it has been given so far and must
 * still be allowed to grow. `sized` keeps the first measurement out of it: before a span has been
 * written the cell is on the CSS default and equality means nothing.
 */
export function isSelfMeasurement(innerPx: number, cellPx: number, sized: boolean): boolean {
	return sized && innerPx > 0 && innerPx === cellPx;
}

/**
 * A cell's span: its measured content, floored by the authored `h`. Raising the floor above the
 * content cannot feed back into the measurement — the measured box is held content-sized by
 * portal-page.svelte's standing rule, and `isSelfMeasurement` catches it if that ever fails.
 */
export function cellSpan(
	contentPx: number,
	h: number,
	gapPx: number = DEFAULT_GAP_PX,
	rowPx: number = MASONRY_ROW_PX
): number {
	return Math.max(contentSpan(contentPx, gapPx, rowPx), authoredSpan(h, gapPx, rowPx));
}

// ── The builder's miniature ──────────────────────────────────────────────────────────────────

/** Shortest a builder tile may render: below this its grip, label and ± steppers clip. */
export const BUILDER_MIN_TILE_PX = 56;

/** The micro-row height that makes one authored unit render at 4rem — the builder's historic
 *  track size, and what it falls back to with no measurements to scale against. */
export const BUILDER_DEFAULT_ROW_PX = 64 / ROWS_PER_UNIT;

/**
 * One builder tile's span. A measured span wins, floored by the authored `h` so the H stepper
 * moves the tile the instant it is clicked rather than a frame later when the preview reports —
 * and so the builder and the portal apply the floor identically.
 */
export function tileSpan(row: { id: string; h: number }, m: PortalGridMetrics): number {
	const floor = authoredSpan(row.h, m.gapPx, m.rowPx);
	const measured = m.spans[row.id];
	return typeof measured === 'number' && Number.isFinite(measured) && measured > 0
		? Math.max(Math.round(measured), floor)
		: floor;
}

/**
 * The builder grid's geometry: the portal's own micro-row, scaled by how much narrower the builder
 * column is. Uniform, so the packing and the proportions BETWEEN tiles are exactly the portal's —
 * the miniature only zooms. The scale is then lifted (never above 1:1) until the shortest tile
 * still has room for its controls; lifting the whole grid rather than clamping one tile is what
 * keeps the packing honest, since a clamped tile would occupy rows the portal never gave it.
 */
export function builderGeometry(
	builderWidth: number,
	m: PortalGridMetrics,
	spans: readonly number[]
): { rowPx: number; gapPx: number } {
	const scaleFrom = (rowPx: number) => ({ rowPx, gapPx: m.gapPx * (rowPx / m.rowPx) });
	if (!m.spanned || m.gridWidth <= 0 || builderWidth <= 0 || m.rowPx <= 0) {
		return scaleFrom(BUILDER_DEFAULT_ROW_PX);
	}
	const scaled = m.rowPx * (builderWidth / m.gridWidth);
	// A tile's visible box is its span minus the rhythm baked into it, so that is what has to
	// clear the floor.
	const usableRows = (spans.length ? Math.min(...spans) : 0) - m.gapPx / m.rowPx;
	const lifted = usableRows > 0 ? Math.max(scaled, BUILDER_MIN_TILE_PX / usableRows) : scaled;
	return scaleFrom(Math.min(lifted, m.rowPx));
}
