/**
 * The admin audit-log "Load more" proxy (`/audit/more`) — the DML record-change trail, NOT the action
 * stream its four siblings page.
 *
 * The FAILURE contract: a proxy failure must surface as a NON-2xx, because both audit pages'
 * `loadMore` map `!res.ok` to `null` and the AuditLog shell keeps its "Load more" button on null. The
 * bug this guards against was returning a swallowed 200 {events:[],hasMore:false} on any failure —
 * which the trail read as a real last page and showed "End of the trail", falsely asserting the whole
 * compliance record-change trail was loaded during a transient outage. It now lives once, in
 * `$lib/audit/actions-page`, which is what stops it being five places it can come back; these cases
 * keep it pinned end to end at the route.
 *
 * And the QUERY it forwards, which is this route's real difference from the four: `GET /admin/audit`
 * takes its own vocabulary (`before_id`/`action`/`tenant_id`) and carries no `has_more` flag, so the
 * shared `actionLogQuery` does not apply and a full page is the signal there is more behind it.
 */
import { describe, expect, it, vi } from 'vitest';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { GET } from './+server';

type Ev = Parameters<typeof GET>[0];

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

/** A full page (100) so hasMore is true on success. */
const fullPage = Array.from({ length: 100 }, (_, i) => ({ id: 1000 - i }));

/** A `fetch` double that records the URL it was asked for, so the forwarded query can be read. */
function recorder(body: unknown = { items: fullPage }) {
	const urls: string[] = [];
	const fetch = vi.fn(async (url: string | URL) => {
		urls.push(String(url));
		return json(body);
	});
	return { urls, fetch: fetch as unknown as typeof globalThis.fetch };
}

function event(over: { user?: unknown; search?: string; fetch?: typeof globalThis.fetch } = {}) {
	return {
		url: new URL(`http://admin.test/audit/more${over.search ?? '?before_id=900'}`),
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
	await GET(event({ search, fetch: rec.fetch }));
	return new URL(rec.urls[0]).searchParams;
}

describe('the /audit/more proxy failure contract', () => {
	it('maps a full API page to {events,hasMore:true} at 200', async () => {
		const res = await GET(event());
		expect(res.status).toBe(200);
		const body = (await res.json()) as { events: unknown[]; hasMore: boolean };
		expect(body.events).toHaveLength(100);
		expect(body.hasMore).toBe(true);
	});

	it('returns a NON-2xx when the API errors (so loadMore returns null, trail keeps the button)', async () => {
		const res = await GET(event({ fetch: vi.fn(async () => json({ detail: 'boom' }, 500)) }));
		expect(res.ok).toBe(false); // NOT a swallowed 200 → no false "End of the trail"
	});

	it('returns a NON-2xx when the API is unreachable', async () => {
		const res = await GET(
			event({
				fetch: vi.fn(async () => {
					throw new Error('ECONNREFUSED');
				})
			})
		);
		expect(res.ok).toBe(false);
	});

	it('refuses an unauthenticated caller with a NON-2xx (an expired session is not "end of trail")', async () => {
		const res = await GET(event({ user: null }));
		expect(res.status).toBe(401);
	});

	it('reports a SHORT page as the last one (`list_audit` carries no has_more flag)', async () => {
		const res = await GET(event({ fetch: recorder({ items: [{ id: 7 }] }).fetch }));
		expect(await res.json()).toEqual({ events: [{ id: 7 }], hasMore: false });
	});
});

describe('the query /audit/more forwards', () => {
	/** The record-change trail has its own vocabulary (`before_id`/`action`/`tenant_id`), which is why
	 *  this route builds its own query instead of sharing the action stream's `actionLogQuery`. */
	it('carries the audit-log filters under the audit-log param names', async () => {
		const q = await sent('?before_id=900&action=UPDATE&actor=ops&tenant_id=3&since=2026-08-01');
		expect(q.get('limit')).toBe(String(PAGE_SIZE));
		expect(q.get('before_id')).toBe('900');
		expect(q.get('action')).toBe('UPDATE');
		expect(q.get('actor')).toBe('ops');
		expect(q.get('tenant_id')).toBe('3');
		expect(q.get('since')).toBe('2026-08-01');
		// The action stream's names are NOT this endpoint's — nothing invents them here.
		expect(q.has('event_type')).toBe(false);
		expect(q.has('tenant')).toBe(false);
		expect(q.has('proxied')).toBe(false);
	});

	it('sends `until` as the END of the typed day (the API bound is exclusive)', async () => {
		// The one rule the two trails share, so it comes from the same helper. Sent as the bare date,
		// the whole of the 5th is missing — which reads as "nothing was changed that day".
		expect((await sent('?until=2026-08-05')).get('until')).toBe('2026-08-05T23:59:59.999Z');
	});

	it('omits an empty filter rather than sending it blank (a blank is a filter, not "no filter")', async () => {
		const q = await sent('?before_id=&action=%20&tenant_id=');
		expect(q.has('before_id')).toBe(false);
		expect(q.has('action')).toBe(false);
		expect(q.has('tenant_id')).toBe(false);
	});
});
