/**
 * The composed-page loader ($lib/page-data.ts). The assertion that matters most is the leak
 * rule: `data` is ALWAYS a non-null object for a signed-in client, even when every fetch fails —
 * `data: null` is the studio preview's demo switch, and passing it through would render fixture
 * documents/holdings/adviser to a real client the moment the API hiccuped.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { BrandingBlock, BrandingPage } from '@graphene/auth';
import type { Adviser, ClientHomeData } from '@graphene/ui';
import { toActivity } from './activity';
import { loadErrorSummary } from './load-errors';
import type { Holding as ApiHolding, Transaction as ApiTransaction } from './api';
import type { ApiDocument } from './documents';
import {
	assemblePageData,
	audienceOf,
	clampPage,
	gateHeaderActions,
	loadHomePage,
	loadPortalPage,
	pagesOf,
	resolveBlockVariants,
	sourceUnion,
	toPortalDocument,
	toPortalHolding,
	type PortalContext
} from './page-data';

// ── fixtures ────────────────────────────────────────────────────────────────────────────────

const block = (type: string, extra: Partial<BrandingBlock> = {}): BrandingBlock => ({
	id: type,
	type,
	visible: true,
	...extra
});

const pageOf = (
	slug: string,
	blocks: BrandingBlock[],
	layouts: Partial<BrandingPage['layouts']> = {}
): BrandingPage => ({
	id: slug,
	slug,
	label: slug,
	nav: true,
	layouts: { default: blocks, ...layouts }
});

const ADVISER: Adviser = { name: 'Jo Cole', firm: 'Cole Wealth', email: 'jo@cole', phone: '01' };

const CONTEXT: PortalContext = {
	contact_id: 7,
	type: 'individual',
	service_type: 'advised',
	account_count: 3,
	valued_account_count: 2,
	adviser: ADVISER
};

const holding = (name: string): ClientHomeData['holdings'][number] => ({
	name,
	wrapper: 'ISA',
	units: 1,
	price: 1,
	value: 1,
	gainPct: 0
});

const HOME: ClientHomeData = {
	totals: { value: 1234, gain: 34, gainPct: 2.8 },
	performance: [{ label: 'Jan', value: 1200 }],
	wrappers: [],
	allocations: [],
	holdings: [holding('Global Equity')],
	activity: [{ date: '2026-07-01', type: 'deposit', description: 'Deposit', amount: 100 }],
	// The provenance stamps ride the same payload (see the HomePayload note in page-data.ts).
	as_of: '2026-07-29',
	performance_period: { from: '2026-01-29', to: '2026-07-29' }
};

/** A raw /crm/holdings row (the paginated envelope's items), as the lake ships it. */
const apiHolding = (over: Partial<ApiHolding> = {}): ApiHolding => ({
	account_id: 1,
	account_name: 'General ISA',
	account_external_id: 'ext-1',
	asset_id: 'a1',
	asset_name: 'Global Equity Fund',
	isin: 'GB00B0000001',
	asset_type: 'fund',
	value: 1000,
	quantity: 10,
	price: 100,
	book_cost: 800,
	growth: 200,
	growth_pct: 25,
	allocation: null,
	twrr: null,
	as_of: '2026-07-01',
	...over
});

/** A raw /crm/transactions row, as the lake ships it (no strict sign on `amount`). */
const apiTxn = (over: Partial<ApiTransaction> = {}): ApiTransaction => ({
	account_id: 1,
	account_name: 'General ISA',
	account_external_id: 'ext-1',
	date: '2026-07-10',
	type: null,
	sub_type: null,
	movement_type: null,
	asset_name: null,
	isin: null,
	amount: 100,
	quantity: null,
	price: null,
	currency: 'GBP',
	status: null,
	...over
});

const API_DOC: ApiDocument = {
	uid: 'doc-1',
	name: 'Annual Review 2026.pdf',
	document_type: 'annual_review',
	source: 'generated',
	status: 'ready',
	content_type: 'application/pdf',
	size_bytes: 512_000,
	created_at: '2026-06-05T09:00:00Z',
	generated_at: null,
	unread: true,
	last_viewed_at: null
};

/** A fetch stub routed by path (exact path+query first — the variant-aware fetch tests pin the
 * query string — then bare pathname for routes that don't care); anything unlisted 404s
 * (fail-fast, no retry backoff). Records path+query so tests can assert what a page did and
 * did NOT fetch, and with which mode/window. */
function stubFetch(routes: Record<string, unknown>, calls: string[] = []): typeof globalThis.fetch {
	return (async (input: RequestInfo | URL) => {
		const u = new URL(String(input));
		const path = u.pathname + u.search;
		calls.push(path);
		const hit = routes[path] ?? routes[u.pathname];
		if (hit === undefined) return new Response('not found', { status: 404 });
		return new Response(JSON.stringify(hit), {
			status: 200,
			headers: { 'content-type': 'application/json' }
		});
	}) as typeof globalThis.fetch;
}

const locals = (pages: BrandingPage[] | null) => ({
	accessToken: 'token',
	branding: pages ? { pages } : null
});

beforeEach(() => {
	// fetchJson logs every failed slice; the failure tests below fail slices on purpose.
	vi.spyOn(console, 'error').mockImplementation(() => {});
	vi.spyOn(console, 'warn').mockImplementation(() => {});
});
afterEach(() => vi.restoreAllMocks());

// ── pure helpers ────────────────────────────────────────────────────────────────────────────

describe('audienceOf', () => {
	it('maps organisation, and everything else (null/unknown/individual) to individual', () => {
		expect(audienceOf('organisation')).toBe('organisation');
		expect(audienceOf('individual')).toBe('individual');
		expect(audienceOf(null)).toBe('individual');
		expect(audienceOf(undefined)).toBe('individual');
		expect(audienceOf('trust')).toBe('individual');
	});
});

describe('pagesOf', () => {
	it('uses a non-empty custom set, else the defaults', () => {
		const custom = [pageOf('home', [])];
		expect(pagesOf({ pages: custom })).toBe(custom);
		expect(pagesOf({ pages: [] }).map((p) => p.slug)).toContain('portfolio');
		expect(pagesOf(null).map((p) => p.slug)).toContain('home');
	});
});

describe('sourceUnion', () => {
	it('unions the blocks’ declared sources; sourceless and unknown types contribute nothing', () => {
		const union = sourceUnion([
			block('portfolioHeader'), // portfolio
			block('accounts'), // portfolio (rides /crm/home)
			block('documents'), // documents
			block('message'), // no sources
			block('news'), // content
			block('somethingFuture') // unknown type
		]);
		expect([...union].sort()).toEqual(['content', 'documents', 'portfolio']);
	});
});

describe('toPortalDocument', () => {
	it('projects a mapped document into the block’s display shape', () => {
		const doc = toPortalDocument({
			uid: 'doc-1',
			name: 'Annual Review 2026.pdf',
			category: 'annual_review',
			createdAt: '2026-06-05T09:00:00Z',
			sizeBytes: 512_000,
			contentType: 'application/pdf',
			unread: true
		});
		expect(doc).toEqual({
			id: 'doc-1',
			name: 'Annual Review 2026.pdf',
			category: 'Statements', // annual_review buckets under review → Statements
			date: '5 Jun 2026',
			isoDate: '2026-06-05T09:00:00Z',
			size: '500 KB',
			format: 'PDF',
			// Read-state rides through so the toolbar's unread note works for real clients.
			unread: true
		});
	});

	it('leaves unread absent when the mapped document carries none — never implies "read"', () => {
		const doc = toPortalDocument({
			uid: 'u',
			name: 'n',
			category: 'other',
			createdAt: '2026-01-01'
		});
		expect(doc.unread).toBeUndefined();
	});

	it('buckets free-text document types into the five display categories', () => {
		const category = (slug: string) =>
			toPortalDocument({ uid: 'u', name: 'n', category: slug, createdAt: '2026-01-01' }).category;
		expect(category('client_agreement')).toBe('Agreements');
		expect(category('illustration')).toBe('Illustrations');
		expect(category('suitability_report')).toBe('Statements');
		expect(category('tax')).toBe('Tax');
		// Unrecognised types must land somewhere visible, not vanish from the grouped view.
		expect(category('id_verification')).toBe('Correspondence');
	});
});

describe('assemblePageData', () => {
	const NONE = {
		home: null,
		documents: null,
		tasks: null,
		applications: null,
		requests: null,
		news: null
	};

	it('is never null — every slice failed still yields an object (the leak rule)', () => {
		const data = assemblePageData(NONE, null);
		expect(data).not.toBeNull();
		expect(data).toEqual({});
	});

	it('spreads the home payload and maps the other slices', () => {
		const data = assemblePageData(
			{
				...NONE,
				home: HOME,
				documents: [API_DOC],
				tasks: [
					{ uid: 't1', title: 'Sign it', description: null, due_date: '2026-08-01' },
					// Extra keys off the wire must not ride through to the renderer.
					{
						uid: 't2',
						title: 'Send it',
						description: 'the form',
						due_date: null,
						priority: 'high'
					} as never
				],
				applications: { done: 2, total: 5 }
			},
			null
		);
		expect(data.totals).toEqual(HOME.totals);
		expect(data.holdings).toHaveLength(1);
		// The provenance stamps must survive the spread — the "as at" rendering keys off them.
		expect(data.as_of).toBe('2026-07-29');
		expect(data.performance_period).toEqual({ from: '2026-01-29', to: '2026-07-29' });
		expect(data.documents?.[0]?.id).toBe('doc-1');
		expect(data.tasks).toEqual([
			{ uid: 't1', title: 'Sign it', description: null, due_date: '2026-08-01' },
			{ uid: 't2', title: 'Send it', description: 'the form', due_date: null }
		]);
		expect(data.onboarding).toEqual({ done: 2, total: 5 });
	});

	it('counts outstanding requests as pending ITEMS on pending requests (the legacy reduction)', () => {
		const data = assemblePageData(
			{
				...NONE,
				requests: [
					{ status: 'pending', items: [{ status: 'pending' }, { status: 'fulfilled' }] },
					{ status: 'pending', items: [{ status: 'pending' }, { status: 'pending' }] },
					// A fulfilled request's items never count, whatever their own status says.
					{ status: 'fulfilled', items: [{ status: 'pending' }] }
				]
			},
			null
		);
		expect(data.requests_outstanding).toBe(3);
	});

	it('THE LEAK RULE for requests: a failed fetch leaves the count ABSENT, never fabricated', () => {
		// null = the fetch failed (or was never made); the documents block must render no banner.
		expect(assemblePageData(NONE, null).requests_outstanding).toBeUndefined();
		// And an honest zero IS stated (the banner still hides — nothing is outstanding).
		expect(assemblePageData({ ...NONE, requests: [] }, null).requests_outstanding).toBe(0);
	});

	it('fills the adviser from context when home has none, and home wins when both exist', () => {
		expect(assemblePageData(NONE, ADVISER).adviser).toEqual(ADVISER);
		const fromHome = { ...ADVISER, name: 'Home Adviser' };
		expect(
			assemblePageData({ ...NONE, home: { ...HOME, adviser: fromHome } }, ADVISER).adviser
		).toEqual(fromHome);
	});
});

describe('gateHeaderActions', () => {
	const classified = (money_movement: boolean) => ({
		uid: 'c1',
		name: 'Book',
		slug: 'book',
		capabilities: { money_movement },
		source: 'matched' as const
	});
	const header = (actions?: string) =>
		block('portfolioHeader', actions === undefined ? {} : { settings: { actions } });

	it('drops the Deposit/Withdraw pair when the book cannot move money', () => {
		// The transfer routes 404 without `money_movement`; the header must not offer two buttons
		// that both land on "We couldn't find that page". Absent/junk normalise to the pair, so
		// they are gated too. The row goes rather than becoming a Contact button: the shell's
		// top bar already has the message button.
		for (const stored of [undefined, 'deposit_withdraw', 'junk']) {
			const [b] = gateHeaderActions([header(stored)], classified(false));
			expect(b.settings?.actions).toBe('none');
		}
	});

	it('keeps an explicit none or contact, keeps the pair for a book that can move money, and copies', () => {
		const none = header('none');
		expect(gateHeaderActions([none], classified(false))[0].settings?.actions).toBe('none');
		const contact = header('contact');
		expect(gateHeaderActions([contact], classified(false))[0]).toBe(contact);
		const pair = header('deposit_withdraw');
		expect(gateHeaderActions([pair], classified(true))[0]).toBe(pair);
		const gated = gateHeaderActions([pair], classified(false))[0];
		expect(gated).not.toBe(pair);
		expect(pair.settings?.actions).toBe('deposit_withdraw');
	});

	it('treats a missing classification as no money movement, and leaves other blocks alone', () => {
		const [h, docs] = gateHeaderActions([header(), block('documents')], null);
		expect(h.settings?.actions).toBe('none');
		expect(docs.settings).toBeUndefined();
		expect(gateHeaderActions([header()], undefined)[0].settings?.actions).toBe('none');
	});

	it('is applied by the composed-page loader from locals.classification', async () => {
		const pages = [pageOf('home', [header('deposit_withdraw')])];
		const advised = await loadPortalPage(
			'home',
			{ ...locals(pages), classification: classified(false) },
			stubFetch(OK_ROUTES)
		);
		expect(advised?.blocks[0].settings?.actions).toBe('none');
		const d2c = await loadPortalPage(
			'home',
			{ ...locals(pages), classification: classified(true) },
			stubFetch(OK_ROUTES)
		);
		expect(d2c?.blocks[0].settings?.actions).toBe('deposit_withdraw');
	});
});

describe('resolveBlockVariants', () => {
	const ctx = {
		audience: 'individual' as const,
		accountCount: 3,
		valuedAccountCount: 2,
		pageSlug: 'home'
	};

	it('resolves auto to a concrete variant from the data and copies the blocks', () => {
		const source = [block('holdings')];
		const resolved = resolveBlockVariants(source, { holdings: Array(10).fill(holding('x')) }, ctx);
		expect(resolved[0].settings?.variant).toBe('table'); // ≥ holdingsTableMin
		// Copies, never mutation — the blocks alias locals.branding (shared request state).
		expect(source[0].settings).toBeUndefined();
	});

	it('keeps an explicit concrete variant', () => {
		const resolved = resolveBlockVariants(
			[block('documents', { settings: { variant: 'recent', limit: 3 } })],
			{ documents: [] },
			ctx
		);
		expect(resolved[0].settings).toEqual({ variant: 'recent', limit: 3 });
	});

	it('sizes holdings against the known total, not the paginated slice', () => {
		const threeRows = Array(3).fill(holding('x'));
		// A page of 3 from a 400-holding book must not read as "3 holdings".
		const paged = resolveBlockVariants(
			[block('holdings')],
			{ holdings: threeRows, holdings_total: 400 },
			ctx
		);
		expect(paged[0].settings?.variant).toBe('table');
		// No total ⇒ the slice IS the data, and 3 stays below the table threshold.
		const bare = resolveBlockVariants([block('holdings')], { holdings: threeRows }, ctx);
		expect(bare[0].settings?.variant).toBe('top');
	});
});

// ── the loaders, with a mocked fetch ────────────────────────────────────────────────────────

const OK_ROUTES = {
	'/crm/context': CONTEXT,
	'/crm/home': HOME,
	'/documents': [API_DOC],
	'/tasks/me': [{ uid: 't1', title: 'Sign it', description: null, due_date: null }],
	'/applications/me': { steps: [], done: 1, total: 4 }
};

describe('loadPortalPage', () => {
	it('returns null for a slug the tenant’s page set does not carry', async () => {
		const result = await loadPortalPage(
			'reports',
			locals([pageOf('home', [])]),
			stubFetch(OK_ROUTES)
		);
		expect(result).toBeNull();
	});

	it('falls back to the default page set when the tenant has no pages', async () => {
		const result = await loadPortalPage('home', locals(null), stubFetch(OK_ROUTES));
		expect(result?.page.slug).toBe('home');
		expect(result?.blocks.length).toBeGreaterThan(0);
		expect(result?.data.totals).toEqual(HOME.totals);
		// Uncustomised ⇒ "View all" follows the default set: holdings live on Portfolio, the
		// library on Insights, and there is no separate accounts page to send someone to.
		expect(result?.links).toEqual({
			accounts: null,
			holdings: '/portfolio',
			activity: '/activity',
			documents: '/documents',
			news: '/insights'
		});
	});

	it('draws no "View all" to a page the firm’s set does not have', async () => {
		// The firm kept Home and Accounts only. The home cards for holdings/activity/documents used
		// to link to the built-in slugs regardless, and each of those routes 404s for this set.
		const pages = [
			pageOf('home', [block('holdings'), block('activity'), block('documents')]),
			pageOf('accounts', [block('accounts')])
		];
		const result = await loadPortalPage('home', locals(pages), stubFetch(OK_ROUTES));
		expect(result?.links).toEqual({
			accounts: '/accounts',
			holdings: null,
			activity: null,
			documents: null,
			news: null
		});
	});

	it('fetches only the union of the visible blocks’ sources', async () => {
		const calls: string[] = [];
		const pages = [
			pageOf('notes', [
				block('message', { settings: { title: 'Hello', body: 'World' } }),
				block('documents', { visible: false }) // hidden ⇒ its source must not be fetched
			])
		];
		await loadPortalPage('notes', locals(pages), stubFetch(OK_ROUTES, calls));
		expect(calls).toEqual(['/crm/context']); // context always; nothing else for a message page
	});

	it('THE LEAK RULE: every fetch failing still returns a non-null data object', async () => {
		const result = await loadPortalPage('home', locals(null), stubFetch({}));
		expect(result).not.toBeNull();
		// Non-null data ⇒ resolveSlices renders empty states; null would render demo fixtures.
		expect(result?.data).not.toBeNull();
		expect(typeof result?.data).toBe('object');
	});

	it('nobody assigned ⇒ the FIRM stands in as the contact, from the brand and its support details', async () => {
		// HOME carries no adviser; the context says there is none either.
		const result = await loadPortalPage(
			'home',
			{
				...locals(null),
				branding: { icon_url: 'https://cdn/acme.png', support: { email: 'help@acme.example' } },
				appName: 'Acme Wealth'
			},
			stubFetch({ ...OK_ROUTES, '/crm/context': { ...CONTEXT, adviser: null } })
		);
		expect(result?.data.adviser).toEqual({
			kind: 'firm',
			name: 'Acme Wealth',
			firm: 'Acme Wealth',
			email: 'help@acme.example',
			phone: '',
			photoUrl: 'https://cdn/acme.png'
		});
	});

	it('a FAILED context read leaves the adviser absent — a gap, not a client with no adviser', async () => {
		const result = await loadPortalPage(
			'home',
			{ ...locals(null), appName: 'Acme Wealth' },
			stubFetch({ '/crm/home': HOME })
		);
		expect(result?.data.adviser ?? null).toBeNull();
	});

	it('a failed context degrades to the individual audience with zero counts', async () => {
		const pages = [
			pageOf('overview', [block('accounts')], {
				// An organisation would see nothing here; individual sees the accounts block.
				organisation: []
			})
		];
		const result = await loadPortalPage(
			'overview',
			locals(pages),
			stubFetch({ '/crm/home': HOME })
		);
		expect(result?.blocks.map((b) => b.type)).toEqual(['accounts']);
		// accountCount degraded to 0 ⇒ the accounts auto rule lands on 'stack'.
		expect(result?.blocks[0].settings?.variant).toBe('stack');
	});

	it('an organisation context picks the organisation layout override', async () => {
		const pages = [pageOf('overview', [block('accounts')], { organisation: [] })];
		const result = await loadPortalPage(
			'overview',
			locals(pages),
			stubFetch({ ...OK_ROUTES, '/crm/context': { ...CONTEXT, type: 'organisation' } })
		);
		// An explicit empty override is honoured — show this audience nothing.
		expect(result?.blocks).toEqual([]);
	});

	it('never mutates the tenant’s branding pages', async () => {
		const pages = [pageOf('home', [block('holdings')])];
		const snapshot = structuredClone(pages);
		await loadPortalPage('home', locals(pages), stubFetch(OK_ROUTES));
		expect(pages).toEqual(snapshot);
	});
});

// The four listing routes are composed-only now: they always call loadPortalPage, which falls
// back to the DEFAULT page set for a null/empty pages set (pagesOf) — so an un-customised
// tenant still renders every default surface — and returns null (→ the route 404s) only when a
// non-empty CUSTOM set deliberately omits the slug.
describe('the composed-only listing routes', () => {
	it('a null-pages tenant composes the default portfolio page, holdings grouped under the allocation', async () => {
		const result = await loadPortalPage('portfolio', locals(null), stubFetch(OK_ROUTES));
		expect(result?.page.slug).toBe('portfolio');
		const holdings = result?.blocks.find((b) => b.type === 'holdings');
		expect(holdings?.settings?.variant).toBe('grouped');
	});

	it('a custom set that omits the slug composes nothing — the route 404s', async () => {
		const pages = [pageOf('home', [])];
		expect(await loadPortalPage('documents', locals(pages), stubFetch(OK_ROUTES))).toBeNull();
	});
});

describe('loadHomePage', () => {
	it('falls back to the default home when the custom set lacks one — / must always render', async () => {
		const pages = [pageOf('reports', [block('documents')])];
		const result = await loadHomePage(locals(pages), stubFetch(OK_ROUTES));
		expect(result.page.slug).toBe('home');
		expect(result.blocks.length).toBeGreaterThan(0);
	});

	it('uses the tenant’s own home when present', async () => {
		const pages = [pageOf('home', [block('message', { settings: { title: 'Hi', body: '' } })])];
		const result = await loadHomePage(locals(pages), stubFetch(OK_ROUTES));
		expect(result.page).toBe(pages[0]);
		expect(result.blocks.map((b) => b.type)).toEqual(['message']);
	});
});

// ── variant-aware fetching (Phase 4) ────────────────────────────────────────────────────────

describe('clampPage', () => {
	it('clamps junk and absent to 1, floors fractions, keeps sane pages', () => {
		expect(clampPage('3')).toBe(3);
		expect(clampPage(7)).toBe(7);
		expect(clampPage('2.9')).toBe(2); // a mangled-but-numeric param still lands on a page
		expect(clampPage(null)).toBe(1);
		expect(clampPage(undefined)).toBe(1);
		expect(clampPage('')).toBe(1);
		expect(clampPage('0')).toBe(1);
		expect(clampPage('-4')).toBe(1);
		expect(clampPage('junk')).toBe(1);
		expect(clampPage(Infinity)).toBe(1);
	});
});

describe('the wrappers mode of the /crm/home fetch', () => {
	const homeCall = (calls: string[]) => calls.find((c) => c.startsWith('/crm/home'));

	const loadWith = async (blocks: BrandingBlock[], accountCount: number) => {
		const calls: string[] = [];
		await loadPortalPage(
			'overview',
			locals([pageOf('overview', blocks)]),
			stubFetch(
				{ ...OK_ROUTES, '/crm/context': { ...CONTEXT, account_count: accountCount } },
				calls
			)
		);
		return calls;
	};

	it('an explicitly pinned per-account look fetches full — never summarised out from under the firm', async () => {
		for (const variant of ['stack', 'cards', 'table']) {
			const calls = await loadWith([block('accounts', { settings: { variant } })], 30);
			expect(homeCall(calls)).toBe('/crm/home?wrappers=full');
		}
	});

	it('auto accounts on a 30-account book resolves summary pre-fetch, so the loader asks for it', async () => {
		const calls = await loadWith([block('accounts')], 30);
		expect(homeCall(calls)).toBe('/crm/home?wrappers=summary');
	});

	it('an explicit summary variant fetches summary', async () => {
		const calls = await loadWith([block('accounts', { settings: { variant: 'summary' } })], 3);
		expect(homeCall(calls)).toBe('/crm/home?wrappers=summary');
	});

	it('auto accounts on a small book resolves stack, so the loader leaves the mode to the API', async () => {
		const calls = await loadWith([block('accounts')], 3);
		expect(homeCall(calls)).toBe('/crm/home?wrappers=auto');
	});

	it('no accounts block at all fetches auto — home totals stay cheap for big books', async () => {
		const calls = await loadWith([block('portfolioHeader')], 300);
		expect(homeCall(calls)).toBe('/crm/home?wrappers=auto');
	});

	it('assembles the summary keys onto the data object (they ride the home payload)', async () => {
		const summary = {
			...HOME,
			wrappers: HOME.wrappers,
			wrapper_groups: [{ product_type: 'ISA', count: 20, value: 1_000_000 }],
			wrappers_total: 30,
			wrappers_summarised: true
		};
		const result = await loadPortalPage(
			'overview',
			locals([pageOf('overview', [block('accounts')])]),
			stubFetch({
				'/crm/context': { ...CONTEXT, account_count: 30 },
				'/crm/home?wrappers=summary': summary
			})
		);
		expect(result?.data.wrapper_groups).toEqual(summary.wrapper_groups);
		expect(result?.data.wrappers_total).toBe(30);
		expect(result?.data.wrappers_summarised).toBe(true);
		expect(result?.blocks[0].settings?.variant).toBe('summary');
	});
});

describe('the dedicated holdings fetch', () => {
	const HOLDINGS_PAGE = {
		items: [apiHolding(), apiHolding({ asset_name: 'Bond Fund', isin: null, book_cost: 0 })],
		total: 60
	};

	it('fires for an explicit table variant, maps the lake fields and paginates', async () => {
		const calls: string[] = [];
		const result = await loadPortalPage(
			'positions',
			locals([pageOf('positions', [block('holdings', { settings: { variant: 'table' } })])]),
			stubFetch({ ...OK_ROUTES, '/crm/holdings?limit=25&offset=0': HOLDINGS_PAGE }, calls)
		);
		expect(calls).toContain('/crm/holdings?limit=25&offset=0');
		expect(result?.data.holdings?.[0]).toEqual({
			name: 'Global Equity Fund',
			ticker: 'GB00B0000001',
			wrapper: 'General ISA',
			units: 10,
			price: 100,
			value: 1000,
			gainPct: 0.25, // growth/book_cost = 200/800, as /crm/home derives it
			assetClass: null,
			lookthroughHref: undefined
		});
		expect(result?.data.holdings_total).toBe(60);
		expect(result?.pagination).toEqual({ page: 1, pageSize: 25 });
		expect(result?.blocks[0].settings?.variant).toBe('table');
	});

	it('page 3 fetches offset 50 and reports page 3', async () => {
		const calls: string[] = [];
		const result = await loadPortalPage(
			'positions',
			locals([pageOf('positions', [block('holdings', { settings: { variant: 'table' } })])]),
			stubFetch({ ...OK_ROUTES, '/crm/holdings?limit=25&offset=50': HOLDINGS_PAGE }, calls),
			3
		);
		expect(calls).toContain('/crm/holdings?limit=25&offset=50');
		expect(result?.pagination).toEqual({ page: 3, pageSize: 25 });
	});

	it('fires for an organisation on the stored auto variant (the audience arm is pre-fetch-decidable)', async () => {
		const calls: string[] = [];
		await loadPortalPage(
			'positions',
			locals([pageOf('positions', [block('holdings')])]),
			stubFetch(
				{
					...OK_ROUTES,
					'/crm/context': { ...CONTEXT, type: 'organisation' },
					'/crm/holdings?limit=25&offset=0': HOLDINGS_PAGE
				},
				calls
			)
		);
		expect(calls).toContain('/crm/holdings?limit=25&offset=0');
	});

	it('does NOT fire for an individual on auto — the accepted gap: their count isn’t knowable pre-fetch', async () => {
		const calls: string[] = [];
		const result = await loadPortalPage(
			'positions',
			locals([pageOf('positions', [block('holdings')])]),
			stubFetch(OK_ROUTES, calls)
		);
		expect(calls.some((c) => c.startsWith('/crm/holdings'))).toBe(false);
		// Home's top-5 slice stands, and nothing paginated this load.
		expect(result?.data.holdings).toHaveLength(1);
		expect(result?.pagination).toBeNull();
	});

	it('does NOT fire for an explicit top variant, organisation or not', async () => {
		const calls: string[] = [];
		await loadPortalPage(
			'positions',
			locals([pageOf('positions', [block('holdings', { settings: { variant: 'top' } })])]),
			stubFetch({ ...OK_ROUTES, '/crm/context': { ...CONTEXT, type: 'organisation' } }, calls)
		);
		expect(calls.some((c) => c.startsWith('/crm/holdings'))).toBe(false);
	});

	it('a failed envelope leaves the home slice standing and pagination null', async () => {
		const result = await loadPortalPage(
			'positions',
			locals([pageOf('positions', [block('holdings', { settings: { variant: 'table' } })])]),
			// No /crm/holdings route ⇒ the dedicated fetch 404s; /crm/home still answers.
			stubFetch(OK_ROUTES)
		);
		expect(result?.data.holdings).toHaveLength(1); // home's slice, not wiped
		expect(result?.data.holdings_total).toBeUndefined();
		expect(result?.pagination).toBeNull();
	});
});

describe('the dedicated activity fetch', () => {
	const TXNS = [
		apiTxn({ type: 'Sell', asset_name: 'Global Equity Fund', amount: 250 }),
		apiTxn({ type: 'Payment', sub_type: 'Deposit', amount: 500 })
	];

	it('fires for an explicit table variant and maps rows through the shared projection', async () => {
		const calls: string[] = [];
		const result = await loadPortalPage(
			'movements',
			locals([pageOf('movements', [block('activity', { settings: { variant: 'table' } })])]),
			stubFetch({ ...OK_ROUTES, '/crm/transactions?limit=200': TXNS }, calls)
		);
		expect(calls).toContain('/crm/transactions?limit=200');
		// The detail keys ride along as null for a cash movement with no custodian words — the
		// list draws its wide columns only when some row fills them.
		const noDetail = { detail: null, quantity: null, price: null, isin: null };
		expect(result?.data.activity).toEqual([
			{
				date: '2026-07-10',
				type: 'sell',
				description: 'Global Equity Fund',
				amount: -250, // a sell is money out
				wrapper: 'General ISA',
				currency: 'GBP',
				...noDetail
			},
			{
				date: '2026-07-10',
				type: 'deposit',
				description: 'Payment',
				amount: 500,
				wrapper: 'General ISA',
				currency: 'GBP',
				...noDetail
			}
		]);
		// Activity has no {items,total} envelope, so it never drives the pager.
		expect(result?.pagination).toBeNull();
	});

	it('fires for an organisation on auto (a fetch heuristic — the auto rule itself has no audience arm)', async () => {
		const calls: string[] = [];
		await loadPortalPage(
			'movements',
			locals([pageOf('movements', [block('activity')])]),
			stubFetch(
				{
					...OK_ROUTES,
					'/crm/context': { ...CONTEXT, type: 'organisation' },
					'/crm/transactions?limit=200': TXNS
				},
				calls
			)
		);
		expect(calls).toContain('/crm/transactions?limit=200');
	});

	it('does NOT fire for an individual on auto — the home slice’s six rows stand', async () => {
		const calls: string[] = [];
		const result = await loadPortalPage(
			'movements',
			locals([pageOf('movements', [block('activity')])]),
			stubFetch(OK_ROUTES, calls)
		);
		expect(calls.some((c) => c.startsWith('/crm/transactions'))).toBe(false);
		expect(result?.data.activity).toEqual(HOME.activity);
	});
});

// ── the listing toolbars' URL-owned query (?q= / ?account=) ─────────────────────────────────
// The composed pages' toolbars run CONTROLLED (composed-page.svelte provides onlistingchange),
// so pkgs/ui does no client-side q/account filtering — these pin that the loader really
// answers the query, and only where a toolbar is actually shown.

describe('the listing query — holdings (server-side)', () => {
	const TOOLBAR_TABLE = { settings: { variant: 'table', toolbar: true } };
	const HOLDINGS_PAGE = { items: [apiHolding()], total: 1 };

	it('threads q into the dedicated fetch — the server answers the search', async () => {
		const calls: string[] = [];
		const result = await loadPortalPage(
			'positions',
			locals([pageOf('positions', [block('holdings', TOOLBAR_TABLE)])]),
			stubFetch({ ...OK_ROUTES, '/crm/holdings?limit=25&offset=0&q=bond': HOLDINGS_PAGE }, calls),
			1,
			{ q: 'bond' }
		);
		expect(calls).toContain('/crm/holdings?limit=25&offset=0&q=bond');
		expect(result?.data.holdings).toHaveLength(1);
		expect(result?.data.holdings_total).toBe(1); // the FILTERED match count, per the API
	});

	it('resolves the account filter’s NAME to account_id via the bare /crm/accounts list', async () => {
		const calls: string[] = [];
		await loadPortalPage(
			'positions',
			locals([pageOf('positions', [block('holdings', TOOLBAR_TABLE)])]),
			stubFetch(
				{
					...OK_ROUTES,
					'/crm/accounts': [
						{ id: 4, uid: 'a-4', name: 'Pension' },
						{ id: 9, uid: 'a-9', name: 'General ISA' }
					],
					'/crm/holdings?limit=25&offset=0&account_id=9': HOLDINGS_PAGE
				},
				calls
			),
			1,
			{ account: 'General ISA' }
		);
		expect(calls).toContain('/crm/accounts');
		expect(calls).toContain('/crm/holdings?limit=25&offset=0&account_id=9');
	});

	it('an unresolvable account name DROPS the filter rather than erroring', async () => {
		const calls: string[] = [];
		await loadPortalPage(
			'positions',
			locals([pageOf('positions', [block('holdings', TOOLBAR_TABLE)])]),
			stubFetch(
				{
					...OK_ROUTES,
					'/crm/accounts': [],
					'/crm/holdings?limit=25&offset=0': { items: [], total: 0 }
				},
				calls
			),
			1,
			{ account: 'Renamed Account' }
		);
		expect(calls).toContain('/crm/holdings?limit=25&offset=0');
	});

	it('an active query FORCES the dedicated fetch a compact toolbar’d block wouldn’t make', async () => {
		// A toolbar'd 'top' block on an individual: no variant/audience trigger — but searching
		// the home payload's top-5 slice would answer against a fraction of the book.
		const calls: string[] = [];
		await loadPortalPage(
			'positions',
			locals([
				pageOf('positions', [block('holdings', { settings: { variant: 'top', toolbar: true } })])
			]),
			stubFetch(
				{ ...OK_ROUTES, '/crm/holdings?limit=25&offset=0&q=bond': { items: [], total: 0 } },
				calls
			),
			1,
			{ q: 'bond' }
		);
		expect(calls).toContain('/crm/holdings?limit=25&offset=0&q=bond');
	});

	it('a stray param on a toolbar-less block filters NOTHING invisibly', async () => {
		const calls: string[] = [];
		await loadPortalPage(
			'positions',
			locals([pageOf('positions', [block('holdings', { settings: { variant: 'table' } })])]),
			stubFetch(
				{ ...OK_ROUTES, '/crm/holdings?limit=25&offset=0': { items: [], total: 0 } },
				calls
			),
			1,
			{ q: 'bond', account: 'General ISA' }
		);
		expect(calls).toContain('/crm/holdings?limit=25&offset=0');
		expect(calls.some((c) => c.includes('q=') || c.includes('account_id='))).toBe(false);
	});
});

/**
 * The account filter's OPTIONS, which are a separate question from answering the filter.
 *
 * These toolbars run controlled, so the rows that come back under an account filter are that
 * account's alone — options derived from them name only it, the control hides itself for having
 * nothing to offer, and the filter is then in force with nothing on the page able to lift it.
 * The loader supplies the client's whole list so the toolbar never has to guess.
 */
describe('listingAccounts — the account filter’s options', () => {
	const TOOLBAR_TABLE = { settings: { variant: 'table', toolbar: true } };
	const ACCOUNTS = [
		{ id: 4, uid: 'a-4', name: 'Pension' },
		{ id: 9, uid: 'a-9', name: null }
	];

	const loadHoldings = async (listing: Record<string, string> = {}) => {
		const calls: string[] = [];
		const result = await loadPortalPage(
			'positions',
			locals([pageOf('positions', [block('holdings', TOOLBAR_TABLE)])]),
			stubFetch(
				{
					...OK_ROUTES,
					'/crm/accounts': ACCOUNTS,
					'/crm/holdings?limit=25&offset=0': { items: [apiHolding()], total: 1 },
					'/crm/holdings?limit=25&offset=0&account_id=4': { items: [], total: 0 }
				},
				calls
			),
			1,
			listing
		);
		return { calls, result };
	};

	it('reads the accounts even with NO filter active — that is when they are needed', async () => {
		const { calls, result } = await loadHoldings();
		expect(calls).toContain('/crm/accounts');
		expect(result?.listingAccounts).toEqual(['Pension', 'a-9']);
	});

	it('names an unnamed account by its uid, the way the API names it on a row', async () => {
		const { result } = await loadHoldings();
		expect(result?.listingAccounts).toContain('a-9');
	});

	it('reads them for a toolbar’d ACTIVITY block too', async () => {
		const calls: string[] = [];
		const result = await loadPortalPage(
			'movements',
			locals([pageOf('movements', [block('activity', TOOLBAR_TABLE)])]),
			stubFetch({ ...OK_ROUTES, '/crm/accounts': ACCOUNTS }, calls),
			1
		);
		expect(calls).toContain('/crm/accounts');
		expect(result?.listingAccounts).toEqual(['Pension', 'a-9']);
	});

	it('is null when no visible block shows a toolbar — nothing asked, so nothing is claimed', async () => {
		const calls: string[] = [];
		const result = await loadPortalPage(
			'positions',
			locals([pageOf('positions', [block('holdings', { settings: { variant: 'table' } })])]),
			stubFetch(
				{ ...OK_ROUTES, '/crm/holdings?limit=25&offset=0': { items: [], total: 0 } },
				calls
			),
			1
		);
		expect(result?.listingAccounts).toBeNull();
		expect(calls).not.toContain('/crm/accounts');
	});

	it('reads the accounts ONCE, and the filter resolve reuses that read', async () => {
		const { calls, result } = await loadHoldings({ account: 'Pension' });
		expect(calls.filter((c) => c === '/crm/accounts')).toHaveLength(1);
		expect(calls).toContain('/crm/holdings?limit=25&offset=0&account_id=4');
		expect(result?.listingAccounts).toEqual(['Pension', 'a-9']);
	});
});

describe('the listing query — activity (loader-side: the API has no q)', () => {
	const TXNS = [
		apiTxn({ type: 'Sell', asset_name: 'Global Equity Fund', amount: 250 }),
		apiTxn({ type: 'Payment', sub_type: 'Deposit', amount: 500, account_name: 'Pension' })
	];
	const toolbarTable = (extra: Record<string, unknown> = {}) =>
		block('activity', { settings: { variant: 'table', toolbar: true, ...extra } });

	const loadActivity = async (blocks: BrandingBlock[], listing: { q?: string; account?: string }) =>
		loadPortalPage(
			'movements',
			locals([pageOf('movements', blocks)]),
			stubFetch({ ...OK_ROUTES, '/crm/transactions?limit=200': TXNS }),
			1,
			listing
		);

	it('filters the (complete) 200-row window by q, same matching as the toolbar', async () => {
		const result = await loadActivity([toolbarTable()], { q: 'global' });
		expect(result?.data.activity?.map((a) => a.description)).toEqual(['Global Equity Fund']);
	});

	it('filters by account label', async () => {
		const result = await loadActivity([toolbarTable()], { account: 'Pension' });
		expect(result?.data.activity?.map((a) => a.wrapper)).toEqual(['Pension']);
		const none = await loadActivity([toolbarTable()], { account: 'Nowhere' });
		expect(none?.data.activity).toEqual([]);
	});

	// The default Activity page is the home card's `list` with the toolbar on. Without this the
	// page would render the home payload's six recent rows under a search box.
	it('a toolbar’d LIST fetches the full window, unfiltered, like a table does', async () => {
		const calls: string[] = [];
		const result = await loadPortalPage(
			'activity',
			locals([
				pageOf('activity', [block('activity', { settings: { variant: 'list', toolbar: true } })])
			]),
			stubFetch({ ...OK_ROUTES, '/crm/transactions?limit=200': TXNS }, calls),
			1,
			{}
		);
		expect(calls).toContain('/crm/transactions?limit=200');
		expect(result?.data.activity).toHaveLength(2);
	});

	it('does NOT filter when no visible activity block shows a toolbar', async () => {
		const result = await loadActivity([block('activity', { settings: { variant: 'table' } })], {
			q: 'global'
		});
		expect(result?.data.activity).toHaveLength(2);
	});
});

describe('the requests_outstanding slice (the documents show_requests banner)', () => {
	const showRequests = block('documents', { settings: { show_requests: true } });

	it('fetches /documents/requests only when a visible documents block asks for it', async () => {
		const calls: string[] = [];
		const result = await loadPortalPage(
			'docs',
			locals([pageOf('docs', [showRequests])]),
			stubFetch(
				{
					...OK_ROUTES,
					'/documents/requests': [
						{ status: 'pending', items: [{ status: 'pending' }, { status: 'pending' }] }
					]
				},
				calls
			)
		);
		expect(calls).toContain('/documents/requests');
		expect(result?.data.requests_outstanding).toBe(2);
	});

	it('does not fetch without the setting', async () => {
		const calls: string[] = [];
		await loadPortalPage(
			'docs',
			locals([pageOf('docs', [block('documents')])]),
			stubFetch(OK_ROUTES, calls)
		);
		expect(calls).not.toContain('/documents/requests');
	});

	it('THE LEAK RULE: a failed fetch leaves the count absent — no banner, never a fabricated 0', async () => {
		// No /documents/requests route ⇒ the slice 404s; everything else answers.
		const result = await loadPortalPage(
			'docs',
			locals([pageOf('docs', [showRequests])]),
			stubFetch(OK_ROUTES)
		);
		expect(result?.data.requests_outstanding).toBeUndefined();
	});
});

describe('toPortalHolding', () => {
	it('says NOTHING where there is no basis — it used to say +0.0%', () => {
		// Null, not zero. Coalescing here printed "+0.0%" on every row whose cost the custodian
		// never sent — more than half a real book — a claim that the holding has neither gained
		// nor lost, where the truth is that we cannot tell. The renderer prints "—".
		expect(toPortalHolding(apiHolding({ book_cost: 0 })).gainPct).toBeNull();
		expect(toPortalHolding(apiHolding({ book_cost: null, growth: null })).gainPct).toBeNull();
		// growth/book_cost = 90,000 ⇒ |pct| > 10 ⇒ the basis is wrong, so no figure is honest.
		expect(toPortalHolding(apiHolding({ growth: 900_000, book_cost: 10 })).gainPct).toBeNull();
		// A real return still comes through, losses included.
		expect(toPortalHolding(apiHolding({ growth: -200, book_cost: 800 })).gainPct).toBe(-0.25);
	});

	it('falls back per-field so a sparse lake row still renders', () => {
		const mapped = toPortalHolding(
			apiHolding({
				asset_name: null,
				isin: null,
				account_name: null,
				quantity: null,
				price: null,
				value: null
			})
		);
		expect(mapped).toEqual({
			name: '—',
			ticker: undefined,
			wrapper: '',
			units: 0,
			price: 0,
			value: 0,
			gainPct: 0.25,
			assetClass: null,
			lookthroughHref: undefined
		});
	});
});

describe('toActivity (the shared sign inference)', () => {
	it.each([
		['Sell', -1],
		['Withdrawal', -1],
		['Fee', -1],
		['Adviser Charge', -1],
		['Deposit', 1],
		['Dividend', 1],
		['Buy', 1]
	])('%s infers sign %d', (type, sign) => {
		expect(toActivity(apiTxn({ type, amount: 100 })).amount).toBe(100 * sign);
	});

	it('buckets the movement into the activity icon types, first match winning', () => {
		expect(toActivity(apiTxn({ type: 'Dividend Buy' })).type).toBe('dividend');
		expect(toActivity(apiTxn({ sub_type: 'Redemption' })).type).toBe('sell');
		expect(toActivity(apiTxn({ movement_type: 'Subscription' })).type).toBe('buy');
		expect(toActivity(apiTxn({ type: 'Mystery' })).type).toBe('buy'); // fallback
	});

	it('describes by asset name, then type, then a generic label', () => {
		expect(toActivity(apiTxn({ asset: 'Fund A', type: 'Buy' })).description).toBe('Fund A');
		expect(toActivity(apiTxn({ asset_name: 'Fund A', type: 'Buy' })).description).toBe('Fund A');
		expect(toActivity(apiTxn({ type: 'Buy' })).description).toBe('Buy');
		expect(toActivity(apiTxn()).description).toBe('Transaction');
	});
});

describe('loadErrorSummary', () => {
	// The point of the banner: a failed read and an empty account render identically without it.
	it('is silent when every slice answered', () => {
		expect(loadErrorSummary([])).toBeNull();
	});

	it('does not expose statuses or internal paths', () => {
		expect(loadErrorSummary([{ slice: '/crm/home', status: 503 }])).toBe(
			'Some of your information could not be loaded. Please try again shortly.'
		);
	});
});

describe('every slice is narrowed to the active client', () => {
	// A login linked to more than one client picks one in the nav switcher, and `contact_scope()`
	// — what the API falls back to — is ALL of them. `/tasks/me` was the one slice `withContact`
	// had been left off, so the action-items block listed one client's tasks on another's page.
	const sliceCalls = async (blockType: string) => {
		const calls: string[] = [];
		await loadPortalPage(
			'overview',
			locals([pageOf('overview', [block(blockType)])]),
			stubFetch(OK_ROUTES, calls),
			1,
			{},
			7
		);
		return calls;
	};

	it('including the action items, which read /tasks/me', async () => {
		const calls = await sliceCalls('actionItems');
		const tasks = calls.find((c) => c.startsWith('/tasks/me'));
		expect(tasks, 'the action-items block did not read /tasks/me').toBeDefined();
		expect(tasks).toContain('contact_id=7');
	});

	it('and every other slice the page asks for', async () => {
		for (const type of ['accounts', 'holdings', 'activity', 'documents']) {
			const calls = await sliceCalls(type);
			// `/crm/context` and the reference reads are login-wide by nature; the client's OWN
			// data is not, so anything reading crm/documents/tasks must name the contact.
			const owned = calls.filter((c) =>
				/^\/(crm\/(accounts|holdings|home|transactions)|documents|tasks)/.test(c)
			);
			expect(owned.length, `${type} read nothing of the client's own`).toBeGreaterThan(0);
			for (const call of owned) expect(call, `${type}: ${call}`).toContain('contact_id=7');
		}
	});
});
