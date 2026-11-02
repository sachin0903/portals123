import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import Card from './card.svelte';
import CardTitle from './card-title.svelte';

/**
 * SSR pins for the finish-axis refactor. The card's surface (background/border/divider) moved off
 * the class list into theme.css's `[data-slot='card']` components-layer rule so the ledger finish
 * can re-point it via tokens; these tests pin what MUST stay on the markup for that contract to
 * hold (the data-slot hooks, token-reading elevation classes, the glass branch) and what must NOT
 * creep back (hardcoded `bg-card`/`border-border` would outrank the tokens and freeze the finish).
 * The token DEFAULTS being byte-identical to the old classes is pinned separately in
 * src/styles/theme-finish.test.ts.
 */

describe('Card (SSR)', () => {
	it('renders the data-slot hook and the token-reading raised shadow by default', () => {
		const { body } = render(Card, { props: {} });
		expect(body).toContain('data-slot="card"');
		expect(body).toContain('data-size="default"');
		// Depth reads the finish token (default --surface-card-shadow: var(--elev-1) — unchanged look).
		expect(body).toContain('shadow-[var(--surface-card-shadow)]');
	});

	it('no longer hardcodes the surface — theme.css owns bg/border via the finish tokens', () => {
		const { body } = render(Card, { props: {} });
		expect(body).not.toContain('bg-card');
		expect(body).not.toContain('border-border/70');
	});

	it('maps the elevation prop onto the finish-axis shadow tokens', () => {
		expect(render(Card, { props: { elevation: 'flat' } }).body).not.toContain('shadow-[');
		expect(render(Card, { props: { elevation: 'floating' } }).body).toContain(
			'shadow-[var(--surface-card-shadow-floating)]'
		);
	});

	it('the glass branch still renders the glass-panel material, with no elevation shadow', () => {
		const { body } = render(Card, { props: { glass: true } });
		expect(body).toContain('glass-panel');
		expect(body).not.toContain('shadow-[var(--surface-card-shadow)]');
	});

	it('does not clip inline suggestion lists', () => {
		// Combobox and AddressLookup position their list inside the field. overflow-hidden on the
		// card cropped it to the panel, which reads as a dropdown that will not open.
		const { body } = render(Card, { props: {} });
		expect(body).toContain('overflow-visible');
		expect(body).not.toContain('overflow-hidden');
		// overflow-visible is not enough on its own: glass cards (backdrop-filter) and any later
		// sibling paint ON TOP of an earlier card's escaped list. The card that holds the open
		// list has to sit above its neighbours — same idea as account-stack's focus-visible:z-10.
		expect(body).toContain('focus-within:z-[1]');
		expect(body).toContain('has-[[role=listbox]]:z-[1]');
	});

	it('caller surface overrides still merge in (utilities beat the components-layer rule)', () => {
		const { body } = render(Card, { props: { class: 'bg-warning-subtle border-warning-line' } });
		expect(body).toContain('bg-warning-subtle');
		expect(body).toContain('border-warning-line');
	});
});

describe('Card.Title (SSR)', () => {
	it('announces itself as a heading — screen-reader heading navigation must not skip cards', () => {
		const { body } = render(CardTitle, { props: {} });
		expect(body).toContain('role="heading"');
		expect(body).toContain('aria-level="2"');
		expect(body).toContain('data-slot="card-title"');
	});

	it('a caller can re-level the heading through restProps', () => {
		const { body } = render(CardTitle, { props: { 'aria-level': 3 } });
		expect(body).toContain('aria-level="3"');
		expect(body).not.toContain('aria-level="2"');
	});

	it('size/tracking moved to the token rule; weight and rhythm stay on the markup', () => {
		const { body } = render(CardTitle, { props: {} });
		expect(body).toContain('font-semibold');
		expect(body).toContain('leading-snug');
		// These would outrank --card-title-size/-tracking and freeze the ledger eyebrow register.
		expect(body).not.toContain('text-lg');
		expect(body).not.toContain('tracking-tight');
	});
});
