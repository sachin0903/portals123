import { describe, expect, it } from 'vitest';
import { gapLabel, isGap, itemKey, pageItems, parsePageInput } from './page-jump.js';

/**
 * The pager's page maths. These were unreachable while they lived inside a `$derived.by` — the
 * only way to observe them was to mount the component and read the DOM back, which is why the
 * gap ranges (the thing the jump control's whole promise rests on) had no test at all.
 */

describe('pageItems', () => {
	it('prints every page when they all fit', () => {
		expect(pageItems(1, 7)).toEqual([1, 2, 3, 4, 5, 6, 7]);
	});

	it('collapses both sides around a middle page', () => {
		// 1 … 9 [10] 11 … 20
		expect(pageItems(10, 20)).toEqual([1, { from: 2, to: 8 }, 9, 10, 11, { from: 12, to: 19 }, 20]);
	});

	it('only collapses the far side when the current page is near an end', () => {
		const items = pageItems(2, 24);
		expect(items).toEqual([1, 2, 3, { from: 4, to: 23 }, 24]);
		// The screenshot case: one gap, and it hides twenty pages.
		expect(items.filter(isGap)).toHaveLength(1);
	});

	it('never collapses a single page — a gap hiding one page is worse than the number', () => {
		// With 8 pages and the current page at 4, the run to the right is 6..7: two pages, and the
		// old rule fills rather than collapses anything shorter than that.
		for (let page = 1; page <= 8; page++) {
			for (const item of pageItems(page, 8)) {
				if (isGap(item)) expect(item.to).toBeGreaterThan(item.from);
			}
		}
	});

	it('always offers the first and last page', () => {
		for (const page of [1, 5, 12, 24]) {
			const items = pageItems(page, 24);
			expect(items[0]).toBe(1);
			expect(items[items.length - 1]).toBe(24);
		}
	});

	it('every hidden page is inside exactly one gap — nothing is unreachable', () => {
		const total = 24;
		for (let page = 1; page <= total; page++) {
			const items = pageItems(page, total);
			const shown = new Set(items.filter((i): i is number => !isGap(i)));
			const covered = new Set<number>();
			for (const gap of items.filter(isGap)) {
				for (let n = gap.from; n <= gap.to; n++) {
					expect(covered.has(n)).toBe(false); // no overlap between the two gaps
					covered.add(n);
				}
			}
			for (let n = 1; n <= total; n++) {
				expect(shown.has(n) || covered.has(n)).toBe(true);
			}
		}
	});

	it('treats a zero or negative page count as one page', () => {
		expect(pageItems(1, 0)).toEqual([1]);
		expect(pageItems(1, -3)).toEqual([1]);
	});

	it('keys are stable and unique across a pager', () => {
		const keys = pageItems(10, 20).map(itemKey);
		expect(new Set(keys).size).toBe(keys.length);
	});
});

describe('parsePageInput', () => {
	it('treats an empty field as nothing to go to — the field opens empty, so this is the norm', () => {
		expect(parsePageInput('', 24)).toBeNull();
	});

	it('reads a plain page number', () => {
		expect(parsePageInput('12', 24)).toBe(12);
		expect(parsePageInput('  7 ', 24)).toBe(7);
	});

	it('clamps out-of-range numbers, matching what clicking the last page does', () => {
		expect(parsePageInput('99', 24)).toBe(24);
		expect(parsePageInput('0', 24)).toBe(1);
	});

	it('refuses anything that is not digits rather than guessing page 1', () => {
		// A typo must disable the submit, not quietly send the reader somewhere.
		for (const raw of ['', '   ', 'abc', '1a', '-4', '3.5', '1e3', '٣']) {
			expect(parsePageInput(raw, 24)).toBeNull();
		}
	});

	it('never returns a page below 1, whatever the page count claims', () => {
		expect(parsePageInput('5', 0)).toBe(1);
	});
});

describe('gapLabel', () => {
	it('states the destination range, so the control is not a blind click', () => {
		expect(gapLabel({ from: 4, to: 23 })).toBe('Go to a page between 4 and 23');
	});

	it('reads naturally when a gap somehow holds one page', () => {
		expect(gapLabel({ from: 7, to: 7 })).toBe('Go to page 7');
	});
});
