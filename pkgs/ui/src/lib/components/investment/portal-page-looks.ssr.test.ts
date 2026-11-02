import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import type { BrandingBlock } from '@graphene/auth';
import { personaByKey } from '../../fixtures/personas.js';
import PortalPage from './portal-page.svelte';

/**
 * The look DISPATCH through PortalPage: that `finish` + a block's `look` setting reach
 * resolveLook and select the right component, and that the default path (no finish, no look)
 * stays the carded rendering. The look components themselves are smoke-tested directly in
 * investment-looks.ssr.test.ts.
 */

const family = personaByKey('family6');

function html(blocks: BrandingBlock[], props: Record<string, unknown> = {}): string {
	return render(PortalPage, {
		props: { blocks, data: family.data, interactive: false, ...props }
	}).body;
}

describe('PortalPage look dispatch (SSR)', () => {
	it('no finish + no look setting keeps the carded rendering (the regression contract)', () => {
		// The stack variant is card-less BY DESIGN (a deck of hero chips), so the regression pin
		// is the deck's own signature: the expandable-stack affordance and the overlap cut.
		const body = html([{ type: 'accounts', settings: { variant: 'stack' } }]);
		expect(body).toContain('Expands the account stack');
		expect(body).toContain('-mt-[3.25rem]');
		// …and a table variant still gets its Card.Root chrome.
		const table = html([{ type: 'accounts', settings: { variant: 'table' } }]);
		expect(table).toContain('data-slot="card"');
	});

	it("finish 'ledger' + look:auto resolves accounts to ink — no Card.Root anywhere", () => {
		const body = html([{ type: 'accounts', settings: { variant: 'stack' } }], {
			finish: 'ledger'
		});
		expect(body).not.toContain('data-slot="card"');
		// The ink register's display-face values.
		expect(body).toContain('var(--money-display, var(--font-sans))');
	});

	it("an explicit look wins over the finish: look 'card' on a ledger portal stays carded", () => {
		const body = html([{ type: 'accounts', settings: { look: 'card', variant: 'table' } }], {
			finish: 'ledger'
		});
		expect(body).toContain('data-slot="card"');
	});

	it("an explicit look 'float' on a card portal renders the chips, chrome-less", () => {
		const body = html([{ type: 'accounts', settings: { look: 'float' } }]);
		expect(body).not.toContain('data-slot="card"');
		// The categorical chip faces.
		expect(body).toContain('oklch(from var(--brand)');
	});

	it('float honours group_by owner through grouping.ts', () => {
		const body = html([{ type: 'accounts', settings: { look: 'float', group_by: 'owner' } }]);
		expect(body).toContain('Daniel Marsh');
		expect(body).toContain('£298,241');
	});

	it("allocation look 'spectrum' renders the band instead of the carded donut", () => {
		const body = html([{ type: 'allocation', settings: { look: 'spectrum' } }]);
		expect(body).not.toContain('data-slot="card"');
		expect(body).toContain('flex:68 68 0%');
	});

	it("holdings look 'sheet' statements the same five rows the carded top-holdings shows", () => {
		const body = html([{ type: 'holdings', settings: { look: 'sheet' } }]);
		expect(body).not.toContain('data-slot="card"');
		// family6 holds 10 positions; the compact default caps at 5 — look ⊥ variant.
		expect(body).toContain('Vanguard FTSE Global All Cap');
		expect(body).not.toContain('L&amp;G Global Property REIT Index');
	});

	it('a paginated table-variant sheet keeps its ?page= links', () => {
		const body = html([{ type: 'holdings', settings: { variant: 'table', look: 'sheet' } }], {
			data: { ...family.data, holdings_total: 60 },
			pagination: { page: 2, pageSize: 20 }
		});
		expect(body).toContain('href="?page=1"');
	});

	it('junk look values degrade to the auto path, never a crash', () => {
		// No finish ⇒ auto ⇒ the carded path — for the default (stack) variant that is the
		// card-less deck, so pin the deck signature rather than a Card slot.
		const body = html([{ type: 'accounts', settings: { look: 'quantum' } }]);
		expect(body).toContain('Expands the account stack');
	});

	it('portfolioHeader ignores the looks axis entirely (its variants ARE its looks)', () => {
		const body = html([{ type: 'portfolioHeader', settings: { variant: 'ledger' } }], {
			finish: 'ledger'
		});
		expect(body).toContain('Portfolio value');
	});
});
