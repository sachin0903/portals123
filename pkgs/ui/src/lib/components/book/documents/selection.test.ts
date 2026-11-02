import { describe, expect, it } from 'vitest';
import type { LibraryRow } from './documents.js';
import { bulkTargets, pickRow, renderedOrder, selectionOf, toggleMany } from './selection.js';

const rows = (...uids: string[]) => uids.map((uid) => ({ uid }));

describe('selectionOf', () => {
	it('reads none, some and all', () => {
		expect(selectionOf([], rows('a', 'b'))).toBe('none');
		expect(selectionOf(['a'], rows('a', 'b'))).toBe('some');
		expect(selectionOf(['a', 'b', 'x'], rows('a', 'b'))).toBe('all');
	});
});

describe('toggleMany', () => {
	it('completes a partial set, clears a full one, and leaves other rows alone', () => {
		expect(toggleMany(['a', 'x'], rows('a', 'b'))).toEqual(['a', 'x', 'b']);
		expect(toggleMany(['a', 'b', 'x'], rows('a', 'b'))).toEqual(['x']);
	});
});

describe('pickRow', () => {
	const order = ['a', 'b', 'c', 'd'];

	it('toggles one row without shift', () => {
		expect(pickRow([], order, 'b', 'a', false)).toEqual(['b']);
		expect(pickRow(['b'], order, 'b', 'a', false)).toEqual([]);
	});

	it('takes the range from the anchor with shift, in either direction', () => {
		expect(pickRow(['a'], order, 'c', 'a', true)).toEqual(['a', 'b', 'c']);
		expect(pickRow(['a', 'b', 'c', 'd'], order, 'b', 'd', true)).toEqual(['a']);
	});

	it('falls back to one row when the anchor is no longer drawn', () => {
		expect(pickRow([], order, 'c', 'gone', true)).toEqual(['c']);
		expect(pickRow([], order, 'c', null, true)).toEqual(['c']);
	});
});

describe('renderedOrder', () => {
	type R = { uid: string; kind: string };
	const list: R[] = [
		{ uid: '1', kind: 'B' },
		{ uid: '2', kind: 'A' },
		{ uid: '3', kind: 'B' }
	];
	const groupBy = (r: R) => [{ key: r.kind, label: r.kind }];

	it('follows the folders and skips shut ones', () => {
		expect(renderedOrder(list, groupBy, [], null, new Set())).toEqual(['2', '1', '3']);
		expect(renderedOrder(list, groupBy, [], null, new Set(['B']))).toEqual(['2']);
	});

	it('is the rows as given without folders', () => {
		expect(renderedOrder(list, undefined, [], null, new Set())).toEqual(['1', '2', '3']);
	});
});

describe('bulkTargets', () => {
	const doc = (over: Partial<LibraryRow>) => ({ isFile: false, ...over }) as LibraryRow;

	it('splits documents from files and leaves protected documents out', () => {
		const list = [
			doc({ uid: 'a' }),
			doc({ uid: 'p', deletable: false }),
			doc({ uid: 'f', isFile: true }),
			doc({ uid: 'n' })
		];
		expect(bulkTargets(['a', 'p', 'f', 'gone'], list)).toEqual({ documents: ['a'], files: ['f'] });
	});
});
