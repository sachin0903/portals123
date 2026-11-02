/**
 * The ⌘K palette's logic ($lib/search.ts, consumed by $lib/SearchBar.svelte).
 *
 * The most important assertion here is the last group: the API's /search is shared by all three
 * portals and returns whatever groups the caller may open. Client financial results either have
 * their own detail route or target the account that owns them. A client end-user is never given a
 * "Clients" group by the API, and this portal must not route one even if it arrives.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
	DEBOUNCE_MS,
	hrefFor,
	isPaletteShortcut,
	MIN_QUERY_LENGTH,
	ROUTES,
	routableGroups,
	scheduleQuery,
	SEARCH_SCOPES,
	SearchClient,
	searchUrl,
	settle,
	type Group,
	type Hit,
	type Results
} from './search';

const hit = (type: string, id: string): Hit => ({
	type,
	id,
	title: `${type} ${id}`,
	subtitle: null,
	meta: null
});

const results = (...groups: Group[]): Results => ({ query: 'cole', groups, truncated: false });

const documents: Group = {
	type: 'document',
	label: 'Documents',
	hits: [hit('document', 'doc_1')]
};

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

describe('routing hits in the client portal', () => {
	it('opens record hits directly and activity hits on their owning account', () => {
		expect(hrefFor(hit('account', 'acc_1'))).toBe('/accounts/acc_1');
		expect(hrefFor(hit('document', 'doc_1'))).toBe('/documents/doc_1');
		expect(hrefFor(hit('insight', 'ins_1'))).toBe('/insights/ins_1');
		expect(hrefFor({ ...hit('holding', 'asset_1'), target: 'acc_1' })).toBe('/accounts/acc_1');
		expect(hrefFor({ ...hit('transaction', 'txn_1'), target: 'acc_1' })).toBe('/accounts/acc_1');
	});

	it('does not search action items: a task hit has no route, so it is dropped', () => {
		expect(hrefFor(hit('task', 'tsk_1'))).toBeNull();
		expect(SEARCH_SCOPES.map((s) => s.type)).not.toContain('task');
	});

	it('routes every client search group and nothing staff-only', () => {
		expect(Object.keys(ROUTES).sort()).toEqual([
			'account',
			'document',
			'holding',
			'insight',
			'transaction'
		]);
	});

	it('never routes a staff-side group, even if one arrives', () => {
		const staffGroup: Group = { type: 'client', label: 'Clients', hits: [hit('client', '7')] };
		expect(hrefFor(hit('client', '7'))).toBeNull();
		expect(hrefFor(hit('case', 'exe_1'))).toBeNull();
		expect(routableGroups(results(staffGroup, documents)).map((g) => g.type)).toEqual(['document']);
	});

	it('drops a group left empty by that filter, so no heading sits over nothing', () => {
		const mixed: Group = {
			type: 'document',
			label: 'Documents',
			hits: [hit('client', '7'), hit('document', 'doc_1')]
		};
		expect(routableGroups(results(mixed))[0].hits.map((h) => h.id)).toEqual(['doc_1']);
		expect(
			routableGroups(results({ type: 'client', label: 'Clients', hits: [hit('client', '7')] }))
		).toEqual([]);
		expect(routableGroups(null)).toEqual([]);
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
		for (const q of ['re', 'rev', 'revi', 'review']) bar.type(q);
		vi.advanceTimersByTime(DEBOUNCE_MS - 1);
		expect(bar.queried).toEqual([]); // still inside the pause: nothing has been asked for yet
		vi.advanceTimersByTime(1);
		expect(bar.queried).toEqual(['review']);
	});

	it('never queries below the minimum length, and clears the palette at once instead', () => {
		const bar = typist();
		bar.type('r');
		vi.advanceTimersByTime(DEBOUNCE_MS * 10);
		expect(bar.queried).toEqual([]);
		expect(bar.idled).toHaveLength(1); // synchronous — the panel must not keep stale hits
		expect(scheduleQuery('r', { query: () => {}, idle: () => {} })).toBeUndefined();
	});

	it('cancels in flight when the query is deleted back down to one character', () => {
		const bar = typist();
		bar.type('review');
		bar.type('r'); // the cleanup runs first, so the pending request is dropped
		vi.advanceTimersByTime(DEBOUNCE_MS * 10);
		expect(bar.queried).toEqual([]);
		expect(bar.idled).toHaveLength(1);
	});

	it('queries again for a query typed, deleted and retyped identically', () => {
		// The effect re-runs on every change of `trimmed`, so this must not be deduplicated away.
		const bar = typist();
		bar.type('re');
		vi.advanceTimersByTime(DEBOUNCE_MS);
		bar.type('');
		bar.type('re');
		vi.advanceTimersByTime(DEBOUNCE_MS);
		expect(bar.queried).toEqual(['re', 're']);
	});
});

describe('settle (what one resolved query does to the palette)', () => {
	it('paints fresh results and clears any previous failure', () => {
		expect(settle({ ok: true, results: results(documents) })).toEqual({
			results: results(documents),
			failed: false
		});
	});

	it('clears the results and raises the retry banner on failure', () => {
		expect(settle({ ok: false })).toEqual({ results: null, failed: true });
	});

	it('leaves the palette untouched when the query was superseded', () => {
		// SearchClient.run returns null for a stale ticket; treating that as "no results" would
		// blank the panel a newer query has already filled.
		expect(settle(null)).toBeNull();
	});
});

describe('palette shortcut', () => {
	it('is ⌘K on macOS and Ctrl+K elsewhere, case-insensitively', () => {
		expect(isPaletteShortcut({ key: 'k', metaKey: true })).toBe(true);
		expect(isPaletteShortcut({ key: 'K', ctrlKey: true })).toBe(true);
	});

	it('ignores a bare k, so typing in a field never opens the palette', () => {
		expect(isPaletteShortcut({ key: 'k' })).toBe(false);
		expect(isPaletteShortcut({ key: 'j', metaKey: true })).toBe(false);
	});
});

it('agrees with search.py on the minimum query length', () => {
	expect(MIN_QUERY_LENGTH).toBe(2);
});
