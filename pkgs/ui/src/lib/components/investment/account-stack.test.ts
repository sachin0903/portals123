import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import AccountStack, { cardKey, fallbackHref, interceptsClick } from './account-stack.svelte';
import type { Wrapper } from './types.js';

const wrapper = (over: Partial<Wrapper>): Wrapper => ({
	type: 'ISA',
	name: 'Stocks & Shares ISA',
	owner: 'Test Owner',
	value: 1000,
	gain: 50,
	gainPct: 0.05,
	trend: [1, 1.1],
	...over
});

// The family6 shape that used to crash the deck: repeated wrapper types, no hrefs.
const familyDeck: Wrapper[] = [
	wrapper({ type: 'ISA', owner: 'Daniel Marsh' }),
	wrapper({ type: 'SIPP', owner: 'Daniel Marsh' }),
	wrapper({ type: 'ISA', owner: 'Laura Marsh', delegated: true }),
	wrapper({ type: 'JISA', name: 'Junior ISA', owner: 'Freya Marsh', delegated: true }),
	wrapper({ type: 'JISA', name: 'Junior ISA', owner: 'Rory Marsh', delegated: true })
];

describe('cardKey', () => {
	it('is unique for duplicate wrapper types without hrefs (two ISAs + two JISAs)', () => {
		// `w.href ?? w.type` handed Svelte duplicate keys here — a thrown duplicate-key error
		// blanked the whole block, and stack is the auto pick for ≤6 accounts (the family default).
		const keys = familyDeck.map(cardKey);
		expect(new Set(keys).size).toBe(familyDeck.length);
	});

	it('still keys off the href when one exists', () => {
		expect(cardKey(wrapper({ href: '/accounts/abc-123' }), 3)).toContain('/accounts/abc-123');
	});
});

describe('fallbackHref', () => {
	it('URL-encodes free-text product types', () => {
		expect(fallbackHref('Offshore Bond')).toBe('/accounts/Offshore%20Bond');
		expect(fallbackHref('ISA')).toBe('/accounts/ISA');
	});
});

describe('interceptsClick', () => {
	it('a deck of one navigates on the first click — there is nothing to fan open', () => {
		expect(interceptsClick(1, false)).toBe(false);
	});

	it('a stacked deck expands on the first click, navigates on the second', () => {
		expect(interceptsClick(5, false)).toBe(true);
		expect(interceptsClick(5, true)).toBe(false);
	});
});

describe('AccountStack (SSR)', () => {
	it('renders every card of a deck with duplicate types and no hrefs', () => {
		const body = render(AccountStack, { props: { wrappers: familyDeck } }).body;
		for (const owner of ['Daniel Marsh', 'Laura Marsh', 'Freya Marsh', 'Rory Marsh'])
			expect(body).toContain(owner);
	});

	it('announces the collapsed deck as expandable', () => {
		const body = render(AccountStack, { props: { wrappers: familyDeck } }).body;
		expect(body).toContain('aria-expanded="false"');
		expect(body).toContain('Expands the account stack');
	});

	it('a single card is a plain link: no expanded state, no expansion hint', () => {
		const body = render(AccountStack, { props: { wrappers: [wrapper({})] } }).body;
		expect(body).not.toContain('aria-expanded');
		expect(body).not.toContain('Expands the account stack');
	});

	it('encodes the fallback href for a free-text product type', () => {
		const body = render(AccountStack, {
			props: { wrappers: [wrapper({ type: 'Offshore Bond' })] }
		}).body;
		expect(body).toContain('href="/accounts/Offshore%20Bond"');
	});

	it("renders an implausible gain percentage as '—', not +1,287.0%", () => {
		const body = render(AccountStack, {
			props: { wrappers: [wrapper({ gainPct: 12.87 })] }
		}).body;
		expect(body).toContain('—');
		expect(body).not.toContain('1,287');
	});

	it("renders a zero-change return neutrally — '0.0%', never a green '+0.0%'", () => {
		const body = render(AccountStack, {
			props: { wrappers: [wrapper({ gainPct: 0, gain: 0 })] }
		}).body;
		expect(body).toContain('0.0%');
		expect(body).not.toContain('+0.0%');
		expect(body).not.toContain('--hero-positive');
	});

	it("group_by 'owner' re-orders an interleaved deck grouped-adjacent, stable within group", () => {
		const interleaved: Wrapper[] = [
			wrapper({ name: 'Daniel ISA', owner: 'Daniel Marsh' }),
			wrapper({ type: 'JISA', name: 'Freya JISA', owner: 'Freya Marsh' }),
			wrapper({ type: 'SIPP', name: 'Daniel SIPP', owner: 'Daniel Marsh' }),
			wrapper({ name: 'Laura ISA', owner: 'Laura Marsh' })
		];
		const body = render(AccountStack, {
			props: { wrappers: interleaved, groupBy: 'owner' }
		}).body;
		// First-appearance group order, original order within: Daniel ×2, then Freya, then Laura.
		const order = ['Daniel ISA', 'Daniel SIPP', 'Freya JISA', 'Laura ISA'].map((n) =>
			body.indexOf(n)
		);
		for (const pos of order) expect(pos).toBeGreaterThan(-1);
		expect([...order]).toEqual([...order].slice().sort((a, b) => a - b));
		// Each card wears its owner as a tiny eyebrow.
		expect(body).toContain('uppercase');
	});

	it('the wrapper-axis eyebrow is skipped when it would only repeat the type badge', () => {
		const body = render(AccountStack, {
			props: { wrappers: familyDeck, groupBy: 'wrapper' }
		}).body;
		// Every group label equals the card's own type badge, so no eyebrow renders at all.
		expect(body).not.toContain('uppercase');
	});

	it('showSparklines={false} removes the sparkline zone entirely', () => {
		// GIA/SIPP (no allowance) fall back to the sparkline — the very cards the switch targets.
		const deck = [wrapper({ type: 'GIA' }), wrapper({ type: 'SIPP' })];
		expect(render(AccountStack, { props: { wrappers: deck } }).body).toContain('<svg');
		expect(
			render(AccountStack, { props: { wrappers: deck, showSparklines: false } }).body
		).not.toContain('<svg');
	});
});
