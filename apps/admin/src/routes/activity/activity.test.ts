/**
 * The estate-wide Events surface (`/events`) — the loader, and the POSTURE of both admin mounts.
 *
 * The loader half pins what an ENTRY into the page does: the `audit:read` gate (a 403, never a silent
 * empty log that reads as "nobody did anything"), the firm lookup that names the firms in the toolbar's
 * dropdown, the `?facets=true` option vocabulary, and the honest failure modes.
 *
 * FILTERS ARE APPLIED BY THE API — the firm filter included — so a filter covers the whole stream
 * rather than the fifty rows that happen to be loaded. They are applied by the PAGE though, in place,
 * so the loader has no filter contract: it asks the unfiltered question. What is pinned is that
 * ABSENCE, because reading filters the page never writes is the trap this design removed.
 *
 * The posture half is a static source scan over both admin mounts, because what regresses there is
 * invisible in the output: dropping the filter fetch back to the loaded rows still WORKS, it just
 * answers a smaller question than the one asked — and re-adding a URL sync still works too, it just
 * brings back the stale-URL trap.
 */
import { globSync, readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { load } from './+page.server';

type LoadEvent = Parameters<typeof load>[0];

const TENANTS = [{ uid: 'ten_7', name: 'Caldwell Wealth' }];

const FACETS = { event_types: ['action.create-tenant'], actors: ['Ops Rachel'] };

const PAGE = { items: [{ id: 91 }], has_more: true, tenants: TENANTS, facets: FACETS };

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

function event(over: { search?: string; permissions?: string[]; fetch?: typeof globalThis.fetch }) {
	const fetch = over.fetch ?? vi.fn(async () => json(PAGE));
	return {
		url: new URL(`http://ops.test/events${over.search ?? ''}`),
		locals: {
			user: { id: 'ops_1' },
			accessToken: 'tok',
			permissions: over.permissions ?? ['audit:read'],
			role: null
		},
		fetch
	} as unknown as LoadEvent & { fetch: ReturnType<typeof vi.fn> };
}

const run = async (e: LoadEvent) =>
	(await load(e)) as Exclude<Awaited<ReturnType<typeof load>>, void>;

describe('the /events loader gate', () => {
	it('bounces an unauthenticated caller to /login', async () => {
		const e = event({});
		(e as { locals: { user: unknown } }).locals.user = null;
		await expect(load(e)).rejects.toMatchObject({ status: 303, location: '/login' });
	});

	it('403s without audit:read — and never calls the API', async () => {
		const e = event({ permissions: ['tenants:read'] });
		await expect(load(e)).rejects.toMatchObject({ status: 403 });
		expect(e.fetch).not.toHaveBeenCalled();
	});
});

describe('the /events loader ignores filter params in the URL', () => {
	it('asks for the newest page, the firm lookup and the vocabulary — nothing the URL asked for', async () => {
		// Every filter in the address bar, none of it forwarded from HERE: the page applies filters
		// itself, in place. The firm is a filter on this surface too, so `tenant` is not sent either.
		const e = event({
			search:
				'?tenant=ten_7&event_type=action.firm-&actor=ops&method=PATCH&since=2026-07-01&until=2026-07-05&proxied=direct&limit=200'
		});
		const result = await run(e);

		const called = new URL(String(e.fetch.mock.calls[0][0]));
		expect(called.pathname).toBe('/admin/audit/actions');
		expect([...called.searchParams.keys()].sort()).toEqual(['facets', 'limit']);
		expect(called.searchParams.get('limit')).toBe(String(PAGE_SIZE));

		expect(result.events).toEqual(PAGE.items);
		expect(result.hasMore).toBe(true);
		// The firm lookup still arrives — it names the firms in the toolbar's dropdown.
		expect(result.tenants).toEqual(TENANTS);
	});

	it('returns no filters for the page to re-apply', async () => {
		const result = await run(event({ search: '?actor=ops' }));
		expect('filters' in result).toBe(false);
	});
});

describe('the /events loader reads the filter vocabulary', () => {
	it('asks for it (`facets=true`) and passes it through for the toolbar', async () => {
		const e = event({});
		const result = await run(e);
		expect(new URL(String(e.fetch.mock.calls[0][0])).searchParams.get('facets')).toBe('true');
		expect(result.facets).toEqual(FACETS);
	});

	it('asks for the ESTATE-WIDE vocabulary — no firm scope, even though the firm is a filter here', async () => {
		// Deliberate: the firm travels in the toolbar's filters, and re-reading the vocabulary per filter
		// is exactly how a bar comes to eat its own options (pick an actor, and the event-type list
		// offers only what that actor did, with no way back to a value you can no longer see). One scan,
		// on entry, over the whole readable scope — which is why the loader sends no `tenant`.
		const e = event({});
		await run(e);
		const called = new URL(String(e.fetch.mock.calls[0][0]));
		expect(called.searchParams.get('facets')).toBe('true');
		expect(called.searchParams.has('tenant')).toBe(false);
	});

	it('degrades to no facets when the API sends none (an older API, or none asked for)', async () => {
		// A missing vocabulary costs the dropdowns their completeness and NOTHING else: the toolbar falls
		// back to the loaded rows' own options. It must never be an error page.
		const absent = await run(
			event({ fetch: vi.fn(async () => json({ items: [], has_more: false, tenants: [] })) })
		);
		expect(absent.facets).toBeNull();
		expect(absent.apiError).toBeNull();

		const nulled = await run(
			event({
				fetch: vi.fn(async () => json({ items: [], has_more: false, tenants: [], facets: null }))
			})
		);
		expect(nulled.facets).toBeNull();
		expect(nulled.apiError).toBeNull();
	});
});

describe('the /events loader failure modes', () => {
	it('surfaces an API 403 as a banner rather than an empty log', async () => {
		const e = event({ fetch: vi.fn(async () => json({ detail: 'nope' }, 403)) });
		const result = await run(e);
		expect(result.events).toEqual([]);
		expect(result.apiError).toContain('audit:read');
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
		expect(result.apiError).toContain('service is unavailable');
		expect(result.facets).toBeNull();
	});
});

describe('every admin Events mount applies its filters at the API, in place, and never in the URL', () => {
	// Normalise to forward slashes: globSync returns backslash paths on Windows, which the
	// `/`-anchored checks below would miss. `*` rather than the literal `[uid]` — brackets are glob
	// character classes, so the literal directory name matches nothing (the count guard catches it).
	const norm = (paths: string[]): string[] => paths.map((p) => p.replaceAll('\\', '/'));
	const mounts = norm([
		...globSync('src/routes/activity/+page.svelte'),
		...globSync('src/routes/tenants/*/activity/+page.svelte')
	]);
	// Scan the CODE, with every kind of comment stripped — HTML, block AND line. Each rule below is
	// explained in a comment in the file it polices, which names the very thing being banned ("no
	// shallow URL write", "never `goto` for a filter") — so a file-wide scan matches its own rationale
	// and fails a compliant mount. That false positive is a recorded hazard in this repo
	// (`switch-columns.test.ts` and `field-shell.test.ts` both hit it on a comment quoting the old
	// string), and the answer there was the same: narrow what is read. The BLOCK strip matters as much
	// as the line one now, because the reasoning for these decisions lives in `/** … */` docs on the
	// functions that implement them. No `http://` appears in these files, so the strip is safe.
	const read = (f: string): string =>
		readFileSync(f, 'utf8')
			.replace(/<!--[\s\S]*?-->/g, '')
			.replace(/\/\*[\s\S]*?\*\//g, '')
			.replace(/\/\/.*$/gm, '');

	it('scans real files (a guard that matches nothing is not a guard)', () => {
		expect(mounts.length).toBe(2);
		expect(mounts.some((f) => f.endsWith('src/routes/activity/+page.svelte'))).toBe(true);
		expect(mounts.some((f) => f.includes('/tenants/'))).toBe(true);
	});

	it('writes NOTHING to the URL — no shallow sync, no filter navigation', () => {
		// Filters are the component's, applied by the API, and never in the address bar. Re-adding a
		// shallow URL sync still works — it just brings back the stale-URL trap (arrive filtered, change
		// a filter, reload jumps back to the old one).
		for (const f of mounts) {
			const src = read(f);
			expect(src).not.toContain('replaceState');
			expect(src).not.toContain('recordFilters');
			expect(src).not.toContain('onFiltersChange');
		}
	});

	it('applies filters AT THE API, in place — so a filter covers the whole stream', () => {
		for (const f of mounts) {
			const src = read(f);
			expect(src).toContain('onFilterApply');
			// Page ONE at those filters: the query builder is called with no cursor.
			expect(src).toMatch(
				/const applyFilters = \(filters: ActionLogFilters\) => fetchPage\(filters\)/
			);
		}
	});

	it('offers the toolbar the API’s own vocabulary, read once and never re-read per filter', () => {
		// This is what retired `appliedTenant`/`seenFilters`: they existed only because applying the FIRM
		// in place made a re-read describe a different firm's stream, which would have put the previous
		// firm's people in this firm's actor dropdown. A vocabulary that never moves cannot go stale.
		for (const f of mounts) {
			const src = read(f);
			expect(src).toContain('facets={data.facets ?? undefined}');
			expect(src).not.toContain('appliedTenant');
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
			// Paging from the loader's idea of them would continue a query nobody is looking at.
			expect(src).toMatch(
				/const fetchNextPage = \(before: number, filters: ActionLogFilters\) => fetchPage\(filters, before\)/
			);
		}
	});

	it('keeps exactly one navigation: the deliberate reload behind Refresh and retry', () => {
		// Refresh re-runs the load on purpose — the unfiltered first page and a fresh vocabulary, the
		// entry state — and needs no URL bookkeeping, so it is a plain `invalidateAll()`. The filters
		// survive it because they live in the component.
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
