import { render } from 'svelte/server';
import { describe, expect, it } from 'vitest';
import HoldingsGrouped from './holdings-grouped.svelte';
import LookThroughButton from './look-through-button.svelte';
import type { Holding } from './types';

describe('LookThroughButton', () => {
	it('is an anchor with the label when given a href', () => {
		const { body } = render(LookThroughButton, { props: { href: '/look-through/f1' } });
		expect(body).toMatch(/<a[^>]*href="\/look-through\/f1"[^>]*data-slot="look-through"/);
		expect(body).toContain('Look through');
		expect(body).toContain('look-through-sheen');
	});

	it('is a decorative span without one, for a row that is already the link', () => {
		const { body } = render(LookThroughButton, { props: {} });
		expect(body).not.toContain('<a');
		expect(body).toMatch(/<span[^>]*data-slot="look-through"[^>]*aria-hidden="true"/);
	});
});

describe('the grouped holdings row', () => {
	const fund: Holding = {
		name: 'IFSL Global Equity Fund',
		wrapper: 'ISA',
		units: 10,
		price: 100,
		value: 1_000,
		gainPct: 0.1,
		lookthroughHref: '/look-through/fund1?account=account1'
	};

	it('shows the button inside the row link, never a second anchor', () => {
		const { body } = render(HoldingsGrouped, { props: { holdings: [fund] } });
		expect(body).toContain('href="/look-through/fund1?account=account1"');
		expect(body).toContain('data-slot="look-through"');
		// One anchor per row: the button inside it is a span.
		expect(body.match(/<a\s/g)).toHaveLength(1);
		expect(body).not.toContain('lucide-chevron-right');
	});

	it('keeps the chevron for a row whose link is the holding itself', () => {
		const { body } = render(HoldingsGrouped, {
			props: { holdings: [{ ...fund, href: '/accounts/a1/transactions' }] }
		});
		expect(body).not.toContain('data-slot="look-through"');
		expect(body).toContain('lucide-chevron-right');
	});
});
