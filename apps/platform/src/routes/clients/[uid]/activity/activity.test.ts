/**
 * The client-record Logs loader (`/clients/[uid]/activity`) — this client's slice of the firm's
 * action-event stream. Pins the cross-client boundary: the `contact` filter is FORCED to the
 * section's contact id (from `parent()`, never a URL param), so the slice can neither miss nor leak
 * across clients — the same guard the admin `/tenants/[uid]/events` loader pins for its tenant
 * scope. Also pins the `activity:read` gate (a 403, never a silent empty log) and the honest failure
 * modes.
 *
 * Filters are applied by the API so they cover the client's whole trail, but by the PAGE, in place —
 * the loader's job is ENTRY: this client's unfiltered newest page, plus `?facets=true` for the
 * toolbar's option lists. It still reads no filter from the URL and writes none there (the source
 * scan over both platform mounts lives in `routes/activity/activity.test.ts`).
 */
import { describe, expect, it, vi } from 'vitest';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { load } from './+page.server';

type LoadEvent = Parameters<typeof load>[0];

const FACETS = { event_types: ['client.updated'], actors: ['Amy Blake'] };

const PAGE = { items: [{ id: 7 }], has_more: true, facets: FACETS };

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

function event(over: {
	search?: string;
	canReadActivity?: boolean;
	user?: unknown;
	fetch?: typeof globalThis.fetch;
}) {
	const fetch = over.fetch ?? vi.fn(async () => json(PAGE));
	const parent = vi.fn(async () => ({
		canReadActivity: over.canReadActivity ?? true,
		clientName: 'Margaret Cole'
	}));
	return {
		params: { uid: 'con_9b2e' },
		url: new URL(`http://firm.test/clients/con_9b2e/activity${over.search ?? ''}`),
		locals: {
			user: over.user === undefined ? { id: 'staff_1' } : over.user,
			accessToken: 'tok'
		},
		fetch,
		parent
	} as unknown as LoadEvent & { fetch: ReturnType<typeof vi.fn> };
}

const run = async (e: LoadEvent) => {
	const loaded = (await load(e)) as Exclude<Awaited<ReturnType<typeof load>>, void>;
	return { ...loaded, ...(await loaded.feed) };
};

describe('the client /activity loader gate', () => {
	it('bounces an unauthenticated caller to /login', async () => {
		const e = event({ user: null });
		await expect(load(e)).rejects.toMatchObject({ status: 303, location: '/login' });
	});

	it('403s without activity:read — and never calls the API', async () => {
		const e = event({ canReadActivity: false });
		await expect(load(e)).rejects.toMatchObject({ status: 403 });
		expect(e.fetch).not.toHaveBeenCalled();
	});
});

describe('the client /activity loader scoping', () => {
	it('sends the section contact, the page size and the facets flag — and ignores every filter in the URL', async () => {
		// `contact` is the SCOPE and survives; the filter vocabulary in the URL must not, because the
		// page applies filters itself, in place, and reading what it never writes is the stale-URL trap.
		const e = event({
			search:
				'?event_type=action.firm-&actor=alice&method=PATCH&until=2026-07-05&proxied=proxied&limit=120'
		});
		await run(e);

		const called = new URL(String(e.fetch.mock.calls[0][0]));
		expect([...called.searchParams.keys()].sort()).toEqual(['contact', 'facets', 'limit']);
		// The client's UID, which is what `?contact=` resolves. It was the serial, which matches no
		// uid — and an unresolvable filter does not narrow, it drops, so page one answered with the
		// whole firm's stream while Load more (which always sent the uid) answered with this client's.
		expect(called.searchParams.get('contact')).toBe('con_9b2e');
		// The loader always reads the FIRST page at its own size; the list grows in place.
		expect(called.searchParams.get('limit')).toBe(String(PAGE_SIZE));
	});

	it('cannot be widened by a contact param in the URL — the route segment wins', async () => {
		const e = event({ search: '?contact=con_other' });
		await run(e);
		const called = new URL(String(e.fetch.mock.calls[0][0]));
		// The forced contact is the route's own uid; a URL contact param is never read, so it can't
		// widen (or narrow to another client) — the cross-client leak guard.
		expect(called.searchParams.get('contact')).toBe('con_9b2e');
	});
});

describe('the client /activity loader reads the filter vocabulary', () => {
	it('asks for it (`facets=true`) and passes it through for the toolbar', async () => {
		// Independent of the filters in force, so applying one can never withdraw another's choices.
		// Note the scope the API gives back: facets are computed over what the caller may READ, which on
		// this endpoint is the whole firm — `contact` narrows the rows, not the vocabulary — so an option
		// here can match no events on THIS client. A complete list with a dead option beats one that
		// silently withdraws the value you wanted to switch to.
		const e = event({});
		const result = await run(e);
		expect(new URL(String(e.fetch.mock.calls[0][0])).searchParams.get('facets')).toBe('true');
		expect(result.facets).toEqual(FACETS);
	});

	it('degrades to no facets when the API sends none (an older API, or none asked for)', async () => {
		const absent = await run(
			event({ fetch: vi.fn(async () => json({ items: [], has_more: false })) })
		);
		expect(absent.facets).toBeNull();
		// A missing vocabulary is a shorter dropdown, never an error page.
		expect(absent.apiError).toBeNull();

		const nulled = await run(
			event({ fetch: vi.fn(async () => json({ items: [], has_more: false, facets: null })) })
		);
		expect(nulled.facets).toBeNull();
		expect(nulled.apiError).toBeNull();
	});
});

describe('the client /activity loader failure modes', () => {
	it('surfaces an API 403 as a banner rather than an empty log', async () => {
		const e = event({ fetch: vi.fn(async () => json({ detail: 'nope' }, 403)) });
		const result = await run(e);
		expect(result.events).toEqual([]);
		expect(result.apiError).toContain('Ask a firm administrator');
		// A failed read costs the filter bar its vocabulary and nothing else.
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
		expect(result.apiError).toContain('Could not load this client’s events');
		expect(result.facets).toBeNull();
	});
});
