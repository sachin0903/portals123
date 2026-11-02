/**
 * The arithmetic behind the PDF viewer's sizing: how a page is fitted to the pane, what the user's
 * zoom does on top of that, how big the backing bitmap is allowed to get, and where the scroll
 * position has to land so a zoom doesn't move the reader.
 *
 * Split out of the viewer for the same reason `pdf-search.ts` was: it is the part with judgement in
 * it and no DOM, and every one of these numbers is one an off-by-a-little in silently degrades the
 * page rather than breaking it — a blurry canvas, a page that jumps to a different paragraph, a
 * bitmap the browser quietly refuses to allocate. None of those announce themselves, so they are
 * pinned here instead.
 *
 * Two things the viewer relies on and this module encodes:
 *
 *  1. **Zoom is a multiplier over the FIT, not an absolute size.** 100% means "as wide as the pane
 *     allows, up to a readable maximum" — which is the size a reader actually wants a document to
 *     open at. An absolute 100% (the PDF's own 72dpi print size) would render an A4 page NARROWER
 *     than the fit on any normal pane, so a control offering it would read as broken. There is
 *     therefore one reset, and "fit the width" and "100%" are the same act.
 *  2. **The fit cap is about reading, the bitmap cap is about memory**, and they are separate
 *     numbers. Fitting stops at `MAX_PAGE_WIDTH` because a longer line of text is harder to track;
 *     the backing store stops at a pixel budget because a page at 300% with a 2× backing store is
 *     hundreds of megabytes of bitmap for a paragraph someone is reading.
 */

/** Widest a fitted page is allowed to be, in CSS px. Past roughly A4-and-a-bit a line of text is
 *  too long to track, and upscaling the render buys nothing. Zoom is how a reader uses a wider
 *  pane; the fit stays readable. */
export const MAX_PAGE_WIDTH = 860;
/** Narrowest a fitted page may be. A pane thinner than this gets a page that overflows and scrolls
 *  sideways rather than an unreadable stamp. */
export const MIN_PAGE_WIDTH = 240;

/** The zoom stops, in order. Discrete steps rather than a free-running multiplier: every stop is a
 *  round percentage a reader can recognise, and a step always changes the page visibly. */
export const ZOOM_STOPS = [0.5, 0.75, 1, 1.25, 1.5, 2, 2.5, 3] as const;
/** The ends of the scale, stated independently of the stops so a test can pin that the two agree —
 *  a stop list that grew past its own bounds would clamp mid-list and the last step would do
 *  nothing. */
export const MIN_ZOOM = 0.5;
export const MAX_ZOOM = 3;

/** Cap the backing-store multiplier: past 2× the extra pixels are invisible and the bitmap is four
 *  times the size of a 1× one. */
export const MAX_DPR = 2;
/** Pixel budget for one page's canvas. A canvas is 4 bytes a pixel, so this is ~48 MB — and with
 *  the viewer painting a few pages either side of the viewport, a handful of those at once. It is
 *  also the region where mobile Safari starts refusing to allocate at all. */
export const MAX_CANVAS_PIXELS = 12_000_000;
/** Longest edge any single canvas may have. Browsers cap this independently of the area (Chrome at
 *  16384, others lower); a tall page at high zoom hits it long before the area budget. */
export const MAX_CANVAS_DIM = 8_192;

/** A finite, positive number or the fallback. Every input here comes from a layout measurement or a
 *  page dictionary, both of which can hand back 0, NaN or nothing at all mid-teardown. */
function positive(value: number, fallback: number): number {
	return Number.isFinite(value) && value > 0 ? value : fallback;
}

/**
 * The scale at which the widest page fills the available width, capped for readability.
 *
 * The WIDEST page, not each page: a document can mix page sizes, and fitting each one to the pane
 * independently would normalise them all to the same width and destroy their relative sizes — the
 * landscape sheet would look no wider than the portrait one.
 */
export function fitScale(widestPageWidth: number, availableWidth: number): number {
	const widest = positive(widestPageWidth, 0);
	if (!widest) return 1; // nothing measured yet; 1 is honest and harmless
	const available = positive(availableWidth, MAX_PAGE_WIDTH);
	const target = Math.max(MIN_PAGE_WIDTH, Math.min(available, MAX_PAGE_WIDTH));
	return target / widest;
}

/** Hold a zoom multiplier inside the scale. A non-finite one resets to the fit rather than
 *  propagating NaN into a canvas size, which renders nothing and reports nothing. */
export function clampZoom(zoom: number): number {
	if (!Number.isFinite(zoom)) return 1;
	return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom));
}

/**
 * The next stop above (`1`) or below (`-1`) the current zoom.
 *
 * Written against the current VALUE rather than an index into the stops, so it is still correct for
 * a zoom that isn't a stop — a stop list edited in a later change, or a value restored from
 * elsewhere, must still step somewhere sensible instead of sticking.
 */
export function stepZoom(zoom: number, direction: 1 | -1): number {
	const from = clampZoom(zoom);
	// A tolerance, because these are floating-point: at 1.25 exactly, `> from` on a stop that is
	// 1.25 + 1e-16 would step to itself and the button would look broken.
	const epsilon = 1e-6;
	if (direction > 0) {
		for (const stop of ZOOM_STOPS) if (stop > from + epsilon) return stop;
		return MAX_ZOOM;
	}
	for (let i = ZOOM_STOPS.length - 1; i >= 0; i--) {
		const stop = ZOOM_STOPS[i];
		if (stop !== undefined && stop < from - epsilon) return stop;
	}
	return MIN_ZOOM;
}

/** The scale to render at: the fit, multiplied by the reader's zoom. */
export function renderScale(fit: number, zoom: number): number {
	return positive(fit, 1) * clampZoom(zoom);
}

/** The zoom as a reader sees it. 100% is the fit — see the module header on why there is no second,
 *  absolute percentage. */
export function zoomLabel(zoom: number): string {
	return `${Math.round(clampZoom(zoom) * 100)}%`;
}

/**
 * The backing-store multiplier for one page's canvas: the device's pixel ratio, capped at `MAX_DPR`
 * and then reduced as far as the canvas budget demands.
 *
 * Reducing it is not a nicety. Zoom multiplies the CSS page size, and the bitmap is that squared:
 * an A4 page fitted to 860 px is a 4 MP bitmap at 2×, and the same page at 300% is 38 MP — which
 * some browsers allocate, some refuse outright (leaving a blank sheet with nothing logged), and all
 * of them charge for. Downsampling costs a little sharpness on a page that is already enlarged well
 * past legibility; refusing to draw costs the page. Deliberately NOT floored at 1: if the CSS page
 * alone exceeds the budget, a soft render is the only render available.
 */
export function backingScale(
	cssWidth: number,
	cssHeight: number,
	devicePixelRatio: number
): number {
	const w = positive(cssWidth, 1);
	const h = positive(cssHeight, 1);
	const dpr = Math.min(positive(devicePixelRatio, 1), MAX_DPR);
	const byArea = Math.sqrt(MAX_CANVAS_PIXELS / (w * h));
	const byEdge = MAX_CANVAS_DIM / Math.max(w, h);
	return Math.min(dpr, byArea, byEdge);
}

/**
 * Where the scroll position sits within the anchor page, as a fraction of that page's height.
 *
 * This is the whole of "a zoom must not move the reader": scaling changes every page's height, so a
 * scrollTop kept as-is lands on a different part of a different page — on a long pack, somewhere
 * else entirely. The fraction is deliberately unclamped and may be negative (the anchor's top is
 * below the viewport top) so the gap between two pages round-trips as itself.
 */
export function anchorFraction(scrollTop: number, sheetTop: number, sheetHeight: number): number {
	if (!Number.isFinite(scrollTop) || !Number.isFinite(sheetTop)) return 0;
	const height = positive(sheetHeight, 0);
	if (!height) return 0;
	return (scrollTop - sheetTop) / height;
}

/** The scrollTop that puts a fraction of the way into a resized page back where it was. */
export function restoreScrollTop(sheetTop: number, sheetHeight: number, fraction: number): number {
	if (!Number.isFinite(sheetTop) || !Number.isFinite(sheetHeight) || !Number.isFinite(fraction)) {
		return 0;
	}
	return Math.max(0, sheetTop + fraction * sheetHeight);
}
