import { describe, expect, it } from 'vitest';
import { applyHoldings, contactHoldingsQuery, unmeasuredIds } from './holdings.js';

const row = (id: number, cash: number | null, growth: number | null = cash) => ({
	id,
	cash,
	growth
});

describe('applyHoldings', () => {
	it('paints a finished read and leaves everyone else as they were', () => {
		const rows = [row(1, null), row(2, null)];
		const next = applyHoldings(rows, {
			timed_out: false,
			metrics: [{ id: 1, cash: 12, growth: 3 }]
		});
		expect(next[0]).toEqual({ id: 1, cash: 12, growth: 3 });
		expect(next[1]).toEqual(row(2, null));
	});

	it('a timeout does not invent zeros', () => {
		const rows = [row(1, null, null)];
		expect(applyHoldings(rows, { timed_out: true, metrics: [] })).toBe(rows);
	});

	it('zero is a measurement, and it is applied', () => {
		const next = applyHoldings([row(1, null)], {
			timed_out: false,
			metrics: [{ id: 1, cash: 0, growth: 0 }]
		});
		expect(next[0]).toEqual({ id: 1, cash: 0, growth: 0 });
	});
});

describe('unmeasuredIds', () => {
	it('skips a row the list already measured, including a flat zero', () => {
		expect(unmeasuredIds([row(1, null), row(2, 0), row(3, 40)])).toEqual([1]);
	});
});

describe('contactHoldingsQuery', () => {
	it('repeats ids the way the API binds a list', () => {
		expect(contactHoldingsQuery([3, 9])).toBe('ids=3&ids=9');
	});
});
