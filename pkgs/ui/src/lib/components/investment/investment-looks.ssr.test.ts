import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import { personaByKey } from '../../fixtures/personas.js';
import AccountsFloat from './accounts-float.svelte';
import AccountsInk from './accounts-ink.svelte';
import AllocationOrbit from './allocation-orbit.svelte';
import AllocationSpectrum from './allocation-spectrum.svelte';
import HoldingsSheet from './holdings-sheet.svelte';
import HoldingsValueBars from './holdings-value-bars.svelte';

/**
 * SSR smoke tests for the six chrome-less LOOK components, rendered directly with real persona
 * data (deliberately not through PortalPage, so these stay green independent of the dispatch).
 * The shared assertion: none of them ever emits a Card slot — the whole point of a look is that
 * the block sits on the page ground.
 */

const family = personaByKey('family6');
const individual = personaByKey('individual2');
const trust = personaByKey('trust300');

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function html(component: any, props: Record<string, unknown>): string {
	return render(component, { props }).body;
}

describe('accounts float (SSR, family6)', () => {
	const body = html(AccountsFloat, {
		wrappers: family.data.wrappers,
		groupBy: 'owner',
		interactive: false
	});

	it('never wraps itself in a card', () => {
		expect(body).not.toContain('data-slot="card"');
	});

	it('clusters chips per owner, in first-appearance order, with Money subtotals', () => {
		const owners = ['Daniel Marsh', 'Laura Marsh', 'Freya Marsh', 'Rory Marsh'];
		const positions = owners.map((o) => body.indexOf(o));
		for (const [i, pos] of positions.entries()) expect(pos, owners[i]).toBeGreaterThan(-1);
		expect([...positions]).toEqual([...positions].slice().sort((a, b) => a - b));
		// Daniel's subtotal (Money splits pence into a nested span — assert the major part).
		expect(body).toContain('£298,241');
	});

	it('every chip wears its categorical face and product type', () => {
		// The relative-colour ramp off --brand — the stack's face scale, reused.
		expect(body).toContain('oklch(from var(--brand)');
		expect(body).toContain('ISA');
		expect(body).toContain('SIPP');
	});

	it('links each chip to its account', () => {
		expect(body).toContain('href="/accounts/ISA"');
	});

	it('flat (no grouping) renders no subtotals', () => {
		const flat = html(AccountsFloat, { wrappers: family.data.wrappers, interactive: false });
		expect(flat).not.toContain('£298,241');
		expect(flat).toContain('Daniel Marsh');
	});

	it('empty book: a quiet page-ground note, still no card', () => {
		const empty = html(AccountsFloat, { wrappers: [], interactive: false });
		expect(empty).toContain('Your accounts will appear here');
		expect(empty).not.toContain('data-slot="card"');
	});
});

describe('accounts ink (SSR, family6)', () => {
	const body = html(AccountsInk, { wrappers: family.data.wrappers, groupBy: 'owner' });

	it('never wraps itself in a card and paints no face gradients — pure typography', () => {
		expect(body).not.toContain('data-slot="card"');
		expect(body).not.toContain('linear-gradient');
	});

	it('opens with the register eyebrow', () => {
		expect(body).toContain('Accounts');
		expect(body).toContain('uppercase');
	});

	it('sections per owner with subtotals in the display numeral face', () => {
		expect(body).toContain('Daniel Marsh');
		expect(body).toContain('£298,241');
		expect(body).toContain('var(--money-display, var(--font-sans))');
	});

	it('rows carry the small-caps type prefix and the owner set small beneath the name', () => {
		expect(body).toContain('JISA');
		expect(body).toContain('Junior ISA');
	});

	it('a delegated account says so inline', () => {
		expect(body).toContain('· delegated');
	});

	it('empty book: the eyebrow stays, a quiet note follows', () => {
		const empty = html(AccountsInk, { wrappers: [] });
		expect(empty).toContain('Your accounts will appear here');
	});
});

describe('allocation orbit (SSR, individual2)', () => {
	const body = html(AllocationOrbit, {
		data: individual.data.allocations,
		total: individual.data.totals?.value,
		interactive: false
	});

	it('never wraps itself in a card', () => {
		expect(body).not.toContain('data-slot="card"');
	});

	it('renders the ring complete (SSR shows the final state, not an empty sweep)', () => {
		expect(body).toContain('aria-label="Asset allocation"');
		// A drawn arc path — an empty pre-sweep donut would emit empty `d` attributes.
		expect(body).toMatch(/d="M[^"]+A/);
	});

	it('sets the total at centre in the display face', () => {
		expect(body).toContain('var(--font-display, var(--font-sans))');
		expect(body).toContain('Total');
	});

	it('legend chips carry label + largest-remainder % (sums to 100)', () => {
		for (const [label, pct] of [
			['Equities', '64%'],
			['Bonds', '24%'],
			['Property', '7%'],
			['Cash', '5%']
		])
			expect(body, `${label} ${pct}`).toMatch(new RegExp(`${label}[\\s\\S]{0,200}${pct}`));
	});

	it('discloses excluded negative slices', () => {
		const negative = html(AllocationOrbit, {
			data: [
				{ label: 'Equities', value: 70, color: 'var(--primary)' },
				{ label: 'Cash', value: -5, color: 'var(--muted-foreground)' }
			],
			total: 65000,
			interactive: false
		});
		expect(negative).toContain('Excludes negative balances (−£5,000.00)');
	});

	it('empty allocations: a quiet note, no allocation chart', () => {
		const empty = html(AllocationOrbit, { data: [], interactive: false });
		expect(empty).toContain('Your allocation will appear here');
		expect(empty).not.toContain('aria-label="Asset allocation"');
	});
});

describe('allocation spectrum (SSR, individual2)', () => {
	const body = html(AllocationSpectrum, {
		data: individual.data.allocations,
		total: individual.data.totals?.value
	});

	it('never wraps itself in a card, and carries no heading at all (intentional)', () => {
		expect(body).not.toContain('data-slot="card"');
		expect(body).not.toMatch(/<h[1-6]/);
	});

	it('one proportional band, flex-grown per slice', () => {
		expect(body).toContain(
			'aria-label="Asset allocation: Equities 64%, Bonds 24%, Property 7%, Cash 5%"'
		);
		expect(body).toContain('flex:64 64 0%');
		expect(body).toContain('flex:5 5 0%');
	});

	it('labels wide segments under the band; small ones collapse into "+2 more"', () => {
		expect(body).toContain('Equities');
		expect(body).toContain('Bonds');
		expect(body).toContain('+2 more');
	});

	it('a negative slice never becomes a segment, and is disclosed', () => {
		const negative = html(AllocationSpectrum, {
			data: [
				{ label: 'Equities', value: 70, color: 'var(--primary)' },
				{ label: 'Cash', value: -5, color: 'var(--muted-foreground)' }
			],
			total: 65000
		});
		expect(negative).toContain('Excludes negative balances (−£5,000.00)');
		expect(negative).not.toContain('flex:-');
	});

	it('empty allocations: a quiet note, no band', () => {
		const empty = html(AllocationSpectrum, { data: [] });
		expect(empty).toContain('Your allocation will appear here');
		expect(empty).not.toContain('rounded-full');
	});
});

describe('holdings sheet (SSR, trust300 slice)', () => {
	const slice = trust.data.holdings!.slice(0, 8);
	const body = html(HoldingsSheet, { holdings: slice, asOf: '2026-07-29' });

	it('never wraps itself in a card — a real table on the page ground', () => {
		expect(body).not.toContain('data-slot="card"');
		expect(body).toContain('<table');
	});

	it('opens with the eyebrow and the honest as-at stamp', () => {
		expect(body).toContain('Holdings');
		expect(body).toContain('as at 29 Jul 2026');
	});

	it('never fabricates a stamp when the payload carried none', () => {
		const unstamped = html(HoldingsSheet, { holdings: slice });
		expect(unstamped).not.toContain('as at');
	});

	it('small-caps column heads over a strong top rule', () => {
		expect(body).toContain('border-t-2');
		for (const head of ['Investment', 'Account', 'Units', 'Price', 'Value', 'Return'])
			expect(body).toContain(head);
	});

	it('rows carry the positions with money in the display face', () => {
		expect(body).toContain('Vanguard FTSE Global All Cap');
		expect(body).toContain('var(--money-display, var(--font-sans))');
	});

	it('low-priority columns collapse by CONTAINER width', () => {
		expect(body).toContain('@container');
		expect(body).toContain('@2xl:table-cell');
	});

	it('empty book: eyebrow + quiet note, no table', () => {
		const empty = html(HoldingsSheet, { holdings: [] });
		expect(empty).toContain('Your funds and positions will appear here.');
		expect(empty).not.toContain('<table');
	});
});

describe('holdings value bars (SSR, trust300 slice)', () => {
	const slice = trust.data.holdings!.slice(0, 8);
	const body = html(HoldingsValueBars, { holdings: slice });

	it('never wraps itself in a card', () => {
		expect(body).not.toContain('data-slot="card"');
	});

	it('the largest holding fills the track; the rest scale against it', () => {
		expect(body).toContain('width:100%;background:var(--brand)');
		// At least one partial bar (the slice values differ).
		expect(body).toMatch(/width:\d{1,2}(\.\d+)?%;background:var\(--brand\)/);
	});

	it('tracks are the brand washed soft', () => {
		expect(body).toContain('color-mix(in oklab, var(--brand) 14%, transparent)');
	});

	it('an unvalued position renders an empty track, not a lying sliver', () => {
		const withZero = html(HoldingsValueBars, {
			holdings: [
				{ name: 'Valued Fund', wrapper: 'GIA', units: 1, price: 100, value: 100, gainPct: 0 },
				{ name: 'Unvalued Fund', wrapper: 'GIA', units: 1, price: 0, value: 0, gainPct: 0 }
			]
		});
		expect(withZero).toContain('width:0%;background:var(--brand)');
	});

	it('empty book: eyebrow + quiet note', () => {
		const empty = html(HoldingsValueBars, { holdings: [] });
		expect(empty).toContain('Your funds and positions will appear here.');
	});
});
