/**
 * The firm Events page loader (`GET /firm/audit/actions` waterfalled into the platform portal).
 *
 * What is app-side — and what these tests pin — is the loader: the `activity:read` gate (a 403 page,
 * never a silent empty log that reads as "nobody did anything") and the honest failure modes (an API
 * 403 or outage surfaces a banner, not fake-empty data). The API scope and the AML exclusion are the
 * API's contract (test_firm_audit_actions.py); nothing here could widen them — the loader sends no
 * tenant parameter at all.
 *
 * FILTERS ARE APPLIED BY THE API, so a filter covers the whole trail rather than the fifty rows that
 * happen to be loaded — but they are applied by the PAGE, in place, not by this loader. The loader's
 * job is ENTRY, and both halves of it are pinned: the unfiltered first page, and `?facets=true` — the
 * complete option vocabulary for the firm's scope, read once, so choosing one filter can never shrink
 * another's choices. The absence is pinned too: the loader ignores filter params in the URL, because
 * reading filters the page never writes is the failure mode this design removed (arrive on an old
 * filtered link, change a filter, and a reload would jump back to the stale ones still in the bar).
 *
 * The last block is a static source scan over BOTH platform Events mounts (this page and the client
 * record's), because what regresses there is invisible in the output: dropping the filter fetch back
 * to the loaded rows still WORKS, it just quietly answers a smaller question than the one asked — and
 * re-adding a URL sync still works too, it just brings back the stale-URL trap.
 */
import { globSync, readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { load } from './+page.server';

type LoadEvent = Parameters<typeof load>[0];

const FACETS = { event_types: ['client.created'], actors: ['Amy Blake'] };

const PAGE = { items: [{ id: 41 }], has_more: true, facets: FACETS };

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), {
		status,
		headers: { 'Content-Type': 'application/json' }
	});

function event(over: { search?: string; permissions?: string[]; fetch?: typeof globalThis.fetch }) {
	const fetch = over.fetch ?? vi.fn(async () => json(PAGE));
	return {
		url: new URL(`http://portal.test/activity${over.search ?? ''}`),
		locals: {
			user: { id: 'user_1' },
			accessToken: 'token',
			permissions: over.permissions ?? ['activity:read'],
			role: 'adviser'
		},
		fetch
	} as unknown as LoadEvent & { fetch: ReturnType<typeof vi.fn> };
}

type Loaded = Exclude<Awaited<ReturnType<typeof load>>, void>;

/** The loader's answer with the streamed feed resolved — what the page sees once the rows land. */
const run = async (e: LoadEvent) => {
	const data = (await load(e)) as Loaded;
	return { ...(await data.feed), canConfigure: data.canConfigure };
};

describe('the /activity loader streams the feed', () => {
	it('returns before the API answers, with the feed as a promise', async () => {
		// The gates are decided and the response starts; the rows land behind the frame. A loader
		// that awaited the API here would put the whole round trip back on the time to first byte.
		const e = event({ fetch: vi.fn(() => new Promise<Response>(() => {})) });
		const data = (await load(e)) as Loaded;
		expect(data.feed).toBeInstanceOf(Promise);
		expect(data.canConfigure).toBe(false);
	});
});

describe('the /activity loader gate', () => {
	it('bounces an unauthenticated caller to /login', async () => {
		const e = event({});
		(e as { locals: { user: unknown } }).locals.user = null;
		await expect(load(e)).rejects.toMatchObject({ status: 303, location: '/login' });
	});

	it('403s without activity:read — and never calls the API', async () => {
		const e = event({ permissions: ['members:read'] });
		await expect(load(e)).rejects.toMatchObject({ status: 403 });
		expect(e.fetch).not.toHaveBeenCalled();
	});

	it('lets system through (there is no activity:write — the trail is read-only by design)', async () => {
		expect((await run(event({ permissions: ['system'] }))).events).toHaveLength(1);
	});
});

describe('the /activity loader ignores filter params in the URL', () => {
	it('asks for the newest page and the filter vocabulary, whatever the URL carries', async () => {
		// The whole filter vocabulary in the address bar, and none of it may reach the API from HERE:
		// filters are applied by the API, but through the page's own in-place fetch. A loader that
		// forwarded these would be reading filters the page never writes — arrive on an old filtered
		// link, move a dropdown, and a reload jumps back to the stale ones still in the bar.
		const e = event({
			search:
				'?event_type=action.firm-&actor=alice&method=PATCH&since=2026-07-01&until=2026-07-05&proxied=proxied&limit=120'
		});
		const result = await run(e);

		const called = new URL(String(e.fetch.mock.calls[0][0]));
		expect(called.pathname).toBe('/firm/audit/actions');
		// The page size and the facets flag. Not the filters, and not a URL-supplied limit — the list
		// grows in place via /activity/more, so the loader always reads the FIRST page at its own size.
		expect([...called.searchParams.keys()].sort()).toEqual(['facets', 'limit']);
		expect(called.searchParams.get('limit')).toBe(String(PAGE_SIZE));
		// There is no tenant parameter to send either — the API hard-codes the firm from the session.
		expect(called.searchParams.get('tenant')).toBeNull();

		expect(result.events).toEqual(PAGE.items);
		expect(result.hasMore).toBe(true);
	});

	it('returns no filters for the page to re-apply', async () => {
		// Nothing carries entry filters, so the component starts unfiltered and owns the state from
		// there. A `filters` key here would be a channel back to the URL by another name.
		const result = await run(event({ search: '?actor=alice' }));
		expect('filters' in result).toBe(false);
	});
});

describe('the /activity loader reads the filter vocabulary', () => {
	it('asks for it (`facets=true`) and passes it through for the toolbar', async () => {
		// The option lists come from the API, over the firm's whole recent scope and independently of
		// the filters in force — so applying one filter can never withdraw another's choices, which is
		// what deriving them from the loaded rows could not promise.
		const e = event({});
		const result = await run(e);
		expect(new URL(String(e.fetch.mock.calls[0][0])).searchParams.get('facets')).toBe('true');
		expect(result.facets).toEqual(FACETS);
	});

	it('degrades to no facets when the API sends none (an older API, or none asked for)', async () => {
		// A missing vocabulary costs the dropdowns their completeness and NOTHING else: the toolbar
		// falls back to the loaded rows' own options. It must never be an error page.
		const absent = await run(
			event({ fetch: vi.fn(async () => json({ items: [], has_more: false })) })
		);
		expect(absent.facets).toBeNull();
		expect(absent.apiError).toBeNull();

		const nulled = await run(
			event({ fetch: vi.fn(async () => json({ items: [], has_more: false, facets: null })) })
		);
		expect(nulled.facets).toBeNull();
		expect(nulled.apiError).toBeNull();
	});
});

describe('the /activity loader failure modes', () => {
	it('surfaces an API 403 as a banner rather than an empty log', async () => {
		const e = event({ fetch: vi.fn(async () => json({ detail: 'nope' }, 403)) });
		const result = await run(e);
		expect(result.events).toEqual([]);
		expect(result.apiError).toContain('Ask a firm administrator');
		// A failed read costs the filter bar its vocabulary too, and says so rather than half-filling it.
		expect(result.facets).toBeNull();
	});

	it('surfaces an unreachable API rather than pretending the trail is empty', async () => {
		const e = event({
			fetch: vi.fn(async () => {
				throw new Error('ECONNREFUSED');
			})
		});
		const result = await run(e);
		expect(result.events).toEqual([]);
		expect(result.apiError).toContain('Could not load the event log');
		expect(result.facets).toBeNull();
	});
});

describe('every platform Events mount applies its filters at the API, in place, and never in the URL', () => {
	// Normalise to forward slashes: globSync returns backslash paths on Windows, which the
	// `/`-anchored checks below would miss. `*` rather than the literal `[id]` — brackets are glob
	// character classes, so the literal directory name matches nothing (the count guard catches it).
	const norm = (paths: string[]): string[] => paths.map((p) => p.replaceAll('\\', '/'));
	const mounts = norm([
		...globSync('src/routes/activity/+page.svelte'),
		...globSync('src/routes/clients/*/activity/+page.svelte')
	]);
	// Scan the CODE, with every kind of comment stripped — HTML, block AND line. Each rule below is
	// explained in a comment in the file it polices, and those comments NAME the thing being banned
	// ("no shallow URL write", "never `goto` for a filter") — so a file-wide scan would match its own
	// rationale and fail a compliant mount. That false positive is a recorded hazard in this repo
	// (`switch-columns.test.ts` and `field-shell.test.ts` both hit it on a comment quoting the old
	// string) and the answer is the same: narrow what is read. The BLOCK strip matters as much as the
	// line one now, because the reasoning for these decisions lives in `/** … */` docs on the functions
	// that implement them. No `http://` appears in these files, so the line-comment strip is safe.
	const read = (f: string): string =>
		readFileSync(f, 'utf8')
			.replace(/<!--[\s\S]*?-->/g, '')
			.replace(/\/\*[\s\S]*?\*\//g, '')
			.replace(/\/\/.*$/gm, '');

	it('scans real files (a guard that matches nothing is not a guard)', () => {
		expect(mounts.length).toBe(2);
		expect(mounts.some((f) => f.endsWith('src/routes/activity/+page.svelte'))).toBe(true);
		expect(mounts.some((f) => f.includes('/clients/'))).toBe(true);
	});

	it('writes NOTHING to the URL — no shallow sync, no filter navigation', () => {
		// Filters are the component's, applied by the API, and never in the address bar. That is
		// invisible in the output: re-adding a shallow URL sync still works, it just brings back the
		// stale-URL trap (arrive filtered, change a filter, reload jumps back to the old one).
		for (const f of mounts) {
			const src = read(f);
			expect(src).not.toContain('replaceState');
			expect(src).not.toContain('recordFilters');
			expect(src).not.toContain('onFiltersChange');
		}
	});

	it('applies filters AT THE API, in place — so a filter covers the whole trail', () => {
		// The filter fetch is the point of the design: narrowing the loaded rows instead would answer
		// "nothing in the newest page" as though it were "nothing ever", which on a compliance record is
		// the wrong answer told confidently.
		for (const f of mounts) {
			const src = read(f);
			expect(src).toContain('onFilterApply');
			// Page ONE at those filters: the query builder is called with no cursor.
			expect(src).toMatch(
				/const applyFilters = \(filters: ActionLogFilters\) => fetchPage\(filters\)/
			);
		}
	});

	it('offers the toolbar the API’s own vocabulary, so one filter cannot shrink another’s options', () => {
		// `data.facets` or `feed.facets`, never a hardcoded/narrowed set -- the per-client page
		// streams its load behind a `feed` snippet (see `Streamed`), the firm-wide one does not,
		// and this check is about where the value comes from, not which local name holds it.
		for (const f of mounts) {
			expect(read(f)).toMatch(/facets=\{(?:data|feed)\.facets \?\? undefined\}/);
		}
	});

	it('builds its query with the SHARED builder for THIS hop, restating nothing by hand', () => {
		for (const f of mounts) {
			const src = read(f);
			// `actionLogBrowserQuery`, not `actionLogQuery`: the next reader of this query is the page's
			// own proxy, which re-reads it with the TOOLBAR's reader — and that reader takes the API's
			// `proxied=true` for "no source filter", quietly showing every source under a control that
			// says otherwise. Each builder names the hop it serves, so neither call site has to remember.
			expect(src).toContain('actionLogBrowserQuery(filters, { before })');
			// And no hand-rolled restatement survives here. It did in all four mounts, and a mapping
			// restated per page is only ever checked by a scan like this one; in the shared builder the
			// hop is EXECUTED by `action-log-query.test.ts`.
			expect(src).not.toContain("query.set('proxied'");
		}
	});

	it('pages AT THE APPLIED FILTERS, not the loader’s', () => {
		for (const f of mounts) {
			const src = read(f);
			// Two arguments, cursor AND filters — the component's filters, which are the applied ones.
			// Paging from the loader's idea of them would continue a query nobody is looking at and
			// append rows from the wrong set, still answering 200.
			expect(src).toMatch(
				/const fetchNextPage = \(before: number, filters: ActionLogFilters\) => fetchPage\(filters, before\)/
			);
		}
	});

	it('keeps exactly one navigation: the deliberate reload behind Refresh and retry', () => {
		// Refresh re-runs the load on purpose — the unfiltered first page and a fresh vocabulary, which
		// is the entry state. It needs no URL bookkeeping, so it is a plain `invalidateAll()` rather
		// than a `goto`, and the filters survive it because they live in the component.
		for (const f of mounts) {
			const src = read(f);
			expect(src).toContain('const reload = () => invalidateAll();');
			expect(src.match(/goto\(/g) ?? []).toHaveLength(0);
		}
	});

	it('fetches through the same-origin proxy, which owns the session token and the failure contract', () => {
		// A non-2xx must reach the component as `null` (rows kept, retry offered) — never as an empty
		// page, which would read as the end of the trail.
		for (const f of mounts) {
			const src = read(f);
			expect(src).toContain('/more?');
			expect(src).toContain('if (!res.ok) return null;');
		}
	});
});

describe('the link to the event registry', () => {
	it('is offered only to a caller who may read the registry', async () => {
		// The registry has its OWN permission and no nav item — this button is how it is reached,
		// so gating it on THIS page's `activity:read` would dangle a 403 at everyone else.
		const withIt = await run(event({ permissions: ['activity:read', 'notifications:read'] }));
		expect(withIt.canConfigure).toBe(true);

		const without = await run(event({ permissions: ['activity:read'] }));
		expect(without.canConfigure).toBe(false);
	});
});
