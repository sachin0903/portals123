import { describe, expect, it } from 'vitest';
import { indirectTotal, normaliseRatio, rankedBreakdown, topHoldings } from './visuals';
import type { LookthroughPosition } from './types';

const position = (ordinal: number, name: string, weight: number): LookthroughPosition => ({
	ordinal,
	name,
	weight,
	indirect_value: weight * 10_000,
	isin: null,
	sedol: null,
	long_name: null,
	currency: 'GBP',
	asset_group: null,
	asset_class: null,
	country_code: null,
	country_name: null,
	maturity_date: null,
	quantity: null,
	price: null,
	prior_price: null,
	movement_percent: null,
	book_cost: null,
	market_value: 0,
	gain_loss: null,
	gain_loss_percent: null
});

describe('look-through visual helpers', () => {
	it('normalises fractions and percentage-shaped values', () => {
		expect(normaliseRatio(0.42)).toBe(0.42);
		expect(normaliseRatio(42)).toBe(0.42);
		expect(normaliseRatio(Number.NaN)).toBe(0);
	});

	it('ranks deterministically and removes empty rows', () => {
		expect(
			rankedBreakdown([
				{ label: 'UK', value: 0.3 },
				{ label: '', value: 0.7 },
				{ label: 'US', value: 0.6 }
			]).map((row) => row.label)
		).toEqual(['US', 'UK']);
	});

	it('lists the largest positions with the share of the fund they make up', () => {
		// 300 positions of a third of a percent each, the shape that made the treemap unreadable.
		const many = Array.from({ length: 300 }, (_, index) =>
			position(index + 1, `Position ${index + 1}`, 0.0033)
		);
		const top = topHoldings([...many, position(999, 'Big one', 0.05)]);
		expect(top.rows).toHaveLength(10);
		expect(top.rows[0]?.name).toBe('Big one');
		expect(top.max).toBeCloseTo(0.05);
		expect(top.share).toBeCloseTo(0.05 + 9 * 0.0033);
		expect(top.count).toBe(301);
	});

	it('reads percentage-shaped weights the same as ratios', () => {
		const top = topHoldings([position(1, 'A', 40), position(2, 'B', 10), position(3, 'C', 0)]);
		expect(top.rows.map((r) => r.name)).toEqual(['A', 'B']);
		expect(top.share).toBeCloseTo(0.5);
		expect(top.max).toBeCloseTo(0.4);
		expect(topHoldings([]).rows).toEqual([]);
		expect(topHoldings([]).share).toBe(0);
	});

	it('totals the client-specific indirect exposure', () => {
		expect(indirectTotal([position(1, 'A', 0.6), position(2, 'B', 0.4)])).toBe(10_000);
	});
});
