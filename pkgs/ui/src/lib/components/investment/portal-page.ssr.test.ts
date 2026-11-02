import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import type { BrandingBlock } from '@graphene/auth';
import { personaByKey } from '../../fixtures/personas.js';
import PortalPage from './portal-page.svelte';

/**
 * SSR smoke tests for the variant dispatch — the parts of Phase 4 a pure-function test can't
 * see: that a concrete variant name reaching PortalPage renders the variant COMPONENT, that the
 * summary derives its groups client-side when `wrapper_groups` didn't ride the payload (the
 * personas carry none), and that a paginated table emits plain `?page=` links.
 */

const trust = personaByKey('trust300');
const family = personaByKey('family6');

function html(blocks: BrandingBlock[], props: Record<string, unknown> = {}): string {
	return render(PortalPage, {
		props: { blocks, data: trust.data, interactive: false, ...props }
	}).body;
}

describe('PortalPage variant dispatch (SSR)', () => {
	it('accounts summary renders from DERIVED groups — the persona has no wrapper_groups', () => {
		expect(trust.data.wrapper_groups).toBeUndefined();
		const body = html([{ type: 'accounts', settings: { variant: 'summary' } }]);
		// The headline count, the single derived GIA bucket and the view-all affordance.
		expect(body).toContain('300 accounts');
		expect(body).toContain('GIA');
		expect(body).toContain('View all');
		// 300 wrappers must NOT have rendered as a stack/cards/table of rows.
		expect(body).not.toContain('Portfolio 001');
	});

	it('"View all" follows the firm’s page set, and is absent where the set has no page', () => {
		const blocks: BrandingBlock[] = [
			{ type: 'accounts', settings: { variant: 'summary' } },
			{ type: 'holdings', settings: { variant: 'top' } },
			{ type: 'activity', settings: { variant: 'list' } }
		];
		const body = html(blocks, {
			interactive: true,
			links: { accounts: '/my-accounts', holdings: null, activity: '/portfolio', documents: null }
		});
		expect(body).toContain('href="/my-accounts"');
		expect(body).toContain('href="/portfolio"');
		// The holdings card draws no "View all" at all — not one to a 404.
		expect(body).not.toContain('href="/holdings"');
		expect((body.match(/View all/g) ?? []).length).toBe(2);
	});

	it('without a page set (legacy client-home) the built-in slugs stand', () => {
		const body = html([{ type: 'holdings', settings: { variant: 'top' } }], { interactive: true });
		expect(body).toContain('href="/holdings"');
	});

	it('a paginated holdings table gets plain ?page= links', () => {
		const body = html([{ type: 'holdings', settings: { variant: 'table' } }], {
			data: { ...trust.data, holdings_total: 60 },
			pagination: { page: 2, pageSize: 20 }
		});
		expect(body).toContain('href="?page=1"');
		expect(body).toContain('href="?page=3"');
		expect(body).toContain('aria-current="page"');
	});

	it('an unpaginated table variant renders no pagination', () => {
		const body = html([{ type: 'holdings', settings: { variant: 'table' } }], {
			pagination: { page: 1, pageSize: 50 }
		});
		expect(body).not.toContain('?page=');
	});

	it('show_chart === false forces the compact strip even on hero', () => {
		const body = html([
			{ type: 'portfolioHeader', settings: { variant: 'hero', show_chart: false } }
		]);
		expect(body).toContain('Portfolio value');
		// The compact strip: one line, the change chip, and no hero-only period toggle.
		expect(body).toContain('hero-surface rounded-xl px-5 py-4');
		expect(body).not.toContain('aria-pressed');
	});

	it('allocation bars renders rows, not the donut', () => {
		const body = html([{ type: 'allocation', settings: { variant: 'bars' } }]);
		expect(body).toContain('Equities');
		expect(body).not.toContain('Asset allocation'); // the donut's aria-label
	});

	it('a summarised book with no wrapper_groups shows the empty state, never a one-page total', () => {
		// The server said "the wrappers slice is partial" (wrappers_summarised) but the groups
		// went missing: deriving from the slice would invent '300 accounts · £<one page>'.
		const body = html([{ type: 'accounts', settings: { variant: 'summary' } }], {
			data: {
				...trust.data,
				wrappers: trust.data.wrappers?.slice(0, 3),
				wrappers_summarised: true,
				wrapper_groups: undefined,
				wrappers_total: 300
			}
		});
		expect(body).toContain('Your accounts will appear here');
		expect(body).not.toContain('300 accounts');
	});

	it('pageHref overrides the bare ?page= default, preserving other params', () => {
		const body = html([{ type: 'holdings', settings: { variant: 'table' } }], {
			data: { ...trust.data, holdings_total: 60 },
			pagination: { page: 2, pageSize: 20 },
			pageHref: (n: number) => `?tab=growth&page=${n}`
		});
		expect(body).toContain('href="?tab=growth&amp;page=1"');
		expect(body).not.toContain('href="?page=1"');
	});

	it('a paginated table disables its sort headers rather than sorting one page', () => {
		const body = html([{ type: 'holdings', settings: { variant: 'table' } }], {
			data: { ...trust.data, holdings_total: 60 },
			pagination: { page: 1, pageSize: 20 }
		});
		expect(body).toContain('aria-disabled="true"');
		expect(body).toContain('Sorting the full list isn’t available yet.');
	});

	it('an unpaginated table keeps live sort headers', () => {
		const body = html([{ type: 'holdings', settings: { variant: 'table' } }]);
		expect(body).not.toContain('Sorting the full list isn’t available yet.');
	});

	it('an unknown block type shows a generic placeholder, never the raw type name', () => {
		const body = html([{ type: 'quantumWidget' }]);
		expect(body).toContain('Nothing to show here');
		// The machine name is data-attribute-only (support/debugging), not copy.
		expect(body).toContain('data-unknown-block-type="quantumWidget"');
		expect(body).not.toContain('>quantumWidget<');
	});

	it('the documents empty state carries no link to a guaranteed-empty page', () => {
		const body = html([{ type: 'documents' }], {
			data: { ...trust.data, documents: [] }
		});
		expect(body).toContain('Your documents will appear here');
		expect(body).not.toContain('View your documents');
	});

	it('an enabled news widget keeps its allocated cell when there are no updates', () => {
		const body = html([{ type: 'news', h: 2 }], {
			data: { ...trust.data, news: [] }
		});
		expect(body).toContain('You’re up to date');
		expect(body).toContain('New insights and updates from your firm will appear here.');
		expect(body).toContain('data-block-id="news"');
	});

	it('two id-less blocks of the same type both render (index-suffixed keys)', () => {
		const body = html([
			{ type: 'message', settings: { title: 'First note', body: 'a' } },
			{ type: 'message', settings: { title: 'Second note', body: 'b' } }
		]);
		expect(body).toContain('First note');
		expect(body).toContain('Second note');
	});
});

describe('the accounts group_by + show_sparklines settings (SSR, family6)', () => {
	function familyHtml(settings: Record<string, unknown>): string {
		return render(PortalPage, {
			props: {
				blocks: [{ type: 'accounts', settings }] as BrandingBlock[],
				data: family.data,
				interactive: false
			}
		}).body;
	}

	it("cards + group_by 'owner': one section per family member, with a Money subtotal", () => {
		const body = familyHtml({ variant: 'cards', group_by: 'owner' });
		// Four owner sections, in the slice's first-appearance order.
		const owners = ['Daniel Marsh', 'Laura Marsh', 'Freya Marsh', 'Rory Marsh'];
		const positions = owners.map((o) => body.indexOf(o));
		for (const [i, pos] of positions.entries()) expect(pos, owners[i]).toBeGreaterThan(-1);
		expect([...positions]).toEqual([...positions].slice().sort((a, b) => a - b));
		// Daniel's subtotal: 51,230.40 + 214,600.00 + 32,410.75 (Money splits pence into a
		// nested span, so assert the major part).
		expect(body).toContain('£298,241');
		// Laura's single-ISA subtotal.
		expect(body).toContain('£44,880');
	});

	it("cards + group_by 'wrapper': sections by product type", () => {
		const body = familyHtml({ variant: 'cards', group_by: 'wrapper' });
		// The JISA section subtotal: 7,420.60 + 5,210.30.
		expect(body).toContain('£12,630');
	});

	it('cards without grouping renders no section subtotals', () => {
		const body = familyHtml({ variant: 'cards' });
		expect(body).not.toContain('£298,241');
	});

	it("a JISA card reads as the child's: 'For {owner}' replaces the bare owner line", () => {
		const body = familyHtml({ variant: 'cards' });
		expect(body).toContain('For Freya Marsh');
		expect(body).toContain('For Rory Marsh');
		// The adults keep the plain owner line.
		expect(body).not.toContain('For Daniel Marsh');
	});

	it('stack: show_sparklines false removes every sparkline svg', () => {
		// family6's SIPP + GIA carry no allowance, so the default deck HAS sparklines.
		expect(familyHtml({ variant: 'stack' })).toContain('<svg');
		expect(familyHtml({ variant: 'stack', show_sparklines: false })).not.toContain('<svg');
	});

	it("stack + group_by 'owner': each card wears its owner eyebrow", () => {
		const body = familyHtml({ variant: 'stack', group_by: 'owner' });
		expect(body).toContain('uppercase');
		// The eyebrow repeats the owner alongside the name line — each owner appears twice.
		const count = body.split('Laura Marsh').length - 1;
		expect(count).toBeGreaterThanOrEqual(2);
	});

	it('junk group_by degrades to the flat look, never a crash', () => {
		const body = familyHtml({ variant: 'cards', group_by: 'product_type' });
		expect(body).toContain('Daniel Marsh');
		expect(body).not.toContain('£298,241');
	});
});

describe('the accounts summary exceptions line (SSR)', () => {
	it('states the unvalued count when the slice is the whole book (trust300 has 20)', () => {
		const body = html([{ type: 'accounts', settings: { variant: 'summary' } }]);
		expect(body).toContain('20 accounts awaiting valuation');
	});

	it('SKIPS the claim when the server summarised — a partial slice cannot count the book', () => {
		const body = html([{ type: 'accounts', settings: { variant: 'summary' } }], {
			data: {
				...trust.data,
				wrappers: trust.data.wrappers?.slice(0, 3),
				wrappers_summarised: true,
				wrapper_groups: [{ product_type: 'GIA', count: 300, value: 1 }],
				wrappers_total: 300
			}
		});
		expect(body).toContain('300 accounts');
		expect(body).not.toContain('awaiting valuation');
	});

	it("summary + group_by 'owner' re-axes the derived tiles", () => {
		const body = render(PortalPage, {
			props: {
				blocks: [
					{ type: 'accounts', settings: { variant: 'summary', group_by: 'owner' } }
				] as BrandingBlock[],
				data: family.data,
				interactive: false
			}
		}).body;
		expect(body).toContain('Daniel Marsh');
		expect(body).toContain('Freya Marsh');
	});
});

describe('allocation negative-slice handling (SSR)', () => {
	it('excludes an overdrawn cash slice from the bars and discloses the £ excluded', () => {
		const body = html([{ type: 'allocation', settings: { variant: 'bars' } }], {
			data: {
				...trust.data,
				totals: { value: 95000, gain: 0, gainPct: 0 },
				allocations: [
					{ label: 'Equities', value: 70, color: 'var(--primary)' },
					{ label: 'Bonds', value: 30, color: 'var(--brand)' },
					{ label: 'Cash', value: -5, color: 'var(--muted-foreground)' }
				]
			}
		});
		expect(body).toContain('Excludes negative balances (−£5,000.00)');
		// The negative slice must not have rendered a bar row.
		expect(body).not.toContain('width:-');
	});

	it('the donut renders no footnote for a clean book', () => {
		const body = html([{ type: 'allocation' }]);
		expect(body).not.toContain('Excludes negative balances');
	});
});
