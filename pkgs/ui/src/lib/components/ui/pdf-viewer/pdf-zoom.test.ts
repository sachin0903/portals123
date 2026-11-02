import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import {
	MAX_CANVAS_DIM,
	MAX_CANVAS_PIXELS,
	MAX_DPR,
	MAX_PAGE_WIDTH,
	MAX_ZOOM,
	MIN_PAGE_WIDTH,
	MIN_ZOOM,
	ZOOM_STOPS,
	anchorFraction,
	backingScale,
	clampZoom,
	fitScale,
	renderScale,
	restoreScrollTop,
	stepZoom,
	zoomLabel
} from './pdf-zoom';

/**
 * Everything here fails QUIETLY in the viewer if it drifts — a soft canvas, a page that jumps to
 * somewhere else in the document on a zoom, a bitmap the browser declines to allocate and reports
 * nothing about. That is why the arithmetic is out here rather than inline in the component.
 */

/** A4 at 72dpi, the size nearly every document in the book actually is. */
const A4_WIDTH = 595;
const A4_HEIGHT = 842;

describe('fitScale', () => {
	it('fits the widest page to the pane', () => {
		expect(fitScale(600, 300)).toBeCloseTo(0.5);
	});

	it('stops widening at the readable cap, however wide the pane', () => {
		// The pane is 2000px; the page stays at 860. A reader on a big monitor gets white margin and
		// the zoom control, not a 2000px line of text.
		expect(fitScale(A4_WIDTH, 2000)).toBeCloseTo(MAX_PAGE_WIDTH / A4_WIDTH);
		expect(fitScale(A4_WIDTH, 2000)).toBe(fitScale(A4_WIDTH, 5000));
	});

	it('floors at a minimum width rather than shrinking to a stamp', () => {
		expect(fitScale(A4_WIDTH, 40)).toBeCloseTo(MIN_PAGE_WIDTH / A4_WIDTH);
	});

	it('survives a page that has not been measured yet', () => {
		// Mid-teardown the page dictionary can be gone; a NaN scale would size a canvas to nothing
		// and render a blank sheet with nothing logged.
		expect(fitScale(0, 800)).toBe(1);
		expect(fitScale(Number.NaN, 800)).toBe(1);
	});

	it('treats an unmeasured pane as a full-width one', () => {
		expect(fitScale(A4_WIDTH, Number.NaN)).toBeCloseTo(MAX_PAGE_WIDTH / A4_WIDTH);
		expect(fitScale(A4_WIDTH, 0)).toBeCloseTo(MAX_PAGE_WIDTH / A4_WIDTH);
	});
});

describe('the zoom scale', () => {
	it('has stops that agree with its stated bounds', () => {
		// Stated separately on purpose: a stop list that grew past its own bounds would be clamped
		// mid-list, and the last step would silently do nothing.
		expect(ZOOM_STOPS[0]).toBe(MIN_ZOOM);
		expect(ZOOM_STOPS[ZOOM_STOPS.length - 1]).toBe(MAX_ZOOM);
		expect([...ZOOM_STOPS]).toEqual([...ZOOM_STOPS].sort((a, b) => a - b));
		expect(ZOOM_STOPS).toContain(1); // the fit has to be reachable by stepping
	});

	it('spans 50% to 300%', () => {
		expect(zoomLabel(MIN_ZOOM)).toBe('50%');
		expect(zoomLabel(MAX_ZOOM)).toBe('300%');
	});
});

describe('clampZoom', () => {
	it('holds a zoom inside the scale', () => {
		expect(clampZoom(0.1)).toBe(MIN_ZOOM);
		expect(clampZoom(12)).toBe(MAX_ZOOM);
		expect(clampZoom(1.25)).toBe(1.25);
	});

	it('resets a non-finite zoom to the fit', () => {
		// Infinity is treated as garbage rather than clamped to the top of the scale: it can only
		// arrive from arithmetic that has already gone wrong, and the fit is the safe answer.
		expect(clampZoom(Number.NaN)).toBe(1);
		expect(clampZoom(Number.POSITIVE_INFINITY)).toBe(1);
		expect(clampZoom(Number.NEGATIVE_INFINITY)).toBe(1);
	});
});

describe('stepZoom', () => {
	it('steps to the neighbouring stop', () => {
		expect(stepZoom(1, 1)).toBe(1.25);
		expect(stepZoom(1, -1)).toBe(0.75);
	});

	it('stops at each end instead of walking off it', () => {
		expect(stepZoom(MAX_ZOOM, 1)).toBe(MAX_ZOOM);
		expect(stepZoom(MIN_ZOOM, -1)).toBe(MIN_ZOOM);
	});

	it('moves off a value that is not a stop, in both directions', () => {
		// Nothing sets a non-stop zoom today, but a step that stuck would be a dead button.
		expect(stepZoom(1.1, 1)).toBe(1.25);
		expect(stepZoom(1.1, -1)).toBe(1);
	});

	it('walks the whole scale end to end', () => {
		let up = MIN_ZOOM;
		for (let i = 0; i < ZOOM_STOPS.length * 2; i++) up = stepZoom(up, 1);
		expect(up).toBe(MAX_ZOOM);
		let down = MAX_ZOOM;
		for (let i = 0; i < ZOOM_STOPS.length * 2; i++) down = stepZoom(down, -1);
		expect(down).toBe(MIN_ZOOM);
	});
});

describe('renderScale', () => {
	it('multiplies the fit by the zoom', () => {
		expect(renderScale(1.4, 1.5)).toBeCloseTo(2.1);
		expect(renderScale(1.4, 1)).toBeCloseTo(1.4);
	});

	it('clamps the zoom rather than trusting it', () => {
		expect(renderScale(2, 99)).toBeCloseTo(2 * MAX_ZOOM);
	});

	it('falls back to the zoom alone when the fit is not a number', () => {
		expect(renderScale(Number.NaN, 1.5)).toBe(1.5);
		expect(renderScale(0, 1.5)).toBe(1.5);
	});
});

describe('zoomLabel', () => {
	it('reads as a whole percentage', () => {
		expect(zoomLabel(1)).toBe('100%');
		expect(zoomLabel(0.75)).toBe('75%');
		expect(zoomLabel(2.5)).toBe('250%');
	});
});

describe('backingScale', () => {
	it('caps at 2× however dense the display', () => {
		expect(backingScale(A4_WIDTH, A4_HEIGHT, 3)).toBe(MAX_DPR);
		expect(backingScale(A4_WIDTH, A4_HEIGHT, 4)).toBe(MAX_DPR);
	});

	it('leaves a 1× display at 1×', () => {
		expect(backingScale(A4_WIDTH, A4_HEIGHT, 1)).toBe(1);
	});

	it('gives a fitted A4 page the full 2×', () => {
		// 860 × 1217 at 2× is a 4.2 MP bitmap — well inside the budget, and the case that has to stay
		// crisp because it is what every document opens at.
		expect(backingScale(MAX_PAGE_WIDTH, 1217, 2)).toBe(MAX_DPR);
	});

	it('reduces the backing store rather than asking for a bitmap the browser may refuse', () => {
		// A4 fitted to 860 and then zoomed to 300%: 2580 × 3651 CSS px. At 2× that is 38 MP, which
		// some browsers allocate, some refuse outright, and all of them charge for.
		const w = MAX_PAGE_WIDTH * 3;
		const h = 1217 * 3;
		const scale = backingScale(w, h, 2);
		expect(scale).toBeLessThan(MAX_DPR);
		expect(w * scale * h * scale).toBeLessThanOrEqual(MAX_CANVAS_PIXELS + 1);
		// Still oversampled: the page is enlarged, not degraded.
		expect(scale).toBeGreaterThan(1);
	});

	it('honours the longest-edge cap for a tall page, not just the area', () => {
		// A banner-shaped page: the area budget alone would allow 2×, the edge cap does not.
		const scale = backingScale(200, 7000, 2);
		expect(Math.max(200, 7000) * scale).toBeLessThanOrEqual(MAX_CANVAS_DIM + 1);
	});

	it('goes below 1× when the CSS page alone exceeds the budget', () => {
		// A soft render is the only render available here; refusing would lose the page entirely.
		const scale = backingScale(6000, 8000, 2);
		expect(scale).toBeLessThan(1);
		expect(scale).toBeGreaterThan(0);
	});

	it('never returns a non-finite multiplier for a degenerate page', () => {
		for (const s of [
			backingScale(0, 0, 2),
			backingScale(Number.NaN, A4_HEIGHT, 2),
			backingScale(A4_WIDTH, A4_HEIGHT, Number.NaN)
		]) {
			expect(Number.isFinite(s)).toBe(true);
			expect(s).toBeGreaterThan(0);
		}
	});
});

describe('keeping the reader’s place across a rescale', () => {
	it('round-trips a position through a page that changed height', () => {
		// Half way down page 3, then zoom in: the same half-way point, at the page's new height.
		const before = anchorFraction(1000, 800, 400);
		expect(before).toBeCloseTo(0.5);
		expect(restoreScrollTop(1600, 800, before)).toBeCloseTo(2000);
	});

	it('round-trips a viewport sitting above the anchor page', () => {
		// Deliberately unclamped: the gap between two pages has to come back as itself, or a zoom
		// taken from the seam between pages lands somewhere else.
		const fraction = anchorFraction(780, 800, 400);
		expect(fraction).toBeLessThan(0);
		expect(restoreScrollTop(800, 400, fraction)).toBeCloseTo(780);
	});

	it('does not divide by an unmeasured page height', () => {
		expect(anchorFraction(500, 100, 0)).toBe(0);
		expect(anchorFraction(500, 100, Number.NaN)).toBe(0);
	});

	it('never asks for a negative scrollTop', () => {
		expect(restoreScrollTop(0, 400, -2)).toBe(0);
	});

	it('gives up on a non-finite measurement instead of propagating it', () => {
		expect(anchorFraction(Number.NaN, 100, 400)).toBe(0);
		expect(restoreScrollTop(Number.NaN, 400, 0.5)).toBe(0);
		expect(restoreScrollTop(100, 400, Number.NaN)).toBe(0);
	});
});

/**
 * The viewer's half of the contract, as a source scan.
 *
 * A sheet's box, the canvas painted into it and its text layer's `--total-scale-factor` are three
 * views of ONE number. Letting them disagree does not break the picture — it produces a working one
 * whose text selection and every search highlight sit a few millimetres out of place, which nothing
 * here or in the SSR suite can observe (it needs a laid-out canvas and a real pdf.js text layer).
 * So the shape that keeps them in step is pinned instead: the layer reads the SLOT, which is the
 * object a rescale updates, and only two places in the file are allowed to resize a sheet.
 */
describe('pdf-viewer.svelte — the sizing invariant', () => {
	const source = readFileSync(new URL('./pdf-viewer.svelte', import.meta.url), 'utf8');

	it('takes the text layer’s scale from the slot, not from a captured local', () => {
		// A local `scale` read at render time is the drift: it is right until the first zoom.
		expect(source).toContain("setProperty('--total-scale-factor', String(slot.scale))");
	});

	it('resizes a sheet in exactly two places — the first layout and the rescale', () => {
		// Anywhere else would move a page without rebuilding the text layer over it.
		expect(source.match(/style\.height = /g) ?? []).toHaveLength(2);
		expect(source.match(/style\.width = /g) ?? []).toHaveLength(2);
	});

	it('checks a paint against the layout generation, never the document one', () => {
		// `token` is the DOCUMENT generation and `buildIndex` waits on it: bumping it for a zoom
		// aborts the index and leaves the find bar reading "reading…" for the rest of the session.
		expect(source).toContain('mine !== paintGen');
		expect(source).not.toContain('paint(el, token)');
		expect(source).not.toContain('paint(sheet, token)');
	});
});
