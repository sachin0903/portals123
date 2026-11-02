import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import AllocationDonut from '../charts/allocation-donut.svelte';
import AccountStack from './account-stack.svelte';
import AllocationOrbit from './allocation-orbit.svelte';
import PortfolioHeader from './portfolio-header.svelte';
import PortfolioHeaderLedger from './portfolio-header-ledger.svelte';
import WidgetHeader from './widget-header.svelte';
import type { Wrapper } from './types.js';

/**
 * The client portal's standalone money figures go through `Money`: pounds, then the pence in
 * their own muted span. Asserted on the rendered HTML (pounds directly followed by a span
 * holding the pence), so a figure slipping back to a plain `formatGBP` string fails here.
 */
const esc = (t: string) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const split = (pounds: string, pence: string) =>
	new RegExp(`${esc(pounds)}<span[^>]*>\\.${pence}</span>`);

const wrapper: Wrapper = {
	type: 'ISA',
	name: 'Stocks & Shares ISA',
	owner: 'Test Owner',
	value: 1234.5,
	gain: 50,
	gainPct: 0.05,
	trend: [1, 1.1]
};

const slices = [
	{ label: 'Equities', value: 600.25, color: '#111' },
	{ label: 'Bonds', value: 400.5, color: '#222' }
];

describe('Money splits in the client portal', () => {
	it('account stack balance', () => {
		const body = render(AccountStack, { props: { wrappers: [wrapper] } }).body;
		expect(body).toMatch(split('£1,234', '50'));
	});

	it('portfolio header: invested, growth and period high', () => {
		const body = render(PortfolioHeader, {
			props: {
				total: 10_000.5,
				gain: 2_000.25,
				gainPct: 0.25,
				performance: [
					{ label: '2026-01-29', value: 8_000 },
					{ label: '2026-07-29', value: 10_000.5 }
				]
			}
		}).body;
		expect(body).toMatch(split('+£2,000', '25'));
		// Invested = total - gain.
		expect(body).toMatch(split('£8,000', '25'));
	});

	it('ledger header: growth figure splits, the percentage stays text', () => {
		const body = render(PortfolioHeaderLedger, {
			props: { total: 10_000.5, gain: 2_000.25, gainPct: 0.25, performance: [] }
		}).body;
		expect(body).toMatch(split('+£2,000', '25'));
		expect(body).toContain('(+25.0%)');
	});

	it('widget header stamp: count stays text, total splits', () => {
		const body = render(WidgetHeader, {
			props: { title: 'Accounts', stamp: '3 accounts', stampAmount: 452_728.25 }
		}).body;
		expect(body).toMatch(/3 accounts(<!--[^>]*-->)* · (<!--[^>]*-->)*<span/);
		expect(body).toMatch(split('£452,728', '25'));
	});

	it('widget header still renders a plain stamp string', () => {
		const body = render(WidgetHeader, {
			props: { title: 'Holdings', stamp: 'as at 28 Sept 2026' }
		}).body;
		expect(body).toContain('as at 28 Sept 2026');
	});

	it('allocation donut: centre total and legend amounts split below £10m', () => {
		const body = render(AllocationDonut, { props: { data: slices, total: 1000.75 } }).body;
		expect(body).toMatch(split('£600', '25'));
		expect(body).toMatch(split('£1,000', '75'));
	});

	it('allocation donut: the centre goes compact text from £10m', () => {
		const body = render(AllocationDonut, {
			props: { data: [{ label: 'Equities', value: 13_000_000, color: '#111' }], total: 13_000_000 }
		}).body;
		expect(body).toContain('£13m');
	});

	it('allocation orbit: exact centre splits, compact stays text', () => {
		const exact = render(AllocationOrbit, { props: { data: slices, total: 1000.75 } }).body;
		expect(exact).toMatch(split('£1,000', '75'));
		const big = render(AllocationOrbit, {
			props: { data: [{ label: 'Equities', value: 13_000_000, color: '#111' }], total: 13_000_000 }
		}).body;
		expect(big).toContain('£13m');
	});
});
