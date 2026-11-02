/**
 * The transactions route after the book extraction (@graphene/ui book/accounts).
 *
 * The page is a thin mount of the shared `TransactionsListing`, and the rows are REAL now:
 * `/crm/transactions` reads `platform.transactions` (every provider, with the account/client/
 * adviser joins) instead of the Seccl lake, so the loader no longer serves fixtures. What is
 * guarded app-side is the query round-trip — the search, filters and page are applied by the
 * API, and forwarding the wrong params silently returns the wrong rows.
 *
 * The fixture corpus is GONE from the package's exports — every transactions surface reads the API
 * now, and an invented ledger is indistinguishable from a real one on screen. What is still
 * asserted here is the vocabulary the queue rules key on.
 */
import { describe, expect, it } from 'vitest';
import { CONTRIBUTION_CATEGORIES, isPensionProduct, PENSION_PRODUCTS } from '@graphene/ui';
import { load } from './+page.server';

describe('the moved vocabularies (@graphene/ui book/accounts)', () => {
	it('serves the queue rule vocabularies', () => {
		expect(PENSION_PRODUCTS.has('SIPP')).toBe(true);
		expect(CONTRIBUTION_CATEGORIES.map((c) => c.value)).toContain('not_a_contribution');
	});

	it('answers "is this a pension?"', () => {
		expect(isPensionProduct('SIPP')).toBe(true);
		expect(isPensionProduct('GIA')).toBe(false);
		expect(isPensionProduct(null)).toBe(false);
	});
});

describe('the /transactions loader', () => {
	const json = (body: unknown, status = 200) =>
		new Response(JSON.stringify(body), {
			status,
			headers: { 'Content-Type': 'application/json' }
		});
	const page = (items: unknown[], total = items.length) => json({ items, total, facets: {} });

	const event = (
		user: unknown,
		fetch: (url: string | URL | Request) => Promise<Response> = async () => page([]),
		search = ''
	) =>
		({
			locals: { user, accessToken: 't' },
			fetch,
			url: new URL(`http://test/transactions${search}`)
		}) as unknown as Parameters<typeof load>[0];

	// The rows are STREAMED (a promise the page resolves), so the book is awaited and flattened here
	// to keep these cases about the query round-trip rather than the plumbing.
	const run = async (e: Parameters<typeof load>[0]) => {
		const loaded = (await load(e)) as Exclude<Awaited<ReturnType<typeof load>>, void>;
		return { ...loaded, ...(await loaded.book) };
	};

	it('reads the real listing', async () => {
		const row = {
			uid: 'seccl:t1',
			date: '2026-08-01',
			account_id: 2,
			account_name: 'Oakden SIPP',
			client_id: 4,
			client_name: 'Max Oakden',
			type: 'deposit',
			amount: 2500,
			status: 'settled',
			product_type: 'SIPP'
		};
		const result = await run(event({ id: 'user_1' }, async () => page([row])));
		expect(result.transactions).toEqual([row]);
		expect(result.matchCount).toBe(1);
	});

	it('asks for the paged envelope', async () => {
		// `/crm/transactions` returns a bare list unless `paged=true` — `limit` there means "how
		// many rows", so without this the pager would have no total to read.
		let requested = '';
		await run(
			event({ id: 'user_1' }, async (url) => {
				requested = String(url);
				return page([]);
			})
		);
		expect(requested).toContain('paged=true');
		expect(requested).toContain('limit=');
	});

	it('forwards the URL search, filters and page', async () => {
		let requested = '';
		await run(
			event(
				{ id: 'user_1' },
				async (url) => {
					requested = String(url);
					return page([]);
				},
				'?q=oakden&type=deposit&offset=50'
			)
		);
		expect(requested).toContain('q=oakden');
		expect(requested).toContain('type=deposit');
		expect(requested).toContain('offset=50');
	});

	it('fails soft to an empty listing and flags it', async () => {
		// An adviser with a full ledger must not be shown "no transactions" because the API 502'd.
		const result = await run(event({ id: 'user_1' }, async () => json({ detail: 'boom' }, 502)));
		expect(result.transactions).toEqual([]);
		expect(result.loadError).toBe(true);
	});

	it('bounces an unauthenticated caller to /login', async () => {
		await expect(load(event(null))).rejects.toMatchObject({
			status: 303,
			location: '/login'
		});
	});
});
