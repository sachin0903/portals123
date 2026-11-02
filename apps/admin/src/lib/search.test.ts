/**
 * The operations firm-search logic ($lib/search.ts, consumed by $lib/SearchBar.svelte).
 *
 * The bar it replaced listed five invented firms; this one calls GET /search/tenants through the
 * portal proxy, which re-checks `tenants:read`. The failure path matters more here than in the
 * other portals: an ops user without that permission gets a 403, and the palette must say so
 * rather than showing an empty firm list that reads as "this instance has no firms".
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
	canSearch,
	canSearchFirmBook,
	DEBOUNCE_MS,
	firmBookRoutes,
	firmBookSearchUrl,
	firmHref,
	firms,
	FIRM_BOOK_SEARCH_PERMISSION,
	MIN_QUERY_LENGTH,
	scheduleQuery,
	SearchClient,
	searchUrl,
	SEARCH_PERMISSION,
	SEARCH_SCOPES,
	settle,
	type Hit,
	type Results
} from './search';
import { BOOK_ROUTE_KEYS, BOOK_SEARCH_SCOPES } from '@graphene/ui';

const hit = (id: string, title: string): Hit => ({
	type: 'tenant',
	id,
	title,
	subtitle: null,
	meta: null
});

const results = (...hits: Hit[]): Results => ({
	query: 'ash',
	groups: hits.length ? [{ type: 'tenant', label: 'Firms', hits }] : [],
	truncated: false
});

function deferredFetch() {
	const pending: { resolve: (r: Results) => void }[] = [];
	const fetchImpl = vi.fn(
		() =>
			new Promise<Response>((resolvePromise) => {
				pending.push({
					resolve: (body) =>
						resolvePromise({ ok: true, json: async () => body } as unknown as Response)
				});
			})
	);
	return { fetchImpl: fetchImpl as unknown as typeof globalThis.fetch, pending };
}

describe('flattening the API groups', () => {
	it('keeps the API order (name, id — search.py orders the query, not the bar)', () => {
		const flat = firms(results(hit('4', 'Ashworth'), hit('9', 'Bryce & Co')));
		expect(flat.map((f) => f.title)).toEqual(['Ashworth', 'Bryce & Co']);
	});

	it('is empty before anything is fetched, and for a no-match result', () => {
		expect(firms(null)).toEqual([]);
		expect(firms(results())).toEqual([]);
	});

	it('opens a firm at its ops record page', () => {
		expect(firmHref('ten_1743d0')).toBe('/tenants/ten_1743d0');
	});
});

describe('SearchClient request sequencing', () => {
	it('encodes the query into the portal proxy URL', async () => {
		const fetchImpl = vi.fn(
			async () => ({ ok: true, json: async () => results() }) as unknown as Response
		);
		const client = new SearchClient(searchUrl, fetchImpl as unknown as typeof globalThis.fetch);
		await client.run('ash & co');
		expect(fetchImpl).toHaveBeenCalledWith('/search?q=ash%20%26%20co', {
			signal: expect.any(AbortSignal)
		});
	});

	it('discards a slow earlier response so it cannot overwrite a newer one', async () => {
		const { fetchImpl, pending } = deferredFetch();
		const client = new SearchClient(searchUrl, fetchImpl);
		const first = client.run('as');
		const second = client.run('ashworth');
		pending[1].resolve(results(hit('4', 'Ashworth')));
		pending[0].resolve(results(hit('9', 'Bryce & Co')));
		await expect(second).resolves.toEqual({ ok: true, results: results(hit('4', 'Ashworth')) });
		await expect(first).resolves.toBeNull();
	});

	it('discards an in-flight response after cancel', async () => {
		const { fetchImpl, pending } = deferredFetch();
		const client = new SearchClient(searchUrl, fetchImpl);
		const run = client.run('as');
		client.cancel();
		pending[0].resolve(results(hit('4', 'Ashworth')));
		await expect(run).resolves.toBeNull();
	});

	it('reports a 403 as a failure, not as "no firms"', async () => {
		const client = new SearchClient(
			searchUrl,
			vi.fn(
				async () => ({ ok: false, status: 403 }) as unknown as Response
			) as unknown as typeof globalThis.fetch
		);
		await expect(client.run('ash')).resolves.toEqual({ ok: false });
	});

	it('reports a network error as a failure rather than throwing at the caller', async () => {
		const client = new SearchClient(
			searchUrl,
			vi.fn(async () => {
				throw new Error('offline');
			}) as unknown as typeof globalThis.fetch
		);
		await expect(client.run('ash')).resolves.toEqual({ ok: false });
	});
});

describe('who is offered the bar at all (canSearch)', () => {
	// The bar used to render for every operations user while both its proxy and search_tenants
	// require `tenants:read` — so a reviewer or audit-only ops user got a control whose only
	// possible answer was "Search is unavailable just now.". SearchBar.svelte now hides itself on
	// this predicate, and src/routes/search/+server.ts gates on the same one.

	it('offers it to a holder of tenants:read', () => {
		expect(canSearch(['tenants:read'], 'reviewer')).toBe(true);
	});

	it('offers it to a holder of tenants:write, because write implies read', () => {
		expect(canSearch(['tenants:write'], 'reviewer')).toBe(true);
	});

	it('offers it to a system principal', () => {
		expect(canSearch(['system'], null)).toBe(true);
	});

	it('withholds it from an ops user without the permission', () => {
		expect(canSearch(['audit:read', 'operations:write'], 'reviewer')).toBe(false);
		expect(canSearch([], null)).toBe(false);
		expect(canSearch(undefined, null)).toBe(false);
		expect(canSearch(null, null)).toBe(false);
	});

	it('cannot be talked round by a role a firm named itself', () => {
		// `can` deliberately ignores the role; a tenant that calls a role "admin" or "system" must
		// not thereby unlock a cross-tenant firm search.
		expect(canSearch([], 'admin')).toBe(false);
		expect(canSearch([], 'system')).toBe(false);
		expect(canSearch([], 'owner')).toBe(false);
	});

	it('cannot be satisfied by a look-alike permission slug', () => {
		// Substring / prefix / suffix neighbours of the real slug, and the wrong resource.
		expect(canSearch(['tenants:rea'], null)).toBe(false);
		expect(canSearch(['tenants:read_all'], null)).toBe(false);
		expect(canSearch(['xtenants:read'], null)).toBe(false);
		expect(canSearch(['tenant:read'], null)).toBe(false);
		expect(canSearch(['TENANTS:READ'], null)).toBe(false);
		expect(canSearch(['systems'], null)).toBe(false);
	});

	it('names the permission the API actually enforces', () => {
		// search.py::search_tenants → require_permission(principal, TENANTS_READ).
		expect(SEARCH_PERMISSION).toBe('tenants:read');
	});
});

describe('debounce (scheduleQuery — what $lib/SearchBar.svelte returns from its $effect)', () => {
	beforeEach(() => vi.useFakeTimers());
	afterEach(() => vi.useRealTimers());

	/** One keystroke: run the effect body, keeping the cleanup the previous keystroke needs. */
	function typist() {
		const queried: string[] = [];
		const idled: number[] = [];
		const sink = { query: (q: string) => queried.push(q), idle: () => idled.push(1) };
		let cleanup: (() => void) | undefined;
		return {
			queried,
			idled,
			type(q: string) {
				cleanup?.(); // Svelte runs the previous run's teardown before re-running the effect
				cleanup = scheduleQuery(q, sink);
			}
		};
	}

	it('sends one request for a burst of keystrokes — the last query, not the first', () => {
		const bar = typist();
		for (const q of ['as', 'ash', 'ashw', 'ashworth']) bar.type(q);
		vi.advanceTimersByTime(DEBOUNCE_MS - 1);
		expect(bar.queried).toEqual([]); // still inside the pause: nothing has been asked for yet
		vi.advanceTimersByTime(1);
		expect(bar.queried).toEqual(['ashworth']);
	});

	it('never queries below the minimum length, and clears the palette at once instead', () => {
		const bar = typist();
		bar.type('a');
		vi.advanceTimersByTime(DEBOUNCE_MS * 10);
		expect(bar.queried).toEqual([]);
		expect(bar.idled).toHaveLength(1); // synchronous — the palette must not keep stale hits
		expect(scheduleQuery('a', { query: () => {}, idle: () => {} })).toBeUndefined();
	});

	it('cancels in flight when the query is deleted back down to one character', () => {
		const bar = typist();
		bar.type('ashworth');
		bar.type('a'); // the cleanup runs first, so the pending request is dropped
		vi.advanceTimersByTime(DEBOUNCE_MS * 10);
		expect(bar.queried).toEqual([]);
		expect(bar.idled).toHaveLength(1);
	});

	it('queries again for a query typed, deleted and retyped identically', () => {
		const bar = typist();
		bar.type('ash');
		vi.advanceTimersByTime(DEBOUNCE_MS);
		bar.type('');
		bar.type('ash');
		vi.advanceTimersByTime(DEBOUNCE_MS);
		expect(bar.queried).toEqual(['ash', 'ash']);
	});
});

describe('settle (what one resolved query does to the palette)', () => {
	it('flattens fresh results and clears any previous failure', () => {
		expect(settle({ ok: true, results: results(hit('4', 'Ashworth')) })).toEqual({
			firms: [hit('4', 'Ashworth')],
			failed: false
		});
	});

	it('clears the list and raises the retry banner on failure — never a silent empty list', () => {
		expect(settle({ ok: false })).toEqual({ firms: [], failed: true });
	});

	it('leaves the palette untouched when the query was superseded', () => {
		expect(settle(null)).toBeNull();
	});
});

it('agrees with search.py on the minimum query length', () => {
	expect(MIN_QUERY_LENGTH).toBe(2);
});

describe('in-firm book search (platform parity)', () => {
	it('uses the shared firm-book catalogue', () => {
		expect(SEARCH_SCOPES).toBe(BOOK_SEARCH_SCOPES);
	});

	it('routes every shared entity key under /tenants/{uid}/operate/…', () => {
		const routes = firmBookRoutes('ten_acme');
		expect(Object.keys(routes).sort()).toEqual([...BOOK_ROUTE_KEYS].sort());
		expect(routes.client?.('c1')).toBe('/tenants/ten_acme/operate/clients/c1');
		expect(routes.account?.('a1')).toBe('/tenants/ten_acme/operate/accounts/a1');
		expect(routes.document?.('d1')).toBe('/tenants/ten_acme/operate/documents/d1');
		expect(routes.case?.('j1')).toBe('/tenants/ten_acme/operate/journeys/j1');
		expect(routes.transaction?.('a1')).toBe('/tenants/ten_acme/operate/accounts/a1');
		expect(routes.message?.('c1')).toBe('/tenants/ten_acme/operate/clients/c1/chat');
		expect(routes.message?.('group:g1')).toBe('/tenants/ten_acme/operate/messages');
		expect(routes.message_group?.('g1')).toBe('/tenants/ten_acme/operate/messages');
		expect(routes.review?.('r1')).toBe('/tenants/ten_acme/operate/reviews?open=r1');
	});

	it('builds the firm proxy URL with optional scope', () => {
		expect(firmBookSearchUrl('ten_acme', 'cole', null)).toBe('/tenants/ten_acme/search?q=cole');
		expect(firmBookSearchUrl('ten_acme', 'cole', 'client')).toBe(
			'/tenants/ten_acme/search?q=cole&scope=clients'
		);
		expect(firmBookSearchUrl('ten_acme', 'cole', 'case')).toBe(
			'/tenants/ten_acme/search?q=cole&scope=journeys'
		);
	});

	it('gates the firm book bar on tenant_portal:read', () => {
		expect(FIRM_BOOK_SEARCH_PERMISSION).toBe('tenant_portal:read');
		expect(canSearchFirmBook(['tenant_portal:read'], null)).toBe(true);
		expect(canSearchFirmBook(['tenant_portal:write'], null)).toBe(true);
		expect(canSearchFirmBook(['tenants:read'], null)).toBe(false);
	});
});
