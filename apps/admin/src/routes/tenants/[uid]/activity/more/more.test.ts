/**
 * One firm's Events "Load more" proxy (`/tenants/[uid]/activity/more`).
 *
 * Two things are pinned. The FAILURE contract: a proxy failure must surface as a NON-2xx, because
 * the page's `loadMore` maps `!res.ok` to `null` and the EventStream keeps its "Load more" button on
 * null — a swallowed 200 `{events:[],hasMore:false}` reads as a real last page ("End of events") and
 * silently truncates the firm's trail at whatever page the outage hit. And the SCOPE: `tenant` is the
 * section's uid, set after the shared query builder, so a `?tenant=` in the query the browser sent
 * cannot re-point this slice at another firm.
 */
import { describe, expect, it, vi } from 'vitest';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { GET } from './+server';

type Ev = Parameters<typeof GET>[0];

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

/** A `fetch` double that records the URL it was asked for, so the forwarded query can be read. */
function recorder(body: unknown = { items: [{ id: 41 }], has_more: true }) {
	const urls: string[] = [];
	const fetch = vi.fn(async (url: string | URL) => {
		urls.push(String(url));
		return json(body);
	});
	return { urls, fetch: fetch as unknown as typeof globalThis.fetch };
}

function event(
	search = '?before=100',
	over: { user?: unknown; uid?: string; fetch?: typeof globalThis.fetch } = {}
) {
	return {
		params: { uid: over.uid ?? 'ten_1743d0' },
		url: new URL(`http://admin.test/tenants/ten_1743d0/activity/more${search}`),
		locals: {
			user: over.user === undefined ? { id: 'ops_1' } : over.user,
			accessToken: 'token'
		},
		fetch: over.fetch ?? recorder().fetch
	} as unknown as Ev;
}

/** The query the API was actually asked for. */
async function sent(search: string, uid?: string): Promise<URLSearchParams> {
	const rec = recorder();
	await GET(event(search, { fetch: rec.fetch, uid }));
	return new URL(rec.urls[0]).searchParams;
}

describe('the per-firm /activity/more proxy failure contract', () => {
	it('maps a successful API page to {events,hasMore} at 200', async () => {
		const res = await GET(event());
		expect(res.status).toBe(200);
		expect(await res.json()).toEqual({ events: [{ id: 41 }], hasMore: true });
	});

	it('returns a NON-2xx when the API errors (so loadMore returns null, feed keeps the button)', async () => {
		const res = await GET(event('?before=100', { fetch: vi.fn(async () => json({}, 500)) }));
		expect(res.ok).toBe(false); // NOT a swallowed 200 → no false "End of events"
	});

	it('returns a NON-2xx when the API is unreachable', async () => {
		const res = await GET(
			event('?before=100', {
				fetch: vi.fn(async () => {
					throw new Error('ECONNREFUSED');
				})
			})
		);
		expect(res.ok).toBe(false);
	});

	it('refuses an unauthenticated caller with a NON-2xx (an expired session is not "end of events")', async () => {
		const res = await GET(event('?before=100', { user: null }));
		expect(res.status).toBe(401);
	});
});

describe('the query the per-firm proxy forwards', () => {
	it('forwards the live poll’s `after` — the other end of the same keyset', async () => {
		// The poll rides this proxy on purpose: same session token, same filters, same failure contract.
		// A separate door would be a second place for the scope to be got wrong.
		const q = await sent('?after=1200&event_type=document.');
		expect(q.get('after')).toBe('1200');
		expect(q.get('event_type')).toBe('document.');
		expect(q.has('before')).toBe(false);
	});

	it('omits `after` when the feed did not ask for one', async () => {
		// `after=` is not "no cursor" at the API — it would be a filter for the empty string.
		expect((await sent('?before=9')).has('after')).toBe(false);
	});

	it('fixes the firm to the section uid and carries the rest of the filters', async () => {
		const q = await sent('?before=9&event_type=action.create-client&actor=ops&method=POST');
		expect(q.get('tenant')).toBe('ten_1743d0');
		expect(q.get('limit')).toBe(String(PAGE_SIZE));
		expect(q.get('before')).toBe('9');
		expect(q.get('event_type')).toBe('action.create-client');
		expect(q.get('actor')).toBe('ops');
		expect(q.get('method')).toBe('POST');
	});

	it('OVERRIDES a `tenant` in the sent query — the scope is not a filter a reader can clear', async () => {
		const q = await sent('?tenant=ten_someone_else&before=9');
		expect(q.getAll('tenant')).toEqual(['ten_1743d0']);
	});

	it('sends `until` as the END of the typed day (the API bound is exclusive)', async () => {
		expect((await sent('?until=2026-08-05')).get('until')).toBe('2026-08-05T23:59:59.999Z');
	});

	it('maps the source filter onto the API bool, and leaves it ABSENT for "all"', async () => {
		expect((await sent('?proxied=proxied')).get('proxied')).toBe('true');
		expect((await sent('?proxied=direct')).get('proxied')).toBe('false');
		// Absent, not `false`: this is the very surface where `proxied=false` would hide the Graphene
		// support activity the firm's trail exists to disclose.
		expect((await sent('?before=1')).has('proxied')).toBe(false);
	});
});
