import { render } from 'svelte/server';
import { describe, expect, it } from 'vitest';
import FundLookthrough from './fund-lookthrough.svelte';
import type { FundLookthroughData } from './types';

const data: FundLookthroughData = {
	fund: { uid: 'fund123', name: 'IFSL Global Equity Fund', source: 'IFSL', fund_code: 'GEF' },
	share_class: { isin: 'GB00TEST1234', sedol: 'TEST123', name: 'Class A Acc' },
	share_classes: [
		{ class_code: 'A', name: 'Class A Acc', isin: 'GB00TEST1234', sedol: 'TEST123' },
		{ class_code: 'B', name: 'Class B Inc', isin: 'GB00TEST5678', sedol: null }
	],
	snapshot: {
		uid: 'snap123',
		as_of_date: '2026-06-30',
		coverage: 0.98,
		residual: 0.02,
		position_count: 1
	},
	available_dates: ['2026-06-30', '2026-03-31'],
	holding: { value: 25_000, currency: 'GBP', account_uid: 'acct123', account_name: 'ISA' },
	positions: [
		{
			ordinal: 1,
			isin: 'US0000000001',
			sedol: null,
			name: 'Example plc',
			long_name: null,
			currency: 'GBP',
			asset_group: 'Equity',
			asset_class: 'Global equity',
			country_code: 'GB',
			country_name: 'United Kingdom',
			maturity_date: '2030-07-22',
			quantity: 10,
			price: 100,
			prior_price: 98,
			movement_percent: 0.02,
			book_cost: 900,
			market_value: 1_000,
			weight: 1,
			indirect_value: 25_000,
			gain_loss: 100,
			gain_loss_percent: 0.111111
		}
	],
	nav_history: {
		class_code: 'A',
		class_name: 'Class A Acc',
		change: 0.02,
		points: [
			{
				date: '2026-06-01',
				class_code: 'A',
				class_name: 'Class A Acc',
				nav: 100,
				units_outstanding: 1_000,
				fund_value: 100_000
			},
			{
				date: '2026-06-30',
				class_code: 'A',
				class_name: 'Class A Acc',
				nav: 102,
				units_outstanding: 1_000,
				fund_value: 102_000
			}
		]
	},
	cash: {
		balances: [{ currency: 'GBP', name: 'British pound', base_value: 2_000, weight: 0.02 }],
		net_weight: 0.02,
		other_residual: 0
	},
	maturity_profile: [{ label: '3–5 years', value: 1 }],
	currency_contracts: [
		{
			share_class_code: 'A',
			name: 'USD hedge',
			currency: 'USD',
			delivery_date: '2026-09-30',
			bought_sold: 'Sold',
			amount: 100_000,
			current_value: 98_000,
			unrealised_gain_loss: 2_000,
			nav_impact: 0.002,
			long_short: 'Short'
		}
	],
	breakdowns: {
		country: [{ label: 'United Kingdom', value: 1 }],
		currency: [{ label: 'GBP', value: 1 }],
		asset_class: [{ label: 'Global equity', value: 1 }]
	}
};

describe('FundLookthrough', () => {
	it('renders the client holding, accessible visual and full positions table', () => {
		const { body, head } = render(FundLookthrough, {
			props: { data, backHref: '/accounts/acct123' }
		});
		const html = `${head}${body}`;
		expect(html).toContain('IFSL Global Equity Fund');
		expect(html).toContain('Your holding');
		// The headline sits on the product's dark hero band, not a pale primary wash.
		expect(html).toMatch(/<section class="hero-surface[^"]*" aria-labelledby="lookthrough-title"/);
		expect(html).toContain('hero-hairline');
		expect(html).toContain('Largest underlying positions');
		// A ranked list, not a treemap: the one position is named, placed and weighted.
		expect(html).toContain('aria-label="Largest underlying positions"');
		expect(html).toContain('United Kingdom · Global equity');
		expect(html).toContain('of the fund');
		expect(html).toContain('Underlying positions');
		// The filters live behind the funnel now (a shut popover, nothing server-side).
		expect(html).toContain('aria-label="Show filters"');
		expect(html).toContain('Historical snapshot');
		expect(html).toContain('NAV performance');
		expect(html).toContain('Cash and residual composition');
		expect(html).toContain('Bond maturity profile');
		// Maturity sits before cash, under the NAV chart.
		expect(html.indexOf('Bond maturity profile')).toBeLessThan(
			html.indexOf('Cash and residual composition')
		);
		expect(html).toContain('Currency hedging');
		expect(html).not.toContain('Underlying gain/loss');
		expect(html).toContain('role="img"');
	});

	it('renders the operations reference view with every matching ISIN and no invented holding', () => {
		const { body } = render(FundLookthrough, {
			props: { data: { ...data, holding: null }, backHref: '/look-through' }
		});
		expect(body).toContain('Matching share-class ISINs');
		expect(body).toContain('GB00TEST1234');
		expect(body).toContain('GB00TEST5678');
		expect(body).toContain('Published fund composition');
		expect(body).not.toContain('Your holding');
		expect(body).not.toContain('Your exposure');
	});
});
