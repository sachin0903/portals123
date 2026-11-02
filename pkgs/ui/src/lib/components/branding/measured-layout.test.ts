import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import type { BrandingBlock } from '@graphene/auth';
import { blockKeys } from './client-blocks.js';
import { layoutToRows } from './pages-view.js';

// The seam: the renderer measures each cell and reports its micro-row span keyed by the block's identity, and the builder draws its tiles in that same unit. Measuring needs a real browser, so the arithmetic is pinned in investment/masonry.test.ts and the wiring here as source shape; lose either side's blockKeys and a measured height lands on the wrong tile.

const here = import.meta.dirname;
const stripComments = (code: string) =>
	code
		.replace(/<!--[\s\S]*?-->/g, '')
		.replace(/\/\*[\s\S]*?\*\//g, '')
		.replace(/(?<!:)\/\/[^\n]*/g, '');
const read = (p: string) => stripComments(readFileSync(join(here, p), 'utf8'));

const portalPage = read('../investment/portal-page.svelte');
const builder = read('layout-builder.svelte');
const shellPreview = read('shell-preview.svelte');
const editor = read('branding-editor.svelte');

describe('one identity for a block, both sides', () => {
	const layout: BrandingBlock[] = [
		{ id: 'holdings', type: 'holdings', w: 8, h: 2 },
		{ id: 'holdings-2', type: 'holdings', w: 4, h: 1 },
		{ type: 'message', w: 12, h: 1 }
	];

	it('the builder’s row ids ARE the renderer’s cell keys', () => {
		expect(layoutToRows(layout).map((r) => r.id)).toEqual(blockKeys(layout));
	});

	it('mints a unique key for every id-less or colliding legacy block', () => {
		const legacy: BrandingBlock[] = [
			{ type: 'accounts', w: 6, h: 1 },
			{ type: 'accounts', w: 6, h: 1 },
			{ id: 'accounts', type: 'accounts', w: 12, h: 1 }
		];
		const keys = blockKeys(legacy);
		expect(new Set(keys).size).toBe(3);
		expect(keys).toEqual(layoutToRows(legacy).map((r) => r.id));
	});

	it('keys the whole layout, so a hidden block does not renumber the ones after it', () => {
		// Key BEFORE filtering: keying the filtered list shifts 'message-2' onto 'message' and hands it the wrong measured height.
		const withHidden: BrandingBlock[] = [
			{ type: 'message', visible: false, w: 12, h: 1 },
			{ type: 'message', visible: true, w: 12, h: 1 }
		];
		expect(blockKeys(withHidden)).toEqual(['message', 'message-2']);
	});

	it('is stable under a settings edit — a re-keyed tile would lose its measurement', () => {
		const edited = layout.map((b) => (b.id === 'holdings' ? { ...b, settings: { limit: 9 } } : b));
		expect(blockKeys(edited)).toEqual(blockKeys(layout));
	});
});

describe('the renderer reports what it measured', () => {
	it('stamps each cell with the block key a height is attributed to', () => {
		expect(portalPage).toMatch(/data-block-id=\{key\}/);
		expect(portalPage).toMatch(/blockKeys\(blocks\)/);
	});

	it('spans cells by the SHARED arithmetic, floor included', () => {
		// Not a local `Math.ceil((h + gap) / 8)`: the builder has to compute the same number.
		expect(portalPage).toMatch(/cellSpan\(\s*inner\.offsetHeight,\s*current\.h,/);
		expect(portalPage).toContain("from './masonry.js'");
	});

	it('hands the finished grid out through onmeasure, with the unit the spans are in', () => {
		expect(portalPage).toMatch(/onmeasure\(\{/);
		for (const field of ['spans:', 'gridWidth:', 'rowPx:', 'gapPx:', 'spanned:']) {
			expect(portalPage).toContain(field);
		}
	});

	it('decides `spanned` from the grid’s own track count, not a media query', () => {
		// This subtree is re-parented into the preview iframe, where the module's `window` is the HOST's — matchMedia would answer for the wrong viewport.
		expect(portalPage).toMatch(/gridTemplateColumns/);
		expect(portalPage).not.toMatch(/matchMedia/);
	});

	it('costs a real client portal nothing: no observer and no report without onmeasure', () => {
		expect(portalPage).toMatch(/if \(!onmeasure \|\| reportQueued\) return;/);
		expect(portalPage).toMatch(
			/if \(!grid \|\| !onmeasure \|\| typeof ResizeObserver === 'undefined'\)/
		);
	});

	it('sets --cw/--ch through style: directives so they cannot wipe the measured --rs', () => {
		// A `style="…"` string is re-written wholesale on update, taking the imperatively-set --rs with it: raising a block's height dropped the cell it had just measured back to the CSS fallback span.
		expect(portalPage).toMatch(/style:--cw=\{blockW\(b\)\}/);
		expect(portalPage).toMatch(/style:--ch=\{blockH\(b\)\}/);
		expect(portalPage).not.toMatch(/style="--cw/);
	});

	it('keeps the measured box content-sized — the feedback loop that reached 147,000px', () => {
		// The floor raises the CELL, never the measured inner box — remove this rule and the loop closes again.
		expect(portalPage).toMatch(/\.client-cell-inner\s*\{[^}]*height:\s*fit-content/);
	});
});

describe('the builder draws the reported grid', () => {
	it('lays out in the portal’s micro-rows, not 4rem tracks', () => {
		expect(builder).toMatch(/grid-auto-rows:\s*\{geometry\.rowPx\}px/);
		expect(builder).not.toMatch(/auto-rows-\[4rem\]/);
	});

	it('zeroes row-gap and carries the rhythm per tile, exactly as the portal does', () => {
		expect(builder).toMatch(/row-gap:\s*0/);
		expect(builder).toMatch(/margin-bottom:\s*\{geometry\.gapPx\}px/);
	});

	it('keeps the portal’s SPARSE flow — the packing rule the two must share', () => {
		// Sparse both sides: `dense` back-fills holes whose size comes from the client's own data, so the arrangement a firm builds is not the one their clients get — and it still renders, so the failure is silent.
		expect(builder).toMatch(/grid-flow-row(?!-dense)/);
		expect(builder).not.toMatch(/grid-flow-row-dense/);
		expect(portalPage).toMatch(/grid-auto-flow:\s*row;/);
		expect(portalPage).not.toMatch(/grid-auto-flow:\s*row dense/);
	});

	it('spans a tile by tileSpan (measured, floored), never by the raw authored h', () => {
		// Whitespace-tolerant because prettier wraps the expression; the regression guarded is the tile spanning by the authored `h` again.
		expect(builder).toMatch(/grid-row: span \{spans\[\s*i\s*\]\}/);
		expect(builder).toMatch(/tileSpan\(r, m\)/);
		expect(builder).not.toMatch(/grid-row: span \{row\.h\}/);
	});
});

describe('the studio carries the numbers across', () => {
	it('passes the preview’s report into the builder', () => {
		expect(shellPreview).toMatch(/\{onmeasure\}/);
		expect(editor).toMatch(/onmeasure=\{reportMetrics\}/);
		expect(editor).toMatch(/metrics=\{gridMetrics\}/);
	});

	it('ignores a single-column reading rather than dropping back to authored spans', () => {
		expect(editor).toMatch(/if \(next\.spanned\) gridMetrics = next;/);
	});

	it('leaves the frame itself generic — it must not learn what a portal block is', () => {
		const frame = read('preview-frame.svelte');
		expect(frame).not.toMatch(/data-block-id|onmeasure|client-cell/);
	});
});
