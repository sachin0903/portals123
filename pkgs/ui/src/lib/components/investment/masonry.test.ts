import { describe, expect, it } from 'vitest';
import {
	AUTHORED_UNIT_PX,
	BUILDER_DEFAULT_ROW_PX,
	BUILDER_MIN_TILE_PX,
	DEFAULT_GAP_PX,
	MASONRY_ROW_PX,
	NO_METRICS,
	ROWS_PER_UNIT,
	authoredSpan,
	builderGeometry,
	cellSpan,
	contentSpan,
	isSelfMeasurement,
	tileSpan,
	type PortalGridMetrics
} from './masonry.js';

/**
 * The arithmetic both the portal and the studio's layout builder lay out from. The defect these
 * pin: the builder used to position tiles by the authored `h` (4rem tracks) while the portal
 * positioned them by measured content (8px micro-rows) and discarded `h` entirely — two dense-flow
 * grids fed different numbers, so blocks landed on different rows in the two pictures.
 */

/** A metrics reading as the portal reports one from a desktop frame. */
function metrics(over: Partial<PortalGridMetrics> = {}): PortalGridMetrics {
	return { ...NO_METRICS, gridWidth: 1600, spanned: true, ...over };
}

describe('authoredSpan — the floor `h` buys', () => {
	it('is the same 6rem-per-unit the non-masonry fallback uses as its min-height', () => {
		expect(AUTHORED_UNIT_PX).toBe(96);
		expect(ROWS_PER_UNIT).toBe(12);
	});

	it('claims the unit height plus one rhythm gap', () => {
		// 96 + 24 = 120px over 8px rows.
		expect(authoredSpan(1)).toBe(15);
		expect(authoredSpan(2)).toBe(27);
		expect(authoredSpan(6)).toBe(75);
	});

	it('scales with the grid gap it is given (the density axis moves it)', () => {
		expect(authoredSpan(1, 16)).toBe(14);
		expect(authoredSpan(1, 32)).toBe(16);
	});

	it('never returns less than one row, whatever junk arrives', () => {
		expect(authoredSpan(0)).toBe(authoredSpan(1));
		expect(authoredSpan(Number.NaN)).toBe(authoredSpan(1));
		expect(authoredSpan(-4)).toBe(authoredSpan(1));
		expect(authoredSpan(1, 0, 0)).toBeGreaterThanOrEqual(1);
	});
});

describe('contentSpan — what a measured block claims', () => {
	it('rounds a natural height UP so content is never clipped by the track maths', () => {
		expect(contentSpan(300)).toBe(Math.ceil((300 + 24) / 8)); // 41
		expect(contentSpan(301)).toBe(41);
		expect(contentSpan(305)).toBe(42);
	});

	it('bakes the rhythm in, because the masonry grid zeroes row-gap', () => {
		expect(contentSpan(200, 24) - contentSpan(200, 0)).toBe(3);
	});

	it('treats an unmeasured (zero / junk) height as the smallest possible cell', () => {
		expect(contentSpan(0)).toBe(3);
		expect(contentSpan(Number.NaN)).toBe(3);
	});
});

describe('cellSpan — measured content, floored by the authored height', () => {
	it('lets content taller than the floor win', () => {
		// A 400px card in a block authored 1 unit high.
		expect(cellSpan(400, 1)).toBe(contentSpan(400));
		expect(cellSpan(400, 1)).toBeGreaterThan(authoredSpan(1));
	});

	it('lifts a short block to the height its author asked for', () => {
		// A 90px message card authored 3 units high: the floor, not the content.
		expect(cellSpan(90, 3)).toBe(authoredSpan(3));
		expect(cellSpan(90, 3)).toBeGreaterThan(contentSpan(90));
	});

	it('makes H a control that changes something — the defect it fixes', () => {
		const short = cellSpan(90, 1);
		expect(cellSpan(90, 2)).toBeGreaterThan(short);
		expect(cellSpan(90, 3)).toBeGreaterThan(cellSpan(90, 2));
	});

	it('leaves a tall block untouched by H, so raising it cannot re-stretch the page', () => {
		expect(cellSpan(600, 1)).toBe(cellSpan(600, 6));
	});
});

describe('tileSpan — what the builder draws a tile at', () => {
	const row = (id: string, h = 1) => ({ id, h });

	it('uses the preview’s measured span when it has one', () => {
		const m = metrics({ spans: { accounts: 41 } });
		expect(tileSpan(row('accounts'), m)).toBe(41);
	});

	it('falls back to the authored floor for a block the preview has not reported', () => {
		// A block just dragged in, or a hidden one the portal does not render at all.
		expect(tileSpan(row('news', 2), metrics({ spans: { accounts: 41 } }))).toBe(authoredSpan(2));
	});

	it('falls back to the authored floor with no metrics at all', () => {
		expect(tileSpan(row('accounts', 2), NO_METRICS)).toBe(authoredSpan(2));
	});

	it('applies the SAME floor the portal applies, so the H stepper moves the tile at once', () => {
		// The measured span is a frame behind an H bump; the floor closes that gap, and it is the
		// number the portal will report next anyway.
		const m = metrics({ spans: { message: 16 } });
		expect(tileSpan(row('message', 1), m)).toBe(16);
		expect(tileSpan(row('message', 3), m)).toBe(authoredSpan(3));
		expect(tileSpan(row('message', 3), m)).toBe(cellSpan(16 * MASONRY_ROW_PX - DEFAULT_GAP_PX, 3));
	});

	it('ignores a junk or non-positive measurement rather than collapsing the tile', () => {
		for (const bad of [0, -3, Number.NaN, undefined as unknown as number]) {
			expect(tileSpan(row('accounts', 2), metrics({ spans: { accounts: bad } }))).toBe(
				authoredSpan(2)
			);
		}
	});
});

describe('builderGeometry — the miniature’s scale', () => {
	it('falls back to the historic 4rem-per-authored-unit track with nothing to scale against', () => {
		const geo = builderGeometry(0, NO_METRICS, []);
		expect(geo.rowPx).toBe(BUILDER_DEFAULT_ROW_PX);
		// One authored unit still renders at 4rem, so an unmeasured builder looks as it always did.
		expect(ROWS_PER_UNIT * geo.rowPx).toBe(64);
	});

	it('ignores a single-column (below md) reading — its spans are not a 12-column arrangement', () => {
		const geo = builderGeometry(500, metrics({ spanned: false, spans: { a: 40 } }), [40]);
		expect(geo.rowPx).toBe(BUILDER_DEFAULT_ROW_PX);
	});

	it('scales the portal’s own row unit by the builder column’s share of the grid width', () => {
		// 400 of 1600 ⇒ a quarter-size miniature; the shortest tile is tall enough not to lift it.
		const geo = builderGeometry(400, metrics(), [200]);
		expect(geo.rowPx).toBeCloseTo(MASONRY_ROW_PX / 4, 10);
		expect(geo.gapPx).toBeCloseTo(DEFAULT_GAP_PX / 4, 10);
	});

	it('lifts the WHOLE grid so the shortest tile keeps its controls — never one tile alone', () => {
		// A 15-row tile (the h=1 floor) at a quarter scale would be 12px of chrome.
		const spans = [15, 41];
		const geo = builderGeometry(400, metrics(), spans);
		const visible = (span: number) => span * geo.rowPx - geo.gapPx;
		expect(visible(15)).toBeGreaterThanOrEqual(BUILDER_MIN_TILE_PX - 0.001);
		// Uniform: the proportion between the two tiles is exactly the portal's.
		expect(visible(41) / visible(15)).toBeCloseTo(
			(41 * MASONRY_ROW_PX - DEFAULT_GAP_PX) / (15 * MASONRY_ROW_PX - DEFAULT_GAP_PX),
			8
		);
	});

	it('never magnifies past the portal’s own scale', () => {
		const geo = builderGeometry(4000, metrics(), [15]);
		expect(geo.rowPx).toBeLessThanOrEqual(MASONRY_ROW_PX);
		expect(geo.gapPx).toBeLessThanOrEqual(DEFAULT_GAP_PX);
	});

	it('survives a tile shorter than the rhythm it carries', () => {
		// gap 24 over 8px rows is 3 rows; a 3-row span leaves nothing visible to scale to.
		const geo = builderGeometry(400, metrics(), [3]);
		expect(Number.isFinite(geo.rowPx)).toBe(true);
		expect(geo.rowPx).toBeGreaterThan(0);
	});
});

describe('the two grids agree', () => {
	it('a page of mixed natural heights packs to the same rows in both', () => {
		// The reported reality: a tall accounts block beside a short adviser card, with the authored
		// spans (all h=1) saying they are the same height. The builder must use the measurements.
		const reported = { portfolioHeader: 45, accounts: 52, adviser: 20, message: 16 };
		const m = metrics({ spans: reported });
		const rows = [
			{ id: 'portfolioHeader', h: 1 },
			{ id: 'accounts', h: 1 },
			{ id: 'adviser', h: 1 },
			{ id: 'message', h: 1 }
		];
		expect(rows.map((r) => tileSpan(r, m))).toEqual([45, 52, 20, 16]);
		// …and NOT the flat authored spans that produced the mismatch.
		expect(rows.map((r) => authoredSpan(r.h))).toEqual([15, 15, 15, 15]);
	});
});
describe('isSelfMeasurement — the runaway backstop', () => {
	// The loop it exists for: span = ceil((inner + gap) / row) sizes the CELL from the box, so a box
	// that then fills the cell reports back `inner + gap` and the block gains one gap per frame.
	// Observed at 24px a frame, past 147,000px — "the branding preview scrolls for ever".
	it('calls a box that exactly fills its cell our own last answer', () => {
		expect(isSelfMeasurement(600, 600, true)).toBe(true);
	});

	it('lets content genuinely taller than its cell grow', () => {
		// The ordinary case on the way up: the span so far is too small and must be raised. Anything
		// coarser than exact equality (a `>=`) would freeze every block at its first measurement.
		expect(isSelfMeasurement(624, 600, true)).toBe(false);
	});

	it('lets content shorter than its cell shrink', () => {
		expect(isSelfMeasurement(400, 600, true)).toBe(false);
	});

	it('never fires before a span has been written', () => {
		// The first measurement lands while the cell is on the CSS default span, where an equality is
		// a coincidence and refusing it would leave the block at that default for ever.
		expect(isSelfMeasurement(192, 192, false)).toBe(false);
	});

	it('ignores an unmeasured box', () => {
		// A hidden block reports 0, and 0 === 0 must not read as a runaway.
		expect(isSelfMeasurement(0, 0, true)).toBe(false);
	});

	it('stops the runaway at the first repeat rather than climbing', () => {
		// Walk the loop: a stretched box reports its cell's height, and without the guard each pass
		// adds the gap. With it, the second pass is refused and the span never moves again.
		const gap = DEFAULT_GAP_PX;
		let span = cellSpan(300, 1, gap, MASONRY_ROW_PX);
		const climbed: number[] = [];
		for (let i = 0; i < 5; i++) {
			const cellPx = span * MASONRY_ROW_PX;
			if (isSelfMeasurement(cellPx, cellPx, true)) break;
			span = cellSpan(cellPx, 1, gap, MASONRY_ROW_PX);
			climbed.push(span);
		}
		expect(climbed).toEqual([]);
	});
});
