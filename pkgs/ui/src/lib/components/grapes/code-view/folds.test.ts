import { describe, expect, it } from 'vitest';
import { foldRanges, hiddenLines, indentOf, rangeAt } from './folds.js';

const doc = (s: string) => s.split('\n');

describe('foldRanges', () => {
	it('reads a region off the indentation, outermost first', () => {
		const lines = doc(['<table>', '  <tr>', '    <td></td>', '  </tr>', '</table>'].join('\n'));
		expect(foldRanges(lines)).toEqual([
			{ head: 0, end: 3 }, // <table> holds lines 1-3
			{ head: 1, end: 2 } // <tr> holds line 2
		]);
	});

	it('gives no range to a line nothing is nested under', () => {
		expect(foldRanges(doc('<br>\n<br>'))).toEqual([]);
	});

	it('does not end a region at a blank line inside it', () => {
		// A blank line is not a dedent. Ending there would fold half a block and leave the rest loose.
		const lines = doc(['<div>', '  <p>a</p>', '', '  <p>b</p>', '</div>'].join('\n'));
		expect(foldRanges(lines)[0]).toEqual({ head: 0, end: 3 });
	});

	it('measures the region from the next NON-blank line', () => {
		const lines = doc(['<div>', '', '  <p>a</p>', '</div>'].join('\n'));
		expect(foldRanges(lines)[0]).toEqual({ head: 0, end: 2 });
	});

	it('counts a tab as one column', () => {
		expect(indentOf('\t\t<td>')).toBe(2);
		expect(indentOf('  <td>')).toBe(2);
		expect(indentOf('<td>')).toBe(0);
	});
});

describe('hiddenLines', () => {
	const ranges = [
		{ head: 0, end: 3 },
		{ head: 1, end: 2 }
	];

	it('hides a collapsed region’s body and keeps its head', () => {
		expect([...hiddenLines(ranges, new Set([1]))]).toEqual([2]);
	});

	it('needs no containment logic for nesting', () => {
		// A nested range is a subset of the one around it, so a plain union is the whole answer — and a
		// head whose own line is already hidden contributes nothing new.
		expect([...hiddenLines(ranges, new Set([0, 1]))].sort()).toEqual([1, 2, 3]);
	});

	it('hides nothing when nothing is collapsed', () => {
		expect(hiddenLines(ranges, new Set()).size).toBe(0);
	});

	it('ignores a collapsed line that is not a fold head', () => {
		// The state can go stale — the pane re-reads the canvas live, so line numbers move under it.
		// A head that no longer exists must simply do nothing rather than hide a region nobody folded.
		expect(hiddenLines(ranges, new Set([99])).size).toBe(0);
	});
});

describe('rangeAt', () => {
	it('finds the range a line heads, or null', () => {
		const ranges = [{ head: 2, end: 5 }];
		expect(rangeAt(ranges, 2)).toEqual({ head: 2, end: 5 });
		expect(rangeAt(ranges, 3)).toBeNull();
	});
});
