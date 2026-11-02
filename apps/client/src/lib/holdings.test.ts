import { describe, expect, it } from 'vitest';
import type { Holding } from '$lib/api';
import { holdingReturn, isCashHolding, orderHoldings, toPortalHolding } from './holdings';

const h = (
	asset_name: string | null,
	asset_type: string | null = 'Stock',
	isin: string | null = 'GB00TEST0001'
): Holding => ({ asset_name, asset_type, isin }) as Holding;

/** The account's actual cash line as each provider publishes it. */
const CASH = h('Cash', 'Cash', null);

describe('isCashHolding — which row is the uninvested balance', () => {
	it('takes the provider at its word', () => {
		expect(isCashHolding(CASH)).toBe(true);
		expect(isCashHolding(h('GBP', 'MRCXXXXXXX', null))).toBe(true); // SEI names it by currency
	});

	it('takes a row with no instrument, called cash', () => {
		expect(isCashHolding(h('Cash', 'Stock', null))).toBe(true);
		expect(isCashHolding(h('Cash', null, '  '))).toBe(true);
	});

	// The reported defect: two Royal London money-market funds sat above the A-Z list, so the
	// table did not look sorted. A money-market fund is an investment the client chose — it has
	// an ISIN, a unit price and a return. The API's own rule counts it as cash on purpose,
	// because it answers a different question (what a withdrawal may be paid FROM).
	it('does NOT take a money-market fund, which is an investment', () => {
		expect(isCashHolding(h('Royal London Short Term Money Market Y Acc'))).toBe(false);
		expect(isCashHolding(h('Royal London Short Term Money Market Y Inc'))).toBe(false);
		expect(isCashHolding(h('Sterling Liquidity Fund'))).toBe(false);
	});

	it('does not take a fund whose name merely contains the word', () => {
		expect(isCashHolding(h('Cashel Capital Global Equity Fund'))).toBe(false);
		expect(isCashHolding(h('Cash Reserve Fund'))).toBe(false);
	});

	// Both halves are required: an unmatched equity can arrive with a blank ISIN.
	it('does not take an instrument-less row that is not called cash', () => {
		expect(isCashHolding(h('Unlisted Holding Ltd', 'Stock', null))).toBe(false);
	});

	it('says no when neither field says anything', () => {
		expect(isCashHolding(h(null, null, null))).toBe(false);
	});

	// The reported row: the custodian names the currency and counts pounds as units.
	it('takes a currency the provider calls cash, whatever it is named', () => {
		expect(isCashHolding(h('Pound Sterling', 'Cash', null))).toBe(true);
	});
});

describe('orderHoldings', () => {
	it('keeps a money-market fund in the A-Z list rather than lifting it', () => {
		// What the reported screenshot showed: two Royal London funds above every E-name.
		const rows = [
			h('Epworth Global Equity Fund for Charities GBP Inc'),
			h('Royal London Short Term Money Market Y Acc'),
			CASH,
			h('AstraZeneca')
		];
		expect(orderHoldings(rows).map((r) => r.asset_name)).toEqual([
			'Cash',
			'AstraZeneca',
			'Epworth Global Equity Fund for Charities GBP Inc',
			'Royal London Short Term Money Market Y Acc'
		]);
	});

	it('puts cash at the top and sorts the rest A-Z', () => {
		const rows = [
			h('Microsoft'),
			h('IFSL Arbion Balanced Opportunities A Inc'),
			CASH,
			h('AstraZeneca')
		];
		expect(orderHoldings(rows).map((r) => r.asset_name)).toEqual([
			'Cash',
			'AstraZeneca',
			'IFSL Arbion Balanced Opportunities A Inc',
			'Microsoft'
		]);
	});

	it('does not reorder the caller’s array', () => {
		const rows = [h('Microsoft'), CASH];
		orderHoldings(rows);
		expect(rows.map((r) => r.asset_name)).toEqual(['Microsoft', 'Cash']);
	});

	it('orders several cash rows among themselves rather than by arrival', () => {
		const rows = [h('USD', 'MRCXXXXXXX', null), h('Equity Fund'), CASH];
		expect(orderHoldings(rows).map((r) => r.asset_name)).toEqual(['Cash', 'USD', 'Equity Fund']);
	});

	it('sorts numerically, so Fund 2 precedes Fund 10', () => {
		const rows = [h('Fund 10'), h('Fund 2')];
		expect(orderHoldings(rows).map((r) => r.asset_name)).toEqual(['Fund 2', 'Fund 10']);
	});

	it('sends a nameless holding to the BOTTOM, never the top', () => {
		const rows = [h(null), h('AstraZeneca'), h('  ')];
		expect(orderHoldings(rows).map((r) => r.asset_name)).toEqual(['AstraZeneca', null, '  ']);
	});
});

describe('holdingReturn', () => {
	const pos = (book_cost: number | null, growth: number | null): Holding =>
		({ book_cost, growth }) as Holding;

	it('derives the return from growth over book cost, as a fraction', () => {
		expect(holdingReturn(pos(88, 12))).toBeCloseTo(0.1364, 4);
		expect(holdingReturn(pos(1000, -250))).toBe(-0.25);
	});

	// The defect: `growth_pct` is the lake's `unrealised_gain_percent`, a PERCENT, and the
	// account record read it as a fraction. Every value then failed `sanePct`'s ±1000% bound and
	// the Return column printed a dash on every row. Nothing here reads that column at all.
	it('ignores growth_pct entirely, whatever the provider put in it', () => {
		const h = { book_cost: 88, growth: 12, growth_pct: 12 } as Holding;
		expect(holdingReturn(h)).toBeCloseTo(0.1364, 4);
	});

	it('says nothing rather than zero when there is no basis to divide by', () => {
		expect(holdingReturn(pos(0, 12))).toBeNull();
		expect(holdingReturn(pos(null, 12))).toBeNull();
		expect(holdingReturn(pos(-5, 12))).toBeNull();
	});

	// Zero is a claim the reader cannot tell from the truth, which is that the basis is wrong.
	it('says nothing for an absurd basis rather than printing +6,303%', () => {
		expect(holdingReturn(pos(1, 6303))).toBeNull();
	});

	it('treats an absent gain against a real cost as flat, which it is', () => {
		expect(holdingReturn(pos(1000, null))).toBe(0);
	});
});

describe('toPortalHolding — cash is an amount', () => {
	it('prints Pound Sterling as cash and drops the units and the price', () => {
		const row = toPortalHolding({
			asset_name: 'Pound Sterling',
			asset_type: 'Cash',
			isin: null,
			quantity: 693.644,
			price: 1,
			value: 693.64,
			book_cost: 693.64,
			growth: 0
		} as Holding);
		expect(row).toMatchObject({
			name: 'Cash',
			ticker: undefined,
			units: 0,
			price: 0,
			value: 693.64,
			gainPct: null,
			cash: true
		});
	});

	it('leaves a fund alone, including one whose name mentions sterling or cash', () => {
		const fund = toPortalHolding({
			...h('Sterling Liquidity Fund'),
			quantity: 120.5,
			price: 1.42,
			value: 171.11,
			book_cost: 150,
			growth: 21.11
		});
		expect(fund.cash).toBeUndefined();
		expect(fund.name).toBe('Sterling Liquidity Fund');
		expect(fund.units).toBe(120.5);
		expect(fund.price).toBe(1.42);
		expect(fund.ticker).toBe('GB00TEST0001');
	});
});
