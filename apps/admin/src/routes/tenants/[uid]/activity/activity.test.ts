/**
 * The per-tenant Events loader (`/tenants/[uid]/events`) — a firm's slice of the action-event
 * stream. Pins: the `audit:read` gate (a 403, never a silent empty log that reads as "nobody did
 * anything"), that the firm is FORCED to the section's uid at the API (`?tenant=<uid>`) and cannot
 * be widened by a URL param, the `?facets=true` option vocabulary, and the honest failure modes. The
 * API scope + the deny list are the API's contract; there is no firm FILTER here (the firm is the URL).
 *
 * Filters are applied by the API so they cover this firm's whole slice, but by the PAGE, in place —
 * the loader's job is ENTRY, and it reads no filter from the URL and writes none there (the source
 * scan over both admin mounts lives in `routes/activity/events.test.ts`).
 */
import { describe, expect, it, vi } from 'vitest';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { load } from './+page.server';

type LoadEvent = Parameters<typeof load>[0];

const FACETS = { event_types: ['client.created'], actors: ['Ops Rachel'] };

const PAGE = { items: [{ id: 7 }], has_more: true, facets: FACETS };

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

function event(over: { search?: string; permissions?: string[]; fetch?: typeof globalThis.fetch }) {
	const fetch = over.fetch ?? vi.fn(async () => json(PAGE));
	return {
		params: { uid: 'ten_7' },
		url: new URL(`http://ops.test/tenants/ten_7/events${over.search ?? ''}`),
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

describe('the per-tenant /events loader gate', () => {
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

describe('the per-tenant /events loader scoping', () => {
	it('sends the section firm, the page size and the facets flag — and ignores every filter in the URL', async () => {
		// `tenant` is the SCOPE and survives; the filter vocabulary in the URL must not, because the page
		// applies filters itself, in place, and reading what it never writes is the stale-URL trap.
		const e = event({
			search:
				'?event_type=action.firm-&actor=alice&method=PATCH&until=2026-07-05&proxied=proxied&limit=120'
		});
		const result = await run(e);

		const called = new URL(String(e.fetch.mock.calls[0][0]));
		expect(called.pathname).toBe('/admin/audit/actions');
		expect([...called.searchParams.keys()].sort()).toEqual(['facets', 'limit', 'tenant']);
		expect(called.searchParams.get('tenant')).toBe('ten_7');
		// The loader always reads the FIRST page at its own size; the list grows in place.
		expect(called.searchParams.get('limit')).toBe(String(PAGE_SIZE));

		expect(result.events).toEqual(PAGE.items);
		expect(result.hasMore).toBe(true);
		expect(result.tenantUid).toBe('ten_7');
		// Nothing carries entry filters — the component starts unfiltered and owns the state from there.
		expect('filters' in result).toBe(false);
	});

	it('cannot be widened by a tenant param in the URL', async () => {
		const e = event({ search: '?tenant=ten_other' });
		await run(e);
		const called = new URL(String(e.fetch.mock.calls[0][0]));
		// Only the section uid is ever sent — a URL tenant param is ignored, not honoured.
		expect(called.searchParams.get('tenant')).toBe('ten_7');
	});
});

describe('the per-tenant /events loader reads the filter vocabulary', () => {
	it('asks for it (`facets=true`), scoped to the section firm, and passes it through', async () => {
		// The API computes facets over the SAME `?tenant=` predicate as the rows, so these are this
		// firm's event types and actors rather than the estate's — and independently of the filters in
		// force, so choosing one can never withdraw another's choices.
		const e = event({});
		const result = await run(e);
		const called = new URL(String(e.fetch.mock.calls[0][0]));
		expect(called.searchParams.get('facets')).toBe('true');
		expect(called.searchParams.get('tenant')).toBe('ten_7');
		expect(result.facets).toEqual(FACETS);
	});

	it('degrades to no facets when the API sends none (an older API, or none asked for)', async () => {
		// A missing vocabulary costs the dropdowns their completeness and NOTHING else: the toolbar falls
		// back to the loaded rows' own options. It must never be an error page.
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

describe('the per-tenant /events loader failure modes', () => {
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
