/**
 * A client's Logs "Load more" proxy (`/clients/[uid]/activity/more`).
 *
 * Two things are pinned. The FAILURE contract: a proxy failure must surface as a NON-2xx, because the
 * page's `loadMore` maps `!res.ok` to `null` and the EventStream keeps its "Load more" button on null
 * — a swallowed 200 `{events:[],hasMore:false}` reads as a real last page ("End of events") and
 * silently truncates the client's record at whatever page the outage hit. And the SCOPE: `contact` is
 * the section's client, set after the shared query builder, so a `?contact=` in the query the browser
 * sent cannot re-point this slice at another client — and no `tenant` is forwarded at all, because the
 * firm endpoint has no such param (its scope is the session's).
 */
import { describe, expect, it, vi } from 'vitest';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { GET } from './+server';

type Ev = Parameters<typeof GET>[0];

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

/**
 * A `fetch` double that records the STREAM url it was asked for, so the forwarded query can be read.
 *
 * The handler resolves the route's uid to the API's serial `contact` id first (a standalone
 * `+server.ts` cannot read the record's layout data), so the double answers that read too — and
 * deliberately does not record it: every assertion below is about the query sent to the stream.
 */
function recorder(body: unknown = { items: [{ id: 41 }], has_more: true }) {
	const urls: string[] = [];
	const fetch = vi.fn(async (url: string | URL) => {
		if (String(url).includes('/crm/contacts/')) {
			return json({ contact: { id: 42, uid: 'con_9b2e' } });
		}
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
		params: { uid: over.uid ?? 'con_9b2e' },
		url: new URL(`http://portal.test/clients/con_9b2e/events/more${search}`),
		locals: {
			user: over.user === undefined ? { id: 'user_1' } : over.user,
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

describe("the client Events /more proxy's failure contract", () => {
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

describe('the query the client Events proxy forwards', () => {
	it('fixes the client to the section id and carries the rest of the filters', async () => {
		const q = await sent('?before=9&event_type=action.upload-document&actor=amy&method=POST');
		// The uid — `?contact=` is a filter value in a URL, so it names the client the way
		// every other URL does.
		expect(q.get('contact')).toBe('con_9b2e');
		expect(q.get('limit')).toBe(String(PAGE_SIZE));
		expect(q.get('before')).toBe('9');
		expect(q.get('event_type')).toBe('action.upload-document');
		expect(q.get('actor')).toBe('amy');
		expect(q.get('method')).toBe('POST');
	});

	it('OVERRIDES a `contact` in the sent query — the scope is not a filter a reader can clear', async () => {
		const q = await sent('?contact=999&before=9');
		expect(q.getAll('contact')).toEqual(['con_9b2e']);
	});

	it('never forwards a `tenant` — the firm endpoint has no such param, the session is the scope', async () => {
		const q = await sent('?tenant=ten_someone_else&before=9');
		expect(q.has('tenant')).toBe(false);
	});

	it('sends `until` as the END of the typed day (the API bound is exclusive)', async () => {
		expect((await sent('?until=2026-08-05')).get('until')).toBe('2026-08-05T23:59:59.999Z');
	});

	it('maps the source filter onto the API bool, and leaves it ABSENT for "all"', async () => {
		expect((await sent('?proxied=proxied')).get('proxied')).toBe('true');
		expect((await sent('?proxied=direct')).get('proxied')).toBe('false');
		// Absent, not `false`: `proxied=false` would hide every action Graphene support took on this
		// client, which is exactly what the firm's own trail exists to disclose.
		expect((await sent('?before=1')).has('proxied')).toBe(false);
	});
});
