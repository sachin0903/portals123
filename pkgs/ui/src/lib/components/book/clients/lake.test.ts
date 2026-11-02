import { describe, expect, it } from 'vitest';
import { applyHoldingsSummary, clientBookTotals, type RawHolding } from './lake';

const holding = (over: Partial<RawHolding>): RawHolding => ({
	value: 0,
	...over
});

describe('clientBookTotals', () => {
	it('returns nulls when there is nothing to sum', () => {
		expect(clientBookTotals([], 100_000)).toEqual({ bookCost: null, unrealisedGain: null });
	});

	it('sums book_cost and growth and scales them to OLTP AUM', () => {
		// Lake total 80, AUM 100 → scale 1.25. Cost 60 and gain 20 become 75 and 25.
		const holdings = [
			holding({ value: 50, book_cost: 40, growth: 10 }),
			holding({ value: 30, book_cost: 20, growth: 10 })
		];
		expect(clientBookTotals(holdings, 100)).toEqual({ bookCost: 75, unrealisedGain: 25 });
	});

	it('keeps an em-dash (null) when the lake never sent a cost or a gain', () => {
		expect(clientBookTotals([holding({ value: 50 })], 50)).toEqual({
			bookCost: null,
			unrealisedGain: null
		});
	});

	it('does not treat a zero cost as missing', () => {
		expect(clientBookTotals([holding({ value: 10, book_cost: 0, growth: 10 })], 10)).toEqual({
			bookCost: 0,
			unrealisedGain: 10
		});
	});
});

describe('applyHoldingsSummary', () => {
	it('leaves the ring empty when the summary is missing — never invents a list', () => {
		expect(applyHoldingsSummary(null, 1_000)).toEqual({
			allocations: [],
			cash: 0,
			bookCost: null,
			unrealisedGain: null,
			accountLegs: []
		});
	});

	it('scales class totals to OLTP AUM the way the unpaged list did', () => {
		// Lake 400 against AUM 1,000 → scale 2.5. Equities sort first.
		const view = applyHoldingsSummary(
			{
				allocations: [
					{ label: 'Cash', value: 100 },
					{ label: 'Equities', value: 300 }
				],
				cash: 100,
				lake_total: 400,
				book_cost: 80,
				unrealised_gain: 20,
				accounts: [{ account_id: 11, cash: 100, invested: 300, as_of: '2026-07-30' }]
			},
			1_000
		);
		expect(view.allocations).toEqual([
			{ label: 'Equities', value: 750 },
			{ label: 'Cash', value: 250 }
		]);
		expect(view.cash).toBe(250);
		expect(view.bookCost).toBe(200);
		expect(view.unrealisedGain).toBe(50);
		expect(view.accountLegs).toEqual([
			{ accountId: 11, cash: 100, invested: 300, asOf: '2026-07-30' }
		]);
	});
});
