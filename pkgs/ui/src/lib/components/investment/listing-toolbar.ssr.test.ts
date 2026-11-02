import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import type { BrandingBlock } from '@graphene/auth';
import { personaByKey } from '../../fixtures/personas.js';
import type { ClientDocument, PortalPageData, Wrapper } from './types.js';
import PortalPage from './portal-page.svelte';

/**
 * SSR coverage for the listing widgets absorbing the legacy listing pages: the `toolbar`
 * setting's search/filter row (on/off, disabled-when-partial), the documents category pills +
 * unread note + `show_requests` banner (honest counts only — the leak rule), and the accounts
 * status chips. Interaction (typing, debounce) is client-side; what SSR can pin is presence,
 * absence and the disable/explain affordances.
 */

const family = personaByKey('family6');

function html(blocks: BrandingBlock[], props: Record<string, unknown> = {}): string {
	return render(PortalPage, {
		props: { blocks, data: family.data, interactive: false, ...props }
	}).body;
}

const SEARCH_DISABLED_TITLE = 'Searching the full list isn’t available yet.';

describe('the listing toolbar (SSR)', () => {
	it('holdings: toolbar renders when the setting is on…', () => {
		const body = html([{ type: 'holdings', settings: { variant: 'table', toolbar: true } }]);
		expect(body).toContain('data-widget-toolbar');
		expect(body).toContain('Search by name or ticker');
		// The account filter, derived from the slice's account labels.
		expect(body).toContain('All accounts');
	});

	it('…and is absent when off (the default for compact home blocks)', () => {
		expect(html([{ type: 'holdings', settings: { variant: 'table' } }])).not.toContain(
			'data-widget-toolbar'
		);
		expect(html([{ type: 'holdings' }])).not.toContain('data-widget-toolbar');
	});

	it('junk toolbar values read as off (`=== true`), never a crash', () => {
		expect(
			html([{ type: 'holdings', settings: { variant: 'table', toolbar: 'yes' } }])
		).not.toContain('data-widget-toolbar');
	});

	it('a PARTIAL slice with no handler disables the search with the explain-title (never a silently wrong answer)', () => {
		const body = html([{ type: 'holdings', settings: { variant: 'table', toolbar: true } }], {
			data: { ...family.data, holdings_total: 400 },
			pagination: { page: 1, pageSize: 25 }
		});
		expect(body).toContain('data-widget-toolbar');
		expect(body).toContain('aria-disabled="true"');
		expect(body).toContain(SEARCH_DISABLED_TITLE);
	});

	it('the same partial slice WITH an app handler keeps the search live (the server answers it)', () => {
		const body = html([{ type: 'holdings', settings: { variant: 'table', toolbar: true } }], {
			data: { ...family.data, holdings_total: 400 },
			onlistingchange: () => {}
		});
		expect(body).not.toContain(SEARCH_DISABLED_TITLE);
	});

	it('a controlled toolbar renders the app-supplied query text', () => {
		const body = html([{ type: 'holdings', settings: { variant: 'table', toolbar: true } }], {
			onlistingchange: () => {},
			listingQuery: { q: 'fundsmith' }
		});
		expect(body).toContain('value="fundsmith"');
	});

	it('a complete slice keeps the toolbar live', () => {
		const body = html([{ type: 'holdings', settings: { variant: 'table', toolbar: true } }], {
			data: { ...family.data, holdings_total: family.data.holdings?.length ?? 0 }
		});
		expect(body).not.toContain(SEARCH_DISABLED_TITLE);
	});

	it('activity: toolbar renders in the card look and the chrome-less timeline look', () => {
		const card = html([{ type: 'activity', settings: { variant: 'table', toolbar: true } }]);
		expect(card).toContain('data-widget-toolbar');
		expect(card).toContain('Search by description or type');
		const timeline = html([{ type: 'activity', settings: { toolbar: true, look: 'timeline' } }]);
		expect(timeline).toContain('data-widget-toolbar');
		// Chrome-less: the toolbar must not have dragged a Card in with it.
		expect(timeline).not.toContain('data-slot="card"');
	});

	// The default Activity page: the home card's list, with the toolbar on, showing EVERY row.
	// A list without a toolbar keeps the home card's six.
	it('activity: a toolbar’d list is uncapped; the home card’s list is not', () => {
		const rows = (body: string) =>
			(body.match(/<li class="flex items-center gap-3 py-3"/g) ?? []).length;
		const all = family.data.activity?.length ?? 0;
		expect(all).toBeGreaterThan(6);
		const page = html([{ type: 'activity', settings: { variant: 'list', toolbar: true } }]);
		expect(rows(page)).toBe(all);
		expect(page).not.toContain('<table');
		const home = html([{ type: 'activity', settings: { variant: 'list' } }]);
		expect(rows(home)).toBe(6);
	});

	it('an empty slice renders the empty state with NO toolbar (nothing to search)', () => {
		const body = html([{ type: 'activity', settings: { variant: 'table', toolbar: true } }], {
			data: { ...family.data, activity: [] }
		});
		expect(body).not.toContain('data-widget-toolbar');
		expect(body).toContain('Your activity will appear here');
		expect(body).toContain('Payments, dividends, trades and fees will show here as they happen.');
	});

	it('a controlled filter with no rows says no matches and keeps the toolbar', () => {
		for (const type of ['holdings', 'activity'] as const) {
			const body = html([{ type, settings: { variant: 'table', toolbar: true } }], {
				data: { ...family.data, [type]: [] },
				listingQuery: { account: 'ISA' },
				listingAccounts: ['ISA', 'SIPP'],
				onlistingchange: () => {}
			});
			expect(body).toContain('data-widget-toolbar');
			expect(body).toContain(type === 'holdings' ? 'No holdings match' : 'No activity matches');
			expect(body).not.toContain(`Your ${type} will appear here`);
		}
	});
});

describe('the documents toolbar: pills, unread note (SSR)', () => {
	const doc = (
		id: string,
		category: ClientDocument['category'],
		unread?: boolean
	): ClientDocument => ({
		id,
		name: `Document ${id}`,
		category,
		date: '1 Jun 2026',
		size: '1 KB',
		format: 'PDF',
		...(unread === undefined ? {} : { unread })
	});

	it('category pills enumerate the data with counts (legacy /documents parity)', () => {
		const body = html([{ type: 'documents', settings: { toolbar: true } }], {
			data: {
				...family.data,
				documents: [
					doc('a', 'Statements'),
					doc('b', 'Statements'),
					doc('c', 'Tax'),
					doc('d', 'Agreements')
				]
			}
		});
		expect(body).toContain('All (4)');
		expect(body).toContain('Statements (2)');
		expect(body).toContain('Tax (1)');
		expect(body).toContain('Agreements (1)');
	});

	it('the unread note renders only when the payload carries read-state', () => {
		// The default documents look is the timeline, where each new row marks itself (no note —
		// see the timeline describe below); the toolbar note belongs to the other looks. Two
		// categories, so the toolbar has pills to show and therefore renders at all (a short
		// single-category list draws no toolbar — filtered-listing.svelte).
		const shelf = (documents: ClientDocument[]) =>
			html([{ type: 'documents', settings: { toolbar: true, look: 'shelf' } }], {
				data: { ...family.data, documents }
			});
		expect(shelf([doc('a', 'Tax', true), doc('b', 'Statements', false)])).toContain('1 unread');
		// No document carries the field ⇒ no note — never implied "all read".
		expect(shelf([doc('a', 'Tax'), doc('b', 'Statements')])).not.toContain('unread');
	});
});

describe('the documents timeline look: the browse bar (SSR)', () => {
	const doc = (id: string, category: ClientDocument['category'], unread?: boolean) => ({
		id,
		name: `Document ${id}`,
		category,
		date: '1 Jun 2026',
		isoDate: '2026-06-01',
		size: '1 KB',
		format: 'PDF',
		...(unread === undefined ? {} : { unread })
	});
	const four = [
		doc('a', 'Statements'),
		doc('b', 'Statements'),
		doc('c', 'Tax'),
		doc('d', 'Agreements')
	];
	const block: BrandingBlock[] = [
		{ type: 'documents', settings: { toolbar: true, look: 'timeline' } }
	];

	it('the categories are tabs with an "All" first — no counts, no tiles', () => {
		const body = html(block, { data: { ...family.data, documents: four } });
		expect(body).toContain('data-browse-bar');
		expect(body).toContain('aria-label="Filter documents by category"');
		expect(body).toMatch(/aria-pressed="true"[^>]*>\s*All\s*</);
		expect(body).toMatch(/>\s*Statements\s*</);
		expect(body).not.toContain('All (4)');
		expect(body).not.toContain('Browse documents by category');
	});

	it('no heading, no count, no note — the nav named the page; each new row marks itself', () => {
		const body = html(block, {
			data: { ...family.data, documents: [doc('a', 'Tax', true), doc('b', 'Statements', false)] }
		});
		expect(body).not.toContain('in total');
		expect(body).not.toContain('unread');
		expect(body).not.toMatch(/<h2[^>]*>\s*Documents\s*</);
		expect(body.split('(new)').length - 1).toBe(1);
	});

	it('search is a button only once the list outgrows a glance', () => {
		expect(html(block, { data: { ...family.data, documents: four } })).not.toContain(
			'aria-label="Search documents"'
		);
		const many = Array.from({ length: 9 }, (_, i) => doc(`d${i}`, i % 2 ? 'Tax' : 'Statements'));
		const body = html(block, { data: { ...family.data, documents: many } });
		expect(body).toContain('aria-label="Search documents"');
		// A button that opens the portal's palette — never a second search box on the page.
		expect(body).not.toContain('type="search"');
	});
});

describe('the documents show_requests banner (SSR) — honest counts only', () => {
	const on: BrandingBlock[] = [{ type: 'documents', settings: { show_requests: true } }];

	it('renders for a real fetched count, linking to /requests when interactive', () => {
		const body = render(PortalPage, {
			props: {
				blocks: on,
				data: { ...family.data, requests_outstanding: 3 } as PortalPageData,
				interactive: true
			}
		}).body;
		expect(body).toContain('data-requests-banner');
		expect(body).toContain('3 document requests need your attention');
		expect(body).toContain('href="/requests"');
	});

	it('singular grammar for one request', () => {
		const body = html(on, { data: { ...family.data, requests_outstanding: 1 } });
		expect(body).toContain('1 document request needs your attention');
	});

	it('inert (no link) when not interactive — the studio preview', () => {
		const body = html(on, { data: { ...family.data, requests_outstanding: 3 } });
		expect(body).toContain('data-requests-banner');
		expect(body).not.toContain('href="/requests"');
	});

	it('NO banner when the loader fetched no count (the leak rule: absent ⇒ nothing, never a fake number)', () => {
		expect(html(on)).not.toContain('data-requests-banner');
		expect(html(on, { data: { ...family.data, requests_outstanding: null } })).not.toContain(
			'data-requests-banner'
		);
	});

	it('NO banner for zero outstanding (a real answer, nothing to flag)', () => {
		expect(html(on, { data: { ...family.data, requests_outstanding: 0 } })).not.toContain(
			'data-requests-banner'
		);
	});

	it('NO banner when the setting is off, whatever the count', () => {
		const body = html([{ type: 'documents' }], {
			data: { ...family.data, requests_outstanding: 3 }
		});
		expect(body).not.toContain('data-requests-banner');
	});

	it('the studio preview (data === null) shows the fixture banner, so firms see what they toggle', () => {
		const body = html(on, { data: null });
		expect(body).toContain('data-requests-banner');
		expect(body).toContain('2 document requests need your attention');
	});
});

describe('accounts status chips (SSR) — legacy /accounts parity', () => {
	const withStatus = (status: string | undefined, i: number): Wrapper => ({
		...(family.data.wrappers?.[i % (family.data.wrappers?.length ?? 1)] as Wrapper),
		name: `Wrapper ${i}`,
		...(status === undefined ? {} : { status })
	});
	const data: PortalPageData = {
		...family.data,
		wrappers: [withStatus('active', 0), withStatus('pending', 1), withStatus(undefined, 2)]
	};

	it('the table variant chips a non-active wrapper, and only that one', () => {
		const body = html([{ type: 'accounts', settings: { variant: 'table' } }], { data });
		expect(body).toContain('data-slot="status-badge"');
		expect(body).toContain('Pending');
		// Exactly one chip: active + status-less rows stay chip-less.
		expect(body.split('data-slot="status-badge"').length - 1).toBe(1);
	});

	it('the cards variant chips a non-active wrapper — as the same quiet pill as "Delegated", not a badge', () => {
		// On the hero face a status colour would fight the categorical palette (account-cards.svelte).
		const body = html([{ type: 'accounts', settings: { variant: 'cards' } }], { data });
		expect(body).toContain('Pending');
		expect(body).not.toContain('data-slot="status-badge"');
	});

	it('the ink look marks status as ink, not a badge (its no-fills grammar)', () => {
		const body = html([{ type: 'accounts', settings: { look: 'ink' } }], { data });
		expect(body).toContain('data-account-status');
		// The label is sentence case like every other chip; THIS look lowercases it in CSS
		// (`class="lowercase"`), which is the register — so the markup says Pending and the reader
		// sees "· pending".
		expect(body).toContain('· Pending');
		expect(body).toContain('lowercase');
		expect(body).not.toContain('data-slot="status-badge"');
	});

	it('no chips anywhere when no wrapper carries a status (today’s payloads)', () => {
		const body = html([{ type: 'accounts', settings: { variant: 'table' } }]);
		expect(body).not.toContain('data-slot="status-badge"');
	});
});

/**
 * The account filter has to survive being USED. These toolbars run controlled on the composed
 * listing pages: the server answers the filter, so the rows that come back are one account's —
 * and every affordance that derives itself from the rows then quietly disappears at exactly the
 * moment it is needed. Two ways that happened, both pinned here.
 */
describe('the account filter, once a filter is in force (SSR)', () => {
	const TOOLBAR: BrandingBlock[] = [
		{ type: 'holdings', settings: { variant: 'table', toolbar: true } }
	];
	const ACCOUNT_LABEL = 'Filter holdings by account';
	/** What the server sends back once an account is chosen: that account's rows, nothing else. */
	const oneAccount: PortalPageData = {
		...family.data,
		holdings: (family.data.holdings ?? []).map((h) => ({ ...h, wrapper: 'Growth SIPP' }))
	};

	it('derives from the rows while it owns the filtering — one account, no control', () => {
		const body = html(TOOLBAR, { data: oneAccount });
		expect(body).toContain('data-widget-toolbar');
		// Right here: a filter over one account cannot narrow anything.
		expect(body).not.toContain(ACCOUNT_LABEL);
	});

	it('takes the supplied list instead when the app answers the filter', () => {
		const body = html(TOOLBAR, {
			data: oneAccount,
			listingAccounts: ['Growth SIPP', 'Family ISA', 'Junior ISA'],
			listingQuery: { account: 'Growth SIPP' },
			onlistingchange: () => {}
		});
		expect(body).toContain(ACCOUNT_LABEL);
		// The trigger names what is in force, so the reader can see they are narrowed.
		expect(body).toContain('Growth SIPP');
	});

	it('keeps the control even where the supplied list is one account, so the filter can be lifted', () => {
		const body = html(TOOLBAR, {
			data: oneAccount,
			listingAccounts: ['Growth SIPP'],
			listingQuery: { account: 'Growth SIPP' },
			onlistingchange: () => {}
		});
		expect(body).toContain(ACCOUNT_LABEL);
	});

	it('keeps the whole toolbar when the filter matches nothing', () => {
		// The other dead end: zero rows used to take the toolbar with them, stranding the reader
		// on an empty listing with no way to clear what emptied it — and no way to tell that from
		// a client who owns nothing.
		const body = html(TOOLBAR, {
			data: { ...family.data, holdings: [] },
			listingAccounts: ['Growth SIPP', 'Family ISA'],
			listingQuery: { account: 'Family ISA' },
			onlistingchange: () => {}
		});
		expect(body).toContain('data-widget-toolbar');
		expect(body).toContain(ACCOUNT_LABEL);
	});
});
