/**
 * The shared portal-search mechanics (./search.ts) — hoisted from the three apps' identical
 * copies by the UI-consolidation sweep. Each app's own `$lib/search.test.ts` keeps testing its
 * PORTAL POLICY (routes, permission gating, result shaping) through its facade; what is pinned
 * here is the machinery every palette shares: the request-sequence race guard, the debounce
 * rules, the settle step and the routability filter.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
	BOOK_ROUTE_KEYS,
	BOOK_SEARCH_SCOPES,
	isSearchShortcut,
	openSearchPalette,
	relevanceOrderedSearchHits,
	routableSearchGroups,
	scheduleSearch,
	SEARCH_OPEN_EVENT,
	searchCategoriesForGroups,
	searchHighlightParts,
	SEARCH_DEBOUNCE_MS,
	SEARCH_MIN_QUERY_LENGTH,
	SEARCH_TIMEOUT_MS,
	SearchClient,
	searchHrefFor,
	searchUrl,
	settleSearch,
	type SearchGroup,
	type SearchHit,
	type SearchResults,
	type SearchRoutes
} from './search.js';

const hit = (type: string, id: string): SearchHit => ({
	type,
	id,
	title: `${type} ${id}`,
	subtitle: null,
	meta: null
});

const results = (...groups: SearchGroup[]): SearchResults => ({
	query: 'cole',
	groups,
	truncated: false
});

const documents: SearchGroup = {
	type: 'document',
	label: 'Documents',
	hits: [hit('document', 'doc_1')]
};

// A deliberately non-portal routes map: the mechanics take whatever policy the app injects.
const routes: SearchRoutes = { document: (id) => `/x/documents/${id}` };

function deferredFetch() {
	const pending: { resolve: (r: SearchResults) => void }[] = [];
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

describe('routability (the "drop hits this portal cannot route" rule)', () => {
	it('routes through the injected map and returns null for an unmapped type', () => {
		expect(searchHrefFor(hit('document', 'doc_1'), routes)).toBe('/x/documents/doc_1');
		expect(searchHrefFor({ ...hit('document', 'txn_1'), target: 'account_1' }, routes)).toBe(
			'/x/documents/account_1'
		);
		expect(searchHrefFor(hit('client', '7'), routes)).toBeNull();
	});

	it('drops unroutable hits, and any group left empty by that — no heading over nothing', () => {
		const mixed: SearchGroup = {
			type: 'document',
			label: 'Documents',
			hits: [hit('client', '7'), hit('document', 'doc_1')]
		};
		expect(routableSearchGroups(results(mixed), routes)[0].hits.map((h) => h.id)).toEqual([
			'doc_1'
		]);
		expect(
			routableSearchGroups(
				results({ type: 'client', label: 'Clients', hits: [hit('client', '7')] }),
				routes
			)
		).toEqual([]);
		expect(routableSearchGroups(null, routes)).toEqual([]);
	});
});

describe('SearchClient request sequencing', () => {
	it('encodes the query into the portal proxy URL', async () => {
		const fetchImpl = vi.fn(
			async () => ({ ok: true, json: async () => results() }) as unknown as Response
		);
		const client = new SearchClient(searchUrl, fetchImpl as unknown as typeof globalThis.fetch);
		await client.run('a & b');
		expect(fetchImpl).toHaveBeenCalledWith('/search?q=a%20%26%20b', {
			signal: expect.any(AbortSignal)
		});
	});

	it('discards a slow earlier response so it cannot overwrite a newer one', async () => {
		const { fetchImpl, pending } = deferredFetch();
		const client = new SearchClient(searchUrl, fetchImpl);
		const first = client.run('re');
		const second = client.run('review');
		pending[1].resolve(results(documents));
		pending[0].resolve(results());
		await expect(second).resolves.toEqual({ ok: true, results: results(documents) });
		await expect(first).resolves.toBeNull();
	});

	it('discards an in-flight response after cancel (palette closed / query too short)', async () => {
		const { fetchImpl, pending } = deferredFetch();
		const client = new SearchClient(searchUrl, fetchImpl);
		const run = client.run('re');
		client.cancel();
		pending[0].resolve(results(documents));
		await expect(run).resolves.toBeNull();
	});

	it('aborts the previous request when a newer query starts', async () => {
		const signals: AbortSignal[] = [];
		const fetchImpl = vi.fn(
			(_url: RequestInfo | URL, init?: RequestInit) =>
				new Promise<Response>((_resolve, reject) => {
					const signal = init?.signal as AbortSignal;
					signals.push(signal);
					signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
				})
		);
		const client = new SearchClient(searchUrl, fetchImpl as typeof globalThis.fetch);
		const first = client.run('re');
		void client.run('review');
		expect(signals[0].aborted).toBe(true);
		await expect(first).resolves.toBeNull();
		client.cancel();
	});

	it('turns a request that exceeds the interactive deadline into a retryable failure', async () => {
		vi.useFakeTimers();
		const fetchImpl = vi.fn(
			(_url: RequestInfo | URL, init?: RequestInit) =>
				new Promise<Response>((_resolve, reject) => {
					init?.signal?.addEventListener('abort', () =>
						reject(new DOMException('Aborted', 'AbortError'))
					);
				})
		);
		const client = new SearchClient(
			searchUrl,
			fetchImpl as typeof globalThis.fetch,
			SEARCH_TIMEOUT_MS
		);
		const pending = client.run('ifsl');
		await vi.advanceTimersByTimeAsync(SEARCH_TIMEOUT_MS);
		await expect(pending).resolves.toEqual({ ok: false });
		vi.useRealTimers();
	});

	it('reports a non-OK response and a network error as failures', async () => {
		const failing = new SearchClient(
			searchUrl,
			vi.fn(
				async () => ({ ok: false, status: 401 }) as unknown as Response
			) as unknown as typeof globalThis.fetch
		);
		await expect(failing.run('cole')).resolves.toEqual({ ok: false });

		const offline = new SearchClient(
			searchUrl,
			vi.fn(async () => {
				throw new Error('offline');
			}) as unknown as typeof globalThis.fetch
		);
		await expect(offline.run('cole')).resolves.toEqual({ ok: false });
	});
});

describe('debounce (scheduleSearch — what a search bar returns from its $effect)', () => {
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
				cleanup = scheduleSearch(q, sink);
			}
		};
	}

	it('sends one request for a burst of keystrokes — the last query, not the first', () => {
		const bar = typist();
		for (const q of ['re', 'rev', 'revi', 'review']) bar.type(q);
		vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS - 1);
		expect(bar.queried).toEqual([]); // still inside the pause: nothing has been asked for yet
		vi.advanceTimersByTime(1);
		expect(bar.queried).toEqual(['review']);
	});

	it('never queries below the minimum length, and clears the palette at once instead', () => {
		const bar = typist();
		bar.type('r');
		vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS * 10);
		expect(bar.queried).toEqual([]);
		expect(bar.idled).toHaveLength(1); // synchronous — the panel must not keep stale hits
		expect(scheduleSearch('r', { query: () => {}, idle: () => {} })).toBeUndefined();
	});

	it('cancels in flight when the query is deleted back down to one character', () => {
		const bar = typist();
		bar.type('review');
		bar.type('r'); // the cleanup runs first, so the pending request is dropped
		vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS * 10);
		expect(bar.queried).toEqual([]);
		expect(bar.idled).toHaveLength(1);
	});

	it('queries again for a query typed, deleted and retyped identically', () => {
		// The effect re-runs on every change of the trimmed query, so this must not be deduplicated.
		const bar = typist();
		bar.type('re');
		vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
		bar.type('');
		bar.type('re');
		vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
		expect(bar.queried).toEqual(['re', 're']);
	});
});

describe('settleSearch (what one resolved query does to the palette)', () => {
	it('paints fresh results and clears any previous failure', () => {
		expect(settleSearch({ ok: true, results: results(documents) })).toEqual({
			results: results(documents),
			failed: false
		});
	});

	it('clears the results and raises the retry banner on failure', () => {
		expect(settleSearch({ ok: false })).toEqual({ results: null, failed: true });
	});

	it('leaves the palette untouched when the query was superseded', () => {
		// SearchClient.run returns null for a stale ticket; treating that as "no results" would
		// blank the panel a newer query has already filled.
		expect(settleSearch(null)).toBeNull();
	});
});

describe('palette shortcut', () => {
	it('is ⌘K on macOS and Ctrl+K elsewhere, case-insensitively', () => {
		expect(isSearchShortcut({ key: 'k', metaKey: true })).toBe(true);
		expect(isSearchShortcut({ key: 'K', ctrlKey: true })).toBe(true);
	});

	it('ignores a bare k, so typing in a field never opens the palette', () => {
		expect(isSearchShortcut({ key: 'k' })).toBe(false);
		expect(isSearchShortcut({ key: 'j', metaKey: true })).toBe(false);
	});
});

it('agrees with search.py on the minimum query length', () => {
	expect(SEARCH_MIN_QUERY_LENGTH).toBe(2);
});

it('names the firm-book catalogue both staff palettes share', () => {
	expect(BOOK_SEARCH_SCOPES.map((scope) => scope.command)).toEqual([
		'everywhere',
		'clients',
		'accounts',
		'documents',
		'journeys',
		'transactions',
		'messages',
		'reviews'
	]);
	expect([...BOOK_ROUTE_KEYS].sort()).toEqual([
		'account',
		'case',
		'client',
		'document',
		'message',
		'message_group',
		'review',
		'transaction'
	]);
});

describe('relevance order (relevanceOrderedSearchHits)', () => {
	const hit = (id: string, rank?: number | null): SearchHit => ({
		type: 'document',
		id,
		title: id,
		subtitle: null,
		meta: null,
		...(rank === undefined ? {} : { rank })
	});

	it('walks the backend rank across groups, unranked hits last in their stable order', () => {
		const groups: SearchGroup[] = [
			{ type: 'account', label: 'Accounts', hits: [hit('a3', 3), hit('a-none')] },
			{ type: 'document', label: 'Documents', hits: [hit('d1', 1), hit('d2', 2)] }
		];
		expect(relevanceOrderedSearchHits(groups).map((h) => h.id)).toEqual([
			'd1',
			'd2',
			'a3',
			'a-none'
		]);
	});

	it('a payload with no ranks (the client portal) keeps its group/row order', () => {
		const groups: SearchGroup[] = [
			{ type: 'account', label: 'Accounts', hits: [hit('a1'), hit('a2')] },
			{ type: 'document', label: 'Documents', hits: [hit('d1')] }
		];
		expect(relevanceOrderedSearchHits(groups).map((h) => h.id)).toEqual(['a1', 'a2', 'd1']);
	});
});

describe('the contextual category strip (searchCategoriesForGroups)', () => {
	const scopes = [
		{ type: 'account', label: 'Accounts' },
		{ type: 'document', label: 'Documents' },
		{ type: 'message', label: 'Messages' }
	];

	it('offers only the categories THIS result contains, in the caller’s order', () => {
		const groups = [{ type: 'document' }, { type: 'account' }, { type: 'review' }];
		expect(searchCategoriesForGroups(groups, scopes).map((c) => c.type)).toEqual([
			'account',
			'document'
		]);
	});

	it('folds aliased group types onto the scope that owns them', () => {
		const groups = [{ type: 'message_group' }];
		const alias = (t: string) => (t === 'message_group' ? 'message' : t);
		expect(searchCategoriesForGroups(groups, scopes, alias).map((c) => c.type)).toEqual([
			'message'
		]);
		expect(searchCategoriesForGroups(groups, scopes)).toEqual([]);
	});
});

describe('highlighting (searchHighlightParts)', () => {
	it('marks literal matches case- and accent-insensitively, keeping the original spelling', () => {
		expect(searchHighlightParts('José Smith', 'jose')).toEqual([
			{ text: 'José', matched: true },
			{ text: ' Smith', matched: false }
		]);
	});

	it('merges overlapping tokens and leaves a non-match untouched', () => {
		expect(searchHighlightParts('Quarterly valuation', 'quarter valuation')).toEqual([
			{ text: 'Quarter', matched: true },
			{ text: 'ly ', matched: false },
			{ text: 'valuation', matched: true }
		]);
		expect(searchHighlightParts('Passport', 'zzz')).toEqual([{ text: 'Passport', matched: false }]);
	});
});

describe('the open-palette bridge (openSearchPalette)', () => {
	afterEach(() => vi.unstubAllGlobals());

	it('dispatches one window event carrying the category to open on', () => {
		// Node has EventTarget and CustomEvent; only `window` itself is missing here.
		const target = new EventTarget();
		vi.stubGlobal('window', target);
		const seen: (string | null)[] = [];
		target.addEventListener(SEARCH_OPEN_EVENT, (e) =>
			seen.push((e as CustomEvent<{ scope: string | null }>).detail.scope)
		);
		openSearchPalette('document');
		openSearchPalette();
		expect(seen).toEqual(['document', null]);
	});

	it('is a no-op with no window (SSR), never a throw', () => {
		vi.stubGlobal('window', undefined);
		expect(() => openSearchPalette('document')).not.toThrow();
	});
});
