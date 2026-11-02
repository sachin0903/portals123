import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * The two rules that keep the masonry grid from measuring its own output.
 *
 * `masonryCell` computes a cell's row span FROM the box inside it and then applies that span TO the
 * cell — so any route by which the cell's height reaches the box closes a loop that adds one gap
 * per frame (masonry.ts::isSelfMeasurement spells out the arithmetic). The routes are CSS, they are
 * engine-specific, and neither shows up in a unit test: this package's vitest has no layout engine,
 * and a browser test would have to reproduce a whole themed portal page to see it.
 *
 * So the rules are pinned as source, the way `switch-columns.test.ts` and `field-shell.test.ts` pin
 * theirs. The scan is scoped to the `<style>` block with comments stripped, because the comments
 * beside these rules quote both `h-full` and `fit-content` at length — a file-wide match would pass
 * on the explanation of a rule someone had just deleted.
 */

const STYLE = (() => {
	const src = readFileSync(join(import.meta.dirname, 'portal-page.svelte'), 'utf8');
	const block = /<style>([\s\S]*)<\/style>/.exec(src);
	if (!block) throw new Error('portal-page.svelte has no <style> block');
	return block[1].replace(/\/\*[\s\S]*?\*\//g, '');
})();

/** The declarations of one rule, by its selector, with whitespace flattened. */
function ruleFor(selector: string): string {
	const at = STYLE.indexOf(selector);
	expect(at, `no rule for \`${selector}\``).toBeGreaterThan(-1);
	const open = STYLE.indexOf('{', at);
	const close = STYLE.indexOf('}', open);
	return STYLE.slice(open + 1, close)
		.replace(/\s+/g, ' ')
		.trim();
}

describe('the masonry feedback guards', () => {
	it('gives the rendered widget the full authored floor without reading from the cell', () => {
		const inner = ruleFor('.client-cell-inner {');
		expect(inner).toContain('display: flex');
		// The floor is a desktop-grid canvas, so it lives in the md+ media block: on a phone's
		// single column it was a 12rem phantom gap under a one-card stack.
		expect(inner).not.toContain('min-height');
		const md = STYLE.slice(STYLE.indexOf('@media (min-width: 768px)'));
		const at = md.indexOf('.client-cell-inner {');
		expect(at).toBeGreaterThan(-1);
		expect(md.slice(at, md.indexOf('}', at))).toContain('min-height: calc(var(--ch) * 6rem)');
		expect(ruleFor('.client-cell-inner > :global(*) {')).toContain('flex: 1 1 auto');
	});

	it('holds the measured box content-sized', () => {
		// Without this the box stretches to the cell, and `span = ceil((inner + gap) / row)` makes
		// the next cell one gap taller — for ever.
		expect(ruleFor('.client-home.masonry .client-cell-inner {')).toContain('height: fit-content');
	});

	it('stops the card asking for a percentage of the box', () => {
		// The half `fit-content` alone does not cover: `fit-content` is
		// `min(max-content, max(min-content, stretch))` and the `stretch` term is the grid height
		// being computed, so Firefox resolved the card's `h-full` straight through it and the loop
		// ran anyway. Every block root carrying `h-full` was affected — action items, accounts
		// summary, adviser, message, and the variants portal-page renders inline.
		expect(ruleFor('.client-home.masonry .client-cell-inner > :global(*) {')).toContain(
			'height: auto'
		);
	});

	it('leaves a real minimum height alone', () => {
		// `height`, not `min-height`: `min-h-*` on a block root (adviser-strip) is an authored floor,
		// and it cannot close the loop because it never resolves against the cell.
		expect(ruleFor('.client-home.masonry .client-cell-inner > :global(*) {')).not.toContain(
			'min-height'
		);
	});

	it('confines the two percentage-height guards to the masonry path', () => {
		// Off the masonry path `h-full` is doing its real job — levelling cards that share a row
		// track — and the non-masonry grid never measures anything, so there is no loop to close.
		expect(STYLE).toContain('.client-home.masonry .client-cell-inner {');
		expect(STYLE).toContain('.client-home.masonry .client-cell-inner > :global(*) {');
	});
});
