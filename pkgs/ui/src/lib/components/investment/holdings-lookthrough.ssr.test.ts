import { render } from 'svelte/server';
import { describe, expect, it } from 'vitest';
import HoldingsFullTable from './holdings-full-table.svelte';
import HoldingsTable from './holdings-table.svelte';
import type { Holding } from './types';

const holding: Holding = {
	name: 'IFSL Global Equity Fund',
	wrapper: 'ISA',
	units: 10,
	price: 100,
	value: 1_000,
	gainPct: 0.1,
	href: '/accounts/account1/transactions?isin=GB00TEST1234',
	lookthroughHref: '/look-through/fund1?account=account1'
};

describe('holding look-through action', () => {
	it('keeps the primary holding href and adds a labelled action to both table variants', () => {
		const compact = render(HoldingsTable, { props: { holdings: [holding] } }).body;
		expect(compact).toContain(holding.href);
		for (const component of [HoldingsTable, HoldingsFullTable]) {
			const { body } = render(component, { props: { holdings: [holding] } });
			expect(body).toContain(holding.lookthroughHref);
			expect(body).toContain('Look through');
		}
	});
});
