import { describe, expect, it } from 'vitest';
import { hideClasses, visibleWhenHidden } from './hide.js';
import type { DataTableColumn } from './types.js';

/** The two ladders, spelled out: a rung added to one map and not the other fails here by name. */
const VIEWPORT = ['sm', 'md', 'lg', 'xl'] as const;
const CONTAINER = ['sm', 'md', 'lg', 'xl', '2xl', '3xl', '4xl', '5xl', '6xl', '7xl'] as const;

const column = (over: Partial<DataTableColumn<unknown>>): DataTableColumn<unknown> => ({
	key: 'x',
	header: 'X',
	...over
});

describe('visibleWhenHidden', () => {
	it('is the inverse of the column’s own hide class, at every breakpoint', () => {
		// The stand-in for a collapsed column (growth drawn under value) must appear exactly
		// where the column does not: same breakpoint, opposite visibility, and a block so it
		// takes its own line.
		for (const below of VIEWPORT) {
			const col = column({ hideBelow: below });
			expect(hideClasses(col)).toBe(`hidden ${below}:table-cell`);
			expect(visibleWhenHidden(col)).toBe(`block ${below}:hidden`);
		}
		for (const width of CONTAINER) {
			const col = column({ hideBelowWidth: width });
			expect(hideClasses(col)).toBe(`hidden @${width}:table-cell`);
			expect(visibleWhenHidden(col)).toBe(`block @${width}:hidden`);
		}
	});

	it('is undefined for a column that never hides, so there is no second permanent copy', () => {
		expect(visibleWhenHidden(column({}))).toBeUndefined();
		expect(visibleWhenHidden(undefined)).toBeUndefined();
	});
});
