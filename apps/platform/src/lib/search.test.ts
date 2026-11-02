/**
 * The header search bar's logic ($lib/search.ts, consumed by $lib/SearchBar.svelte).
 *
 * The bar it replaced filtered a hardcoded fixture list, so none of this existed; what it now does
 * is easy to get silently wrong, and each of these is a specific way it could:
 *
 * - an out-of-order response repainting the panel with a stale query's hits;
 * - routing a hit type this portal has no page for (the API returns typed handles for every group
 *   the caller may open, and each portal routes a different subset);
 * - a group heading left standing over zero rows once the unroutable hits are dropped;
 * - the keyboard highlight drifting out of step with the flattened list, so Enter opens something
 *   other than what is highlighted.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { BOOK_ROUTE_KEYS, BOOK_SEARCH_SCOPES } from '@graphene/ui';
import {
	categoriesForGroups,
	COMMAND_PREFIX,
	consumeSearchGuide,
	DEBOUNCE_MS,
	flatten,
	highlightParts,
	hrefFor,
	MIN_QUERY_LENGTH,
	moveActive,
	PALETTE_SHORTCUTS,
	paletteKeyboardAction,
	parsePaletteQuery,
	relevanceOrderedHits,
	ROUTES,
	routableGroups,
	scheduleQuery,
	scopedSearchUrl,
	SEARCH_SCOPES,
	searchShortcutLabel,
	SCOPE_PREFIX,
	SearchClient,
	searchGuideStorageKey,
	searchUrl,
	settle,
	uploadDocumentHref,
	type Group,
	type Hit,
	type Results
} from './search';

const hit = (type: string, id: string, title = `${type} ${id}`): Hit => ({
	type,
	id,
	title,
	subtitle: null,
	meta: null
});

const results = (...groups: Group[]): Results => ({ query: 'cole', groups, truncated: false });

const clients: Group = { type: 'client', label: 'Clients', hits: [hit('client', '7')] };
const documents: Group = {
	type: 'document',
	label: 'Documents',
	hits: [hit('document', 'doc_1'), hit('document', 'doc_2')]
};

/** A fetch stub whose responses resolve when told to, so ordering is deterministic. */
function deferredFetch() {
	const pending: { url: string; resolve: (r: Results) => void }[] = [];
	const fetchImpl = vi.fn((input: RequestInfo | URL) => {
		return new Promise<Response>((resolvePromise) => {
			pending.push({
				url: String(input),
				resolve: (body) =>
					resolvePromise({ ok: true, json: async () => body } as unknown as Response)
			});
		});
	});
	return { fetchImpl: fetchImpl as unknown as typeof globalThis.fetch, pending };
}

describe('routing hits to this portal', () => {
	it('maps each type to its own record page, never a list page', () => {
		expect(hrefFor(hit('client', '7'))).toBe('/clients/7');
		expect(hrefFor(hit('document', 'doc_1'))).toBe('/documents/doc_1');
		expect(hrefFor(hit('task', 'tsk_1'))).toBeNull();
		expect(hrefFor(hit('case', 'exe_1'))).toBe('/journeys/exe_1');
		expect(hrefFor({ ...hit('transaction', 'txn_1'), target: 'acc_1' })).toBe('/accounts/acc_1');
		// A message hit names a conversation (or `group:` a group). Both used to link the thread
		// PROXY (`/chats/{uid}`), which is JSON the inbox fetches — clicking it never opened a page.
		expect(hrefFor(hit('message', 'cnv_1'))).toBe('/chats?conversation=cnv_1');
		expect(hrefFor({ ...hit('message', 'msg_1'), target: 'group:grp_1' })).toBe(
			'/chats?group=grp_1'
		);
		expect(hrefFor(hit('message_group', 'grp_1'))).toBe('/chats?group=grp_1');
		expect(hrefFor(hit('review', 'rev_1'))).toBe('/reviews/rev_1');
	});

	it('drops an unroutable type instead of mislinking it', () => {
		// 'tenant' is the ops bar's type; if the API ever returns one here it must not be offered.
		expect(hrefFor(hit('tenant', '3'))).toBeNull();
		const groups = routableGroups(
			results({ type: 'tenant', label: 'Firms', hits: [hit('tenant', '3')] }, documents)
		);
		expect(groups.map((g) => g.type)).toEqual(['document']);
	});

	it('drops a group left empty by that filter, so no heading sits over nothing', () => {
		const mixed: Group = {
			type: 'document',
			label: 'Documents',
			hits: [hit('tenant', '3'), hit('document', 'doc_1')]
		};
		const groups = routableGroups(results(mixed));
		expect(groups).toHaveLength(1);
		expect(groups[0].hits.map((h) => h.id)).toEqual(['doc_1']);

		expect(routableGroups(results({ type: 'x', label: 'X', hits: [hit('tenant', '3')] }))).toEqual(
			[]
		);
	});

	it('has no results to flatten before anything has been fetched', () => {
		expect(flatten(null)).toEqual([]);
		expect(routableGroups(null)).toEqual([]);
	});
});

describe('keyboard walk over the flattened list', () => {
	const flat = flatten(results(clients, documents));

	it('flattens in display order, so index N is the Nth row shown', () => {
		expect(flat.map((h) => h.id)).toEqual(['7', 'doc_1', 'doc_2']);
	});

	it('wraps in both directions', () => {
		expect(moveActive('ArrowDown', 0, flat.length)).toBe(1);
		expect(moveActive('ArrowDown', 2, flat.length)).toBe(0);
		expect(moveActive('ArrowUp', 0, flat.length)).toBe(2);
		expect(moveActive('ArrowUp', 1, flat.length)).toBe(0);
	});

	it('leaves other keys and an empty list alone (the caller must not preventDefault)', () => {
		expect(moveActive('Enter', 0, flat.length)).toBeNull();
		expect(moveActive('a', 0, flat.length)).toBeNull();
		expect(moveActive('ArrowDown', 0, 0)).toBeNull();
		expect(moveActive('ArrowUp', 0, 0)).toBeNull();
	});

	it('walks one cross-category relevance order when the API supplies ranks', () => {
		const ordered = relevanceOrderedHits([
			{ ...clients, hits: [{ ...clients.hits[0], rank: 3 }] },
			{
				...documents,
				hits: [
					{ ...documents.hits[0], rank: 1 },
					{ ...documents.hits[1], rank: 2 }
				]
			}
		]);
		expect(ordered.map((item) => item.id)).toEqual(['doc_1', 'doc_2', '7']);
	});
});

describe('SearchClient request sequencing', () => {
	it('encodes the query into the portal proxy URL', async () => {
		const fetchImpl = vi.fn(
			async () => ({ ok: true, json: async () => results() }) as unknown as Response
		);
		const client = new SearchClient(searchUrl, fetchImpl as unknown as typeof globalThis.fetch);
		await client.run('a & b/c');
		expect(fetchImpl).toHaveBeenCalledWith('/search?q=a%20%26%20b%2Fc', {
			signal: expect.any(AbortSignal)
		});
	});

	it('discards a slow earlier response so it cannot overwrite a newer one', async () => {
		const { fetchImpl, pending } = deferredFetch();
		const client = new SearchClient(searchUrl, fetchImpl);

		const first = client.run('sm');
		const second = client.run('smith');
		expect(pending).toHaveLength(2);

		// The NEW query answers first, then the stale one arrives.
		pending[1].resolve(results(documents));
		pending[0].resolve(results(clients));

		await expect(second).resolves.toEqual({ ok: true, results: results(documents) });
		// null means "superseded": the bar leaves its state untouched rather than repainting 'sm'.
		await expect(first).resolves.toBeNull();
	});

	it('discards an in-flight response after cancel (query fell below the minimum)', async () => {
		const { fetchImpl, pending } = deferredFetch();
		const client = new SearchClient(searchUrl, fetchImpl);
		const run = client.run('sm');
		client.cancel();
		pending[0].resolve(results(documents));
		await expect(run).resolves.toBeNull();
	});

	it('reports a non-OK response as a failure, so the bar can show a retry banner', async () => {
		const fetchImpl = vi.fn(async () => ({ ok: false, status: 500 }) as unknown as Response);
		const client = new SearchClient(searchUrl, fetchImpl as unknown as typeof globalThis.fetch);
		await expect(client.run('cole')).resolves.toEqual({ ok: false });
	});

	it('reports a network error as a failure rather than throwing at the caller', async () => {
		const fetchImpl = vi.fn(async () => {
			throw new Error('offline');
		});
		const client = new SearchClient(searchUrl, fetchImpl as unknown as typeof globalThis.fetch);
		await expect(client.run('cole')).resolves.toEqual({ ok: false });
	});

	it('swallows a superseded network error too', async () => {
		let fail: (e: Error) => void = () => {};
		const fetchImpl = vi.fn(
			() =>
				new Promise<Response>((_, reject) => {
					fail = reject;
				})
		);
		const client = new SearchClient(searchUrl, fetchImpl as unknown as typeof globalThis.fetch);
		const run = client.run('sm');
		client.cancel();
		fail(new Error('offline'));
		await expect(run).resolves.toBeNull();
	});
});

describe('Spotlight scopes', () => {
	it('advertises the shortcut for the user’s keyboard rather than printing both', () => {
		expect(searchShortcutLabel({ platform: 'MacIntel' })).toBe('⌘ K');
		expect(searchShortcutLabel({ userAgentData: { platform: 'macOS' } })).toBe('⌘ K');
		expect(searchShortcutLabel({ platform: 'Win32' })).toBe('Ctrl K');
		expect(searchShortcutLabel({ userAgent: 'X11; Linux x86_64' })).toBe('Ctrl K');
	});

	it('offers contextual filters only for categories present in mixed results', () => {
		expect(categoriesForGroups([documents, clients])).toEqual([
			{ type: 'client', label: 'Clients', command: 'clients' },
			{ type: 'document', label: 'Documents', command: 'documents' }
		]);
	});

	it('collapses message records and group threads into the one Messages filter', () => {
		expect(categoriesForGroups([{ type: 'message' }, { type: 'message_group' }])).toEqual([
			{ type: 'message', label: 'Messages', command: 'messages' }
		]);
	});

	it('uses stable application order rather than API result order for category filters', () => {
		expect(categoriesForGroups([documents, clients]).map((category) => category.type)).toEqual([
			'client',
			'document'
		]);
	});

	it('offers the guide once per signed-in user and version', () => {
		const values = new Map<string, string>();
		const storage = {
			getItem: (key: string) => values.get(key) ?? null,
			setItem: (key: string, value: string) => values.set(key, value)
		};

		expect(consumeSearchGuide(storage, 'one@example.test')).toBe(true);
		expect(consumeSearchGuide(storage, 'one@example.test')).toBe(false);
		expect(consumeSearchGuide(storage, 'two@example.test')).toBe(true);
		expect(searchGuideStorageKey('one@example.test')).not.toBe(
			searchGuideStorageKey('two@example.test')
		);
	});

	it('advertises the prefixes the palette actually answers to', () => {
		const advertised = PALETTE_SHORTCUTS.flatMap((shortcut) => shortcut.keys);
		expect(advertised).toContain(COMMAND_PREFIX);
		expect(advertised).toContain(SCOPE_PREFIX);
		// Every row explains itself, so the guide never shows a key with no stated purpose.
		expect(PALETTE_SHORTCUTS.every((shortcut) => shortcut.keys.length && shortcut.label)).toBe(
			true
		);
	});

	it('builds a validated scoped proxy URL without changing Everywhere', () => {
		expect(scopedSearchUrl('cole & co', null)).toBe('/search?q=cole%20%26%20co');
		expect(scopedSearchUrl('cole & co', 'client')).toBe('/search?q=cole%20%26%20co&scope=clients');
	});

	it('uses the shared firm-book catalogue', () => {
		expect(SEARCH_SCOPES).toBe(BOOK_SEARCH_SCOPES);
	});

	it('keeps commands and scoping on separate triggers', () => {
		expect(parsePaletteQuery('/create')).toEqual({ mode: 'commands', term: 'create' });
		expect(parsePaletteQuery('>rev')).toEqual({ mode: 'scopes', term: 'rev' });
		expect(parsePaletteQuery('  cole  ')).toEqual({ mode: 'search', term: 'cole' });
	});

	it('reads an inline scope token as a scoped search, and an unknown one as plain text', () => {
		expect(parsePaletteQuery('clients: ashworth')).toEqual({
			mode: 'search',
			term: 'ashworth',
			scope: 'client'
		});
		expect(parsePaletteQuery('journeys:')).toEqual({ mode: 'search', term: '', scope: 'case' });
		expect(parsePaletteQuery('everywhere: cole')).toEqual({
			mode: 'search',
			term: 'cole',
			scope: null
		});
		// No scope key at all, so the pinned scope stands and the colon stays part of the query.
		expect(parsePaletteQuery('ref: 12/A')).toEqual({ mode: 'search', term: 'ref: 12/A' });
	});

	it('defines the palette keyboard flow without hijacking keys while closed', () => {
		expect(
			paletteKeyboardAction({ key: 'k', metaKey: true }, { open: false, query: '', scope: null })
		).toEqual({ kind: 'toggle' });
		expect(paletteKeyboardAction({ key: '/' }, { open: false, query: '', scope: null })).toEqual({
			kind: 'open',
			prefix: '/'
		});
		expect(paletteKeyboardAction({ key: '>' }, { open: false, query: '', scope: null })).toEqual({
			kind: 'open',
			prefix: '>'
		});
		expect(
			paletteKeyboardAction({ key: '/', editable: true }, { open: false, query: '', scope: null })
		).toBeNull();
		expect(
			paletteKeyboardAction({ key: '>', editable: true }, { open: false, query: '', scope: null })
		).toBeNull();
		expect(
			paletteKeyboardAction({ key: 'Backspace' }, { open: true, query: '', scope: 'case' })
		).toEqual({ kind: 'clear-scope' });
		expect(
			paletteKeyboardAction({ key: 'Escape' }, { open: true, query: '', scope: 'case' })
		).toEqual({ kind: 'clear-scope' });
		expect(
			paletteKeyboardAction({ key: '4', altKey: true }, { open: false, query: '', scope: null })
		).toBeNull();
	});
});

describe('visible match highlighting', () => {
	it('preserves the original spelling while matching case-insensitively', () => {
		expect(highlightParts('Joe Biden', 'joe')).toEqual([
			{ text: 'Joe', matched: true },
			{ text: ' Biden', matched: false }
		]);
	});

	it('matches without accents but renders the accented source text', () => {
		expect(highlightParts('José Álvarez', 'jose alvarez')).toEqual([
			{ text: 'José', matched: true },
			{ text: ' ', matched: false },
			{ text: 'Álvarez', matched: true }
		]);
	});

	it('highlights each visible query word wherever it occurs', () => {
		expect(highlightParts('John Doe · Joint account', 'john account')).toEqual([
			{ text: 'John', matched: true },
			{ text: ' Doe · Joint ', matched: false },
			{ text: 'account', matched: true }
		]);
	});

	it('leaves fuzzy or hidden-field matches unmarked rather than inventing an explanation', () => {
		expect(highlightParts('John Wick GIA', 'joe')).toEqual([
			{ text: 'John Wick GIA', matched: false }
		]);
	});

	it('returns markup-looking text as data so the renderer never needs raw HTML', () => {
		expect(highlightParts('<script>Joe</script>', 'joe')).toEqual([
			{ text: '<script>', matched: false },
			{ text: 'Joe', matched: true },
			{ text: '</script>', matched: false }
		]);
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
		for (const q of ['co', 'col', 'cole', 'coleman']) bar.type(q);
		vi.advanceTimersByTime(DEBOUNCE_MS - 1);
		expect(bar.queried).toEqual([]); // still inside the pause: nothing has been asked for yet
		vi.advanceTimersByTime(1);
		expect(bar.queried).toEqual(['coleman']);
	});

	it('never queries below the minimum length, and clears the panel at once instead', () => {
		const bar = typist();
		bar.type('c');
		vi.advanceTimersByTime(DEBOUNCE_MS * 10);
		expect(bar.queried).toEqual([]);
		expect(bar.idled).toHaveLength(1); // synchronous — the panel must not keep stale hits
		expect(scheduleQuery('c', { query: () => {}, idle: () => {} })).toBeUndefined();
	});

	it('cancels in flight when the query is deleted back down to one character', () => {
		const bar = typist();
		bar.type('cole');
		bar.type('c'); // the cleanup runs first, so the pending request is dropped
		vi.advanceTimersByTime(DEBOUNCE_MS * 10);
		expect(bar.queried).toEqual([]);
		expect(bar.idled).toHaveLength(1);
	});

	it('queries again for a query typed, deleted and retyped identically', () => {
		// The effect re-runs on every change of `trimmed`, so this must not be deduplicated away.
		const bar = typist();
		bar.type('cole');
		vi.advanceTimersByTime(DEBOUNCE_MS);
		bar.type('');
		bar.type('cole');
		vi.advanceTimersByTime(DEBOUNCE_MS);
		expect(bar.queried).toEqual(['cole', 'cole']);
	});
});

describe('settle (what one resolved query does to the panel)', () => {
	it('paints fresh results and clears any previous failure', () => {
		expect(settle({ ok: true, results: results(documents) })).toEqual({
			results: results(documents),
			failed: false
		});
	});

	it('clears the results and raises the retry banner on failure', () => {
		expect(settle({ ok: false })).toEqual({ results: null, failed: true });
	});

	it('leaves the panel untouched when the query was superseded', () => {
		// SearchClient.run returns null for a stale ticket; treating that as "no results" would
		// blank the panel a newer query has already filled.
		expect(settle(null)).toBeNull();
	});
});

describe('contract with the API', () => {
	it('agrees with search.py on the minimum query length', () => {
		// apps/api/app/routers/search.py::MIN_QUERY_LENGTH — a shorter query returns an empty result,
		// so querying below this would be a guaranteed-empty round trip.
		expect(MIN_QUERY_LENGTH).toBe(2);
	});

	it('routes every shared firm-book entity key', () => {
		// Tasks remain an API concern for journeys, but are deliberately not a standalone search type.
		expect(Object.keys(ROUTES).sort()).toEqual([...BOOK_ROUTE_KEYS].sort());
	});
});

describe('the "Upload document" command (uploadDocumentHref)', () => {
	const holding =
		(...slugs: string[]) =>
		(slug: string) =>
			slugs.includes(slug) ||
			(slug.endsWith(':read') && slugs.includes(slug.replace(':read', ':write')));

	it('is withheld without documents:write', () => {
		expect(uploadDocumentHref('/clients/[uid]', 'con_1', holding('documents:read'))).toBeNull();
		expect(uploadDocumentHref('/documents', undefined, holding('documents:show'))).toBeNull();
	});

	it("opens the client's own Documents tab on any page of a client's record", () => {
		// No documents:show needed: the tab is not the firm-wide page.
		const writer = holding('documents:write');
		for (const route of ['/clients/[uid]', '/clients/[uid]/documents', '/clients/[uid]/details']) {
			expect(uploadDocumentHref(route, 'con_1', writer)).toBe('/clients/con_1/documents?upload=1');
		}
		// A route group in the id does not hide the record.
		expect(uploadDocumentHref('/(app)/clients/[uid]', 'con_1', writer)).toBe(
			'/clients/con_1/documents?upload=1'
		);
	});

	it('opens the Documents page elsewhere, and only with documents:show', () => {
		expect(uploadDocumentHref('/', undefined, holding('documents:write', 'documents:show'))).toBe(
			'/documents?upload=1'
		);
		expect(uploadDocumentHref('/', undefined, holding('documents:write'))).toBeNull();
		// A uid param on some OTHER record is not a client.
		expect(uploadDocumentHref('/journeys/[uid]', 'jrn_1', holding('documents:write'))).toBeNull();
		// `/clients` (the listing) is not a record either.
		expect(uploadDocumentHref('/clients', undefined, holding('documents:write'))).toBeNull();
	});
});
