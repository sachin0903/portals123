/**
 * The estate-wide Events "Load more" proxy (`/activity/more`).
 *
 * Two things are pinned. The FAILURE contract: a proxy failure must surface as a NON-2xx, because
 * the page's `loadMore` maps `!res.ok` to `null` and the EventStream keeps its "Load more" button on
 * null. A swallowed 200 `{events:[],hasMore:false}` is read as a real last page — "End of events" —
 * which asserts the whole trail was loaded when a request merely broke. And the QUERY it forwards:
 * this proxy now shares the loader's filter→query mapping (`actionLogQuery`), so the two rules that
 * are silent when they drift are worth an assertion at the route as well as at the builder — the
 * exclusive `until` bound, and `proxied` as a tri-state over the API's bool.
 */
import { describe, expect, it, vi } from 'vitest';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { GET } from './+server';

type Ev = Parameters<typeof GET>[0];

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

/** A `fetch` double that records what it was called with, so the forwarded query and headers can be
 *  read back. */
function recorder(body: unknown = { items: [{ id: 41 }], has_more: true }) {
	const urls: string[] = [];
	const inits: (RequestInit | undefined)[] = [];
	const fetch = vi.fn(async (url: string | URL, init?: RequestInit) => {
		urls.push(String(url));
		inits.push(init);
		return json(body);
	});
	return { urls, inits, fetch: fetch as unknown as typeof globalThis.fetch };
}

function event(
	search = '?before=100',
	over: { user?: unknown; fetch?: typeof globalThis.fetch } = {}
) {
	return {
		url: new URL(`http://admin.test/activity/more${search}`),
		locals: {
			user: over.user === undefined ? { id: 'ops_1' } : over.user,
			accessToken: 'token'
		},
		fetch: over.fetch ?? recorder().fetch
	} as unknown as Ev;
}

/** The query the API was actually asked for. */
async function sent(search: string): Promise<URLSearchParams> {
	const rec = recorder();
	await GET(event(search, { fetch: rec.fetch }));
	return new URL(rec.urls[0]).searchParams;
}

describe('the /activity/more proxy failure contract', () => {
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

describe('the query /activity/more forwards', () => {
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

	it('asks the action stream for the keyset page it was given', async () => {
		const q = await sent('?before=900');
		expect(q.get('limit')).toBe(String(PAGE_SIZE));
		expect(q.get('before')).toBe('900');
	});

	it('carries every filter, the firm one included (this surface is cross-firm)', async () => {
		const q = await sent(
			'?before=9&tenant=ten_1&event_type=action.create-client&actor=ops&method=POST&since=2026-08-01'
		);
		expect(q.get('tenant')).toBe('ten_1');
		expect(q.get('event_type')).toBe('action.create-client');
		expect(q.get('actor')).toBe('ops');
		expect(q.get('method')).toBe('POST');
		expect(q.get('since')).toBe('2026-08-01');
	});

	it('sends `until` as the END of the typed day (the API bound is exclusive)', async () => {
		// Sent as the bare date, the whole of the 5th is missing from the answer — which reads as
		// "nobody did anything that day", never as a bug.
		const q = await sent('?until=2026-08-05');
		expect(q.get('until')).toBe('2026-08-05T23:59:59.999Z');
	});

	it('maps the source filter onto the API bool, and leaves it ABSENT for "all"', async () => {
		expect((await sent('?proxied=proxied')).get('proxied')).toBe('true');
		expect((await sent('?proxied=direct')).get('proxied')).toBe('false');
		// Absent, not `false`: `proxied=false` would hide every action Graphene support took on a
		// firm's behalf, which is the opposite of unfiltered.
		expect((await sent('?before=1')).has('proxied')).toBe(false);
		expect((await sent('?proxied=')).has('proxied')).toBe(false);
	});

	it('omits an empty filter rather than sending it blank (a blank is a filter, not "no filter")', async () => {
		const q = await sent('?before=&actor=%20&event_type=');
		expect(q.has('before')).toBe(false);
		expect(q.has('actor')).toBe(false);
		expect(q.has('event_type')).toBe(false);
	});

	it("sends the session's own token as a bearer header", async () => {
		const rec = recorder();
		await GET(event('?before=1', { fetch: rec.fetch }));
		expect((rec.inits[0]?.headers as Record<string, string>).Authorization).toBe('Bearer token');
	});
});
